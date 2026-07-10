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
    # Default to "no verified AWS identity available" so the suite stays hermetic regardless
    # of whatever real AWS credentials happen to be configured on the machine running it --
    # otherwise a dev machine with live creds would silently override every test's mocked
    # authz.operator() with a real STS-derived identity. The verified-identity path has its
    # own dedicated tests below that explicitly re-enable it.
    monkeypatch.setattr(plan_gate.authz, "verified_operator", lambda: None)
    return log_dir


def _stub_tf(plan):
    """Return a fake _tf that answers `show -json` with the given plan payload."""
    def _tf(dir_, *args, capture=False):
        if "show" in args:
            return 0, json.dumps(plan), ""
        return 0, "", ""
    return _tf


def _stub_apply_success(applied=("aws_s3_bucket.data",), failed=(), errors=None):
    """Fake _apply_with_json_capture for a successful (or partially-successful) apply,
    matching the (dir_, applied, failed, errors) mutate-in-place contract: appends into the
    caller-provided containers and returns just the returncode."""
    def _apply(dir_, out_applied, out_failed, out_errors):
        out_applied.extend(applied)
        out_failed.extend(failed)
        out_errors.update(errors or {})
        return 0 if not failed else 1
    return _apply


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
    monkeypatch.setattr(plan_gate, "_apply_with_json_capture", _stub_apply_success())
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
    monkeypatch.setattr(plan_gate, "_apply_with_json_capture", _stub_apply_success())
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


def _record_pending(dir_, plan_hash, planner=None, destroy=False):
    os.makedirs(plan_gate._state_dir(dir_), exist_ok=True)
    rec = {"plan_hash": plan_hash, "dir": dir_, "canonical_dir": plan_gate._canonical_dir(dir_),
           "destroy": destroy}
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
    monkeypatch.setattr(plan_gate, "_apply_with_json_capture", _stub_apply_success())
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


def test_destroy_flag_flows_from_pending_through_approval_to_apply_audit_record(gate_env, monkeypatch):
    """Item 6 finding 1: the plan-stage audit record already noted destroy=True/False, but the
    apply-stage record didn't -- a reviewer reading only the apply audit trail couldn't tell
    create/modify from teardown without cross-referencing the earlier plan record. destroy now
    rides on the approval record (written by stage_approve, read by stage_apply) so every
    apply-stage audit entry -- including the terminal OK/FAILED one -- self-describes direction."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_B))  # PLAN_B is a delete-only plan
    monkeypatch.setattr(plan_gate, "_apply_with_json_capture", _stub_apply_success(applied=(), failed=()))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate, "_timed_input", lambda *a, **k: "y")

    current, _ = plan_gate._plan_hash("d")
    _record_pending("d", current, destroy=True)

    assert plan_gate.stage_approve("d", mode="gatekeeper") is True
    saved = json.load(open(plan_gate._approved_path("d", current), encoding="utf-8"))
    assert saved["destroy"] is True

    assert plan_gate.stage_apply("d") is True
    audit_lines = [json.loads(line) for line in
                   open(os.path.join(plan_gate.LOG_DIR, "audit.jsonl"), encoding="utf-8")
                   if line.strip()]
    apply_records = [r for r in audit_lines if r.get("action") == "apply"]
    assert apply_records, "expected at least one apply-stage audit record"
    # The terminal apply record (status OK) must self-describe as a destroy, standalone --
    # no need to join against the plan-stage record to know direction.
    terminal = apply_records[-1]
    assert terminal["status"] == "OK"
    assert terminal["destroy"] is True


def test_planner_and_approver_prefer_verified_aws_identity_over_env_var(gate_env, monkeypatch):
    """2026-07-07 Phase 1 identity fix: when a real AWS session is active, the plan-stage
    planner and approve-stage approver must come from the AWS-STS-verified identity, not
    whatever MINUS_OPERATOR happens to be set to -- otherwise the two-person production
    rule is defeated by just setting the env var to two different strings."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate.authz, "authorize", lambda *a, **k: (True, "enforced", "ok"))
    # A real AWS session is active as "dave@corp" (verified) -- but someone has ALSO set
    # MINUS_OPERATOR to "carol", trying to spoof a different identity for the RBAC check.
    monkeypatch.setattr(plan_gate.authz, "verified_operator", lambda: "dave@corp")
    monkeypatch.setattr(plan_gate.authz, "operator", lambda: "carol")

    ok = plan_gate.stage_plan("d", policy_mode="production")
    assert ok is True
    pending = json.load(open(plan_gate._pending_path("d"), encoding="utf-8"))
    assert pending["planner"] == "dave@corp"  # the verified identity, not the spoofed env var

    monkeypatch.setattr(plan_gate, "_timed_input", lambda *a, **k: "y")
    current, _ = plan_gate._plan_hash("d")
    assert plan_gate.stage_approve("d", mode="gatekeeper", policy_mode="production") is False
    # dave planned it, dave (verified) is also the one approving -> two-person rule catches
    # this exactly BECAUSE it used the verified identity for both, not the spoofed "carol".
    audit = open(os.path.join(plan_gate.LOG_DIR, "audit.jsonl"), encoding="utf-8").read()
    assert "self_approval_in_production" in audit


