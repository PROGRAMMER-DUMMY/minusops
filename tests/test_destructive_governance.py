"""
Destructive operations must never reach an unattended apply.

A teardown goes through the SAME loop as any other change (plan --destroy -> approve ->
apply), which means hash-binding, RBAC and the audit chain already cover it. The dangerous
combination is destroy + --mode auto-approve: nobody reviews, and every resource goes away.

These are the checks that stand between an agent-driven session and an unattended teardown.
"""
import destructive_change_gate as g5
import plan_gate

_DESTROY_PLAN = {
    "resource_changes": [
        {"address": "aws_s3_bucket.data", "mode": "managed", "type": "aws_s3_bucket",
         "change": {"actions": ["delete"], "before": {"bucket": "prod-lake"}, "after": None}},
        {"address": "aws_iam_role.etl", "mode": "managed", "type": "aws_iam_role",
         "change": {"actions": ["delete"], "before": {"name": "etl"}, "after": None}},
    ]
}


def test_a_destroy_plan_is_never_autonomous_eligible():
    """Foundational: G5 must not classify a teardown as safe to ship unattended."""
    result = g5.classify(_DESTROY_PLAN)
    assert result["autonomous_eligible"] is False


def test_destroy_plus_auto_approve_is_refused(tmp_path, capsys):
    """The specific combination that would delete production without a human."""
    classification = g5.classify(_DESTROY_PLAN)
    blocked = plan_gate._reject_if_destructive_and_auto_approve(
        str(tmp_path), "auto-approve", classification, destroy=True)
    assert blocked is True
    assert "REFUSING auto-approve" in capsys.readouterr().err


def test_there_is_no_bypass_flag_for_the_destructive_check():
    """Structural. Every other _reject_if_* has a policy-mode or env carve-out; this one
    deliberately has none, and a future edit adding one should fail here loudly."""
    import ast
    import inspect
    src = inspect.getsource(plan_gate._reject_if_destructive_and_auto_approve)
    fn = ast.parse(src.lstrip()).body[0]
    # Strip the docstring before checking: it legitimately NAMES the carve-outs in order to
    # say they are absent, so scanning raw source matches its own documentation.
    body = fn.body[1:] if (isinstance(fn.body[0], ast.Expr)
                           and isinstance(fn.body[0].value, ast.Constant)) else fn.body
    code = "\n".join(ast.dump(node) for node in body)
    for bypass in ("MINUS_ALLOW", "policy_mode", "environ", "force"):
        assert bypass not in code, (
            f"{bypass!r} appeared in the destructive auto-approve check's CODE -- this gate "
            f"is deliberately non-overridable in dev and production alike")


def test_gatekeeper_mode_is_the_documented_path_not_a_bypass(tmp_path):
    """Blocking auto-approve must route to human review, not to a dead end."""
    classification = g5.classify(_DESTROY_PLAN)
    assert plan_gate._reject_if_destructive_and_auto_approve(
        str(tmp_path), "gatekeeper", classification, destroy=True) is False


def test_stateful_deletes_are_named_in_the_classification():
    """A reviewer must see WHICH resources disappear, not just that something is unsafe."""
    result = g5.classify(_DESTROY_PLAN)
    blob = repr(result)
    assert "aws_s3_bucket" in blob


def test_stage_approve_refuses_auto_approve_for_destroy_plan(tmp_path, monkeypatch, capsys):
    """stage_approve must refuse --mode auto-approve when destroy=True."""
    import json
    import os
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(plan_gate, "LOG_DIR", str(log_dir))
    monkeypatch.setattr(plan_gate, "_source_status_for_hash", lambda _h: {"status": "CURRENT", "stale": False, "reason": ""})
    monkeypatch.setattr(plan_gate, "_credential_posture", lambda: {"connected": True, "type": "temporary"})
    monkeypatch.setattr(plan_gate, "_plan_hash", lambda d: ("abc123hash", None))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate.authz, "operator", lambda: "alice")
    monkeypatch.setattr(plan_gate.authz, "verified_operator", lambda: "alice")
    monkeypatch.setattr(plan_gate.authz, "authorize", lambda *a, **k: (True, "open", "ok"))

    # Write pending plan with destroy=True
    pending_record = {
        "plan_hash": "abc123hash",
        "canonical_dir": plan_gate._canonical_dir(str(tmp_path)),
        "destroy": True,
    }
    pending_file = plan_gate._pending_path(str(tmp_path))
    os.makedirs(os.path.dirname(pending_file), exist_ok=True)
    with open(pending_file, "w", encoding="utf-8") as f:
        json.dump(pending_record, f)

    approved = plan_gate.stage_approve(str(tmp_path), mode="auto-approve", policy_mode="dev")
    assert approved is False
    err = capsys.readouterr().err
    assert "REFUSING auto-approve" in err
    assert "Teardowns require interactive human review" in err


def test_stage_apply_refuses_auto_approved_destroy_record(tmp_path, monkeypatch, capsys):
    """stage_apply must refuse even if an approval record with approval_mode=auto-approve exists for a destroy plan."""
    import json
    import os
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(plan_gate, "LOG_DIR", str(log_dir))
    monkeypatch.setattr(plan_gate, "_source_status_for_hash", lambda _h: {"status": "CURRENT", "stale": False, "reason": ""})
    monkeypatch.setattr(plan_gate, "_credential_posture", lambda: {"connected": True, "type": "temporary"})
    monkeypatch.setattr(plan_gate, "_plan_hash", lambda d: ("abc123hash", None))
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate, "_classify_plan", lambda d: g5.classify(_DESTROY_PLAN))
    monkeypatch.setattr(plan_gate.audit_chain, "verify", lambda p: (True, []))

    # Plant an approval record that had approval_mode="auto-approve"
    approved_record = {
        "plan_hash": "abc123hash",
        "dir": str(tmp_path),
        "canonical_dir": plan_gate._canonical_dir(str(tmp_path)),
        "identity": "123456789012",
        "approved_by": "alice",
        "approver": "alice",
        "approval_mode": "auto-approve",
        "destroy": True,
    }
    approved_file = plan_gate._approved_path(str(tmp_path), "abc123hash")
    os.makedirs(os.path.dirname(approved_file), exist_ok=True)
    with open(approved_file, "w", encoding="utf-8") as f:
        json.dump(approved_record, f)

    # Calling stage_apply even with default mode="gatekeeper" must catch the approval_mode and refuse
    applied = plan_gate.stage_apply(str(tmp_path), mode="gatekeeper", policy_mode="dev")
    assert applied is False
    err = capsys.readouterr().err
    assert "REFUSING auto-approve apply" in err

