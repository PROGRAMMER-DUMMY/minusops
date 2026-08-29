"""
`minusctl` entrypoint: one front door for the whole control plane.

WHY THIS IS A FRONT DOOR AND NOT A REWRITE. core/reporting/minusctl.py carries nineteen
subcommands and the tests that prove each of them. Moving that code would risk a regression in
the deploy lifecycle to gain a directory layout. So this package owns the NEW surface --
`use`, `runs list/describe`, `gate`, `cost`, `source` -- and hands every legacy subcommand to
the existing implementation unchanged. Nothing moves, so nothing breaks, and the operator sees
one CLI either way.

The new commands are the ones that needed writing: they resolve the active run from
`.minus/context.json` so `--dir runs/<id>/terraform` stops being the most-typed argument in
the tool.

`runs` is the one name owned by both. This package handles `list` and `describe`; anything
else (`runs show`, and whatever the legacy parser accepts) falls through, so an existing
invocation keeps working.

Depends on: core/cli/context.py, core/cli/formatters.py, core/cli/commands/*,
    core/reporting/minusctl.py (the legacy implementations)
Shells out to: nothing directly -- `terraform`, the `aws` CLI and `docker` are reached only
    through the engines the commands front
Used by: the `minusctl` console script (pyproject.toml [project.scripts])
"""
import argparse
import os
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# core/ and its flat subpackages go on sys.path so the engines this CLI fronts import by
# their bare names, exactly as they do everywhere else in the repo. core/cli itself is a real
# package reached relatively, so there is one module object per file rather than two.
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers",
             "integrations"):
    _path = os.path.join(_CORE_DIR, _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)
