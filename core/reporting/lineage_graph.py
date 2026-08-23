"""
Dataset-to-dataset lineage for a governed medallion pipeline (PRD v13 FR-03).

The architecture diagram (`drawio_generator.py`) answers "what resources exist and how are
they wired". This answers a different question: "where does a record go, and what happens
to it on the way". Those are not the same picture. A Glue job and an S3 bucket are one edge
on the topology and three hops in the lineage -- read, transform, land -- and it is the
lineage an auditor asks for when they want to know whether a bad record can reach Gold.

THE RULE THIS FILE EXISTS TO ENFORCE: the graph describes THIS stack, never the medallion
pattern in general. Bronze -> Silver -> Gold is what the pattern looks like; drawing it for
a run that has no data-quality module would put a quality gate and a quarantine branch on
the page for controls that do not exist. An auditor reads a rendered control as a control.
So a node is emitted only when the stack provisions the thing it stands for, which is the
same doctrine `serving.py` applies to connection endpoints.

Masking is the sharpest case. `masking.enforced` is False unless Lake Formation actually
governs the stack, because "this column is masked" is a compliance claim, and the only
thing that makes it true is a service enforcing it.

Depends on: nothing (standard library only -- PRD v13 invariant 4)
Shells out to: nothing
Used by: app/console_app.py (View 2), tests/test_lineage_graph.py
"""

# Which module has to be present for a hop to be real. A hop whose module is absent is not
# drawn -- see the module docstring on why that matters more here than in a topology view.
_REQUIRES = {
    "ingress":      ("ingestion-webhook", "ingestion-sftp", "speed-layer-kinesis",
                     "ingestion-appflow", "metadata-control-table"),
    "bronze":       ("storage-medallion-s3",),
    "transform":    ("compute-glue-etl", "compute-emr-serverless", "databricks-workspace"),
    "silver":       ("storage-medallion-s3",),
    "quality_gate": ("dq-great-expectations",),
    "quarantine":   ("dq-great-expectations",),
    "gold":         ("storage-medallion-s3",),
    "serving":      ("query-athena", "consumption-redshift-serverless", "dbt-semantic-layer",
                     "cube-semantic-layer"),
}

# The medallion path, in the order a record travels it. `quality_gate` forks: clean records
# continue to Gold, rejects divert to quarantine and travel no further.
_SPINE = ("ingress", "bronze", "transform", "silver", "quality_gate", "gold", "serving")

_NODES = {
    "ingress": dict(
        label="Ingress sources", layer="ingress", kind="source",
        detail="API Gateway, Kinesis, AppFlow, SFTP"),
    "bronze": dict(
        label="S3 Bronze landing", layer="bronze", kind="dataset",
        table_format="Raw JSON / CSV", partitioning="ingest_date=YYYY/MM/DD",
        retention="Glacier Instant Retrieval after 90 days", encryption="SSE-KMS (CMK)"),
    "transform": dict(
        label="PySpark transformation", layer="transform", kind="compute",
        detail="AWS Glue 4.0 / EMR Serverless"),
    "silver": dict(
        label="S3 Silver stage", layer="silver", kind="dataset",
        table_format="Parquet (cleaned, conformed)", partitioning="event_date=YYYY/MM/DD",
        retention="Glacier Instant Retrieval after 90 days", encryption="SSE-KMS (CMK)"),
    "quality_gate": dict(
        label="Data quality gate", layer="quality", kind="gate",
        detail="Great Expectations contracts"),
    "quarantine": dict(
        label="Quarantine isolation", layer="quality", kind="dataset",
        table_format="Raw rejected records", partitioning="rejected_date=YYYY/MM/DD",
        retention="Retained for investigation; no lifecycle expiry",
        encryption="SSE-KMS (CMK)"),
    "gold": dict(
        label="S3 Gold curated lakehouse", layer="gold", kind="dataset",
        table_format="Apache Iceberg v2 (ACID)", partitioning="event_date=YYYY/MM/DD",
        retention="Current snapshot retained; expired snapshots vacuumed",
        encryption="SSE-KMS (CMK)"),
    "serving": dict(
        label="Serving consumption", layer="serving", kind="serving",
        detail="Athena workgroup, Redshift Serverless, dbt MetricFlow"),
}

