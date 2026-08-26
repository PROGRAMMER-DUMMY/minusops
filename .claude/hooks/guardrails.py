"""
PreToolUse adapter: run core/governance/agent_guardrails.py against a real tool call.

The guardrails module was written to sit "at the one place tool calls funnel through" and
then sat with zero callers, because in this repo that place is not Python -- it is the agent
runtime's own hook. 216 lines of refusal logic that nothing invokes is worse than no
guardrail: the file's existence implies a coverage that does not exist.

PROTOCOL. Reads the hook payload as JSON on stdin. Exit 0 allows the call, exit 2 blocks it
and shows stderr to the agent. Exit 1 is a non-blocking error -- used when the payload cannot
be understood, because a hook that cannot parse its input must not silently allow (that hides
the outage) and must not block every call (that bricks the session). It says so, loudly, and
gets out of the way.

TWO SCOPES, DELIBERATELY DIFFERENT.

  Bash commands are always checked, against an ALLOWLIST first: a binary absent from
  agent_guardrails._ALLOWED_COMMANDS is refused by default, so `make teardown` and
  `npm run reset` stop without anyone having predicted them. Allowlisted binaries are then
  refined by the destructive rules -- `git` may run, `git push --force` may not -- and the
  human-gated `minusctl gate apply` is refused regardless of who is driving, because none of
  those is made safe by a developer being at the keyboard.

  It does not close interpreter paths. `python cleanup.py` is allowed because `python`
  must be, and that script can call boto3. No allowlist of binaries closes an interpreter;
  the IAM credential is what bounds it.

  Writes are checked ONLY when a run scope is declared, via MINUS_AGENT_RUN_ID.
  evaluate_write() refuses every path when no run is set, which is right for an autonomous
  agent -- one that has not said which run it is working on is not entitled to the tree --
  and wrong for a developer editing this repo, where it would block every edit including the
  ones that maintain the guardrail. An undeclared scope means the write check does not
  APPLY; that is not the same as the write being approved, and the two are not conflated.

THIS IS STILL NOT A SANDBOX. It stops a mistake, not an intent. Real containment is an OS
jail, a read-only mount, or credentials that cannot perform the action.

Depends on: core/governance/agent_guardrails.py
Shells out to: nothing
Used by: .claude/settings.json (PreToolUse), tests/test_guardrails_hook.py
"""
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "core", "governance"))

import agent_guardrails  # noqa: E402

RUN_ID_ENV = "MINUS_AGENT_RUN_ID"
AUTHORIZED_ENV = "MINUS_AGENT_HUMAN_AUTHORIZED"

ALLOW, BLOCK, BROKEN = 0, 2, 1

# The tools that write a file, and the payload key each one carries the path in.
_WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "NotebookEdit": "notebook_path"}


def _human_authorized():
    """`is True`, never truthiness -- the same rule the module itself applies.

    Only the exact string "true" counts. An operator who exports the variable to "no" has
    said no, and every other spelling is ambiguous enough to refuse.
    """
    return os.environ.get(AUTHORIZED_ENV, "").strip().lower() == "true"


def decide(payload):
    """Return (exit_code, message). Pure, so the tests do not need a subprocess."""
    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return BROKEN, f"guardrails: tool_input for {tool} is not an object"

    if tool == "Bash":
        command = tool_input.get("command") or ""
        decision = agent_guardrails.evaluate(
            command, human_authorized=_human_authorized() is True)
        if decision["allowed"]:
            return ALLOW, ""
        return BLOCK, _explain(decision)

    if tool in _WRITE_TOOLS:
        run_id = (os.environ.get(RUN_ID_ENV) or "").strip()
        if not run_id:
            # Not applicable, which is not the same as approved -- see the module docstring.
            return ALLOW, ""
        path = tool_input.get(_WRITE_TOOLS[tool]) or ""
        if not path:
            return BROKEN, f"guardrails: {tool} carried no path to check"
        decision = agent_guardrails.evaluate_write(
            path, run_id=run_id, workspace=payload.get("cwd") or _REPO)
        if decision["allowed"]:
            return ALLOW, ""
        return BLOCK, _explain(decision)

    return ALLOW, ""


def _explain(decision):
    """What was refused, why, and what the agent should do instead of retrying."""
    lines = [f"BLOCKED by MinusOps guardrail {decision['rule']}: {decision['reason']}"]
    if decision.get("requires_human"):
        lines.append(
            "This action requires a verified human. Ask the operator to run it themselves, "
            f"or to set {AUTHORIZED_ENV}=true for this session if they intend to authorize "
            "it. Do not rephrase the command to get past this.")
    else:
        lines.append(
            "This is refused for every caller and cannot be authorized by a flag. If the "
            "action is genuinely intended, the operator runs it themselves.")
    return "\n".join(lines)


def main(argv=None):
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(f"guardrails: could not parse the hook payload ({exc}); "
              f"the guardrail did NOT run on this call", file=sys.stderr)
        return BROKEN

    if not isinstance(payload, dict):
        print("guardrails: hook payload was not an object; the guardrail did NOT run",
              file=sys.stderr)
        return BROKEN

    code, message = decide(payload)
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
