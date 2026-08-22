"""
`minusctl source {status|diff|anchor}` -- has the generated Terraform been edited by hand?

Generated HCL that someone hand-edited is no longer what the architecture decision records,
and the next regeneration silently discards the edit. `status` answers whether that has
happened, `diff` shows what changed, and `anchor` accepts the current state as the new
baseline.

`anchor` is the only one that writes, and it is opt-in for that reason: anchoring a drifted
directory is how an unreviewed manual edit becomes the official baseline.

Depends on: core/cli/context.py, core/governance/source_guard.py
Shells out to: nothing
Used by: core/cli/main.py
"""
import json

from .. import context as cli_context
from .. import formatters


def add_parser(sub):
    parser = sub.add_parser("source", help="generated-source drift: status, diff, anchor")
    parser.add_argument("action", choices=["status", "diff", "anchor"])
    parser.add_argument("--run", help="run id (defaults to the active run)")
    parser.add_argument("--dir", help="Terraform directory (defaults to the active run)")
    parser.add_argument("--json", action="store_true")
    return parser


def _status(tf_dir):
    import source_guard
    return source_guard.status(tf_dir)


def _diff(tf_dir):
    import source_guard
    return source_guard.diff(tf_dir)


def _anchor(tf_dir):
    import source_guard
    return source_guard.write_baseline(tf_dir, label="anchored")


def run(args):
    tf_dir = args.dir
    if not tf_dir:
        try:
            tf_dir = cli_context.resolve_run(args.run)["terraform_dir"]
        except cli_context.ContextError as exc:
            print(f"[ERR] {exc}")
            return 1

    if args.action == "status":
        result = _status(tf_dir)
    elif args.action == "diff":
        result = _diff(tf_dir)
    else:
        result = _anchor(tf_dir)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0
    print(_format(args.action, result))
    return 0 if _ok(args.action, result) else 1


def _ok(action, result):
    if action == "anchor":
        return True
    return str((result or {}).get("status", "")).lower() in ("clean", "ok", "match", "")


def _format(action, result):
    if action == "anchor":
        return f"baseline anchored: {formatters.cell((result or {}).get('path'))}"
    status = (result or {}).get("status", "unknown")
    lines = [f"source: {status}"]
    for key in ("modified", "added", "removed"):
        entries = (result or {}).get(key) or []
        for entry in entries:
            lines.append(f"  {key[:3].upper()} {formatters.cell(entry)}")
    return "\n".join(lines)
