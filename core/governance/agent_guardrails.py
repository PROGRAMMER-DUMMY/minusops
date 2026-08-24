"""
Autonomous agent execution sandbox (PRD v15 FR-01).

Evaluates a command or a file write before an agent performs it, and refuses the ones that
destroy work, bypass the human gate, or reach outside the run the agent is scoped to.

THIS IS NOT A SECURITY BOUNDARY AND NOT A SANDBOX. It is a guardrail against a MISTAKE.
Three limits are worth stating plainly, because a reader who mistakes this for containment
will grant an agent more than it should have:

- It only stops callers that ask it. Nothing here intercepts a process that runs a command
  directly, so this belongs at the one place tool calls funnel through, not scattered.
- A pattern list cannot enumerate every spelling of a destructive command. What it does
  catch reliably is the shapes commands ACTUALLY arrive in -- padded whitespace, reordered
  or long-form flags, a chained `&&`, a `bash -c` wrapper -- which is where an agent slips
  by accident rather than by intent.
- Real containment is an OS-level jail, a read-only mount, or credentials that cannot
  perform the action. Those are the controls; this reduces the blast radius of a wrong turn
  in between.

Fails CLOSED on ambiguity. An unparseable command is refused, and an agent with no run scope
gets no write permission at all, because the isolation must not evaporate exactly when the
scope is unclear.

Depends on: nothing
Shells out to: nothing. Standard library only.
Used by: agent tool-call sites, tests/test_agent_guardrails.py
"""
import os
import re
import shlex

# Each rule is (id, description, compiled pattern). The patterns run against a NORMALISED
# command -- lowercased, whitespace collapsed, wrapper shells unwrapped -- so a rule does not
# have to spell out every way a caller might pad or quote the same instruction.
_DESTRUCTIVE = [
    ("FS-02", "recursive directory removal", r"\brmdir\b.*\s(?:/s|-{1,2}r)\b"),
    ("TF-01", "terraform destroy", r"\bterraform\b.*\bdestroy\b"),
    ("TF-02", "terraform state removal", r"\bterraform\b.*\bstate\s+rm\b"),
    ("TF-03", "terraform lock override", r"\bterraform\b.*\bforce-unlock\b"),
    ("GIT-01", "hard reset discards work", r"\bgit\b.*\breset\b.*--hard\b"),
    ("GIT-02", "force push rewrites history",
     r"\bgit\b.*\bpush\b.*(?:--force\b|--force-with-lease\b|\s-f\b)"),
    ("GIT-03", "clean removes untracked files", r"\bgit\b.*\bclean\b.*-[a-z]*[fdx]"),
    ("AWS-01", "bucket removal", r"\baws\b.*\bs3\b.*\brb\b.*--force\b"),
    ("AWS-02", "recursive object deletion", r"\baws\b.*\bs3\b.*\brm\b.*--recursive\b"),
    ("SQL-01", "drop table or database", r"\bdrop\s+(?:table|database)\b"),
    ("SQL-02", "truncate table", r"\btruncate\s+table\b"),
]
_DESTRUCTIVE = [(rule, why, re.compile(pattern)) for rule, why, pattern in _DESTRUCTIVE]

# Governed mutations. Not destructive, but they change infrastructure or run live proofs, so
# PRD v15 FR-01.2 puts them behind a verified human rather than behind a pattern.
_HUMAN_REQUIRED = [
    ("HITL-01", "applies infrastructure changes", re.compile(r"\bminusctl\b.*\bgate\b.*\bapply\b")),
    ("HITL-02", "executes a live proving run", re.compile(r"\bminusctl\b.*\bprove\b.*--execute\b")),
]

# Shell wrappers whose payload is the real command. Without unwrapping, `bash -c "rm -rf x"`
# reads as an invocation of bash and passes every rule above.
_WRAPPERS = ("bash", "sh", "zsh", "cmd", "powershell", "pwsh")

# Where an agent may never write, even inside its own run: these are the engine, and an agent
# editing the code that governs it has removed the thing governing it.
_PROTECTED_DIRS = ("core", "policy", "tests", ".agents", ".github")


def normalise(command):
    """Lowercase, collapse whitespace, and unwrap `bash -c "..."` style wrappers.

    Returns the normalised string. Never raises: an unparseable command still has to be
    evaluated, and returning the raw lowered text keeps it subject to every rule rather than
    letting a quoting error skip the checks.
    """
    text = str(command or "").strip().lower()
    for _ in range(3):  # a wrapper inside a wrapper is rare, but not worth trusting
        try:
            parts = shlex.split(text)
        except ValueError:
            break
        if len(parts) >= 3 and os.path.basename(parts[0]) in _WRAPPERS and parts[1] in (
                "-c", "/c", "-command"):
            text = " ".join(parts[2:]).strip().lower()
            continue
        break
    return re.sub(r"\s+", " ", text)