def _approve_as(dir_, current_hash, verified_identity, monkeypatch, policy_mode="production"):
    """Helper: approve a plan as a given verified identity, bypassing the two-person-rule
    machinery (planner recorded as someone else) so these tests isolate the apply-time
    identity-binding check specifically."""
    monkeypatch.setattr(plan_gate.authz, "verified_operator", lambda: verified_identity)
    monkeypatch.setattr(plan_gate.authz, "authorize", lambda *a, **k: (True, "enforced", "ok"))
    monkeypatch.setattr(plan_gate, "_timed_input", lambda *a, **k: "y")
    _record_pending(dir_, current_hash, planner="someone-else")
    assert plan_gate.stage_approve(dir_, mode="gatekeeper", policy_mode=policy_mode) is True


def test_apply_refused_when_identity_mismatches_approver_in_production(gate_env, monkeypatch):
    """Phase 1 item 2: the credentials running `apply` must belong to whoever the
    approval record says approved -- otherwise two different people can jointly satisfy
    'approved' + 'applied' without either being accountable for the whole action."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    current, _ = plan_gate._plan_hash("d")
    _approve_as("d", current, "carol@corp", monkeypatch, policy_mode="production")

    # A DIFFERENT identity now tries to run apply.
    monkeypatch.setattr(plan_gate.authz, "verified_operator", lambda: "mallory@evil")
    assert plan_gate.stage_apply("d", policy_mode="production") is False
    audit = open(os.path.join(plan_gate.LOG_DIR, "audit.jsonl"), encoding="utf-8").read()
    assert "apply_identity_mismatches_approver" in audit
    # The approval must survive a refused apply-identity check (recoverable: re-run apply
    # as the right identity), same as the other apply-time credential/session gates.
    assert os.path.exists(plan_gate._approved_path("d", current))


def test_apply_proceeds_when_identity_matches_approver_in_production(gate_env, monkeypatch):
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_apply_with_json_capture", _stub_apply_success())
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    current, _ = plan_gate._plan_hash("d")
    _approve_as("d", current, "carol@corp", monkeypatch, policy_mode="production")

    # The SAME identity that approved now applies.
    monkeypatch.setattr(plan_gate.authz, "verified_operator", lambda: "carol@corp")
    assert plan_gate.stage_apply("d", policy_mode="production") is True


def test_apply_identity_mismatch_only_warns_in_dev_mode(gate_env, monkeypatch):
    """Dev mode is explicitly single-operator/relaxed -- a mismatch here warns (audited) but
    doesn't block, since a lone operator legitimately re-authenticates between steps."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_apply_with_json_capture", _stub_apply_success())
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    current, _ = plan_gate._plan_hash("d")
    _approve_as("d", current, "carol@corp", monkeypatch, policy_mode="dev")

    monkeypatch.setattr(plan_gate.authz, "verified_operator", lambda: "dave@corp")
    assert plan_gate.stage_apply("d", policy_mode="dev") is True  # proceeds despite the mismatch
    audit = open(os.path.join(plan_gate.LOG_DIR, "audit.jsonl"), encoding="utf-8").read()
    assert "apply_identity_mismatches_approver_dev" in audit


def test_apply_identity_check_skipped_when_approval_predates_verified_identity(gate_env, monkeypatch):
    """An approval recorded before this feature existed (or with no cloud session at
    approve-time) has no approver_verified_identity to compare against -- the check must
    not invent a rejection out of nothing verifiable."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate, "_apply_with_json_capture", _stub_apply_success())
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    current, _ = plan_gate._plan_hash("d")
    # verified_operator() was None at approval time (env-var fallback used instead).
    _approve_as("d", current, None, monkeypatch, policy_mode="production")

    # Apply-time DOES have a verified identity now -- still nothing to compare against.
    monkeypatch.setattr(plan_gate.authz, "verified_operator", lambda: "anyone@corp")
    assert plan_gate.stage_apply("d", policy_mode="production") is True


def test_planner_falls_back_to_env_var_without_a_cloud_session(gate_env, monkeypatch):
    """No AWS session yet (e.g. dev-mode planning before credentials are configured) ->
    verified_operator() returns None -> falls back to the existing MINUS_OPERATOR/OS-user
    behavior, same as before this fix."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))
    monkeypatch.setattr(plan_gate.authz, "verified_operator", lambda: None)
    monkeypatch.setattr(plan_gate.authz, "operator", lambda: "eve")

    assert plan_gate.stage_plan("d") is True
    pending = json.load(open(plan_gate._pending_path("d"), encoding="utf-8"))
    assert pending["planner"] == "eve"


def test_non_destroy_apply_audit_record_says_destroy_false(gate_env, monkeypatch):
    """Companion to the test above: a normal create/modify apply must NOT be mislabeled as a
    destroy -- proves the fix threads the real flag through, rather than defaulting everything
    to one value."""
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN_A))  # PLAN_A is a create-only plan
    monkeypatch.setattr(plan_gate, "_apply_with_json_capture", _stub_apply_success())
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate, "_timed_input", lambda *a, **k: "y")

    current, _ = plan_gate._plan_hash("d")
    _record_pending("d", current, destroy=False)

    assert plan_gate.stage_approve("d", mode="gatekeeper") is True
    assert plan_gate.stage_apply("d") is True
    audit_lines = [json.loads(line) for line in
                   open(os.path.join(plan_gate.LOG_DIR, "audit.jsonl"), encoding="utf-8")
                   if line.strip()]
    terminal = [r for r in audit_lines if r.get("action") == "apply"][-1]
    assert terminal["status"] == "OK"
    assert terminal["destroy"] is False
