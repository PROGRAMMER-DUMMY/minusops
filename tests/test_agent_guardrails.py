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


# --- PRD v15 WP-02: dynamic budget alignment ---------------------------------------------
#
# Kept here rather than in a new file because it is the same class of decision: a rule that
# changes what an agent may do without a human in the room.

import budget_alignment as budget


def test_a_budget_above_the_estimate_is_left_alone():
    """The operator's number stands when it already covers the architecture. Raising it
    anyway would inflate every guardrail in the fleet."""
    result = budget.align(declared_usd=2000, estimated_usd=1258.29)

    assert result["guardrail_usd"] == 2000
    assert result["overridden"] is False


def test_a_budget_below_the_estimate_is_raised_with_headroom():
    result = budget.align(declared_usd=500, estimated_usd=1258.29)

    assert result["guardrail_usd"] == pytest.approx(1572.86, abs=0.01)
    assert result["overridden"] is True


def test_an_override_records_what_the_operator_actually_declared():
    """Silently provisioning a 1,573 dollar alarm over a declared 500 dollar cap turns a
    cost control into a rubber stamp. The alignment is allowed; hiding it is not."""
    result = budget.align(declared_usd=500, estimated_usd=1258.29)

    assert result["declared_usd"] == 500
    assert "500" in result["reason"] and "1,258" in result["reason"]


def test_no_declared_budget_still_sizes_from_the_estimate():
    result = budget.align(declared_usd=0, estimated_usd=1258.29)

    assert result["guardrail_usd"] == pytest.approx(1572.86, abs=0.01)
    assert result["overridden"] is False, "there was no operator figure to override"


def test_no_estimate_leaves_the_declared_budget_untouched():
    """Without a BCM figure there is nothing to align to. Inventing headroom over a number
    nobody computed would be fabrication."""
    result = budget.align(declared_usd=500, estimated_usd=None)

    assert result["guardrail_usd"] == 500
    assert result["aligned"] is False


def test_neither_a_budget_nor_an_estimate_yields_no_guardrail_not_a_default():
    """A hardcoded 500 dollar default is what produced the 252 percent false positive in the
    first place. Absent both inputs the answer is None, and the caller reports it."""
    result = budget.align(declared_usd=0, estimated_usd=None)

    assert result["guardrail_usd"] is None


def test_the_headroom_multiplier_is_the_one_the_prd_states():
    assert budget.HEADROOM == 1.25


# --- PRD v15 WP-04: the upfront roadmap and the volume/budget contradiction --------------

import requirements as reqs


def test_the_seven_step_roadmap_is_available_to_open_a_build():
    """FR-04. An operator who cannot see the shape of the work cannot tell which step they
    are being asked to approve."""
    roadmap = reqs.lifecycle_roadmap()

    assert len(roadmap) == 7
    assert roadmap[0]["step"] == 1
    joined = " ".join(step["title"].lower() for step in roadmap)
    for expected in ("grilling", "decision", "synthesis", "topology", "reflector",
                     "plan gate", "human"):
        assert expected in joined, expected


def test_the_roadmap_renders_as_plain_ascii():
    text = reqs.format_roadmap()

    assert all(ord(ch) < 128 for ch in text)
    assert "1" in text and "7" in text


def test_a_volume_that_outruns_the_budget_is_raised_during_grilling():
    """FR-02.2. Raising the guardrail silences the alarm; only this puts the choice back in
    front of the operator while it can still be answered by changing the architecture."""
    finding = budget.contradiction(declared_usd=500, estimated_usd=1258.29)

    assert finding is not None
    assert finding["over_by_pct"] == pytest.approx(251.7, abs=0.1)
    assert "500" in finding["message"]
    assert len(finding["options"]) >= 2


def test_a_budget_that_covers_the_estimate_raises_nothing():
    assert budget.contradiction(declared_usd=2000, estimated_usd=1258.29) is None


def test_no_contradiction_is_claimed_without_both_numbers():
    """An unstated budget is not a contradiction with an estimate, and an unpriced
    architecture is not a contradiction with a budget."""
    assert budget.contradiction(declared_usd=0, estimated_usd=1258.29) is None
    assert budget.contradiction(declared_usd=500, estimated_usd=None) is None
