"""
Agent-first CLI diagnostics: fuzzy run resolution, stage interception, actionable errors.

The reader of these messages is usually an agent, not a person scrolling a terminal. An agent
cannot infer "run the previous step" from `FileNotFoundError: requirements.json` -- it needs
the exact command. So every failure here answers three questions in a fixed order: what
failed, why, and the literal command to run next.

Kept out of `minusctl.py` on purpose. The lifecycle spans two entry points -- minusctl owns
create/next/readiness, plan_gate owns plan/approve/apply -- and a prerequisite check that
lives in only one of them can only intercept half the mistakes.

Depends on: core/reporting/runs.py; core/governance/plan_gate.py, imported lazily inside
    `missing_plan_prerequisite` so importing this module never drags in the gate
Shells out to: nothing — no cloud CLI, no `terraform`, no network. It only reads the
    run workspace and plan_gate's on-disk records.
Used by: core/reporting/minusctl.py, core/governance/plan_gate.py,
    tests/test_cli_diagnostics.py
"""
import difflib
import json
import os
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)

import runs  # noqa: E402

_RULE = "=" * 70

# Deliberately narrow. difflib will happily "match" two run ids that share a date prefix and
# differ everywhere else; at 0.6 a transposed or dropped character still matches while a
# genuinely different run does not.
_FUZZY_CUTOFF = 0.6
_MAX_SUGGESTIONS = 3
_MAX_RECENT = 5


# --- error shape ---------------------------------------------------------------------------

def format_agent_error(title, reason, fix_command, context=None):
    """Three-part failure: what failed, why, and the exact next command.

    `fix_command` is a string or a list of strings, and it must be copy-pasteable as written.
    A "fix" that still needs the reader to substitute a placeholder is a fourth problem, not
    a solution -- so callers resolve run ids and paths before they get here.
    """
    lines = [_RULE, f"[X] WHAT FAILED: {title}", f"[?] WHY IT FAILED: {reason}",
             "[>] ACTION REQUIRED:"]
    commands = [fix_command] if isinstance(fix_command, str) else list(fix_command)
    lines += [f"       {command}" for command in commands]
    if context:
        lines.append("")
        lines += [f"    {key}: {value}" for key, value in context.items()]
    lines.append(_RULE)
    return "\n".join(lines)


def fail(title, reason, fix_command, context=None, stream=None):
    """Print an agent error to stderr. Returns 2, for `return fail(...)` at a call site."""
    print(format_agent_error(title, reason, fix_command, context),
          file=stream or sys.stderr)
    return 2


# --- fuzzy run resolution ------------------------------------------------------------------

def _stage_of(run):
    """Coarse lifecycle stage, from artifacts on disk rather than a recorded status field.

    A status field records what a command CLAIMED; the files record what actually happened,
    and the two diverge exactly when someone needs this listing most.
    """
    root = run.get("root") or ""
    tf_dir = run.get("terraform_dir") or os.path.join(root, "terraform")
    if os.path.isdir(os.path.join(root, "reports")) and os.listdir(
            os.path.join(root, "reports")):
        return "planned"
    if os.path.isdir(tf_dir) and any(n.endswith(".tf") for n in _safe_listdir(tf_dir)):
        return "synthesized"
    if os.path.exists(os.path.join(root, "architecture_decision.json")):
        return "decided"
    if os.path.exists(os.path.join(root, "requirements.json")):
        return "requirements"
    return "created"


def _safe_listdir(path):
    try:
        return os.listdir(path)
    except OSError:
        return []


# A goal string is free text written by a person or an agent and it lands in terminal output.
# Control characters in it can clear the screen or forge lines that look like our own -- so the
# tip is sanitised on the way out, not trusted on the way in.
_CONTROL = {c: None for c in range(32)}
_CONTROL[127] = None
_TIP_MAX = 110


def _clean(value, limit=60):
    text = str(value or "").translate(_CONTROL).strip()
    text = " ".join(text.split())
    # "..." not U+2026: this prints to a Windows console that is still cp1252 by default,
    # where the ellipsis renders as a literal "?" -- noise inside the very field meant to
    # help someone recognise a run.
    return text[:limit - 3] + "..." if len(text) > limit else text


