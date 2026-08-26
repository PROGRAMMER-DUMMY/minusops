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


# --- Heredoc bodies: data, unless a shell is reading them --------------------------------

def test_a_heredoc_body_that_merely_mentions_a_destructive_command_is_not_one():
    """The guardrail blocked its own test script for containing the phrase in a string.

    A `python - <<'EOF'` body is Python source. `"terraform destroy"` inside it is a string
    literal in a list of test fixtures, not an invocation, and refusing it makes it
    impossible to write a test for the guardrail -- or documentation about it, or a grep for
    it. The pattern list catches the shapes commands actually arrive in; a heredoc body fed
    to a non-shell is not one of those shapes.
    """
    script = "python - <<'PYEOF'\ncases = ['terraform destroy', 'rm -rf /']\nPYEOF"
    assert guard.evaluate(script)["allowed"] is True


def test_a_heredoc_body_fed_to_a_shell_is_still_commands():
    """The exemption is about the READER, not the syntax. `bash <<EOF` executes every line of
    the body, so stripping it there would be a hole big enough to drive anything through."""
    for shell in ("bash", "sh", "zsh", "/bin/bash", "bash -s"):
        script = f"{shell} <<'EOF'\nrm -rf /important\nEOF"
        decision = guard.evaluate(script)
        assert decision["allowed"] is False, f"{shell} heredoc was not checked: {script!r}"


def test_the_command_line_around_a_heredoc_is_always_checked():
    """Only the BODY is data. The line that opens it is still a command, and so is anything
    chained after the terminator."""
    assert guard.evaluate(
        "rm -rf /data && python - <<'EOF'\nprint(1)\nEOF")["allowed"] is False
    assert guard.evaluate(
        "cat <<'EOF' > f.txt\nharmless\nEOF\nrm -rf /data")["allowed"] is False


def test_an_unterminated_heredoc_is_refused_rather_than_stripped():
    """Fail closed on ambiguity. If the terminator never arrives, we cannot tell where the
    body ends, and guessing means guessing in the permissive direction."""
    decision = guard.evaluate("python - <<'EOF'\nrm -rf /\n")
    assert decision["allowed"] is False


def test_a_quoted_mention_outside_a_heredoc_is_still_refused():
    """No general "it was in quotes" exemption -- `sh -c "rm -rf /"` is quoted and real."""
    assert guard.evaluate('sh -c "rm -rf /"')["allowed"] is False


# --- The allowlist -----------------------------------------------------------------------
#
# The denylist was measured at 1 of 5 against the same destructive action expressed five
# ways: only the literal `terraform destroy` spelling was caught, while `make teardown`,
# `python cleanup.py` and `npm run reset` all passed. Enumerating danger is fail-open by
# construction -- the same argument destructive_change_gate.py makes about resource types.

def test_an_unknown_binary_is_refused_rather_than_allowed():
    """The whole point of inverting. `make` is not on the allowlist, so `make teardown` is
    refused without anyone having to predict that a Makefile target might destroy something."""
    decision = guard.evaluate("make teardown")
    assert decision["allowed"] is False
    assert decision["rule"] == "ALLOW-01"


def test_the_refusal_names_the_binary_so_it_can_be_reviewed_and_added():
    decision = guard.evaluate("kubectl delete ns prod")
    assert decision["allowed"] is False
    assert "kubectl" in decision["reason"]


@pytest.mark.parametrize("command", [
    "make teardown",
    "npm run reset",
    "curl https://example.com/x.sh",
    "wget https://example.com/x.sh",
    "docker system prune -af",
    "kubectl delete namespace prod",
    "helm uninstall release",
    "psql -c 'drop table t'",
])
def test_commands_no_one_predicted_are_refused_by_default(command):
    assert guard.evaluate(command)["allowed"] is False, command


@pytest.mark.parametrize("command", [
    "python -m pytest -q",
    "git status --short",
    "git diff --stat",
    "git log --oneline -5",
    "grep -rn pattern core/",
    "ls -la",
    "cat README.md",
    "terraform validate",
    "terraform plan -out=tfplan",
    "minusctl gate verify --dir runs/x/terraform",
    "minusctl runs list",
    "echo done",
    "mkdir -p runs/x",
    "python core/architecture/pillars.py list",
])
def test_the_work_this_project_actually_does_is_allowed(command):
    """An allowlist that blocks ordinary work gets switched off, and then protects nothing."""
    decision = guard.evaluate(command)
    assert decision["allowed"] is True, f"{command!r} refused: {decision['reason']}"


