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
import hashlib
import json
import os
import sys
import time

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


class _AppendLock:
    """Cross-platform mutual-exclusion lock for append(), via an atomically-created sidecar
    file -- `os.O_CREAT | os.O_EXCL` is atomic on both POSIX and Windows, so this needs no
    fcntl/msvcrt split and no new dependency. Without this, last_hash() (read) and the
    subsequent write in append() are two separate steps: two concurrent writers can both read
    the same prev_hash and both append, forking the chain. That is a completeness bug in the
    log independent of any audit standard -- the same class of defect as a silent-corruption
    bug in the tool's own trust primitive, not a compliance nicety."""

    def __init__(self, path):
        self._lock_path = path + _LOCK_SUFFIX
        self._fd = None

    def __enter__(self):
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        while True:
            try:
                self._fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"could not acquire audit-chain lock at {self._lock_path!r} within "
                        f"{_LOCK_TIMEOUT_SECONDS}s -- a prior writer may have crashed while "
                        "holding it; remove the .lock file manually if so"
                    )
                time.sleep(_LOCK_POLL_SECONDS)

    def __exit__(self, exc_type, exc, tb):
        if self._fd is not None:
            os.close(self._fd)
        try:
            os.remove(self._lock_path)
        except OSError:
            pass


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
