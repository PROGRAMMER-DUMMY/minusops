"""
Autonomous agent execution sandbox.

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
Used by: .claude/hooks/guardrails.py -- the PreToolUse adapter registered in
    .claude/settings.json, which is the one place this repo's tool calls funnel through.
    That registration is not decoration: this module had ZERO call sites for its whole life,
    and 216 lines of refusal logic nothing invokes is worse than no guardrail, because the
    file's existence implies a coverage that does not exist.
    Also tests/test_agent_guardrails.py, tests/test_guardrails_hook.py
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

# THE ALLOWLIST -- what may run at all. The rules above then decide HOW.
#
# A denylist catches only the spelling it enumerates: `terraform destroy` is refused while
# `make teardown`, `python cleanup.py` and `npm run reset` are the same instruction wearing
# different clothes. Enumerating danger is fail-open by construction, which is the
# same argument destructive_change_gate.py makes about resource types and the reason its gate
# is AUTO_SHIP_ELIGIBLE_TYPES rather than a denylist. AWS reached the same shape: the AWS API
# MCP Server's READ_OPERATIONS_ONLY matches each command against known-read-only actions and
# runs it only on a hit.
#
# Membership is a reviewed fact. A binary absent from here is refused as ALLOW-01 naming the
# binary, so adding one is a deliberate act with a name attached rather than a silent default.
#
# WHAT THIS DOES NOT CLOSE. `python` has to be here -- this project runs pytest and its own
# CLIs through it -- so `python anything.py` is allowed and that script can call boto3. No
# allowlist of BINARIES closes an interpreter; only the credential does. The allowlist raises
# the floor (no make, no npm, no curl piped to a shell, no unknown tool) and the IAM boundary
# is still what holds. test_an_interpreter_running_a_file_is_allowed_and_this_is_a_known_limit
# pins that gap open on purpose so no reader mistakes this for containment.
_ALLOWED_COMMANDS = frozenset({
    # This control plane, and the tools it drives.
    "minusctl", "minus-gate", "minus-bcm", "minus-runs", "minus-demo", "minus-resolve",
    "minus-workflow", "minus-accelerator", "minus-update-module", "minus-schema-watch",
    "terraform", "tflint", "checkov", "trivy", "opa", "tfsec", "infracost",
    # Interpreters and package tooling. See the limit noted above.
    "python", "python3", "py", "pip", "pip3", "pytest", "uv", "uvx",
    # Version control. `git` is allowlisted; GIT-01..03 still refuse its destructive verbs.
    "git", "gh",
    # Ordinary read and inspect.
    "ls", "dir", "cat", "head", "tail", "less", "more", "grep", "rg", "find", "wc", "sort",
    "uniq", "cut", "tr", "sed", "awk", "diff", "jq", "yq", "file", "stat", "du", "df",
    "which", "where", "type", "command", "env", "printenv", "date", "whoami", "hostname", "pwd", "tree",
    "echo", "printf", "true", "false", "sleep", "test", "basename", "dirname", "realpath",
    "xargs", "tee",
    # Ordinary file work. `rm` is here so a single file can be removed; FS-01 still refuses a
    # recursive force delete, which is the shape that destroys work.
    "mkdir", "cp", "mv", "touch", "rm", "rmdir", "ln", "chmod", "tar", "zip", "unzip",
    "cd", "pushd", "popd", "export", "set",
    # The cloud CLI. Its own destructive verbs are refused by AWS-01/AWS-02, and the IAM
    # credential is what actually bounds it.
    "aws",
})

# Where an agent may never write, even inside its own run: these are the engine, and an agent
# editing the code that governs it has removed the thing governing it.
_PROTECTED_DIRS = ("core", "policy", "tests", ".agents", ".github")


# Commands that EXECUTE what they are fed on stdin. A heredoc addressed to one of these is
# a script; a heredoc addressed to anything else is data that happens to be quoted.
_SHELL_READERS = {"bash", "sh", "zsh", "ksh", "dash", "ash", "busybox"}

# `<<EOF`, `<<-EOF`, `<<'EOF'`, `<<"EOF"`.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def _strip_heredoc_bodies(text):
    """Drop heredoc bodies that no shell will execute, keeping every real command line.

    This guardrail blocked its own test script for containing the string "terraform destroy"
    in a list of fixtures. A `python - <<'EOF'` body is Python source; a `cat <<'EOF'` body is
    file content. Neither is a shell command, and refusing them makes it impossible to write
    a test, a document, or a grep about the very commands being guarded.

    The exemption is about the READER, not the syntax. `bash <<'EOF'` executes every line of
    its body, so the body is kept and checked. When any token on the opening line looks like
    a shell the body is kept -- an approximation that errs toward checking.

    An UNTERMINATED heredoc returns the text untouched. If the terminator never arrives there
    is no way to know where the body ends, and guessing would mean guessing permissively.
    """
    if "<<" not in text:
        return text
    lines = text.split("\n")
    kept, index = [], 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        match = _HEREDOC.search(line)
        if not match:
            index += 1
            continue

        delimiter = match.group(2)
        end = index + 1
        while end < len(lines) and lines[end].strip() != delimiter:
            end += 1
        if end >= len(lines):
            return text                                  # unterminated: check everything

        if any(os.path.basename(token) in _SHELL_READERS for token in line.split()):
            kept.extend(lines[index + 1:end + 1])         # a shell runs this body
        # Otherwise the body AND its terminator are dropped. Keeping the terminator left a
        # bare word on its own line, which the allowlist then read as an unknown binary and
        # refused -- `python - <<'PYEOF' ... PYEOF` failed on the word PYEOF.
        index = end + 1
    return "\n".join(kept)


def normalise(command):
    """Lowercase, collapse whitespace, and unwrap `bash -c "..."` style wrappers.

    Newlines become `;` before the whitespace collapse. A newline IS a shell command
    separator, and flattening it to a space instead made `cat <<'EOF' > f.txt` ... `EOF` then
    `rm -rf /data` a single segment beginning with `cat`, so the per-segment parse looked at
    the wrong verb and the delete went unnoticed.

    Returns the normalised string. Never raises: an unparseable command still has to be
    evaluated, and returning the raw lowered text keeps it subject to every rule rather than
    letting a quoting error skip the checks.
    """
    text = _strip_heredoc_bodies(str(command or "").strip()).lower()
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ; ")
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


def _extract_substitutions(text):
    """Pull `$(...)` and backtick spans out as commands in their own right.

    A substitution RUNS a command, so `echo $(rm -rf /)` has to be checked. The outer text
    must also not be tokenized as though the substitution's words were its own, or
    `d="/tmp/gate$(date +%s)"` is refused for a binary named `+%s)"`.

    Returns (outer_with_spans_blanked, [inner commands]). Nested substitutions are extracted
    recursively. `$((...))` is arithmetic, not a command, so it is blanked without being
    checked -- refusing `$((1+2))` as an unknown binary would be nonsense.
    """
    inner = []
    out = []
    index = 0
    while index < len(text):
        if text.startswith("$((", index):
            depth, scan = 0, index + 1
            while scan < len(text):
                if text[scan] == "(":
                    depth += 1
                elif text[scan] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                scan += 1
            out.append(" ")
            index = scan + 1
            continue
        if text.startswith("$(", index):
            depth, scan = 1, index + 2
            while scan < len(text) and depth:
                if text[scan] == "(":
                    depth += 1
                elif text[scan] == ")":
                    depth -= 1
                    if not depth:
                        break
                scan += 1
            body = text[index + 2:scan]
            nested_outer, nested_inner = _extract_substitutions(body)
            inner.append(nested_outer)
            inner.extend(nested_inner)
            out.append(" ")
            index = scan + 1
            continue
        if text[index] == "`":
            scan = text.find("`", index + 1)
            if scan == -1:
                out.append(text[index])
                index += 1
                continue
            inner.append(text[index + 1:scan])
            out.append(" ")
            index = scan + 1
            continue
        out.append(text[index])
        index += 1
    return "".join(out), inner


def _segments(text):
    """Split a command line on UNQUOTED shell separators.

    Every rule runs per segment. Without splitting, `echo hello && rm -rf build` is one string
    in which the destructive half is easy to miss, and chaining is how a destructive command
    most often reaches a tool call in the first place.

    Quote-aware because a regex split is not. `sed 's/x1b\\[[0-9;]*m//g'` carries a `;` inside
    a single-quoted script; splitting on it leaves a fragment whose first token reads as the
    binary `g'`, and an ordinary log-stripping pipeline is refused. That is the shape of
    false positive that gets a guardrail switched off.

    An UNBALANCED quote falls back to splitting the remainder anyway: a quoting error must not
    turn the rest of the line into one inert string that no rule looks inside.
    """
    text, substitutions = _extract_substitutions(text)

    parts, current = [], []
    quote = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            index += 1
            continue
        if text.startswith("&&", index) or text.startswith("||", index):
            parts.append("".join(current))
            current = []
            index += 2
            continue
        if char in ";|":
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    parts.append("".join(current))

    if quote:
        # The quote never closed, so the tail was never really quoted. Re-split it naively
        # rather than trusting a broken quoting state to have hidden nothing.
        tail = parts.pop()
        parts.extend(re.split(r"&&|\|\||;|\|", tail))

    # Each substitution is its own command line, so it gets the same treatment recursively.
    for substitution in substitutions:
        parts.extend(_segments(substitution))

    return [part.strip() for part in parts if part.strip()]


def _is_recursive_force_delete(segment):
    """rm/del carrying BOTH a recursive and a force flag, however they are spelled.

    Parsed rather than pattern-matched because the flags combine: `-rf`, `-fr`, `-r -f`,
    `--recursive --force` and `/s /q` are all the same instruction, and a regex covering
    every arrangement is harder to read than the parse and still misses one.
    """
    tokens = _command_tokens(segment)
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


# Commands whose ARGUMENT is the real command. Allowlisting one of these without stepping
# through it would allow everything behind it: `env make teardown` would pass on the strength
# of `env`. Skipped so the check lands on what actually runs.
_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".com", ".ps1")

_PREFIX_WRAPPERS = frozenset({
    "command", "env", "nohup", "time", "xargs", "nice", "ionice", "stdbuf", "timeout",
    "sudo", "doas", "setsid", "exec",
})

# `timeout` takes a duration before the command it wraps. Without skipping it the check lands
# on the duration: `timeout 5 rm -rf /` was refused for `5` not being an allowed command,
# which is the right answer for the wrong reason -- and the delete parser, looking at the same
# tokens, never saw `rm` at all.
_WRAPPERS_WITH_OPERAND = frozenset({"timeout"})
_DURATION = re.compile(r"^\d+(\.\d+)?[smhd]?$")


def _command_tokens(segment):
    """The segment's tokens with leading noise removed: `VAR=value` assignments, flags before
    the command, dangling punctuation, and prefix wrappers.

    Shared by _binary_of and _is_recursive_force_delete so both agree on where the command
    starts. They did not, briefly: `time rm -rf build` was refused by neither, because the
    delete parser looked at token[0] and found `time`.
    """
    tokens = list(segment.split())
    while tokens:
        token = tokens[0]
        if "=" in token and not token.startswith(("-", "/", ".")):
            tokens.pop(0)
            continue
        if token.startswith(("-", ">", "<", "(", ")", "&")):
            tokens.pop(0)
            continue
        bare = token.strip("\"'")
        if "$" in bare:
            # Kept whole: basename("${BIN}/thing") is "thing", which hides that the path came
            # from a variable and would let an unresolvable command read as a known one.
            tokens[0] = bare.lower()
            return tokens
        name = os.path.basename(bare).lower()
        # Windows installs console scripts as `minusctl.exe`. Matching the raw basename meant
        # the one command this repo tells everyone to use was not on its own allowlist.
        for suffix in _EXECUTABLE_SUFFIXES:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        if not any(char.isalnum() for char in name):
            tokens.pop(0)
            continue
        if name in _PREFIX_WRAPPERS:
            tokens.pop(0)
            if name in _WRAPPERS_WITH_OPERAND and tokens and _DURATION.match(tokens[0]):
                tokens.pop(0)
            continue
        tokens[0] = name
        return tokens
    return []


def _binary_of(segment):
    """The command a segment invokes, stripped of path, assignments and prefix wrappers.

    `/usr/bin/make` and `make` are the same instruction, `FOO=1 make teardown` runs make, and
    so does `env make teardown`. Returns "" for a segment with nothing invocable in it (a bare
    redirect, a lone wrapper, a stray operator) -- there is no command there to refuse.
    """
    tokens = _command_tokens(segment)
    return tokens[0] if tokens else ""


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

    # The specific rules run FIRST, so a refusal carries the reason that explains it. Checking
    # the allowlist first would report `terraform destroy` as ALLOW-01 if terraform were ever
    # taken off the list -- technically a refusal, and useless to the operator reading it.
    for segment in _segments(text):
        if _is_recursive_force_delete(segment):
            return _refuse("FS-01", f"refused: recursive force delete ({command})")
        for rule, why, pattern in _DESTRUCTIVE:
            if pattern.search(segment):
                return _refuse(rule, f"refused: {why} ({command})")

    for segment in _segments(text):
        binary = _binary_of(segment)
        if binary and "$" in binary:
            # The command NAME comes from a variable, so it only exists once the shell
            # expands it -- resolving it would mean running the thing being checked. Refusing
            # is right; saying "not on the allowlist" was not, because it reads as a missing
            # binary and sends the operator to edit the list.
            return _refuse("ALLOW-02",
                           f"refused: the command name comes from a shell variable "
                           f"({binary!r}) and cannot be checked before it runs ({command}). "
                           f"Write the command literally", binary=binary)
        if binary and binary not in _ALLOWED_COMMANDS:
            return _refuse("ALLOW-01",
                           f"refused: {binary!r} is not on the allowlist of commands this "
                           f"agent may run ({command}). Adding it is a reviewed decision, "
                           f"not a default", binary=binary)

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
