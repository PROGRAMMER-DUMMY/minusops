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
