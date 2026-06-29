"""
Tests for the deploy gate's core guarantee: you can only apply the exact plan you
approved. These exercise the plan-hash logic and the approval/apply state machine
without invoking real Terraform — `_tf` is monkeypatched to return canned plan JSON.
"""
import json
import os

import pytest

import plan_gate


# Two distinct canned `terraform show -json` payloads -> two distinct plan hashes.
PLAN_A = {
    "resource_changes": [
        {"address": "aws_s3_bucket.data", "type": "aws_s3_bucket",
         "name": "data", "change": {"actions": ["create"]}}
    ],
    "output_changes": {},
}
PLAN_B = {
    "resource_changes": [
        {"address": "aws_s3_bucket.data", "type": "aws_s3_bucket",
         "name": "data", "change": {"actions": ["delete"]}}
    ],
    "output_changes": {},
}


@pytest.fixture
def gate_env(tmp_path, monkeypatch):
    """Point the gate's state files at a temp dir so tests never touch the repo."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(plan_gate, "LOG_DIR", str(log_dir))
    monkeypatch.setattr(
        plan_gate,
        "_source_status_for_hash",
        lambda _h: {"status": "CURRENT", "stale": False, "reason": ""},
    )
    # Default to a safe (temporary) credential posture; posture enforcement has its own tests.
    monkeypatch.setattr(plan_gate, "_credential_posture", lambda: {"connected": True, "type": "temporary"})
    return log_dir


def _stub_tf(plan):
    """Return a fake _tf that answers `show -json` with the given plan payload."""
    def _tf(dir_, *args, capture=False):
        if "show" in args:
            return 0, json.dumps(plan), ""
        return 0, "", ""
    return _tf


def test_plan_hash_is_deterministic(monkeypatch):
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    h1, _ = plan_gate._plan_hash("anydir")
    h2, _ = plan_gate._plan_hash("anydir")
    assert h1 and h1 == h2


def test_plan_hash_changes_with_plan(monkeypatch):
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    ha, _ = plan_gate._plan_hash("d")
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_B))
    hb, _ = plan_gate._plan_hash("d")
    assert ha != hb


def test_approve_rejects_stale_plan(gate_env, monkeypatch):
    """If the recorded pending hash doesn't match the current plan, approval is refused."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    os.makedirs(plan_gate._state_dir("d"), exist_ok=True)
    with open(plan_gate._pending_path("d"), "w", encoding="utf-8") as f:
        json.dump({
            "plan_hash": "stale-hash-that-does-not-match",
            "canonical_dir": plan_gate._canonical_dir("d"),
        }, f)
    assert plan_gate.stage_approve("d", mode="gatekeeper") is False
    current, _ = plan_gate._plan_hash("d")
    assert not os.path.exists(plan_gate._approved_path("d", current))


def test_approve_then_apply_happy_path(gate_env, monkeypatch):
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate, "_timed_input", lambda *a, **k: "y")

    current, _ = plan_gate._plan_hash("d")
    os.makedirs(plan_gate._state_dir("d"), exist_ok=True)
    with open(plan_gate._pending_path("d"), "w", encoding="utf-8") as f:
        json.dump({
            "plan_hash": current,
            "dir": "d",
            "canonical_dir": plan_gate._canonical_dir("d"),
        }, f)

    assert plan_gate.stage_approve("d", mode="gatekeeper") is True
    saved = json.load(open(plan_gate._approved_path("d", current), encoding="utf-8"))
    assert saved["plan_hash"] == current
    assert saved["canonical_dir"] == plan_gate._canonical_dir("d")
    # The approval record must never contain secrets.
    assert "secret" not in json.dumps(saved).lower()

    # Apply against the SAME plan succeeds and consumes (clears) the approval.
    assert plan_gate.stage_apply("d") is True
    assert not os.path.exists(plan_gate._approved_path("d", current))


def test_apply_refuses_when_plan_drifted(gate_env, monkeypatch):
    """Approve PLAN_A, then the .tf changes (now PLAN_B) -> apply must refuse."""
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    # Compute and record PLAN_A's hash as the approved one.
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    approved_hash, _ = plan_gate._plan_hash("d")
    os.makedirs(plan_gate._approval_dir("d"), exist_ok=True)
    with open(plan_gate._approved_path("d", approved_hash), "w", encoding="utf-8") as f:
        json.dump({
            "plan_hash": approved_hash,
            "dir": "d",
            "canonical_dir": plan_gate._canonical_dir("d"),
        }, f)

    # Now the current plan is PLAN_B (drift).
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_B))
    assert plan_gate.stage_apply("d") is False
    # A refused apply voids the now-untrustworthy approval.
    assert not os.path.exists(plan_gate._approved_path("d", approved_hash))


def test_apply_with_no_approval_is_refused(gate_env, monkeypatch):
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    assert plan_gate.stage_apply("d") is False


def test_approve_refuses_unknown_source_provenance(gate_env, monkeypatch):
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(
        plan_gate,
        "_source_status_for_hash",
        lambda _h: {"status": "UNKNOWN", "stale": False, "reason": "source snapshot unavailable"},
    )
    current, _ = plan_gate._plan_hash("d")
    os.makedirs(plan_gate._state_dir("d"), exist_ok=True)
    with open(plan_gate._pending_path("d"), "w", encoding="utf-8") as f:
        json.dump({
            "plan_hash": current,
            "dir": "d",
            "canonical_dir": plan_gate._canonical_dir("d"),
        }, f)

    assert plan_gate.stage_approve("d", mode="gatekeeper") is False


def test_apply_refuses_source_drift(gate_env, monkeypatch):
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(
        plan_gate,
        "_source_status_for_hash",
        lambda _h: {"status": "STALE", "stale": True, "reason": "source changed"},
    )
    current, _ = plan_gate._plan_hash("d")
    os.makedirs(plan_gate._approval_dir("d"), exist_ok=True)
    with open(plan_gate._approved_path("d", current), "w", encoding="utf-8") as f:
        json.dump({
            "plan_hash": current,
            "dir": "d",
            "canonical_dir": plan_gate._canonical_dir("d"),
        }, f)

    assert plan_gate.stage_apply("d") is False


def test_apply_refuses_approval_from_another_directory(gate_env, monkeypatch):
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    current, _ = plan_gate._plan_hash("target")
    os.makedirs(plan_gate._approval_dir("target"), exist_ok=True)
    with open(plan_gate._approved_path("target", current), "w", encoding="utf-8") as f:
        json.dump({
            "plan_hash": current,
            "dir": "other",
            "canonical_dir": plan_gate._canonical_dir("other"),
        }, f)

    assert plan_gate.stage_apply("target") is False
    assert not os.path.exists(plan_gate._approved_path("target", current))


def test_verify_fails_when_security_scan_blocks(gate_env, monkeypatch):
    def ok_tf(dir_, *args, capture=False):
        return 0, "", ""

    def blocking_scan(args, capture=False):
        return 2, "", "[OPTIMIZER] Blocking findings detected: SEC-01"

    monkeypatch.setattr(plan_gate, "_tf", ok_tf)
    monkeypatch.setattr(plan_gate, "_run", blocking_scan)
    monkeypatch.setattr(plan_gate, "SCAN", __file__)

    assert plan_gate.stage_verify("d") is False
