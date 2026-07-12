"""
The audit trail must be tamper-evident: any edit, deletion, or reorder is detectable.
"""
import json
import threading
import time

import audit_chain


def test_chain_appends_and_verifies(tmp_path):
    log = tmp_path / "audit.jsonl"
    audit_chain.append(str(log), {"action": "plan", "status": "OK"})
    audit_chain.append(str(log), {"action": "approve", "status": "APPROVED"})
    audit_chain.append(str(log), {"action": "apply", "status": "OK"})

    ok, errors = audit_chain.verify(str(log))
    assert ok, errors

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["prev_hash"] == audit_chain.GENESIS
    assert rows[1]["prev_hash"] == rows[0]["entry_hash"]
    assert rows[2]["prev_hash"] == rows[1]["entry_hash"]


def test_modifying_a_record_breaks_the_chain(tmp_path):
    log = tmp_path / "audit.jsonl"
    audit_chain.append(str(log), {"action": "apply", "status": "OK"})
    audit_chain.append(str(log), {"action": "apply", "status": "OK"})

    rows = log.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(rows[0])
    tampered["status"] = "DENIED"           # silently rewrite history
    rows[0] = json.dumps(tampered)
    log.write_text("\n".join(rows) + "\n", encoding="utf-8")

    ok, errors = audit_chain.verify(str(log))
    assert not ok
    assert any("entry_hash mismatch" in e for e in errors)


def test_deleting_a_record_breaks_the_chain(tmp_path):
    log = tmp_path / "audit.jsonl"
    audit_chain.append(str(log), {"action": "a"})
    audit_chain.append(str(log), {"action": "b"})
    audit_chain.append(str(log), {"action": "c"})

    rows = log.read_text(encoding="utf-8").splitlines()
    del rows[1]  # remove the middle record
    log.write_text("\n".join(rows) + "\n", encoding="utf-8")

    ok, errors = audit_chain.verify(str(log))
    assert not ok
    assert any("prev_hash" in e for e in errors)


def test_chain_status_tolerates_legacy_prefix(tmp_path):
    log = tmp_path / "audit.jsonl"
    # Records written before chaining existed (no prev_hash/entry_hash) ...
    with open(log, "w", encoding="utf-8") as f:
        f.write(json.dumps({"action": "legacy-plan", "status": "OK"}) + "\n")
        f.write(json.dumps({"action": "legacy-apply", "status": "OK"}) + "\n")
    # ... then chaining begins and appends link to GENESIS (last line has no entry_hash).
    audit_chain.append(str(log), {"action": "plan", "status": "OK"})
    audit_chain.append(str(log), {"action": "apply", "status": "OK"})

    status = audit_chain.chain_status(str(log))
    assert status["intact"] and status["ok"]
    assert status["legacy_count"] == 2 and status["chained_count"] == 2


def test_chain_status_flags_tampered_chained_segment(tmp_path):
    log = tmp_path / "audit.jsonl"
    with open(log, "w", encoding="utf-8") as f:
        f.write(json.dumps({"action": "legacy"}) + "\n")
    audit_chain.append(str(log), {"action": "a"})
    audit_chain.append(str(log), {"action": "b"})

    rows = log.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(rows[1])           # first chained record
    tampered["action"] = "rewritten"
    rows[1] = json.dumps(tampered)
    log.write_text("\n".join(rows) + "\n", encoding="utf-8")

    status = audit_chain.chain_status(str(log))
    assert not status["intact"]
    assert any("entry_hash mismatch" in e for e in status["errors"])


def test_seal_archives_legacy_log_and_starts_clean_chain(tmp_path):
    log = tmp_path / "audit.jsonl"
    # A log with pre-chaining records AND an old-format chained record (bad entry_hash).
    with open(log, "w", encoding="utf-8") as f:
        f.write(json.dumps({"action": "legacy-1"}) + "\n")
        f.write(json.dumps({"action": "old-chained", "prev_hash": audit_chain.GENESIS,
                            "entry_hash": "deadbeef"}) + "\n")
    assert audit_chain.chain_status(str(log))["intact"] is False  # cannot verify as-is

    entry = audit_chain.seal(str(log))
    assert entry["action"] == "chain-anchor" and entry["archived_sha256"]
    backup = tmp_path / entry["archived_path"]
    assert backup.exists()  # old content preserved as evidence

    # New chain is clean and continues to verify after further appends.
    audit_chain.append(str(log), {"action": "plan"})
    status = audit_chain.chain_status(str(log))
    assert status["intact"] and status["chained_count"] == 2 and status["legacy_count"] == 0
    ok, _ = audit_chain.verify(str(log))
    assert ok


