"""
`minusctl runs list` and `minusctl runs describe` -- the two questions an operator asks about
generated workspaces: what exists, and what is this one (PRD v6 FR-02/FR-03).

`list` marks the active run `[*]` and everything else `[ ]`, so the marker reads as a column
rather than as a rendering bug, and filters on the three axes a platform team actually slices
by: domain, lifecycle tier, orchestrator.

`describe` renders the specification card by pulling each fact from its CANONICAL source --
run.json for identity, requirements.json and architecture_decision.json for the architecture,
bcm/ for spend, terraform/outputs.json for live endpoints. Nothing is reconstructed: a bucket
name rebuilt from a name_prefix is the guess that sends an operator to the wrong bucket, which
is the same reasoning seed.py's read_outputs() carries.

An absent fact renders as `-` and an absent cost renders as `unpriced`. Neither is ever `0`
or `$0.00`; see formatters.money().

Depends on: core/cli/context.py, core/cli/formatters.py, core/reporting/runs.py
Shells out to: nothing
Used by: core/cli/main.py
"""
import json
import os

from .. import context as cli_context
from .. import formatters
import runs
import serving

TIERS = ("dev", "test", "uat", "prod")

# Card label -> the keys that may hold it, in priority order. The PRD names attributes
# (`table_format`, `serving_layer`) that no schema declares under those exact names, so each
# label also lists the requirements.json field that carries the same fact. First hit wins;
# nothing is inferred from a module list, because "we generated compute-glue-etl" is not the
# same claim as "the compute engine is Glue 4.0 with 10 G.1X workers".
ARCHITECTURE_FIELDS = (
    ("Ingestion Source", ("ingestion_source", "sources")),
    ("Storage Format", ("storage_format", "storage_zones")),
    ("Table Format", ("table_format", "catalog")),
    ("Compute Engine", ("compute_engine", "transforms")),
    ("Orchestration", ("orchestration",)),
    ("Data Quality", ("data_quality",)),
    ("Serving Layer", ("serving_layer", "consumption")),
)

# Terraform output name -> card label. Only endpoints an operator would paste somewhere.
ENDPOINT_FIELDS = (
    ("region", "Region"),
    ("bronze_bucket", "Bronze Storage"),
    ("silver_bucket", "Silver Storage"),
    ("gold_bucket", "Gold Storage"),
    ("quarantine_bucket", "Quarantine Storage"),
    ("dag_path", "Airflow DAG Path"),
)


def add_parser(sub):
    parser = sub.add_parser("runs", help="list or describe run workspaces")
    action = parser.add_subparsers(dest="runs_action", required=True)
    listing = action.add_parser("list", help="table of every run workspace")
    listing.add_argument("--domain", help="only runs owned by this data domain")
    listing.add_argument("--tier", choices=TIERS, help="only runs declared at this tier")
    listing.add_argument("--orchestrator", help="e.g. mwaa, stepfunctions")
    listing.add_argument("--json", action="store_true")
    describe = action.add_parser("describe", help="full specification card for one run")
    describe.add_argument("run_id", nargs="?", help="defaults to the active run")
    describe.add_argument("--json", action="store_true")
    return parser


def run(args):
    if args.runs_action == "list":
        return _list(args)
    return _describe(args)


# --- list -----------------------------------------------------------------------------

def _matches(item, args):
    """Every declared filter must match. An undeclared field never matches a filter on it:
    silence is not a wildcard, and including an unclassified run in a `--tier prod` listing
    is how one ends up treated as production."""
    for flag, field in (("domain", "domain"), ("tier", "tier"),
                        ("orchestrator", "orchestrator")):
        wanted = getattr(args, flag, None)
        if wanted and str(item.get(field) or "").lower() != wanted.lower():
            return False
    return True


def _list(args):
    records = [item for item in runs.list_runs() if _matches(item, args)]
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
        filtered = any(getattr(args, flag, None) for flag in ("domain", "tier", "orchestrator"))
        # "no runs match" and "there are no runs" are very different statements to read at
        # the end of a filtered command.
        print("no runs match that filter" if filtered else
              "no runs yet -- `minusctl create \"<request>\" --name <workload>` makes one")
        return 0

    # Cost comes from the same BCM evidence `describe` reads. Two views of one run
    # disagreeing about spend is worse than neither showing it -- the reader believes
    # whichever they saw last.
    rows = [["[*]" if item.get("run_id") == active else "[ ]",
             item.get("run_id"),
             item.get("domain"),
             item.get("compute_engine"),
             item.get("orchestrator"),
             formatters.money(_spend(item)),
             item.get("governance_status") or "GENERATED"] for item in records]
    print(formatters.table(
        ["Active", "Run Name", "Domain", "Engine", "Orchestrator", "Cost/Mo", "Status"], rows))
    return 0


