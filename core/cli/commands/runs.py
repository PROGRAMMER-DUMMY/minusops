"""
`minusctl runs list` and `minusctl runs describe` -- the two questions an operator asks about
generated workspaces: what exists, and what is this one.

`list` marks the active run with `[*]` so the answer to "which one am I on" is on the same
screen as "which ones are there". `describe` renders the full specification card, reading
whatever the run has actually produced rather than restating the request.

The FinOps section reports `not priced` when no BCM evidence exists. Rendering `$0.00` there
would be the one number on the card an executive remembers, and it would be wrong.

Depends on: core/cli/context.py, core/cli/formatters.py, core/reporting/runs.py
Shells out to: nothing
Used by: core/cli/main.py
"""
import json
import os

from .. import context as cli_context
from .. import formatters
import runs


def add_parser(sub):
    parser = sub.add_parser("runs", help="list or describe run workspaces")
    action = parser.add_subparsers(dest="runs_action", required=True)
    listing = action.add_parser("list", help="table of every run workspace")
    listing.add_argument("--json", action="store_true")
    describe = action.add_parser("describe", help="full specification card for one run")
    describe.add_argument("run_id", nargs="?", help="defaults to the active run")
    describe.add_argument("--json", action="store_true")
    return parser


def run(args):
    if args.runs_action == "list":
        return _list(args)
    return _describe(args)


def _list(args):
    records = runs.list_runs()
    try:
        active = cli_context.active_run_id()
    except cli_context.ContextError:
        # A broken context must not stop the operator seeing what exists -- `runs list` is
        # exactly where they go to fix it.
        active = None
    if args.json:
        print(json.dumps(records, indent=2))
        return 0
    if not records:
        print("no runs yet -- `minusctl create \"<request>\" --name <workload>` makes one")
        return 0
    # A space, not "", so the marker column stays blank rather than filling with the `-`
    # that formatters.cell() gives an empty value. A column of dashes reads as bullets.
    rows = [["[*]" if item.get("run_id") == active else " ",
             item.get("run_id"),
             item.get("domain"),
             item.get("orchestrator"),
             item.get("owner"),
             item.get("created_at")] for item in records]
    print(formatters.table(["", "RUN", "DOMAIN", "ORCHESTRATOR", "OWNER", "CREATED"], rows))
    return 0


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _describe(args):
    try:
        record = cli_context.resolve_run(args.run_id)
    except cli_context.ContextError as exc:
        print(f"[ERR] {exc}")
        return 1

    root = record["root"]
    requirements = _read_json(os.path.join(root, "requirements.json")) or {}
    decision = _read_json(os.path.join(root, "architecture_decision.json")) or {}
    outputs = _read_json(os.path.join(root, "terraform", "outputs.json")) or {}

    if args.json:
        print(json.dumps({"run": record, "requirements": requirements,
                          "decision": decision, "outputs": outputs}, indent=2))
        return 0

    print(formatters.card(record["run_id"], [
        ("Metadata", [
            ("Run id", record.get("run_id")),
            ("Pipeline", record.get("name") or record.get("blueprint")),
            ("Domain", record.get("domain")),
            ("Orchestrator", record.get("orchestrator")),
            ("Owner", record.get("owner")),
            ("Created", record.get("created_at")),
            ("Request", record.get("request")),
        ]),
        ("Architecture", [
            ("Cloud", record.get("cloud")),
            ("Blueprint", record.get("blueprint")),
            ("Modules", decision.get("modules") or requirements.get("modules")),
            ("Decision recorded", "yes" if decision else "no"),
        ]),
        ("FinOps", [
            ("Estimated monthly", formatters.money(record.get("estimated_monthly_cost"))),
            ("Evidence", "BCM Pricing Calculator" if record.get("estimated_monthly_cost")
             is not None else "none -- run `minusctl cost estimate`"),
        ]),
        ("Resource endpoints", sorted(
            (key, value) for key, value in outputs.items()
            if not isinstance(value, (dict, list))) or [("Outputs", None)]),
        ("Artifact paths", [
            ("Terraform", record.get("terraform_dir")),
            ("Reports", record.get("reports_dir")),
            ("BCM", record.get("bcm_dir")),
            ("Root", root),
        ]),
    ]))
    return 0
