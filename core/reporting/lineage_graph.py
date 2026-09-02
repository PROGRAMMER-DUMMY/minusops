"""
Dataset-to-dataset lineage for a governed medallion pipeline.

The architecture diagram (`drawio_generator.py`) answers "what resources exist and how are
they wired". This answers a different question: "where does a record go, and what happens
to it on the way".
"""
import json
import os
import re

# Which module has to be present for a hop to be real.
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

# The medallion path, in the order a record travels it.
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

_DEFAULT_PII = (
    dict(column="ssn", unmasked_for=["billing", "compliance"],
         masked_for=["analytics", "data_science"], masked_example="***-**-1234"),
    dict(column="email", unmasked_for=["billing"],
         masked_for=["analytics", "data_science"], masked_example="****@****.com"),
    dict(column="card_number", unmasked_for=["billing"],
         masked_for=["analytics", "data_science"], masked_example="****-****-****-4242"),
)

_LAKE_FORMATION = "governance-lakeformation"

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
    parts = (address or "").split(".")
    out = []
    while len(parts) > 2 and parts[0] == "module":
        out += parts[:2]
        parts = parts[2:]
    return (".".join(out) + ".") if out else ""


def _lifecycle_summary(after):
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
    resources = _managed(plan_json)
    config = {r["address"]: r for r in _config_resources_deep(plan_json)}

    buckets, facts = {}, {}
    for res in resources:
        if res.get("type") != "aws_s3_bucket":
            continue
        # The for_each/count key only. Falling back to "is one of these words anywhere in the
        # address" classified any bucket whose name happens to contain bronze, silver or gold
        # -- `aws_s3_bucket.gold_partner_logos` became the Gold zone of a data lake. A zone is
        # something the plan declares, not something a name hints at.
        key_name = _instance_key(res.get("address")).lower()
        node = (_ZONE_NODE.get(key_name)
                or ("quarantine" if res.get("address", "").endswith(".quarantine") else None))
        if not node:
            continue
        buckets[res.get("address")] = node
        after = (res.get("change") or {}).get("after") or {}
        entry = facts.setdefault(node, {})
        entry["label"] = f"S3 {node.title()} -- {after.get('bucket') or res.get('address')}"
        entry["facts_source"] = "plan"

    for res in resources:
        rtype = res.get("type")
        if rtype not in ("aws_s3_bucket_server_side_encryption_configuration",
                         "aws_s3_bucket_lifecycle_configuration"):
            continue
        address = res.get("address")
        prefix = _module_prefix(address)
        declared = config.get(_base(address), {}).get("expressions", {})
        after = (res.get("change") or {}).get("after") or {}
        key = _instance_key(address)
        suffix = f'["{key}"]' if key else ""
        targets = {buckets.get(prefix + ref + suffix) or buckets.get(prefix + ref)
                   for ref in _refs(declared)}
        targets.discard(None)
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
    """The dataset flow for one run."""
    modules = _selected(decision)
    if plan_json:
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
            for key in ("partitioning", "table_format", "retention", "encryption"):
                node.pop(key, None)
            node.update(derived[node_id])
        nodes.append(node)

    edges = [dict(**{"from": src, "to": dst}, label=label, branch=branch)
             for src, dst, label, branch in _EDGES if src in seen and dst in seen]

    # No synthesised silver -> gold hop. One was appended here whenever both zones existed and
    # no quality gate did, labelled "[4] Curate" -- a transform nothing in the plan declares.
    # _EDGES above is the whole set, and every member of it is gated on both endpoints being
    # present, which is what keeps an edge from pointing at a node that was never drawn.
    return {"nodes": nodes, "edges": edges, "masking": _masking(decision, modules)}


def _modules_from_plan(plan_json):
    """Module ids referenced by a plan's resource addresses or inferred from managed resource types."""
    found = set()
    changes = (plan_json or {}).get("resource_changes", []) or []
    for change in changes:
        address = str(change.get("address", ""))
        if address.startswith("module."):
            segment = address.split(".")[1]
            found.add(segment)
            found.add(segment.replace("_", "-"))

    # A stack composed flat has no `module.` prefix to read, so capability is inferred from the
    # resource types the plan declares. Each mapping below has to be one the resource type
    # actually establishes -- the type is a declared fact, unlike a name.
    #
    # `aws_s3_bucket -> storage-medallion-s3` was not one of those. Any bucket, for any purpose,
    # claimed the medallion module, and all three zone nodes hang off it in _REQUIRES: a single
    # bucket named company-logos rendered as bronze -> silver -> gold. The medallion zones are
    # claimed only when the plan declares medallion ZONE KEYS, which is the same declared
    # for_each key plan_facts() reads.
    #
    # `aws_sfn_state_machine -> metadata-control-table` was not one either. A state machine is
    # orchestration; it is not a control table, and asserting one from the other put an ingress
    # hop on the page for a stack that has none. Kinesis maps to speed-layer-kinesis, which is
    # what it actually is and is already an ingress requirement.
    for change in changes:
        rtype = change.get("type", "")
        if rtype == "aws_s3_bucket":
            if _ZONE_NODE.get(_instance_key(change.get("address")).lower()):
                found.add("storage-medallion-s3")
        elif rtype in ("aws_glue_job", "aws_emrserverless_application", "aws_glue_crawler"):
            found.add("compute-glue-etl")
        elif "lakeformation" in rtype:
            found.add("governance-lakeformation")
        elif rtype in ("aws_athena_workgroup", "aws_redshiftserverless_workgroup"):
            found.add("query-athena")
        elif rtype == "aws_kinesis_stream":
            found.add("speed-layer-kinesis")

    return found


def _masking(decision, modules):
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
    for node in (graph or {}).get("nodes", []):
        if node.get("id") == node_id:
            return node
    return None


def as_markdown(graph):
    lines = ["| Hop | From | To | Branch |", "| :--- | :--- | :--- | :--- |"]
    labels = {n["id"]: n["label"] for n in (graph or {}).get("nodes", [])}
    for edge in (graph or {}).get("edges", []):
        lines.append(f"| {edge['label']} | {labels.get(edge['from'], edge['from'])} "
                     f"| {labels.get(edge['to'], edge['to'])} | {edge.get('branch') or '-'} |")
    if len(lines) == 2:
        lines.append("| - | no lineage: this stack provisions no medallion hops | - | - |")
    return "\n".join(lines)
