"""
Tamper-evident audit log — hash-chained, append-only JSONL.

Every record is cryptographically linked to the one before it:

    entry_hash = sha256(prev_hash + canonical(record_without_entry_hash))

The first record links to the genesis hash (64 zeros). Editing, deleting, or
reordering any record breaks the chain at that point, which `verify()` detects.
This turns `.agents/logs/audit.jsonl` from a plain log (silently editable) into
evidence: a reviewer can prove the trail has not been altered since it was written.

All MinusOps components (plan_gate, approval, audit_logger) append through here so
there is a single, continuous chain across the whole control plane.
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
    """Return the entry_hash of the final record, or GENESIS for an empty/absent log."""
    if not os.path.exists(path):
        return GENESIS
    last = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                last = line
    if not last:
        return GENESIS
    try:
        return json.loads(last).get("entry_hash", GENESIS)
    except json.JSONDecodeError:
        return GENESIS


# REAL BUG FIXED (2026-07-12), root-caused and reproduced live before changing anything (see
# docs/audit_chain_lock_fix_scope.md): the prior design created the lock sidecar via
# `os.open(path, O_CREAT|O_EXCL)` and deleted it in __exit__ via `os.remove()`. On Windows, a
# concurrent acquire attempt racing that delete can get `PermissionError(13, 'Permission
# denied')` from the CREATE call instead of either succeeding or raising `FileExistsError` --
# NTFS's delete-then-recreate semantics for the same path have no equivalent to POSIX's atomic
# unlink-while-open. Confirmed directly: 852/4800 cycles in a tight repro, and the real
# 8-thread/15-append test failed 2/8 consecutive local runs with this exact signature, matching
# real CI failures byte-for-byte. The old retry loop only caught FileExistsError, so this
# PermissionError propagated straight out of append() as a hard, unhandled exception.
#
# FIX: remove the delete-recreate cycle entirely rather than widen what's caught. The lock
# sidecar is now created once and NEVER deleted; acquire/release toggle an OS-native advisory
# region lock on that persistent, reopened-each-time file (`fcntl.flock` on POSIX, `msvcrt.
# locking` on Windows) instead of the file's mere existence. There is no longer a delete window
# for Windows to race against, so there is nothing to catch a wrong error from.
#
# This does NOT broadly catch PermissionError (that would trade a fail-loud crash for a
# fail-open hang on a genuine, non-transient permission denial -- strictly worse for a
# tamper-evidence lock). Three outcomes are kept structurally distinct:
#   1. Can't even open the lock file (bad directory, real ACL denial) -- raises immediately,
#      outside the retry loop, never caught here.
#   2. The region is held by another writer right now -- the ONLY retried case, matched by a
#      narrow, single-cause signal per platform: `BlockingIOError` from `fcntl.flock(...,
#      LOCK_NB)` (POSIX's own contention signal, verified against its documented semantics),
#      or `OSError` with `errno == EACCES` specifically from `msvcrt.locking(..., LK_NBLCK)`
#      (confirmed empirically on real Windows -- this is that call's own specific, documented
#      "already locked" signal, not a generic permission error; verified directly that a
#      DIFFERENT OSError would NOT be swallowed by this narrow check).
#   3. Anything else -- re-raised immediately, never retried.
#
# Belt-and-suspenders intra-process threading.Lock, not assumed unnecessary: this session's own
# dev environment is Windows-only, so `fcntl.flock`'s open-file-description semantics across
# threads within one process (each opening its own fd) could not be empirically verified here
# the way `msvcrt.locking`'s thread behavior was (confirmed directly: 12 threads x 200
# non-atomic increments through msvcrt.locking, zero races). Rather than assume flock() behaves
# the same way on a platform this session cannot test, intra-process mutual exclusion is
# guaranteed independently via a real threading.Lock per resolved lock path, so correctness
# never depends on that unverified assumption holding on POSIX.
#
# Crashed-writer behavior CHANGES for the better: OS-level advisory locks are released by the
# kernel the moment the holding process's file descriptors are torn down -- including a crash or
# kill, not just a clean exit (confirmed directly: killing a real subprocess mid-lock, a fresh
# acquire attempt afterward succeeds immediately, no manual `.lock` file cleanup needed). The
# old "remove the .lock file manually if a prior writer crashed" instruction no longer applies.

_thread_locks_guard = threading.Lock()
_thread_locks = {}


def _thread_lock_for(path):
    """One threading.Lock per resolved lock path, process-wide -- see the module-level note
    above on why this is not assumed redundant with the OS-level lock."""
    with _thread_locks_guard:
        lock = _thread_locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _thread_locks[path] = lock
        return lock


class _Contended(Exception):
    """Internal sentinel: the lock region is held by someone else right now. Always retried
    until the deadline -- never confused with a genuine failure to acquire (see module note)."""


class _AppendLock:
    """Cross-platform mutual-exclusion lock for append(): a threading.Lock for intra-process
    safety, plus an OS-native advisory region lock (fcntl.flock / msvcrt.locking) on a
    persistent, never-deleted sidecar file for inter-process safety. See the module-level
    comment above `_thread_locks_guard` for the full rationale and the bug this replaced."""

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
                # needs a byte to exist there -- direct testing on this session's Windows
                # machine showed CRT auto-extension already makes this work on a genuinely
                # empty file, but this write is kept as a defensive guarantee across Windows/
                # Python versions this session could not test, not because it was proven
                # necessary here.
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
    hash-chaining was introduced — no entry_hash) while still proving the chained segment is
    intact and detecting tampering. Returns a dict:

        {ok, legacy_count, chained_count, errors, intact}

    Rules that keep this honest (a legacy prefix is NOT a free pass to drop records):
      - all legacy (un-chained) records must precede the first chained record; a legacy record
        appearing *after* chaining began is flagged (possible insertion / downgrade);
      - the first chained record must link to GENESIS — so chained records cannot be silently
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


def seal(path):
    """
    One-time migration for a log that predates (or pre-dates the current format of) hash-chaining:
    archive the existing file to `<path>.<ts>.bak`, record its SHA-256, and start a FRESH chain
    whose first record is an anchor committing to that digest. The old content is preserved as
    evidence (and its hash is in the chain), but verification proceeds against a clean, continuous
    chain from here. This is the honest alternative to weakening verify() to ignore mismatches.

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
    return append(path, {
        "action": "chain-anchor",
        "component": "audit_chain.seal",
        "archived_path": os.path.basename(backup),
        "archived_sha256": digest,
        "archived_bytes": len(raw),
        "note": "legacy/pre-chaining audit records archived; fresh chain starts here",
        "timestamp": ts,
    })


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tamper-evident audit chain")
    ap.add_argument("command", choices=["verify", "seal"])
    ap.add_argument("--path", default=os.path.join(os.getcwd(), ".agents", "logs", "audit.jsonl"))
    args = ap.parse_args(argv)
    if args.command == "seal":
        entry = seal(args.path)
        if entry is None:
            print(f"[audit] nothing to seal (empty/absent): {args.path}")
            return 0
        print(f"[audit] sealed legacy log -> {entry['archived_path']} "
              f"(sha256 {entry['archived_sha256'][:12]}…); fresh chain anchored at {args.path}")
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