def get_run_description_tip(run_root):
    """One line describing what a run is FOR, so a suggested id can be recognised or rejected.

    Fail-soft by construction: every failure mode -- absent, unreadable, malformed, or
    half-written by a concurrent synthesis -- returns a usable string rather than raising.
    A diagnostic that crashes while explaining an earlier error is worse than the error.

    This describes INTENT, not what was built. requirements.json can be edited after
    synthesis, so the tip disambiguates workloads; it is not a description of the stack.
    """
    path = os.path.join(run_root or "", "requirements.json")
    if not os.path.exists(path):
        return "workspace initialized (requirements pending)"
    try:
        with open(path, encoding="utf-8") as handle:
            spec = json.load(handle)
    except (OSError, ValueError):
        # A partially-written file during `create` parses as invalid JSON; that is a normal
        # transient, not a corrupted workspace, so it is reported plainly.
        return "requirements.json present but unreadable right now"
    if not isinstance(spec, dict):
        return "requirements.json is not an object"

    goal = _clean(spec.get("goal"), 60)
    volume = _clean((spec.get("data_pipeline") or {}).get("data_volume")
                    if isinstance(spec.get("data_pipeline"), dict) else "", 24)
    owner = _clean(spec.get("owner") or spec.get("gathered_by"), 22)

    parts = [goal or "(no goal recorded)"]
    detail = ", ".join(p for p in (volume, f"owner: {owner}" if owner else "") if p)
    if detail:
        parts.append(f"({detail})")
    return _clean(" ".join(parts), _TIP_MAX)


def describe_run(run):
    """(run_id, stage, tip) for one run record."""
    return (run.get("run_id", "?"), _stage_of(run),
            get_run_description_tip(run.get("root") or ""))


def format_candidates(run_ids):
    """Numbered candidates, each with its stage and what it is for.

    Numbered rather than bulleted because the point is that a human or agent CHOOSES one.
    A bare id list invites accepting the first suggestion, which is exactly the failure this
    exists to prevent: two runs from the same day differ only in what they are for.
    """
    lines = []
    for index, run_id in enumerate(run_ids, 1):
        run = runs.get_run(run_id) or {"run_id": run_id}
        _, stage, tip = describe_run(run)
        lines.append(f"       [{index}] runs/{run_id}")
        lines.append(f"           description: {tip}")
        lines.append(f"           stage      : {stage}")
    return chr(10).join(lines)


def recent_runs(limit=_MAX_RECENT):
    return [(r.get("run_id", "?"), _stage_of(r)) for r in runs.list_runs()[:limit]]


def suggest_runs(run_id):
    """Close matches for a mistyped run id, best first.

    Matches on the id AND on its prefix: run ids are timestamps, so a truncated id
    ("20260819-1014") is the common "typo" and is a prefix, not an edit-distance neighbour.
    """
    known = [r.get("run_id", "") for r in runs.list_runs()]
    prefix_hits = [r for r in known if r.startswith(run_id)]
    fuzzy = difflib.get_close_matches(run_id, known, n=_MAX_SUGGESTIONS, cutoff=_FUZZY_CUTOFF)
    ordered = prefix_hits + [f for f in fuzzy if f not in prefix_hits]
    return ordered[:_MAX_SUGGESTIONS]


def resolve_run_or_fail(run_id=None, command="next"):
    """(run, None) or (None, exit_code). Never raises SystemExit -- the caller decides."""
    run = runs.get_run(run_id)
    if run:
        return run, None

    if not runs.list_runs():
        return None, fail(
            "No run workspaces exist yet.",
            "Nothing has been created in runs/ on this machine.",
            'minusctl create "<what you want to build>"')

    suggestions = suggest_runs(run_id or "")
    listing = "\n".join(f"       - runs/{rid}  (stage: {stage})"
                        for rid, stage in recent_runs())
    if suggestions:
        best = suggestions[0]
        reason = (f"No run matches {run_id!r}. Closest existing id is {best!r} -- "
                  "likely a typo or a truncated timestamp.")
        fix = [f"minusctl {command} --run {best}"]
    else:
        reason = (f"No run matches {run_id!r}, and nothing in runs/ is close enough to "
                  "guess at.")
        fix = [f"minusctl {command} --run <id from the list below>"]

    print(f"\n[?] Recent runs:\n{listing}", file=sys.stderr)
    return None, fail(f"Run workspace {run_id!r} not found.", reason, fix)


# --- prerequisite interception -------------------------------------------------------------