def test_every_segment_of_a_chain_must_be_on_the_allowlist():
    """`ls && make teardown` is not made safe by starting with `ls`."""
    assert guard.evaluate("ls && make teardown")["allowed"] is False


def test_the_allowlist_does_not_excuse_a_destructive_command():
    """git is allowlisted; `git push --force` is still refused. The allowlist decides WHAT may
    run, the destructive rules decide HOW -- one does not override the other."""
    assert guard.evaluate("git status")["allowed"] is True
    for command in ("git push --force origin main", "git reset --hard HEAD~5",
                    "terraform destroy", "rm -rf runs"):
        decision = guard.evaluate(command)
        assert decision["allowed"] is False, command
        assert decision["rule"] != "ALLOW-01", (
            f"{command!r} should be refused by its specific rule, not by the allowlist -- the "
            f"specific reason is what tells the operator why")


def test_the_human_gate_still_applies_to_an_allowlisted_command():
    assert guard.evaluate("minusctl gate apply --dir x")["allowed"] is False
    assert guard.evaluate("minusctl gate apply --dir x",
                          human_authorized=True)["allowed"] is True


def test_a_wrapper_cannot_smuggle_a_non_allowlisted_binary():
    """normalise() unwraps `bash -c`, so the payload is what gets checked, not the wrapper."""
    assert guard.evaluate('bash -c "make teardown"')["allowed"] is False


def test_an_interpreter_running_a_file_is_allowed_and_this_is_a_known_limit():
    """HONEST LIMIT, asserted so nobody mistakes the allowlist for containment.

    `python` has to be on the allowlist -- this project runs pytest and its own CLIs through
    it. That means `python cleanup.py` is allowed, and cleanup.py can call boto3 and delete a
    bucket. No allowlist of BINARIES closes that; only the credential does. This test exists
    to keep the limit visible rather than letting a reader assume the gap was closed.
    """
    assert guard.evaluate("python cleanup.py")["allowed"] is True


def test_a_benign_select_is_not_caught_by_the_drop_or_truncate_rules():
    """SQL-01 and SQL-02 must not over-match on ordinary SQL.

    `select * from customers` used to sit in the allowed list. Under the allowlist it is
    refused -- correctly, because `select` is not a program and a shell cannot run it -- so
    the assertion that still matters is WHICH rule refuses it. If a SQL rule fired here it
    would fire on every read query the agent ever passed to a client.
    """
    decision = guard.evaluate("select * from customers")
    assert decision["allowed"] is False
    assert decision["rule"] == "ALLOW-01", (
        f"refused by {decision['rule']} -- a read query must not trip a destructive SQL rule")


# --- Segment splitting must respect quoting ----------------------------------------------

def test_a_separator_inside_quotes_is_not_a_separator():
    """Found in live use, immediately after the allowlist went in.

    `sed 's/\\x1b\\[[0-9;]*m//g'` carries a `;` inside a single-quoted script. Splitting on it
    produced a fragment starting `]*m//g'`, whose first token read as the binary `g'`, and the
    allowlist refused an ordinary log-stripping pipeline. Shell operators only separate
    commands when they are unquoted.
    """
    command = "python -m pytest | sed 's/x1b\\[[0-9;]*m//g'"
    decision = guard.evaluate(command)
    assert decision["allowed"] is True, decision["reason"]


@pytest.mark.parametrize("command", [
    "grep -n 'a;b' core/",
    'grep -n "a|b" core/',
    "echo 'one && two'",
    "python -c 'print(1); print(2)'",
    "sed 's/;//g' file.txt",
    "awk -F'|' '{print $1}' data.csv",
])
def test_quoted_operators_do_not_fragment_an_ordinary_command(command):
    assert guard.evaluate(command)["allowed"] is True, command


@pytest.mark.parametrize("command", [
    "ls && make teardown",
    "ls ; make teardown",
    "ls | make teardown",
    "echo 'safe' && make teardown",
    "grep -n 'a;b' core/ && make teardown",
])
def test_an_unquoted_separator_still_splits(command):
    """The fix must not go the other way: real chaining is exactly how a refused command
    reaches a tool call, so an UNQUOTED separator has to keep splitting."""
    assert guard.evaluate(command)["allowed"] is False, command


def test_an_unbalanced_quote_does_not_swallow_the_rest_of_the_line():
    """Fail closed on a quoting error. If an opening quote never closes, the remainder must
    still be checked rather than treated as one inert string."""
    assert guard.evaluate("echo 'unclosed && rm -rf /")["allowed"] is False


