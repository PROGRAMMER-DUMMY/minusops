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

from .commands import cost, gate, source, use  # noqa: E402
from .commands import runs as runs_cmd  # noqa: E402

# Owned by this package. Everything else is delegated.
NATIVE = {
    "use": use,
    "runs": runs_cmd,
    "gate": gate,
    "cost": cost,
    "source": source,
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


def known_commands():
    """Every subcommand this CLI accepts, native and delegated."""
    return sorted(set(NATIVE) | set(DELEGATED))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="minusctl",
        description="MinusOps control plane. Plan-bound, fail-closed, local by default.",
        epilog="Delegated subcommands: " + ", ".join(sorted(DELEGATED))
               + ". Run `minusctl <command> --help` for any of them.")
    sub = parser.add_subparsers(dest="command", required=True)
    for module in (use, runs_cmd, gate, cost, source):
        module.add_parser(sub)
    return parser


def _delegate(argv):
    """Hand off to the legacy CLI, whose parser and tests are unchanged."""
    import minusctl
    return minusctl.main(argv)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        build_parser().print_help()
        return 2

    command = argv[0]
    if command in ("-h", "--help"):
        build_parser().print_help()
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
