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


def test_apply_refuses_when_audit_chain_tampered(gate_env, monkeypatch):
    # Audit finding 2026-07-03: audit_chain.verify() existed but nothing in the deploy path
    # called it -- tamper-evidence was opt-in, not load-bearing. This proves it now is.
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate, "_timed_input", lambda *a, **k: "y")

    current, _ = plan_gate._plan_hash("d")
    os.makedirs(plan_gate._state_dir("d"), exist_ok=True)
    with open(plan_gate._pending_path("d"), "w", encoding="utf-8") as f:
        json.dump({"plan_hash": current, "dir": "d", "canonical_dir": plan_gate._canonical_dir("d")}, f)
    assert plan_gate.stage_approve("d", mode="gatekeeper") is True

    # Hand-edit the audit log out-of-band -- exactly what verify() exists to catch.
    audit_path = gate_env / "audit.jsonl"
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert lines, "approve should have written at least one audit record"
    rec = json.loads(lines[0])
    rec["status"] = "TAMPERED"
    lines[0] = json.dumps(rec)
    audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert plan_gate.stage_apply("d") is False
    # Refused before even reaching the plan-hash check -- the approval record is still there,
    # proving it wasn't ordinary hash/drift logic that blocked this apply.
    assert os.path.exists(plan_gate._approved_path("d", current))


def test_apply_proceeds_with_an_untampered_audit_chain(gate_env, monkeypatch):
    # Sanity check: a normal chain built from real gate activity never blocks apply.
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate, "_timed_input", lambda *a, **k: "y")

    current, _ = plan_gate._plan_hash("d")
    os.makedirs(plan_gate._state_dir("d"), exist_ok=True)
    with open(plan_gate._pending_path("d"), "w", encoding="utf-8") as f:
        json.dump({"plan_hash": current, "dir": "d", "canonical_dir": plan_gate._canonical_dir("d")}, f)

    assert plan_gate.stage_approve("d", mode="gatekeeper") is True
    assert plan_gate.stage_apply("d") is True


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


def _record_pending(dir_, plan_hash, planner=None):
    os.makedirs(plan_gate._state_dir(dir_), exist_ok=True)
    rec = {"plan_hash": plan_hash, "dir": dir_, "canonical_dir": plan_gate._canonical_dir(dir_)}
    if planner is not None:
        rec["planner"] = planner
    with open(plan_gate._pending_path(dir_), "w", encoding="utf-8") as f:
        json.dump(rec, f)


def test_production_rejects_open_allowlist(gate_env, monkeypatch, capsys):
    """Production: no approver allowlist -> approval refused, no approval recorded."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate.authz, "authorize", lambda *a, **k: (True, "open", "open mode"))
    current, _ = plan_gate._plan_hash("d")
    _record_pending("d", current, planner="dave")

    assert plan_gate.stage_approve("d", mode="auto-approve", policy_mode="production") is False
    assert not os.path.exists(plan_gate._approved_path("d", current))
    assert "no approver allowlist" in capsys.readouterr().err
    audit = open(os.path.join(plan_gate.LOG_DIR, "audit.jsonl"), encoding="utf-8").read()
    assert "open_allowlist_in_production" in audit


def test_production_rejects_self_approval(gate_env, monkeypatch, capsys):
    """Production two-person rule: approver == planner -> refused."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate.authz, "operator", lambda: "alice")
    monkeypatch.setattr(plan_gate.authz, "authorize", lambda *a, **k: (True, "enforced", "ok"))
    current, _ = plan_gate._plan_hash("d")
    _record_pending("d", current, planner="alice")

    assert plan_gate.stage_approve("d", mode="auto-approve", policy_mode="production") is False
    assert not os.path.exists(plan_gate._approved_path("d", current))
    assert "cannot approve their own plan" in capsys.readouterr().err
    audit = open(os.path.join(plan_gate.LOG_DIR, "audit.jsonl"), encoding="utf-8").read()
    assert "self_approval_in_production" in audit


def test_production_rejects_missing_planner(gate_env, monkeypatch, capsys):
    """Production: a plan with no recorded planner can't prove separation -> refused."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate.authz, "operator", lambda: "bob")
    monkeypatch.setattr(plan_gate.authz, "authorize", lambda *a, **k: (True, "enforced", "ok"))
    current, _ = plan_gate._plan_hash("d")
    _record_pending("d", current)  # no planner recorded

    assert plan_gate.stage_approve("d", mode="auto-approve", policy_mode="production") is False
    audit = open(os.path.join(plan_gate.LOG_DIR, "audit.jsonl"), encoding="utf-8").read()
    assert "missing_planner_in_production" in audit


def test_production_approves_with_allowlist_and_distinct_approver(gate_env, monkeypatch):
    """Production happy path: enforced allowlist + approver != planner -> approval recorded."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate.authz, "operator", lambda: "carol")
    monkeypatch.setattr(plan_gate.authz, "authorize", lambda *a, **k: (True, "enforced", "ok"))
    current, _ = plan_gate._plan_hash("d")
    _record_pending("d", current, planner="dave")

    assert plan_gate.stage_approve("d", mode="auto-approve", policy_mode="production") is True
    assert os.path.exists(plan_gate._approved_path("d", current))