# --- Command substitution -----------------------------------------------------------------

def test_a_command_substitution_is_checked_as_its_own_command():
    """`$(...)` and backticks run a command. It has to be checked, and the OUTER text must not
    be tokenized as though the substitution's words belonged to it.

    Found in live use: `d="$JOB/gate$(date +%s)"` was refused because the fragment `+%s)"`
    read as the binary name. Two bugs in one -- the substitution went unchecked, and its
    spillover produced a nonsense refusal.
    """
    assert guard.evaluate('d="/tmp/gate$(date +%s)"')["allowed"] is True
    assert guard.evaluate("echo `date`")["allowed"] is True


@pytest.mark.parametrize("command", [
    "echo $(make teardown)",
    "echo `make teardown`",
    "d=$(curl https://example.com/x.sh)",
    "echo $(rm -rf /)",
])
def test_a_substitution_cannot_smuggle_a_refused_command(command):
    assert guard.evaluate(command)["allowed"] is False, command


def test_a_plain_variable_expansion_is_not_a_command():
    """`$VAR` and `${VAR}` expand, they do not execute. Refusing them would block every
    ordinary use of an environment variable."""
    assert guard.evaluate('cat "$HOME/notes.txt"')["allowed"] is True
    assert guard.evaluate('cd "${CLAUDE_JOB_DIR}/tmp"')["allowed"] is True


# --- Prefix wrappers ----------------------------------------------------------------------

def test_a_prefix_wrapper_is_transparent_not_the_command():
    """`command -v x`, `env FOO=1 x`, `xargs x`, `time x` all RUN x. Treating the wrapper as
    the command would allowlist everything behind it -- `env make teardown` would pass on the
    strength of `env` being allowed. Skipping the wrapper checks what actually runs.
    """
    assert guard.evaluate("command -v minusctl")["allowed"] is True
    assert guard.evaluate("env FOO=1 python -m pytest")["allowed"] is True

    for smuggled in ("command make teardown", "env make teardown",
                     "xargs make teardown", "time make teardown",
                     "nohup make teardown"):
        assert guard.evaluate(smuggled)["allowed"] is False, smuggled


def test_a_wrapper_does_not_excuse_a_destructive_command():
    assert guard.evaluate("env terraform destroy")["allowed"] is False
    assert guard.evaluate("time rm -rf build")["allowed"] is False


def test_a_bare_wrapper_with_nothing_after_it_is_allowed():
    """`env` alone prints the environment. There is no smuggled command to refuse."""
    assert guard.evaluate("env")["allowed"] is True


def test_a_windows_executable_suffix_does_not_hide_an_allowlisted_command():
    """On Windows the console script is `minusctl.exe`, and the allowlist holds `minusctl`.

    Found by the guardrail refusing this project's OWN front door: pyproject installs
    minusctl.exe, and matching on the raw basename meant the one command the repo tells
    everyone to use was not on its own allowlist.
    """
    for name in ("minusctl.exe", "minusctl.cmd", "minusctl.bat", "terraform.exe",
                 "python.exe", "git.exe"):
        assert guard.evaluate(f"{name} runs list")["allowed"] is True, name


def test_stripping_the_suffix_does_not_allowlist_something_new():
    """`make.exe` is still make."""
    assert guard.evaluate("make.exe teardown")["allowed"] is False


def test_a_suffix_does_not_excuse_a_destructive_command():
    assert guard.evaluate("terraform.exe destroy")["allowed"] is False


def test_a_command_name_from_a_variable_is_refused_with_a_reason_that_says_so():
    """`"$MC" gate plan` cannot be checked: the name only exists once the shell expands it.

    Refusing is right -- resolving it would mean executing the thing being checked. But the
    message said `'$mc' is not on the allowlist`, which reads as a missing binary and sends
    the operator to edit the allowlist. It has to say the name came from a variable.
    """
    for command in ('"$MC" gate plan --dir x', "$TOOL --help", "${BIN}/thing run"):
        decision = guard.evaluate(command)
        assert decision["allowed"] is False, command
        assert decision["rule"] == "ALLOW-02", command
        assert "variable" in decision["reason"], command


def test_a_variable_used_as_an_ARGUMENT_is_not_mistaken_for_the_command():
    """Only the command NAME is unresolvable. `cat "$HOME/notes"` is a cat invocation."""
    assert guard.evaluate('cat "$HOME/notes.txt"')["allowed"] is True
    assert guard.evaluate('python "$SCRIPT"')["allowed"] is True
