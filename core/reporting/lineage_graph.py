"""
Dataset-to-dataset lineage for a governed medallion pipeline.

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


# --- Plan-derived facts -------------------------------------------------------------------
#
# Everything in _NODES above is the medallion PATTERN's default: 90 days to Glacier because
# that is storage-medallion-s3's default, `ingest_date=YYYY/MM/DD` because that is the shape
# the module suggests. None of it is a claim about the stack in front of you.
#
# When a plan is supplied, the facts it actually states replace the pattern's, and every node
# says which it is carrying. A partitioning scheme no plan states stays absent rather than
# being filled in from the pattern -- an auditor reads a rendered fact as a fact.

# Zone key on a medallion bucket -> the lineage node it stands for.
_ZONE_NODE = {"raw": "bronze", "bronze": "bronze", "landing": "bronze",
              "cleaned": "silver", "clean": "silver", "silver": "silver", "stage": "silver",
              "curated": "gold", "gold": "gold", "presentation": "gold"}


def _instance_key(address):
    start = (address or "").rfind('["')
    return (address or "")[start + 2:-2] if start != -1 else ""


def _managed(plan_json):
    return [r for r in (plan_json or {}).get("resource_changes", [])
            if r.get("mode") == "managed"]


def _refs(expressions):
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "references" in node:
                found.extend(node["references"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(expressions)
    return found


def _base(address):
    cut = (address or "").rfind('["')
    return (address or "")[:cut] if cut != -1 else (address or "")


def _config_resources_deep(plan_json):
    """Every configured resource, including inside module calls.

    A composed stack keeps its resources under configuration.root_module.module_calls, so a
    lookup that reads root_module.resources alone finds nothing for exactly the stacks this
    product generates.
    """
    out = []

    def walk(module, prefix):
        for res in module.get("resources", []) or []:
            entry = dict(res)
            name = f"{res.get('type')}.{res.get('name')}"
            entry["address"] = f"{prefix}{res.get('address') or name}"
            out.append(entry)
        for call, body in (module.get("module_calls") or {}).items():
            walk((body or {}).get("module") or {}, f"{prefix}module.{call}.")

    walk((plan_json or {}).get("configuration", {}).get("root_module", {}) or {}, "")
    return out


def _module_prefix(address):
    """`module.a.module.b.aws_s3_bucket.x` -> `module.a.module.b.` (empty at the root)."""
    parts = (address or "").split(".")
    out = []
    while len(parts) > 2 and parts[0] == "module":
        out += parts[:2]
        parts = parts[2:]
    return (".".join(out) + ".") if out else ""


def _lifecycle_summary(after):
    """What a lifecycle rule actually states, transition or expiry. None when it states no day."""
    for rule in after.get("rule") or []:
        for transition in (rule or {}).get("transition") or []:
            if (transition or {}).get("days"):
                cls = (transition.get("storage_class") or "a colder class").title()
                return f"Transitions to {cls} after {transition['days']} days"
        for expiry in (rule or {}).get("expiration") or []:
            if (expiry or {}).get("days"):
                return f"Expires after {expiry['days']} days"
    return None


def plan_facts(plan_json):
    """Per lineage node, the storage facts THIS plan states. An absent key is unstated."""
    resources = _managed(plan_json)
    config = {r["address"]: r for r in _config_resources_deep(plan_json)}

    buckets, facts = {}, {}
    for res in resources:
        if res.get("type") != "aws_s3_bucket":
            continue
        # A medallion bucket is identified by its for_each zone key; the quarantine
        # bucket is a named resource, so it is identified by that name.
        node = (_ZONE_NODE.get(_instance_key(res.get("address")).lower())
                or ("quarantine" if res.get("address", "").endswith(".quarantine") else None))
        if not node:
            continue
        buckets[res.get("address")] = node
        after = (res.get("change") or {}).get("after") or {}
        entry = facts.setdefault(node, {})
        entry["label"] = f"S3 {node.title()} -- {after.get('bucket') or res.get('address')}"
        entry["facts_source"] = "plan"

    # Encryption and retention live on separate resources, each resolved through its own
    # reference back to the bucket. Those references are module-LOCAL, so they only match
    # once the referring resource's own module prefix is put back on the front.
    for res in resources:
        rtype = res.get("type")
        if rtype not in ("aws_s3_bucket_server_side_encryption_configuration",
                         "aws_s3_bucket_lifecycle_configuration"):
            continue
        address = res.get("address")
        prefix = _module_prefix(address)
        declared = config.get(_base(address), {}).get("expressions", {})
        after = (res.get("change") or {}).get("after") or {}
        # A for_each'd config resource refers to its bucket by BASE address; the two
        # share an instance key, so `...sse.zone["bronze"]` configures `...bucket.zone["bronze"]`
        # and not all three zones at once.
        key = _instance_key(address)
        suffix = f'["{key}"]' if key else ""
        targets = {buckets.get(prefix + ref + suffix) or buckets.get(prefix + ref)
                   for ref in _refs(declared)}
        targets.discard(None)
        # A module that for_each'es the buckets themselves refers to `each.value`, so no
        # reference names a bucket at all. The instance keys are the same set by
        # construction, so same module plus same key identifies it without guessing.
        if not targets and key:
            targets = {node for addr, node in buckets.items()
                       if addr.startswith(prefix) and _instance_key(addr) == key}
        for node in targets:
            entry = facts.setdefault(node, {})
            if rtype.endswith("server_side_encryption_configuration"):
                entry["encryption"] = _encryption_summary(after)
            else:
                summary = _lifecycle_summary(after)
                if summary:
                    entry["retention"] = summary
    return facts


def _encryption_summary(after):
    for rule in after.get("rule") or []:
        for default in (rule or {}).get("apply_server_side_encryption_by_default") or []:
            algorithm = (default or {}).get("sse_algorithm")
            if algorithm == "aws:kms":
                return "SSE-KMS (customer managed key)"
            if algorithm:
                return f"{algorithm} (S3 managed)"
    return "Server-side encryption configured"


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

    derived = plan_facts(plan_json) if plan_json else {}
    nodes = []
    for node_id in order:
        node = dict(id=node_id, **_NODES[node_id])
        node.setdefault("facts_source", "pattern default")
        if node_id in derived:
            # A stated fact replaces the pattern's; one the plan never states is dropped
            # rather than left showing the pattern's value under a "plan" label.
            for key in ("partitioning", "table_format", "retention", "encryption"):
                node.pop(key, None)
            node.update(derived[node_id])
        nodes.append(node)
    edges = [dict(**{"from": src, "to": dst}, label=label, branch=branch)
             for src, dst, label, branch in _EDGES if src in seen and dst in seen]

    return {"nodes": nodes, "edges": edges, "masking": _masking(decision, modules)}


def _modules_from_plan(plan_json):
    """Module ids referenced by a plan's resource addresses (`module.<id>....`).

    A Terraform address segment cannot contain a hyphen, so `storage-medallion-s3` appears as
    `module.storage_medallion_s3`. Returning the address form matched nothing in _REQUIRES,
    which is keyed by catalog id -- so the plan-only path silently produced an empty graph
    and only a decision record ever populated one.
    """
    found = set()
    for change in (plan_json or {}).get("resource_changes", []) or []:
        address = str(change.get("address", ""))
        if address.startswith("module."):
            segment = address.split(".")[1]
            found.add(segment)
            found.add(segment.replace("_", "-"))
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
