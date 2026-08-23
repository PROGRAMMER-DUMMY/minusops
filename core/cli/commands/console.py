"""
`minusctl console` -- serve the visual governance console (PRD v13, WP-05).

Thin by design, exactly like `gate.py`: the console decides nothing, so a wrapper that grew
behaviour would be a second place governance rules could live. This resolves the run through
the same fail-closed context hierarchy every other command uses and hands off.

Aliases `ui` and `dashboard` exist because `dashboard` is what operators typed for a year;
pointing it at the console is kinder than an unknown-command error, and it is how the
deprecation actually reaches people.

Depends on: core/cli/context.py, app/console_app.py
Shells out to: nothing
Used by: core/cli/main.py
"""
import os
import sys

from .. import context

ALIASES = ("ui", "dashboard")


def add_parser(subparsers):
    parser = subparsers.add_parser(
        "console", help="Serve the visual governance console for a run.")
    parser.add_argument("--run", help="Run workspace to open; defaults to the active run")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default: loopback)")
    parser.add_argument("--port", type=int, default=8050)
    return parser


def run(args):
    """Resolve the run, then serve. Refuses rather than guessing which run to show."""
    try:
        record = context.resolve_run(getattr(args, "run", None))
    except context.ContextError as err:
        sys.stderr.write(f"Error: {err}\n")
        return 1

    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from app import console_app

    print(f"[console] run: {record.get('run_id')}")
    return console_app.main(["--host", args.host, "--port", str(args.port)])