# (artifact relative to the run root, step number, human name, the command that produces it).
# Ordered: the FIRST missing one is reported, because telling someone their approval is
# missing when they have not synthesised yet sends them to the wrong end of the pipeline.
_LIFECYCLE = (
    ("requirements.json", 1, "Requirements",
     'minusctl create "<what you want to build>"'),
    ("architecture_decision.json", 2, "Architecture decision record",
     "minusctl decision template --write --run {run_id}"),
    ("terraform", 3, "Synthesis (generated HCL)",
     'python core/generation/synthesizer.py "<requirements summary>" --run {run_id}'),
)


def missing_prerequisite(run_root, run_id="<id>", up_to=3):
    """The first unmet lifecycle step for a run root, or None.

    `up_to` bounds how far along the pipeline the caller actually needs: `readiness` needs
    synthesis, `create` needs nothing. Checking further than the command requires would block
    work that is legitimately incomplete.
    """
    needed = _LIFECYCLE[:up_to]
    if not needed:
        return None

    # The artifact the command actually CONSUMES is the one that decides. Earlier steps are
    # inputs to synthesis, not to the caller: `minusctl demo` reaches step 3 without ever
    # writing requirements.json, and demanding its inputs after the fact is archaeology that
    # blocks a run which is provably far enough along.
    if _present(run_root, needed[-1][0]):
        return None

    # Target missing: report the EARLIEST unmet step, because that is where work resumes.
    # Telling someone their synthesis is missing when they have no requirements yet sends
    # them to the wrong end of the pipeline.
    for artifact, step, name, command in needed:
        if not _present(run_root, artifact):
            return {"artifact": artifact, "step": step, "name": name,
                    "command": command.format(run_id=run_id)}
    return None


def _present(run_root, artifact):
    path = os.path.join(run_root, artifact)
    if artifact == "terraform":
        # mkdir is not generation: an empty directory would otherwise read as done.
        return os.path.isdir(path) and any(n.endswith(".tf") for n in _safe_listdir(path))
    return os.path.exists(path)


def require_stage(run, command, up_to=3):
    """None to proceed, or an exit code after printing the interception."""
    gap = missing_prerequisite(run.get("root", ""), run.get("run_id", "<id>"), up_to=up_to)
    if not gap:
        return None
    return fail(
        f"`{command}` needs step {gap['step']} ({gap['name']}), which has not run.",
        f"{gap['artifact']} is missing from {run.get('root', '?')}.",
        gap["command"],
        {"run": run.get("run_id", "?"), "stage reached": _stage_of(run)})


def missing_plan_prerequisite(tf_dir):
    """Step 4/5 for a Terraform directory: is there a recorded plan, and is it approved?

    Reads plan_gate's own record paths rather than guessing at filenames -- approval is
    stored as <plan_hash>.json in the approval dir, not the `approved.json` a caller might
    reasonably expect.
    """
    try:
        import plan_gate
    except Exception as exc:
        return {"step": 4, "name": "Plan", "reason": f"plan gate unavailable: {exc}",
                "command": f"minusctl gate plan --dir {tf_dir}"}

    pending_path = plan_gate._pending_path(tf_dir)
    if not os.path.exists(pending_path):
        return {"step": 4, "name": "Plan",
                "reason": f"no plan record at {pending_path}",
                "command": f"minusctl gate plan --dir {tf_dir}"}
    try:
        with open(pending_path, encoding="utf-8") as handle:
            plan_hash = json.load(handle).get("plan_hash", "")
    except (OSError, ValueError) as exc:
        return {"step": 4, "name": "Plan", "reason": f"plan record unreadable: {exc}",
                "command": f"minusctl gate plan --dir {tf_dir}"}

    if not os.path.exists(plan_gate._approved_path(tf_dir, plan_hash)):
        return {"step": 5, "name": "Approval",
                "reason": f"plan {plan_hash[:12]}... has no approval on record",
                "command": f"minusctl gate approve --dir {tf_dir}"}
    return None


# --- help text -----------------------------------------------------------------------------

def epilog(examples, requires=(), produces=(), next_step=""):
    """Subcommand epilog: what it needs, what it leaves behind, what to run next.

    argparse strips whitespace unless the formatter preserves it, so callers must pair this
    with RawDescriptionHelpFormatter -- otherwise the examples reflow into one paragraph and
    stop being copy-pasteable, which is the only reason they are here.
    """
    parts = ["examples:"] + [f"  {e}" for e in examples]
    if requires:
        parts += ["", "requires:"] + [f"  {r}" for r in requires]
    if produces:
        parts += ["", "produces:"] + [f"  {p}" for p in produces]
    if next_step:
        parts += ["", f"next: {next_step}"]
    return "\n".join(parts)