for _path in (_CORE_DIR, os.path.dirname(_CORE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from . import theme
from .commands import console, cost, gate, iam, source, use, diagram, author, pattern, derive  # noqa: E402
from .commands import runs as runs_cmd  # noqa: E402

# Owned by this package. Everything else is delegated.
NATIVE = {
    "console": console,
    "use": use,
    "runs": runs_cmd,
    "gate": gate,
    "iam": iam,
    "cost": cost,
    "source": source,
    "diagram": diagram,
    "author": author,
    "pattern": pattern,
    "derive": derive,
}

# Handled by core/reporting/minusctl.py. Listed rather than discovered so that dropping one
# is a visible edit here instead of a silent behaviour change.
DELEGATED = (
    "create", "policy", "guard", "reports", "next", "package", "readiness", "conformance",
    "validate", "decision", "accelerator", "prove", "audit", "demo", "doctor", "adopt",
    "seed", "export", "diagnose",
)

# `runs` is native for list/describe and delegated for anything else.
_RUNS_NATIVE_ACTIONS = ("list", "describe")

# One sentence per command, because an operator choosing between `conformance` and
# `readiness` cannot do it from two words. These are what the help screen renders; each
# command's own `--help` still carries its full flag list.
COMMAND_HELP = {
    "create": "Resolve a request into a requirements-first run workspace.",
    "use": "Select the active run; gate, cost, source and prove then default to it.",
    "runs": "List every run workspace, or describe one in full.",
    "next": "Print the next safe command for a run, and any failure it is carrying.",
    "gate": "Deploy gate: verify, plan, approve, apply, status. Plan-hash bound.",
    "source": "Generated-source drift: status, diff, or anchor a new baseline.",
    "guard": "Report whether generated Terraform has been edited by hand.",
    "policy": "Inspect or promote the OPA Rego policy rules the gate enforces.",
    "decision": "Read or template the architecture decision record for a run.",
    "conformance": "Score a run against the AWS analytics reference architecture.",
    "readiness": "Score whether a run is presentable to an enterprise reviewer.",
    "audit": "Verify the tamper-evident audit chain has not been altered.",
    "iam": "IAM checks: probe whether an MFA trust-policy condition works for you.",
    "cost": "AWS BCM Pricing Calculator: prepare a profile, then estimate.",
    "prove": "Prove the pipeline end to end; --execute runs the live 5-hop harness.",
    "seed": "The older 3-hop data proof. Prefer `prove --execute`.",
    "diagnose": "Explain a failure: evidence, root cause, options, next command.",
    "validate": "Offline `terraform validate` for a run, credential-free.",
    "export": "Package a run into a domain repository with its CI/CD workflow.",
    "package": "Write the enterprise handoff bundle for a run.",
    "accelerator": "Scaffold reviewable accelerator artifacts for a run.",
    "demo": "Generate a no-cloud demo run and report, without Terraform or AWS.",
    "doctor": "Diagnose the local environment: tools, credentials, container runtime.",
    "adopt": "Inventory existing Terraform and scan it before adopting it.",
    "reports": "Explore plan reports: services, resources, IAM roles, diffs.",
    "console": "Serve the visual governance console: topology, lineage, trace, vault.",
    "diagram": "Generate Draw.io architecture diagrams from Terraform plan.",
    "author": "Submit agent-authored HCL for novel resource composition.",
    "pattern": "Approved architecture-pattern registry: list, match, capture.",
    "derive": "Evaluate what stated architectural facts imply for sizing and engine.",
}

# Grouped by where a command sits in the lifecycle, so the screen reads as a workflow rather
# than an alphabetical dump. Every command belongs to exactly one group; a test enforces that
# the grouping and `known_commands()` stay in step, so adding a command without placing it
# fails rather than silently vanishing from the help.
COMMAND_GROUPS = (
    ("Workspace and lifecycle", ("create", "use", "runs", "next", "console", "derive")),
    ("Deploy gate and governance",
     ("gate", "source", "guard", "policy", "decision", "conformance", "readiness", "audit")),
    ("Cost and verification", ("cost", "prove", "seed", "diagnose", "validate")),
    ("Delivery and handoff", ("export", "package", "accelerator", "demo", "diagram", "author", "pattern")),
    ("Environment", ("doctor", "iam", "adopt", "reports")),
)

USAGE = "minusctl <command> [options]"


def known_commands():
    """Every subcommand this CLI accepts, native and delegated."""
    return sorted(set(NATIVE) | set(DELEGATED))


def format_help(enabled=None, stream=None):
    """The grouped help screen.

    Rendered by hand rather than through argparse's formatter: argparse renders subparsers as
    one flat blob plus a `{a,b,c,...}` usage line, which across 24 commands is the wall this
    replaces. Colour is decided once here and passed down, so the style functions stay pure.
    """
    if enabled is None:
        enabled = theme.supports_color(stream)

    width = max(len(name) for name in COMMAND_HELP)
    lines = ["MinusOps control plane. Plan-bound, fail-closed, local by default.", "",
             theme.heading("Usage:", enabled), f"  {USAGE}", ""]

    for title, members in COMMAND_GROUPS:
        lines.append(theme.heading(title, enabled))
        for name in members:
            # Colour the name, pad OUTSIDE the escape. Styling the padded label paints a
            # block out to the column edge rather than highlighting the word.
            label = theme.command(name, enabled) + " " * (width - len(name))
            lines.append(f"  {label}  {COMMAND_HELP[name]}")
        lines.append("")

    lines.append(theme.heading("Options:", enabled))
    lines.append(f"  {'-h, --help'.ljust(width)}  Show this message and exit.")
    lines.append("")
    lines.append(theme.dim(
        "Run `minusctl <command> --help` for a command's own flags. Colour follows NO_COLOR "
        "and MINUS_COLOR.", enabled))
    return "\n".join(lines)


def build_parser():
    """Argparse parser for the NATIVE commands.

    Every command is also registered as a bare subparser so `minusctl <command>` is a valid
    invocation rather than a parse error. The help screen comes from `format_help()`, not from
    argparse, and delegated commands never reach `parse_args` -- `main()` hands their whole
    argv to the owning implementation first, so their real flags are parsed there.
    """
    parser = argparse.ArgumentParser(prog="minusctl", usage=USAGE, add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for module in (use, runs_cmd, gate, iam, cost, source, diagram, console, author, pattern, derive):
        module.add_parser(sub)
    for name in sorted(DELEGATED):
        sub.add_parser(name, help=COMMAND_HELP.get(name, ""), add_help=False)
    return parser


def _delegate(argv):
    """Hand off to the legacy CLI, whose parser and tests are unchanged."""
    import minusctl
    return minusctl.main(argv)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(format_help())
        return 2

    command = argv[0]
    if command in ("-h", "--help"):
        print(format_help())
        raise SystemExit(0)

    if command in DELEGATED:
        return _delegate(argv)
    if command == "runs" and (len(argv) < 2 or argv[1] not in _RUNS_NATIVE_ACTIONS):
        return _delegate(argv)
    if command not in NATIVE:
        # Unknown: let the legacy parser produce the error, so there is one list of valid
        # commands rather than two that can disagree.
        return _delegate(argv)

    args = build_parser().parse_args(argv)
    return NATIVE[args.command].run(args)


# Run as `python -m cli.main` (or via the `minusctl` console script); the relative
# imports above mean direct file execution is not a supported entry point.
if __name__ == "__main__":
    sys.exit(main())
