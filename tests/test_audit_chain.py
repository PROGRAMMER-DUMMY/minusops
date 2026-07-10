"""
The audit trail must be tamper-evident: any edit, deletion, or reorder is detectable.
"""
import json
import threading

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

    # The lock's own sidecar file must not be left behind once every writer has finished.
    assert not (tmp_path / "audit.jsonl.lock").exists()


def test_append_lock_times_out_instead_of_hanging_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_chain, "_LOCK_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(audit_chain, "_LOCK_POLL_SECONDS", 0.01)
    log = tmp_path / "audit.jsonl"
    stale_lock = tmp_path / "audit.jsonl.lock"
    stale_lock.write_bytes(b"")  # simulate a crashed writer that never released the lock

    try:
        audit_chain.append(str(log), {"action": "should-not-hang"})
        assert False, "expected TimeoutError"
    except TimeoutError as exc:
        assert "audit-chain lock" in str(exc)


def test_chain_status_flags_legacy_record_inserted_after_chaining(tmp_path):
    log = tmp_path / "audit.jsonl"
    audit_chain.append(str(log), {"action": "a"})
    # An un-chained record appearing after chaining began = downgrade/insertion attempt.
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"action": "smuggled", "status": "APPROVED"}) + "\n")

    status = audit_chain.chain_status(str(log))
    assert not status["intact"]
    assert any("after chaining began" in e for e in status["errors"])