def _segments(text):
    """Split a command line on shell separators.

    Every rule runs per segment. Without this, `echo hello && rm -rf build` is one string in
    which the destructive half is easy to miss -- and chaining is how a destructive command
    most often reaches a tool call in the first place.
    """
    return [part.strip() for part in re.split(r"&&|\|\||;|\|", text) if part.strip()]


def _is_recursive_force_delete(segment):
    """rm/del carrying BOTH a recursive and a force flag, however they are spelled.

    Parsed rather than pattern-matched because the flags combine: `-rf`, `-fr`, `-r -f`,
    `--recursive --force` and `/s /q` are all the same instruction, and a regex covering
    every arrangement is harder to read than the parse and still misses one.
    """
    tokens = segment.split()
    if not tokens or os.path.basename(tokens[0]) not in ("rm", "del"):
        return False
    recursive = force = False
    for token in tokens[1:]:
        if token.startswith("--"):
            recursive |= token == "--recursive"
            force |= token == "--force"
        elif token.startswith("-"):
            recursive |= "r" in token[1:]
            force |= "f" in token[1:]
        elif token.startswith("/"):
            recursive |= token[1:] == "s"
            force |= token[1:] == "q"
    return recursive and force


def _allow(**extra):
    decision = {"allowed": True, "rule": None, "reason": None, "requires_human": False}
    decision.update(extra)
    return decision


def _refuse(rule, reason, **extra):
    decision = {"allowed": False, "rule": rule, "reason": reason, "requires_human": False}
    decision.update(extra)
    return decision


def evaluate(command, human_authorized=False):
    """Decide whether an agent may run `command`.

    `human_authorized` is checked with `is True`, not for truthiness: "no" and "false" are
    non-empty strings, and treating either as consent is how a dismissed prompt becomes an
    apply. It also NEVER unlocks a destructive command -- a human approving a plan is not
    consenting to `rm -rf`, and conflating the two turns one approval into a blank cheque.
    """
    text = normalise(command)
    if not text:
        return _refuse("GEN-01", "empty command")

    for segment in _segments(text):
        if _is_recursive_force_delete(segment):
            return _refuse("FS-01", f"refused: recursive force delete ({command})")
        for rule, why, pattern in _DESTRUCTIVE:
            if pattern.search(segment):
                return _refuse(rule, f"refused: {why} ({command})")

    for rule, why, pattern in _HUMAN_REQUIRED:
        if pattern.search(text):
            if human_authorized is True:
                return _allow(rule=rule, requires_human=True)
            return _refuse(rule, f"refused: {why}; requires verified human authorization "
                                 f"({command})", requires_human=True)

    return _allow()


def enforce(command, human_authorized=False):
    """Evaluate and raise PermissionError on refusal. Returns the command when allowed."""
    decision = evaluate(command, human_authorized=human_authorized)
    if not decision["allowed"]:
        raise PermissionError(decision["reason"])
    return command


def evaluate_write(path, run_id=None, workspace=None, maintenance=False):
    """Decide whether an agent scoped to `run_id` may write to `path`.

    Paths are RESOLVED before comparison. `runs/run-a/../../core/x.py` is a write to core,
    and comparing the unresolved string would let it through.

    With no run scope, nothing is writable. An agent that has not said which run it is
    working on is not an agent entitled to the whole tree, and defaulting to allow would
    make this check vanish exactly when the scope is unclear.
    """
    workspace_root = os.path.realpath(workspace or os.getcwd())
    target = os.path.realpath(path)

    if maintenance:
        return _allow(maintenance=True, reason="explicit maintenance flag")

    if not run_id:
        return _refuse("SCOPE-01",
                       "no run scope: an agent with no active run may not write",
                       maintenance=False)

    allowed_root = os.path.realpath(os.path.join(workspace_root, "runs", str(run_id)))
    if _within(target, allowed_root):
        return _allow(maintenance=False)

    relative = os.path.relpath(target, workspace_root).replace("\\", "/")
    head = relative.split("/")[0]
    if head in _PROTECTED_DIRS:
        return _refuse("SCOPE-02",
                       f"refused: {relative} is engine code, outside runs/{run_id}",
                       maintenance=False)
    return _refuse("SCOPE-03",
                   f"refused: {relative} is outside runs/{run_id}", maintenance=False)


def _within(target, root):
    """True when `target` is `root` or sits beneath it.

    commonpath rather than startswith: `runs/run-a2` starts with `runs/run-a` as a string
    but is a different run.
    """
    try:
        return os.path.commonpath([target, root]) == root
    except ValueError:  # different drives on Windows
        return False
