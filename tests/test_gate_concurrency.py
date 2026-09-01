"""
P1.1 -- two operators planning the same directory must not corrupt each other's approval.

_write_json_atomic fixed torn writes (a killed process can no longer leave truncated gate
state) but NOT lost updates. Without a lock, operator B's pending_plan.json write can land
between A reading and A approving, so A records an approval bound to B's plan hash while
believing it is their own. That is approval integrity -- the guarantee the whole product
rests on.

audit_chain._AppendLock already solves exactly this (threading.Lock + an OS-native advisory
lock, Windows-tested). Reused rather than written a second time.
"""
import json
import threading

import plan_gate


def test_concurrent_writers_do_not_interleave(tmp_path):
    """Every write must land whole. A torn or interleaved file fails to parse."""
    path = str(tmp_path / "pending.json")
    errors = []

    def writer(n):
        try:
            for _ in range(20):
                plan_gate._write_json_atomic(path, {"plan_hash": f"hash-{n}", "payload": "x" * 400})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    data = json.load(open(path, encoding="utf-8"))
    assert data["plan_hash"].startswith("hash-")
    assert data["payload"] == "x" * 400


def test_read_modify_write_does_not_lose_a_concurrent_update(tmp_path):
    """The real bug: read-modify-write under contention. Without holding a lock across the
    whole read-modify-write, increments are lost and the final count is < 40."""
    path = str(tmp_path / "pending.json")
    plan_gate._write_json_atomic(path, {"count": 0})

    def bump():
        for _ in range(20):
            with plan_gate._gate_state_lock(path):
                current = json.load(open(path, encoding="utf-8"))
                current["count"] += 1
                plan_gate._write_json_atomic(path, current)

    threads = [threading.Thread(target=bump) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert json.load(open(path, encoding="utf-8"))["count"] == 40


def test_the_lock_is_reused_not_reimplemented():
    """Structural: a second hand-rolled Windows lock is how the first one got its bugs."""
    import inspect
    src = inspect.getsource(plan_gate._gate_state_lock)
    assert "audit_chain" in src, "reuse audit_chain._AppendLock rather than writing another"