def test_append_is_safe_under_concurrent_writers(tmp_path):
    # Without a lock, last_hash() (read) and the write in append() are two separate steps:
    # concurrent callers can read the same prev_hash and both append, forking the chain. This
    # proves the fix actually does what it exists for -- not just that append() still works
    # solo, but that the chain stays linear and uncorrupted under real contention.
    log = tmp_path / "audit.jsonl"
    n_threads = 8
    appends_per_thread = 15
    errors = []

    def worker(worker_id):
        try:
            for i in range(appends_per_thread):
                audit_chain.append(str(log), {"worker": worker_id, "seq": i})
        except Exception as exc:  # pragma: no cover - failure path asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors

    lines = [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == n_threads * appends_per_thread  # no lost writes

    ok, chain_errors = audit_chain.verify(str(log))
    assert ok, chain_errors  # the chain stayed linear -- no forked prev_hash from a race

    # REAL FIX (2026-07-12): the lock sidecar is now created ONCE and deliberately never
    # deleted -- acquire/release toggle an OS-native advisory region lock on this persistent
    # file instead of the file's existence (see audit_chain.py's own module-level comment for
    # the full root-cause writeup). A prior version of this test asserted the sidecar was gone
    # after every writer finished; that assumption is now wrong by design, not a regression --
    # the delete-then-recreate cycle that assumption depended on is exactly what raced on
    # Windows (PermissionError(13) from a concurrent create), so removing the delete removes
    # the race it was in.
    assert (tmp_path / "audit.jsonl.lock").exists()


def test_append_lock_serializes_threads_not_just_avoids_exceptions(tmp_path):
    """A green run because timing happened not to overlap is not proof of mutual exclusion --
    this directly records enter/exit timestamps around the real critical section (_AppendLock
    itself, not append()'s higher-level behavior) across many threads and asserts NO TWO
    INTERVALS OVERLAP. This is the caution the approved fix scope raised explicitly: threads
    sharing one process are a different hazard than separate processes, and this proves
    real serialization, not merely "no exception was raised."""
    log = str(tmp_path / "audit.jsonl")
    intervals = []
    intervals_guard = threading.Lock()
    n_threads = 12
    iterations = 40

    def worker():
        for _ in range(iterations):
            with audit_chain._AppendLock(log):
                start = time.monotonic()
                time.sleep(0.001)  # widen the critical section to make an overlap easy to catch
                end = time.monotonic()
            with intervals_guard:
                intervals.append((start, end))

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(intervals) == n_threads * iterations
    intervals.sort()
    for (_, prev_end), (next_start, _) in zip(intervals, intervals[1:]):
        assert next_start >= prev_end, (
            f"overlapping critical sections detected: {prev_end} (prior exit) > "
            f"{next_start} (next entry) -- the lock did not serialize these threads"
        )


def test_append_lock_gives_up_when_genuinely_and_persistently_held(tmp_path, monkeypatch):
    """Rewritten crashed-writer analog (2026-07-12): under OS-level advisory locking, a bare
    leftover .lock FILE with no live holder is not contended at all -- see the dedicated test
    below proving exactly that. To prove append() still gives up rather than hangs when a lock
    is genuinely, persistently held, this holds a REAL, live OS-level lock on a background
    thread for longer than the (shortened) timeout, then asserts TimeoutError."""
    monkeypatch.setattr(audit_chain, "_LOCK_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(audit_chain, "_LOCK_POLL_SECONDS", 0.01)
    log = tmp_path / "audit.jsonl"

    holder_ready = threading.Event()
    release_holder = threading.Event()

    def hold_lock():
        with audit_chain._AppendLock(str(log)):
            holder_ready.set()
            release_holder.wait(timeout=5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    holder_ready.wait(timeout=5)
    try:
        try:
            audit_chain.append(str(log), {"action": "should-not-hang"})
            assert False, "expected TimeoutError"
        except TimeoutError as exc:
            assert "audit-chain lock" in str(exc)
    finally:
        release_holder.set()
        holder.join(timeout=5)


def test_append_lock_fails_fast_on_a_genuine_access_error_not_a_10s_hang(tmp_path):
    """Negative control proving fail-loud is real, not traded for a fail-open hang (the core
    requirement of the approved fix -- broadly catching PermissionError would risk exactly this
    failing silently for the full timeout). A path whose parent component is itself a FILE, not
    a directory, can never succeed no matter how long this waits -- confirmed to raise
    IMMEDIATELY (not after the timeout), because opening the lock file happens once, outside
    the retry loop, and a genuine failure there is never treated as retriable contention."""
    import time as _time
    blocker = tmp_path / "not_a_directory.txt"
    blocker.write_text("x", encoding="utf-8")
    bad_log = str(blocker / "subdir" / "audit.jsonl")

    start = _time.monotonic()
    try:
        with audit_chain._AppendLock(bad_log):
            pass
        assert False, "expected an OSError, not a successful acquire"
    except TimeoutError:
        assert False, "a genuine access error must not be retried into a timeout"
    except OSError:
        elapsed = _time.monotonic() - start
        assert elapsed < 1.0, f"took {elapsed}s -- looks like it was retried, not raised immediately"


def test_append_lock_stale_leftover_file_needs_no_manual_cleanup(tmp_path):
    """Real, disclosed behavioral improvement: a bare leftover .lock file with no live process
    holding the OS-level lock on it (the old crashed-writer failure mode) is simply not
    contended under the new design -- confirmed directly here, not assumed. The kernel releases
    a process's advisory locks the moment its descriptors are torn down, including on a crash,
    so a merely-stale file left behind never needs the "remove it manually" step the old design
    required."""
    log = tmp_path / "audit.jsonl"
    stale_lock = tmp_path / "audit.jsonl.lock"
    stale_lock.write_bytes(b"")  # a leftover file, but genuinely nobody holds a lock on it

    entry = audit_chain.append(str(log), {"action": "should-not-need-cleanup"})
    assert entry["entry_hash"]
    ok, errors = audit_chain.verify(str(log))
    assert ok, errors


def test_chain_status_flags_legacy_record_inserted_after_chaining(tmp_path):
    log = tmp_path / "audit.jsonl"
    audit_chain.append(str(log), {"action": "a"})
    # An un-chained record appearing after chaining began = downgrade/insertion attempt.
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"action": "smuggled", "status": "APPROVED"}) + "\n")

    status = audit_chain.chain_status(str(log))
    assert not status["intact"]
    assert any("after chaining began" in e for e in status["errors"])
