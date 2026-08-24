"""
PRD v15 WP-01/WP-05: the autonomous agent execution sandbox.

What these tests hold is narrow and worth stating: agent_guardrails is a guardrail against a
MISTAKE, not a security boundary against an adversary. A caller that never asks it is never
stopped by it, and a determined process can spell a command in ways no pattern list catches.
The tests below therefore pin two things -- that the obvious destructive forms are refused,
and that the ways an agent would ACCIDENTALLY slip past (extra whitespace, flag order,
long-form flags, a subshell) are also refused.

Depends on: core/governance/agent_guardrails.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import os

import pytest

import agent_guardrails as guard


# --- The destructive blacklist -----------------------------------------------------------

@pytest.mark.parametrize("command", [
    "rm -rf /",
    "rm -rf ./runs",
    "rm -r -f build",
    "rm --recursive --force build",
    "rmdir /s /q C:\\\\build",
    "del /s /q C:\\\\build",
    "terraform destroy",
    "terraform destroy -auto-approve",
    "terraform state rm aws_s3_bucket.bronze",
    "terraform force-unlock 1234",
    "git reset --hard HEAD~1",
    "git push --force origin main",
    "git clean -fdx",
    "aws s3 rb s3://prod-lake --force",
    "aws s3 rm s3://prod-lake --recursive",
    "DROP TABLE customers",
    "drop database analytics",
    "truncate table events",
])
def test_destructive_commands_are_refused(command):
    decision = guard.evaluate(command)

    assert not decision["allowed"], command
    assert decision["rule"], "a refusal must name the rule that produced it"


@pytest.mark.parametrize("command", [
    "rm    -rf   build",
    "  rm -rf build  ",
    "RM -RF build",
    "echo hello && rm -rf build",
    "echo hello; terraform destroy",
    "bash -c 'rm -rf build'",
    "sh -c \"git reset --hard\"",
])
def test_the_ways_an_agent_would_slip_past_are_also_refused(command):
    """Not adversarial evasion -- these are the shapes a command actually arrives in when an
    agent chains steps or pads whitespace. A guardrail that only matches the canonical
    spelling stops nothing in practice."""
    assert not guard.evaluate(command)["allowed"], command


@pytest.mark.parametrize("command", [
    "terraform plan",
    "terraform init",
    "git status",
    "git log --oneline",
    "python -m pytest",
    "aws s3 ls s3://prod-lake",
    "minusctl gate plan",
    "rm build.log",
    "select * from customers",
])
def test_ordinary_commands_are_allowed(command):
    """A guardrail that blocks the day job gets disabled, and then it protects nothing."""
    assert guard.evaluate(command)["allowed"], command


def test_a_refusal_raises_when_asked_to_enforce():
    with pytest.raises(PermissionError) as excinfo:
        guard.enforce("terraform destroy")

    assert "terraform destroy" in str(excinfo.value)


def test_enforce_returns_the_command_when_it_is_allowed():
    assert guard.enforce("terraform plan") == "terraform plan"


# --- Human-in-the-loop gates -------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "minusctl gate apply",
    "minusctl gate apply --run 20260824-x",
    "minusctl prove --execute",
])
def test_mutating_gates_are_refused_to_an_autonomous_agent(command):
    decision = guard.evaluate(command)

    assert not decision["allowed"]
    assert decision["requires_human"] is True


def test_a_mutating_gate_is_allowed_with_verified_human_authorization():
    decision = guard.evaluate("minusctl gate apply", human_authorized=True)

    assert decision["allowed"]


def test_human_authorization_is_an_identity_check_not_a_truthy_one():
    """`human_authorized="no"` is a non-empty string. Treating it as consent is how a
    dismissed prompt becomes an apply."""
    for value in ("no", "false", 0, "", [], {}, 1, "yes"):
        decision = guard.evaluate("minusctl gate apply", human_authorized=value)
        assert not decision["allowed"], value


def test_human_authorization_never_unlocks_a_destructive_command():
    """The HITL gate exists for governed mutations. A human clicking approve on a plan is
    not consent to `rm -rf`, and conflating the two turns one approval into a blank cheque."""
    decision = guard.evaluate("terraform destroy", human_authorized=True)

    assert not decision["allowed"]


# --- Run-scoped workspace isolation -------------------------------------------------------

def test_a_write_inside_the_active_run_is_allowed(tmp_path):
    target = tmp_path / "runs" / "run-a" / "terraform" / "main.tf"

    assert guard.evaluate_write(str(target), run_id="run-a",
                                workspace=str(tmp_path))["allowed"]


def test_a_write_into_another_run_is_refused(tmp_path):
    target = tmp_path / "runs" / "run-b" / "terraform" / "main.tf"

    decision = guard.evaluate_write(str(target), run_id="run-a", workspace=str(tmp_path))

    assert not decision["allowed"]
    assert "run-b" in decision["reason"] or "outside" in decision["reason"]


def test_a_write_to_a_core_engine_module_is_refused(tmp_path):
    target = tmp_path / "core" / "governance" / "plan_gate.py"

    assert not guard.evaluate_write(str(target), run_id="run-a",
                                    workspace=str(tmp_path))["allowed"]


def test_traversal_out_of_the_run_is_refused(tmp_path):
    """`runs/run-a/../../core/x.py` resolves outside the run. Comparing the unresolved
    string would let it through."""
    target = os.path.join(str(tmp_path), "runs", "run-a", "..", "..", "core", "x.py")

    assert not guard.evaluate_write(target, run_id="run-a", workspace=str(tmp_path))["allowed"]


def test_the_maintenance_flag_permits_a_write_outside_the_run(tmp_path):
    """Deliberate maintenance is a real case. It is explicit, and it is recorded in the
    decision so an audit can tell it from ordinary agent activity."""
    target = tmp_path / "core" / "governance" / "plan_gate.py"

    decision = guard.evaluate_write(str(target), run_id="run-a", workspace=str(tmp_path),
                                    maintenance=True)

    assert decision["allowed"]
    assert decision["maintenance"] is True


def test_with_no_run_scope_writes_are_not_silently_unrestricted(tmp_path):
    """An agent with no run is not an agent with the whole filesystem. Defaulting to allow
    would make the isolation vanish exactly when the scope is unclear.

    The target is deliberately an ORDINARY path, not one under core/. An earlier version of
    this test used core/x.py and passed even with the scope check deleted, because the
    protected-directory rule caught it instead -- the test proved a different guard than the
    one it named. Mutation testing surfaced that; the fixture is the fix.
    """
    decision = guard.evaluate_write(str(tmp_path / "scratch" / "notes.txt"), run_id=None,
                                    workspace=str(tmp_path))

    assert not decision["allowed"]
    assert decision["rule"] == "SCOPE-01", "refused by the wrong guard"


# --- Invariants ----------------------------------------------------------------------------

def test_the_guardrail_uses_only_the_standard_library():
    import ast
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "core", "governance", "agent_guardrails.py")
    tree = ast.parse(open(path, encoding="utf-8").read())

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= {"os", "re", "shlex", "socket", "subprocess", "sys"}, imported


def test_the_guardrail_carries_no_emoji():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "core", "governance", "agent_guardrails.py")
    text = open(path, encoding="utf-8").read()

    assert all(ord(ch) < 128 for ch in text), "non-ascii character in the guardrail"


def test_the_module_states_that_it_is_not_a_security_boundary():
    """A reader who mistakes this for a sandbox will grant an agent more than it should
    have. The limit belongs in the module, not only in a review comment."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "core", "governance", "agent_guardrails.py")
    head = open(path, encoding="utf-8").read()[:4000].lower()

    assert "not a security boundary" in head or "not a sandbox" in head
