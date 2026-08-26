"""
Tamper-evident audit log -- hash-chained, append-only JSONL.

Every record is cryptographically linked to the one before it:

    entry_hash = sha256(prev_hash + canonical(record_without_entry_hash))

The first record links to the genesis hash (64 zeros). Editing, deleting, or
reordering any record breaks the chain at that point, which `verify()` detects.
This turns `.agents/logs/audit.jsonl` from a plain log (silently editable) into
evidence: a reviewer can prove the trail has not been altered since it was written.

All MinusOps components (plan_gate, approval, audit_logger) append through here so
there is a single, continuous chain across the whole control plane.

Depends on: nothing (stdlib only)
Shells out to: nothing
Used by: core/governance/approval.py, core/governance/audit_logger.py,
    core/governance/ephemeral_apply.py, core/governance/plan_gate.py,
    core/generation/synthesizer.py, core/reporting/minusctl.py
"""
import argparse
import errno
import hashlib
import json
import os
import sys
import threading
import time

if os.name == "nt":
    import msvcrt
else:
    import fcntl

GENESIS = "0" * 64
_LOCK_SUFFIX = ".lock"
_LOCK_TIMEOUT_SECONDS = 10
_LOCK_POLL_SECONDS = 0.05


def _canonical(record):
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def _entry_hash(prev_hash, record_without_hash):
    return hashlib.sha256((prev_hash + _canonical(record_without_hash)).encode("utf-8")).hexdigest()


def last_hash(path):
    """Return the entry_hash of the last CHAINED record, or GENESIS for an empty/absent log.

    The last chained record, not the last line. A record appended past the chain -- by a
    writer using a bare `open(path, "a")` instead of append() -- carries no entry_hash, and
    taking the final line at face value would hand the next writer GENESIS, making a record
    written mid-log claim to be the first one ever. That turned one bug into two in this
    repo: 85 unchained reconciliation records were written, and 24 legitimate entries that
    followed them were corrupted by exactly this fallback.

    Skipping back to the last chained entry conceals nothing -- chain_status() still reports
    the unchained record as a possible insertion. It only stops the damage spreading to
    records that were written correctly.
    """
    if not os.path.exists(path):
        return GENESIS
    found = GENESIS
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                entry_hash = json.loads(line).get("entry_hash")
            except json.JSONDecodeError:
                continue
            if entry_hash:
                found = entry_hash
    return found


# LOCK DESIGN -- three constraints, each of which caused a real failure when violated.
#
# 1. The lock sidecar is created once and NEVER deleted. Locking by file EXISTENCE
#    (`os.open(O_CREAT|O_EXCL)` on acquire, `os.remove()` on release) is broken on Windows: an
#    acquire racing the delete gets `PermissionError(13)` from the CREATE call instead of
#    `FileExistsError`, because NTFS has no equivalent to POSIX's atomic unlink-while-open.
#    Mutual exclusion comes from an OS-native advisory REGION lock on the persistent,
#    reopened-each-time file (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows). Do not
#    reintroduce a delete-on-release: there would again be a window for Windows to race.
#
# 2. Do NOT broadly catch PermissionError. That trades a fail-loud crash for a fail-open hang
#    on a genuine, non-transient denial -- strictly worse for a tamper-evidence lock. Three
#    outcomes stay structurally distinct:
#      a. Cannot open the lock file at all (bad directory, real ACL denial) -- raises
#         immediately, outside the retry loop, never caught here.
#      b. The region is held by another writer right now -- the ONLY retried case, matched by
#         a narrow, single-cause signal per platform: `BlockingIOError` from `fcntl.flock(...,
#         LOCK_NB)`, or `OSError` with `errno == EACCES` specifically from
#         `msvcrt.locking(..., LK_NBLCK)`. Each is that call's own documented "already locked"
#         signal; a different OSError must not be swallowed by the check.
#      c. Anything else -- re-raised immediately, never retried.
#
# 3. The intra-process threading.Lock is not redundant with the OS lock. `fcntl.flock`'s
#    open-file-description semantics across threads in one process (each opening its own fd)
#    were never verified on POSIX here, so intra-process exclusion is guaranteed independently
#    by a real threading.Lock per resolved lock path rather than assumed from flock().
#
# A crashed writer needs no cleanup: the kernel releases an advisory lock when the holder's
# file descriptors are torn down, crash or kill included. Never add a "delete the stale .lock
# file by hand" instruction -- there is no stale state to delete.

