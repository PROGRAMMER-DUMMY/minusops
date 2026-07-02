"""
Data-pipeline reference model + conformance / gap analysis.

This is the shared "brain" for the data-pipeline specialization: it classifies any
Terraform resource into one of the canonical analytics layers and then scores a plan
against (a) the AWS serverless data-analytics *reference architecture* (layer coverage
and wiring) and (b) the AWS Well-Architected *Data Analytics Lens* best practices.

Design rules (deliberate):
  * Generic and cloud-agnostic. Classification is keyword rules with an honest
    fallback — an unknown resource type (any cloud) never raises; it lands in the
    "other" role instead of breaking the report. Adding coverage = adding a keyword.
  * Deterministic and plan-derived. No prices, no LLM, no fabricated relationships.
    "Unwired" is only reported when the plan's own references confirm the absence of
    a dependency (from `configuration.module_calls`), never guessed.

Grounding (see docs / memory `aws-reference-architectures-for-design`):
  * Reference architecture — https://aws.amazon.com/blogs/big-data/aws-serverless-data-analytics-pipeline-reference-architecture/
  * Well-Architected Data Analytics Lens — https://docs.aws.amazon.com/wellarchitected/latest/analytics-lens/
"""
import json
import os
import re
import sys

# Canonical layers of the analytics reference architecture, in flow order.
CANONICAL_LAYERS = ["ingestion", "storage", "catalog", "processing", "consumption", "governance"]

# A finer-grained role per resource; each maps to exactly one canonical layer.
ROLE_LAYER = {
    "ingest": "ingestion",
    "stage": "storage", "store_other": "storage",
    "catalog": "catalog",
    "transform": "processing", "orchestrate": "processing",
    "consume": "consumption",
    "security": "governance", "observability": "governance",
    "other": "other",
}

# Ordered keyword rules (first match wins) on the provider-stripped, lower-cased type.
# Order matters: more specific roles (catalog, ingest, orchestrate) precede the
# generic "store" so e.g. aws_glue_catalog_database is catalog, not storage.
_RULES = [
    ("catalog", ["glue_catalog", "glue_crawler", "glue_registry", "lakeformation", "lake_formation",
                 "data_catalog", "datacatalog", "dataplex", "purview", "schema_registry"]),
    ("ingest", ["dms_", "database_migration", "datasync", "transfer_", "appflow", "firehose",
                "kinesis_stream", "kinesis_video", "msk", "kafka", "data_exchange", "dataexchange",
                "eventbridge_pipe", "sftp", "event_source_mapping",
                # Azure / GCP streaming + ingestion
                "eventhub", "event_hub", "iothub", "pubsub", "data_factory", "datastream"]),
    ("orchestrate", ["sfn", "step_functions", "state_machine", "mwaa", "airflow", "composer",
                     "scheduler", "managed_workflows", "data_pipeline", "glue_trigger", "glue_workflow",
                     "logic_app", "workflow"]),
    ("transform", ["glue_job", "lambda", "emr", "batch_job", "batch_compute", "ecs_task", "dataproc",
                   "dataflow", "databricks", "spark", "databrew", "kinesis_analytics", "processing",
                   # Azure / GCP compute + transform
                   "hdinsight", "synapse_spark", "function_app", "cloud_function", "cloud_run"]),
    ("consume", ["athena", "redshift", "quicksight", "sagemaker", "opensearch", "elasticsearch",
                 "bigquery", "synapse", "looker", "power_bi", "workgroup",
                 # Azure / GCP warehouse + BI + ML
                 "kusto", "data_explorer", "vertex", "ml_"]),
    ("observability", ["cloudwatch", "cloudtrail", "budgets", "sns", "log_group", "logs_",
                       "metric_alarm", "anomaly", "monitor", "log_analytics", "logging_metric",
                       "notification_channel", "consumption_budget"]),
    ("security", ["iam", "kms", "secrets", "secret", "security_group", "_vpc", "vpc_",
                  "subnet", "key_vault", "acm", "waf", "guardduty", "macie", "network_acl",
                  "role_assignment", "service_account", "crypto_key"]),
    ("store", ["s3", "bucket", "storage_account", "gcs", "dynamodb", "rds", "efs", "fsx",
               "lake", "blob", "filesystem", "table_bucket",
               # Azure / GCP storage + operational DBs
               "cosmosdb", "cosmos_db", "spanner", "bigtable", "data_lake_storage", "sql_database"]),
]