def test_dev_mode_allows_open_self_approval(gate_env, monkeypatch, capsys):
    """Dev lane is untouched: open allowlist + self-approval still approves, no prod rejection."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate.authz, "operator", lambda: "alice")
    monkeypatch.setattr(plan_gate.authz, "authorize", lambda *a, **k: (True, "open", "open mode"))
    current, _ = plan_gate._plan_hash("d")
    _record_pending("d", current, planner="alice")

    assert plan_gate.stage_approve("d", mode="auto-approve", policy_mode="dev") is True
    assert os.path.exists(plan_gate._approved_path("d", current))
    assert "(production)" not in capsys.readouterr().err


def test_production_apply_rejects_static_creds_override(gate_env, monkeypatch, capsys):
    """Production: MINUS_ALLOW_STATIC_CREDS is not honored -> apply refused, approval kept."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate, "_credential_posture", lambda: {"connected": True, "type": "long_term"})
    monkeypatch.setenv("MINUS_ALLOW_STATIC_CREDS", "1")
    current, _ = plan_gate._plan_hash("d")
    os.makedirs(plan_gate._approval_dir("d"), exist_ok=True)
    with open(plan_gate._approved_path("d", current), "w", encoding="utf-8") as f:
        json.dump({"plan_hash": current, "dir": "d",
                   "canonical_dir": plan_gate._canonical_dir("d")}, f)

    assert plan_gate.stage_apply("d", policy_mode="production") is False
    # Approval is kept so the operator can re-auth with a temporary session and retry.
    assert os.path.exists(plan_gate._approved_path("d", current))
    assert "not honored in production" in capsys.readouterr().err
    audit = open(os.path.join(plan_gate.LOG_DIR, "audit.jsonl"), encoding="utf-8").read()
    assert "static_creds_override_denied_in_production" in audit


def test_dev_apply_honors_static_creds_override(gate_env, monkeypatch):
    """Dev lane: the audited static-cred downgrade still applies."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate, "_credential_posture", lambda: {"connected": True, "type": "long_term"})
    monkeypatch.setenv("MINUS_ALLOW_STATIC_CREDS", "1")
    current, _ = plan_gate._plan_hash("d")
    os.makedirs(plan_gate._approval_dir("d"), exist_ok=True)
    with open(plan_gate._approved_path("d", current), "w", encoding="utf-8") as f:
        json.dump({"plan_hash": current, "dir": "d",
                   "canonical_dir": plan_gate._canonical_dir("d")}, f)

    assert plan_gate.stage_apply("d", policy_mode="dev") is True


def test_verify_fails_when_security_scan_blocks(gate_env, monkeypatch):
    def ok_tf(dir_, *args, capture=False):
        return 0, "", ""

    def blocking_scan(args, capture=False):
        return 2, "", "[OPTIMIZER] Blocking findings detected: SEC-01"

    monkeypatch.setattr(plan_gate, "_tf", ok_tf)
    monkeypatch.setattr(plan_gate, "_run", blocking_scan)
    monkeypatch.setattr(plan_gate, "SCAN", __file__)

    assert plan_gate.stage_verify("d") is False


def test_verify_passes_policy_mode_and_log_dir_to_scanner(gate_env, monkeypatch):
    seen = {}

    def ok_tf(dir_, *args, capture=False):
        return 0, "", ""

    def scan(args, capture=False):
        seen["args"] = args
        return 0, "", ""

    monkeypatch.setattr(plan_gate, "_tf", ok_tf)
    monkeypatch.setattr(plan_gate, "_run", scan)
    monkeypatch.setattr(plan_gate, "SCAN", __file__)

    assert plan_gate.stage_verify("d", policy_mode="production") is True
    assert "--policy-mode" in seen["args"]
    assert seen["args"][seen["args"].index("--policy-mode") + 1] == "production"
    assert "--log-dir" in seen["args"]
    assert seen["args"][seen["args"].index("--log-dir") + 1] == plan_gate.LOG_DIR


# ---- loophole #1: dev-mode applies only into declared sandbox accounts ----
def test_dev_apply_refused_for_nonsandbox_account(monkeypatch, capsys):
    import plan_gate
    monkeypatch.setenv("MINUS_SANDBOX_ACCOUNTS", "111111111111")
    assert plan_gate._reject_if_nonsandbox_dev(".", "999999999999", "dev") is True
    assert "not in" in capsys.readouterr().err


def test_dev_apply_allowed_for_sandbox_account(monkeypatch):
    import plan_gate
    monkeypatch.setenv("MINUS_SANDBOX_ACCOUNTS", "111111111111, 222222222222")
    assert plan_gate._reject_if_nonsandbox_dev(".", "222222222222", "dev") is False


def test_dev_apply_warns_when_sandboxes_undeclared(monkeypatch, capsys):
    import plan_gate
    monkeypatch.delenv("MINUS_SANDBOX_ACCOUNTS", raising=False)
    assert plan_gate._reject_if_nonsandbox_dev(".", "999999999999", "dev") is False
    assert "WARNING" in capsys.readouterr().err


def test_production_mode_not_subject_to_sandbox_list(monkeypatch):
    import plan_gate
    monkeypatch.setenv("MINUS_SANDBOX_ACCOUNTS", "111111111111")
    assert plan_gate._reject_if_nonsandbox_dev(".", "999999999999", "production") is False