_thread_locks_guard = threading.Lock()
_thread_locks = {}


def _thread_lock_for(path):
    """One threading.Lock per resolved lock path, process-wide -- see constraint 3 in the
    LOCK DESIGN note above on why this is not redundant with the OS-level lock."""
    with _thread_locks_guard:
        lock = _thread_locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _thread_locks[path] = lock
        return lock


class _Contended(Exception):
    """Internal sentinel: the lock region is held by someone else right now. Always retried
    until the deadline -- never confused with a genuine failure to acquire (LOCK DESIGN 2)."""


class _AppendLock:
    """Cross-platform mutual-exclusion lock for append(): a threading.Lock for intra-process
    safety, plus an OS-native advisory region lock (fcntl.flock / msvcrt.locking) on a
    persistent, never-deleted sidecar file for inter-process safety. See the LOCK DESIGN note
    above `_thread_locks_guard` for the three constraints this shape has to satisfy."""

    def __init__(self, path):
        self._lock_path = path + _LOCK_SUFFIX
        self._thread_lock = _thread_lock_for(self._lock_path)
        self._thread_lock_acquired = False
        self._file = None

    def __enter__(self):
        if not self._thread_lock.acquire(timeout=_LOCK_TIMEOUT_SECONDS):
            raise TimeoutError(
                f"could not acquire audit-chain lock at {self._lock_path!r} within "
                f"{_LOCK_TIMEOUT_SECONDS}s (intra-process contention)"
            )
        self._thread_lock_acquired = True
        try:
            self._file = open(self._lock_path, "a+b")
            if os.name == "nt":
                # msvcrt.locking locks a byte range starting at the current file position and
                # needs a byte to exist there. CRT auto-extension covers the empty-file case on
                # the Windows/Python versions tested; this write is a defensive guarantee for
                # the ones that were not, so do not remove it as dead code.
                self._file.seek(0, os.SEEK_END)
                if self._file.tell() == 0:
                    self._file.write(b"\0")
                    self._file.flush()
                self._file.seek(0)

            deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    if os.name == "nt":
                        try:
                            msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
                        except OSError as exc:
                            if exc.errno != errno.EACCES:
                                raise
                            raise _Contended() from None
                    else:
                        try:
                            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            raise _Contended() from None
                    return self
                except _Contended:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"could not acquire audit-chain lock at {self._lock_path!r} within "
                            f"{_LOCK_TIMEOUT_SECONDS}s -- another writer holds it, or this is a "
                            "persistent permissions/filesystem issue preventing the lock region "
                            "from ever being released"
                        )
                    time.sleep(_LOCK_POLL_SECONDS)
        except Exception:
            if self._file is not None:
                self._file.close()
                self._file = None
            self._thread_lock.release()
            self._thread_lock_acquired = False
            raise

    def __exit__(self, exc_type, exc, tb):
        try:
            if os.name == "nt":
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
            if self._thread_lock_acquired:
                self._thread_lock.release()
                self._thread_lock_acquired = False