# Medallion / zone ordering for the storage spine (generic keywords).
_STAGE_RANK = {"landing": 0, "raw": 1, "bronze": 1, "clean": 2, "cleaned": 2, "staged": 2,
               "stage": 2, "silver": 2, "curated": 3, "presentation": 3, "gold": 3, "serving": 4}
_PROVIDER_PREFIXES = ("aws_", "azurerm_", "azuread_", "azapi_", "google_", "google-beta_",
                      "oci_", "alicloud_", "ibm_", "yandex_")


def _strip_provider(rtype):
    t = (rtype or "").lower()
    for p in _PROVIDER_PREFIXES:
        if t.startswith(p):
            return t[len(p):]
    return t


def classify_role(rtype, instance_key="", name=""):
    """Return the fine-grained role for a resource type (with 'store' split into stage/other)."""
    t = _strip_provider(rtype)
    role = "other"
    for candidate, needles in _RULES:
        if any(n in t for n in needles):
            role = candidate
            break
    if role == "store":
        key = (instance_key or name or "").lower()
        role = "stage" if key in _STAGE_RANK else "store_other"
    return role


def layer_of(role):
    return ROLE_LAYER.get(role, "other")


def stage_rank(instance_key="", name=""):
    return _STAGE_RANK.get((instance_key or name or "").lower(), 40)


def _instance_key(address):
    m = re.search(r'\["([^"]+)"\]', address or "")
    return m.group(1) if m else ""


def extract_resources(plan):
    """Flatten a `terraform show -json` plan into classified resource dicts (managed only)."""
    out = []
    for rc in (plan or {}).get("resource_changes", []):
        rtype = rc.get("type", "")
        if rc.get("mode") == "data":
            continue
        addr = rc.get("address", rtype)
        ikey = _instance_key(addr)
        role = classify_role(rtype, ikey, rc.get("name", ""))
        out.append({
            "address": addr,
            "type": rtype,
            "name": rc.get("name", ""),
            "module": rc.get("module_address", ""),
            "instance_key": ikey,
            "role": role,
            "layer": layer_of(role),
        })
    out.sort(key=lambda r: r["address"])
    return out