_EDGES = (
    ("ingress", "bronze", "[1] Ingest", None),
    ("bronze", "transform", "[2] Read raw", None),
    ("transform", "silver", "[3] Conform", None),
    ("silver", "quality_gate", "[4] Assert contracts", None),
    ("quality_gate", "gold", "[5] Promote clean", "accept"),
    ("quality_gate", "quarantine", "[5] Divert rejected", "reject"),
    ("gold", "serving", "[6] Serve", None),
)

# A starting point for a Lake-Formation-governed stack, NOT a claim about which columns a
# particular pipeline holds. A run that declares `pii_columns` replaces these outright.
_DEFAULT_PII = (
    dict(column="ssn", unmasked_for=["billing", "compliance"],
         masked_for=["analytics", "data_science"], masked_example="***-**-1234"),
    dict(column="email", unmasked_for=["billing"],
         masked_for=["analytics", "data_science"], masked_example="****@****.com"),
    dict(column="card_number", unmasked_for=["billing"],
         masked_for=["analytics", "data_science"], masked_example="****-****-****-4242"),
)

_LAKE_FORMATION = "governance-lakeformation"


def _selected(decision):
    decision = decision or {}
    modules = decision.get("selected_modules") or decision.get("modules") or []
    return {str(m).strip() for m in modules if str(m).strip()}


def _present(node_id, modules):
    return bool(set(_REQUIRES.get(node_id, ())) & modules)


def build_lineage(decision=None, plan_json=None):
    """The dataset flow for one run.

    Returns {"nodes": [...], "edges": [...], "masking": {...}}. Nodes appear only for hops
    the stack actually provisions, and every edge is guaranteed to connect two nodes that
    are in the result -- a dangling edge renders as a line into empty space.
    """
    modules = _selected(decision)
    if plan_json:
        # A plan is stronger evidence than a decision: it is what Terraform will build.
        modules |= _modules_from_plan(plan_json)

    present = [n for n in (*_SPINE[:5], "quarantine", *_SPINE[5:]) if _present(n, modules)]
    seen, order = set(), []
    for node_id in present:
        if node_id not in seen:
            seen.add(node_id)
            order.append(node_id)

    nodes = [dict(id=node_id, **_NODES[node_id]) for node_id in order]
    edges = [dict(**{"from": src, "to": dst}, label=label, branch=branch)
             for src, dst, label, branch in _EDGES if src in seen and dst in seen]

    return {"nodes": nodes, "edges": edges, "masking": _masking(decision, modules)}


def _modules_from_plan(plan_json):
    """Module ids referenced by a plan's resource addresses (`module.<id>....`)."""
    found = set()
    for change in (plan_json or {}).get("resource_changes", []) or []:
        address = str(change.get("address", ""))
        if address.startswith("module."):
            found.add(address.split(".")[1])
    return found


def _masking(decision, modules):
    """Column-level access controls, or an explicit statement that none are enforced.

    `enforced` is False unless Lake Formation is in the stack. Reporting a masked column
    without something enforcing the mask states a control that does not exist, and this
    view is read by people whose job is to check exactly that.
    """
    if _LAKE_FORMATION not in modules:
        return {"enforced": False, "columns": [],
                "reason": "no Lake Formation module in this stack; no column-level "
                          "controls are enforced"}

    declared = (decision or {}).get("pii_columns")
    columns = [dict(c) for c in declared] if declared else [dict(c) for c in _DEFAULT_PII]
    for column in columns:
        column.setdefault("masked_example", "****")
        column.setdefault("unmasked_for", [])
        column.setdefault("masked_for", [])
    return {"enforced": True, "columns": columns,
            "reason": "Lake Formation TBAC governs column access for this stack"}


def find_node(graph, node_id):
    """The node, or None. Returns None rather than raising because the caller is usually a
    view asking "do we have a Gold table", and absence is a normal answer."""
    for node in (graph or {}).get("nodes", []):
        if node.get("id") == node_id:
            return node
    return None


def as_markdown(graph):
    """The lineage as a flow table, for PR comments and deploy reports."""
    lines = ["| Hop | From | To | Branch |", "| :--- | :--- | :--- | :--- |"]
    labels = {n["id"]: n["label"] for n in (graph or {}).get("nodes", [])}
    for edge in (graph or {}).get("edges", []):
        lines.append(f"| {edge['label']} | {labels.get(edge['from'], edge['from'])} "
                     f"| {labels.get(edge['to'], edge['to'])} | {edge.get('branch') or '-'} |")
    if len(lines) == 2:
        lines.append("| - | no lineage: this stack provisions no medallion hops | - | - |")
    return "\n".join(lines)