def append(path, record):
    """Append a record to the chained log and return the stored entry (with hashes)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with _AppendLock(path):
        prev = last_hash(path)
        entry = dict(record)
        entry["prev_hash"] = prev
        entry["entry_hash"] = _entry_hash(prev, entry)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    return entry


def verify(path):
    """Return (ok, errors). Walks the chain and re-derives every link."""
    errors = []
    if not os.path.exists(path):
        return True, errors
    prev = GENESIS
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {i}: invalid JSON ({exc})")
                break
            if rec.get("prev_hash") != prev:
                errors.append(f"line {i}: prev_hash does not match prior entry (chain broken or reordered)")
            without = {k: v for k, v in rec.items() if k != "entry_hash"}
            recalculated = _entry_hash(prev, without)
            if rec.get("entry_hash") != recalculated:
                errors.append(f"line {i}: entry_hash mismatch (record was modified)")
            prev = rec.get("entry_hash", prev)
    return (not errors), errors


def chain_status(path):
    """
    Richer view than verify(): tolerate a *legacy unchained prefix* (records written before
    hash-chaining was introduced -- no entry_hash) while still proving the chained segment is
    intact and detecting tampering. Returns a dict:

        {ok, legacy_count, chained_count, errors, intact}

    Rules that keep this honest (a legacy prefix is NOT a free pass to drop records):
      - all legacy (un-chained) records must precede the first chained record; a legacy record
        appearing *after* chaining began is flagged (possible insertion / downgrade);
      - the first chained record must link to GENESIS -- so chained records cannot be silently
        deleted from the front (that would leave a non-GENESIS prev_hash with nothing before it);
      - every chained link is re-derived exactly as in verify().
    """
    result = {"ok": True, "legacy_count": 0, "chained_count": 0, "errors": [], "intact": True}
    if not os.path.exists(path):
        return result
    prev = GENESIS
    seen_chained = False
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                result["errors"].append(f"line {i}: invalid JSON ({exc})")
                break
            chained = "entry_hash" in rec
            if not chained:
                if seen_chained:
                    result["errors"].append(f"line {i}: un-chained record after chaining began (possible insertion)")
                else:
                    result["legacy_count"] += 1
                continue
            if not seen_chained:
                seen_chained = True
                if rec.get("prev_hash") != GENESIS:
                    result["errors"].append(f"line {i}: first chained record does not link to genesis "
                                            "(chained records may have been removed from the front)")
                prev = GENESIS
            if rec.get("prev_hash") != prev:
                result["errors"].append(f"line {i}: prev_hash does not match prior entry (chain broken or reordered)")
            without = {k: v for k, v in rec.items() if k != "entry_hash"}
            if rec.get("entry_hash") != _entry_hash(prev, without):
                result["errors"].append(f"line {i}: entry_hash mismatch (record was modified)")
            prev = rec.get("entry_hash", prev)
            result["chained_count"] += 1
    result["intact"] = not result["errors"]
    result["ok"] = result["intact"]
    return result


DEFAULT_SEAL_REASON = "legacy/pre-chaining audit records archived; fresh chain starts here"


def seal(path, reason=None, operator=None):
    """
    Archive a log that can no longer verify, and anchor a fresh chain to its digest.

    The existing file moves to `<path>.<ts>.bak`, its SHA-256 goes into the first record of a
    new chain, and verification proceeds cleanly from there. Nothing is deleted: the old
    content survives as evidence and the chain commits to exactly those bytes. This is the
    honest alternative to weakening verify() until the mismatches stop being reported.

    `reason` MATTERS and should almost always be given. The default note says the records
    predate chaining, which was true for the migration this was written for. It was false for
    the case that actually arose: 85 records written PAST an existing chain by a buggy writer.
    An anchor that misdescribes the break is worse than none, because the next reader concludes
    the log predates hash-chaining and stops looking for the defect.

    Returns the anchor entry, or None if there was nothing to seal.
    """
    if not os.path.exists(path) or not os.path.getsize(path):
        return None
    import datetime
    raw = open(path, "rb").read()
    digest = hashlib.sha256(raw).hexdigest()
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = f"{path}.{ts}.bak"
    os.replace(path, backup)
    record = {
        "action": "chain-anchor",
        "component": "audit_chain.seal",
        "archived_path": os.path.basename(backup),
        "archived_sha256": digest,
        "archived_bytes": len(raw),
        "note": reason or DEFAULT_SEAL_REASON,
        "timestamp": ts,
    }
    if operator:
        record["operator"] = operator
    return append(path, record)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tamper-evident audit chain")
    ap.add_argument("command", choices=["verify", "seal"])
    ap.add_argument("--path", default=os.path.join(os.getcwd(), ".agents", "logs", "audit.jsonl"))
    ap.add_argument("--reason", help="Why this log is being sealed. Recorded in the anchor.")
    ap.add_argument("--operator", help="Who sealed it. Recorded in the anchor.")
    args = ap.parse_args(argv)
    if args.command == "seal":
        entry = seal(args.path, reason=args.reason, operator=args.operator)
        if entry is None:
            print(f"[audit] nothing to seal (empty/absent): {args.path}")
            return 0
        print(f"[audit] sealed legacy log -> {entry['archived_path']} "
              f"(sha256 {entry['archived_sha256'][:12]}...); fresh chain anchored at {args.path}")
        return 0
    ok, errors = verify(args.path)
    if ok:
        print(f"[audit] chain OK: {args.path}")
        return 0
    print(f"[audit] CHAIN INTEGRITY FAILURE: {args.path}", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