def _refs(expr):
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "references" in node:
                found.extend(node["references"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(expr)
    return found


def module_dependencies(plan):
    """Map each module name -> set of module names it references (from input expressions).

    Used to answer 'is orchestration actually wired to a processing job?' from the
    plan's real references — never guessed.
    """
    calls = (plan or {}).get("configuration", {}).get("root_module", {}).get("module_calls", {})
    deps = {}
    for mname, call in calls.items():
        seen = set()
        for _, expr in (call.get("expressions") or {}).items():
            for ref in _refs(expr):
                if ref.startswith("module."):
                    dep = ref.split(".")[1].split("[")[0]
                    if dep != mname:
                        seen.add(dep)
        deps[mname] = seen
    return deps


def layer_coverage(resources):
    """Return {layer: [resources]} for the canonical layers plus 'other'."""
    cov = {layer: [] for layer in CANONICAL_LAYERS + ["other"]}
    for r in resources:
        cov.setdefault(r["layer"], []).append(r)
    return cov


# --- Scale tiers (researched thresholds recorded in docs/project_plan.md) ----
def volume_tier(daily_gb):
    """gb < 1 TB/day; tb 1–50 TB/day; pb > 50 TB/day. None when volume is undeclared —
    tier checks then stay silent rather than guessing a scale."""
    try:
        daily_gb = float(daily_gb or 0)
    except (TypeError, ValueError):
        return None
    if daily_gb <= 0:
        return None
    if daily_gb < 1024:
        return "gb"
    if daily_gb <= 51200:
        return "tb"
    return "pb"


# --- Well-Architected + reference-architecture conformance checks -----------
_SEV_WEIGHT = {"HIGH": 12, "MEDIUM": 6, "LOW": 2, "INFO": 0}

# Per-layer severity when a layer is entirely absent. Ingestion/consumption are
# commonly out of a single pipeline's scope, so their absence is informational.
_MISSING_SEVERITY = {
    "ingestion": "INFO", "storage": "HIGH", "catalog": "MEDIUM",
    "processing": "MEDIUM", "consumption": "LOW", "governance": "HIGH",
}


def _finding(fid, severity, title, detail, ref):
    return {"id": fid, "category": "ARCHITECTURE", "severity": severity,
            "title": title, "detail": detail, "reference": ref}


def conformance(plan, daily_data_gb=None):
    """Score a plan against the reference architecture + Well-Architected Analytics Lens.

    Returns a deterministic report: layer coverage, findings (each tied to a WA/ref
    principle), a 0-100 score, and a status. Everything is derived from the plan.
    When the run declares a daily volume, tier-conditional checks apply on top —
    what is hygiene at GB/day is an incident at TB/day (thresholds cited per finding).
    """
    resources = extract_resources(plan)
    cov = layer_coverage(resources)
    deps = module_dependencies(plan)
    types = {r["type"] for r in resources}

    def has(*needles):
        return any(any(n in t for n in needles) for t in types)

    findings = []

    # 1) Reference-architecture layer coverage (which of the 6 layers are present).
    for layer in CANONICAL_LAYERS:
        if not cov.get(layer):
            findings.append(_finding(
                f"ARCH-LAYER-{layer.upper()}", _MISSING_SEVERITY[layer],
                f"No {layer} layer detected",
                f"The plan has no resources classified into the {layer} layer of the analytics "
                "reference architecture.",
                "AWS Serverless Data Analytics Pipeline reference architecture"))

    # 2) Orchestration present but not wired to any processing job (real refs only).
    orch = [r for r in resources if r["role"] == "orchestrate"]
    xf = [r for r in resources if r["role"] == "transform"]
    if orch and xf:
        xf_modules = {r["module"].split(".")[-1] for r in xf if r["module"].startswith("module.")}
        wired = any(xf_modules & deps.get(o["module"].split(".")[-1], set())
                    for o in orch if o["module"].startswith("module."))
        if not wired:
            findings.append(_finding(
                "ARCH-ORCH-UNWIRED", "MEDIUM",
                "Orchestration is not wired to processing jobs",
                "An orchestrator is present but its configuration does not reference any "
                "processing job — the pipeline will not actually run end to end.",
                "WA Analytics Lens BP 6.1 (illustrate data-flow dependencies)"))

    # 3) Well-Architected checks (only when the relevant layer exists).
    if cov.get("storage"):
        if not has("kms"):
            findings.append(_finding(
                "WA-SEC-KMS", "MEDIUM", "No customer-managed KMS key for the data lake",
                "Storage is present without a customer-managed KMS key. WA recommends CMK "
                "encryption at rest for governed data.",
                "WA Analytics Lens — Security (data protection)"))
        if not has("server_side_encryption", "encryption"):
            findings.append(_finding(
                "WA-SEC-SSE", "MEDIUM", "No server-side encryption configuration on storage",
                "Storage buckets have no explicit server-side encryption configuration.",
                "WA Analytics Lens — Security (encryption at rest)"))
        if not has("versioning", "replication"):
            findings.append(_finding(
                "WA-REL-DR", "LOW", "No versioning/replication for data recovery",
                "Storage has no versioning or replication — limits recovery/replay (RPO).",
                "WA Analytics Lens BP 6.5 (disaster recovery plan)"))

    # BP 1.2 / 6.2 — operational monitoring of jobs.
    if not cov.get("governance") or not has("cloudwatch", "monitor", "log_group", "metric_alarm"):
        findings.append(_finding(
            "WA-OPS-MONITORING", "MEDIUM", "No job/operational monitoring detected",
            "No CloudWatch (or equivalent) monitoring found. WA requires monitoring of data "
            "processing jobs and source availability.",
            "WA Analytics Lens BP 1.2 / 6.2"))

    # BP 6.3 — failure notification target.
    if (cov.get("processing") or cov.get("ingestion")) and not has("sns", "notification", "chatbot", "topic"):
        findings.append(_finding(
            "WA-REL-NOTIFY", "LOW", "No failure-notification target",
            "No SNS topic / notification target found to alert stakeholders on ETL job failures.",
            "WA Analytics Lens BP 6.3 (notify stakeholders of job failures)"))

    # 4) Scale-tier checks — only when the run DECLARES a volume (never guessed).
    tier = volume_tier(daily_data_gb)
    if tier in ("tb", "pb"):
        names = " ".join(r["address"].lower() for r in resources)
        if cov.get("processing") and "compact" not in names:
            findings.append(_finding(
                "TIER-COMPACTION", "HIGH",
                f"No small-file compaction at {tier.upper()}-scale volume",
                "At >= 1 TB/day the small-files problem dominates: AWS measured 100k small "
                "files scanning 62-88% slower with S3 throttling errors (target ~128 MB "
                "objects). Add the compaction-glue module or table-format-native compaction.",
                "AWS Athena performance tuning (file size / small files)"))
        if has("athena") and not has("redshift", "snowflake", "synapse", "bigquery"):
            findings.append(_finding(
                "TIER-WAREHOUSE", "MEDIUM",
                "Athena-only consumption at scale",
                "Athena workgroups default to ~20 concurrent queries; at this volume tier "
                "concurrent BI load typically needs a warehouse-class engine "
                "(consumption-redshift-serverless) for dashboards.",
                "Athena service quotas / published concurrency comparisons"))
    if tier == "pb" and not has("glue_catalog_table", "s3tables", "iceberg", "databricks", "lakeformation"):
        findings.append(_finding(
            "TIER-TABLE-FORMAT", "HIGH",
            "No open table format at PB-scale volume",
            "Beyond ~100 TB, folder-level metadata stops scaling — file/snapshot tracking, "
            "ACID commits, and partition indexes are why Iceberg/Delta exist (documented "
            "migrations at 115 TB+ broke warehouse economics without one). Add the "
            "table-format-iceberg module.",
            "Netflix/Iceberg design rationale; TRM Labs lakehouse migration"))

    findings.sort(key=lambda f: (_SEV_WEIGHT.get(f["severity"], 0) * -1, f["id"]))
    score = max(0, 100 - sum(_SEV_WEIGHT.get(f["severity"], 0) for f in findings))
    status = "READY" if score >= 90 else "NEEDS_WORK" if score >= 60 else "INCOMPLETE"

    return {
        "score": score,
        "status": status,
        "volume_tier": tier,
        "layers": {layer: {"present": bool(cov.get(layer)), "count": len(cov.get(layer, []))}
                   for layer in CANONICAL_LAYERS},
        "other_count": len(cov.get("other", [])),
        "resource_total": len(resources),
        "findings": findings,
    }


def _main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python core/architecture_model.py <plan.json>", file=sys.stderr)
        return 2
    path = argv[0]
    if not os.path.exists(path):
        print(f"not found: {path}", file=sys.stderr)
        return 2
    with open(path, encoding="utf-8") as f:
        plan = json.load(f)
    report = conformance(plan)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
