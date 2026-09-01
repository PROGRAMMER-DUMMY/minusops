"""
The PreToolUse adapter that finally gives agent_guardrails a caller.

The module had 216 lines of refusal logic and zero call sites for its whole life, so the
tests that matter are the ones proving it now actually RUNS on a tool call -- end to end,
through a subprocess, over the real stdin/exit-code protocol. A unit test of `decide()`
alone would reproduce the original failure exactly: logic that passes its tests and
intercepts nothing.

Depends on: .claude/hooks/guardrails.py, core/governance/agent_guardrails.py
Shells out to: the hook, as a subprocess (deliberately -- that is how it is invoked)
Used by: nothing (pytest entry point)
"""
import json
import os
import subprocess
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HOOK = os.path.join(_REPO, ".claude", "hooks", "guardrails.py")

ALLOW, BLOCK, BROKEN = 0, 2, 1


def _invoke(payload, **env):
    """Run the hook the way the agent runtime does: JSON on stdin, meaning in the exit code."""
    environment = dict(os.environ)
    environment.pop("MINUS_AGENT_RUN_ID", None)
    environment.pop("MINUS_AGENT_HUMAN_AUTHORIZED", None)
    environment.update({k: str(v) for k, v in env.items()})
    return subprocess.run([sys.executable, _HOOK], input=json.dumps(payload),
                          capture_output=True, text=True, env=environment)


def _bash(command):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}, "cwd": _REPO}


# --- It is actually wired ----------------------------------------------------------------

def test_the_hook_file_exists_and_is_registered():
    """A hook nothing registers is agent_guardrails all over again."""
    assert os.path.exists(_HOOK)
    settings = json.load(open(os.path.join(_REPO, ".claude", "settings.json"),
                              encoding="utf-8"))
    hooks = json.dumps(settings.get("hooks", {}))
    assert "PreToolUse" in settings.get("hooks", {}), "the hook is not registered"
    assert "guardrails.py" in hooks, "settings.json does not point at the adapter"


# --- Refusals that hold for everyone -----------------------------------------------------

@pytest.mark.parametrize("command", [
    "rm -rf /",
    "rm -fr runs",
    "rm --recursive --force important",
    "terraform destroy -auto-approve",
    "git push --force origin main",
    "git reset --hard HEAD~5",
])
def test_a_destructive_command_is_blocked(command):
    done = _invoke(_bash(command))
    assert done.returncode == BLOCK, f"{command!r} was not blocked: {done.stdout}"
    assert "BLOCKED by MinusOps guardrail" in done.stderr


def test_a_destructive_command_stays_blocked_even_when_a_human_authorized_the_session():
    """A human approving a plan is not consenting to `rm -rf`. Conflating the two turns one
    approval into a blank cheque."""
    done = _invoke(_bash("rm -rf runs"), MINUS_AGENT_HUMAN_AUTHORIZED="true")
    assert done.returncode == BLOCK


def test_a_destructive_command_hidden_in_a_chain_is_still_caught():
    done = _invoke(_bash("cd /tmp && ls && rm -rf /important"))
    assert done.returncode == BLOCK


def test_an_ordinary_command_is_allowed_silently():
    done = _invoke(_bash("python -m pytest -q"))
    assert done.returncode == ALLOW
    assert done.stderr == ""


# --- The human gate ----------------------------------------------------------------------

def test_the_apply_gate_is_refused_without_a_verified_human():
    done = _invoke(_bash("minusctl gate apply --dir runs/x/terraform"))
    assert done.returncode == BLOCK
    assert "requires a verified human" in done.stderr
    assert "Do not rephrase" in done.stderr


def test_the_apply_gate_passes_once_a_human_authorized_the_session():
    done = _invoke(_bash("minusctl gate apply --dir runs/x/terraform"),
                   MINUS_AGENT_HUMAN_AUTHORIZED="true")
    assert done.returncode == ALLOW


@pytest.mark.parametrize("value", ["no", "false", "0", "yes", "TRUE ", "1", ""])
def test_only_the_exact_string_true_counts_as_authorization(value):
    """"no" is a non-empty string. Treating it as consent is how a dismissed prompt becomes
    an apply."""
    done = _invoke(_bash("minusctl gate apply --dir runs/x/terraform"),
                   MINUS_AGENT_HUMAN_AUTHORIZED=value)
    expected = ALLOW if value.strip().lower() == "true" else BLOCK
    assert done.returncode == expected, f"{value!r} was treated as {done.returncode}"


# --- Write scope -------------------------------------------------------------------------

def _write(path):
    return {"hook_event_name": "PreToolUse", "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x"}, "cwd": _REPO}


def test_a_scoped_agent_may_write_inside_its_own_run():
    done = _invoke(_write(os.path.join(_REPO, "runs", "run-a", "notes.txt")),
                   MINUS_AGENT_RUN_ID="run-a")
    assert done.returncode == ALLOW


def test_a_scoped_agent_may_not_write_engine_code():
    done = _invoke(_write(os.path.join(_REPO, "core", "governance", "plan_gate.py")),
                   MINUS_AGENT_RUN_ID="run-a")
    assert done.returncode == BLOCK
    assert "engine code" in done.stderr


def test_a_scoped_agent_may_not_escape_its_run_by_traversal():
    """`runs/run-a/../../core/x.py` is a write to core. Comparing the unresolved string
    would let it through."""
    done = _invoke(_write(os.path.join(_REPO, "runs", "run-a", "..", "..", "core", "x.py")),
                   MINUS_AGENT_RUN_ID="run-a")
    assert done.returncode == BLOCK


def test_a_run_whose_name_merely_prefixes_another_is_not_the_same_run():
    done = _invoke(_write(os.path.join(_REPO, "runs", "run-a2", "notes.txt")),
                   MINUS_AGENT_RUN_ID="run-a")
    assert done.returncode == BLOCK


def test_an_undeclared_run_scope_makes_the_write_check_not_apply():
    """evaluate_write() refuses everything with no run scope, which is right for an
    autonomous agent and wrong for a developer editing this repo -- it would block the edits
    that maintain the guardrail itself. Not applicable is not the same as approved, and the
    Bash checks still run either way."""
    done = _invoke(_write(os.path.join(_REPO, "core", "governance", "plan_gate.py")))
    assert done.returncode == ALLOW
    assert _invoke(_bash("rm -rf runs")).returncode == BLOCK, \
        "an undeclared write scope must not disable the command checks"


# --- Failure modes -----------------------------------------------------------------------

def test_an_unparseable_payload_is_a_visible_error_not_a_silent_allow():
    """Neither 0 nor 2. Allowing hides that the guardrail stopped running; blocking every
    call bricks the session."""
    done = subprocess.run([sys.executable, _HOOK], input="{not json",
                          capture_output=True, text=True)
    assert done.returncode == BROKEN
    assert "did NOT run" in done.stderr


def test_an_unknown_tool_is_allowed():
    done = _invoke({"hook_event_name": "PreToolUse", "tool_name": "Read",
                    "tool_input": {"file_path": "x"}})
    assert done.returncode == ALLOW


def test_a_write_with_no_path_is_reported_rather_than_waved_through():
    done = _invoke({"hook_event_name": "PreToolUse", "tool_name": "Write",
                    "tool_input": {}}, MINUS_AGENT_RUN_ID="run-a")
    assert done.returncode == BROKEN


def test_the_hook_is_ascii_only():
    offenders = sorted({c for c in open(_HOOK, encoding="utf-8").read() if ord(c) > 127})
    assert not offenders, f"non-ASCII characters: {offenders}"