# --- describe -------------------------------------------------------------------------

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _first(sources, keys):
    """First value found for any of `keys` across `sources`, in order."""
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = source.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def _spend(item):
    """A run's monthly spend: BCM evidence first, then whatever the registry recorded."""
    evidence = _bcm_spend(item.get("root") or "")
    if evidence is not None:
        return evidence
    recorded = item.get("estimated_monthly_cost")
    return recorded if isinstance(recorded, (int, float)) else None


def _bcm_spend(root):
    """Spend from BCM evidence only. No evidence, no number -- the same refusal
    core/cost/budget_calculator.py exists to make."""
    document = _read_json(os.path.join(root, "bcm", "estimated_monthly_spend.json"))
    if not isinstance(document, dict):
        return None
    for key in ("estimated_monthly_cost", "estimated_monthly_spend", "total_usd"):
        if isinstance(document.get(key), (int, float)):
            return document[key]
    return None


def _artifact(root, relative):
    """A workspace-relative path, annotated when the file is not there.

    Relative because an absolute Windows path is 120 characters that wrap in every terminal
    and cannot be pasted anywhere repo-relative. Annotated because a path printed for a file
    that does not exist sends the reader to an empty directory and makes them doubt the tool
    rather than the run."""
    path = os.path.join(root, *relative.split("/"))
    exists = os.path.exists(path)
    try:
        shown = os.path.relpath(path, runs.WORKSPACE)
    except ValueError:
        # Different drive on Windows -- relpath refuses, and absolute is still correct.
        shown = path
    return shown if exists else f"{shown}  (missing)"


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
    pipeline = requirements.get("data_pipeline") or {}
    spend = _spend(record)

    if args.json:
        print(json.dumps({"run": record, "requirements": requirements,
                          "decision": decision, "outputs": outputs,
                          "estimated_monthly_cost": spend}, indent=2))
        return 0

    # Order matters. FR-03 names architecture_decision.json and requirements.json as the
    # canonical sources, so they win. run.json trails as a last resort: its `compute_engine`
    # is the short label the list table shows ("Glue 4.0"), not the architecture statement
    # the decision record carries ("AWS Glue 4.0, PySpark, 10x G.1X"). Reading it first would
    # let the summary silently outrank the decision it summarises.
    sources = (decision, pipeline, requirements, record)
    architecture = [(label, _first(sources, keys)) for label, keys in ARCHITECTURE_FIELDS]

    endpoints = [("Estimated Spend", formatters.money(spend)),
                 ("Spend Evidence",
                  "AWS BCM Pricing Calculator" if spend is not None
                  else "none -- run `minusctl cost estimate`")]
    for key, label in ENDPOINT_FIELDS:
        value = outputs.get(key)
        if value:
            endpoints.append((label, value if key == "region" else f"s3://{value}"))

    # Only what the stack actually provisioned (PRD v9 s3). An endpoint for infrastructure
    # that does not exist fails at connect time and the analyst blames the tool.
    served = serving.endpoints(outputs, modules=decision.get("selected_modules") or [])
    sections = [
        ("Metadata", [
            ("Domain", record.get("domain")),
            ("Workload", record.get("name") or record.get("blueprint")),
            ("Owner", record.get("owner")),
            ("Created At", record.get("created_at")),
            ("Lifecycle Tier", record.get("tier")),
            ("Governance Status", record.get("governance_status") or "GENERATED"),
            ("Request", record.get("request")),
        ]),
        ("Architecture Attributes", architecture),
        ("FinOps & Resource Endpoints", endpoints),
        ("Artifact Paths", [
            ("Terraform HCL", _artifact(root, "terraform/main.tf")),
            ("Proving Report", _artifact(root, "reports/proving_report.json")),
            ("Decision Record", _artifact(root, "architecture_decision.json")),
            ("Requirements", _artifact(root, "requirements.json")),
        ]),
    ]
    if served:
        # Inserted before Artifact Paths: an analyst wants the address, not the file layout.
        sections.insert(-1, ("Serving Endpoints & Consumption",
                             [(e["label"], e["connection"]) for e in served]))
    print(formatters.card(f"PIPELINE SPECIFICATION: {record['run_id']}", sections))
    return 0
