"""
Composable Terraform module registry (the building blocks the architect assembles).

Instead of a monolithic blueprint per scenario, MinusOps keeps small, vetted Terraform modules
under `modules/<id>/` and selects + composes them to match gathered requirements. Each entry
here is metadata: what requirement the module satisfies, the services it provisions, its inputs,
and the values it exports for wiring. Requirement -> module selection is therefore deterministic
and auditable, and new capabilities are added by dropping in a module + a row here — never by
forking a giant recipe.

The composed Terraform still goes through the normal deploy gate (validate + SEC scan + plan-hash
approval + BCM cost); these modules are starting blocks, not an apply-without-review shortcut.
"""
import os
import re
import sysconfig

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _candidate_module_roots():
    """Module assets can come from a source checkout, Docker workdir, or wheel data-files."""
    candidates = [
        os.environ.get("MINUSOPS_MODULES_DIR"),
        os.path.join(os.getcwd(), "modules"),
        os.path.join(REPO_ROOT, "modules"),
        os.path.join(sysconfig.get_path("data") or "", "modules"),
        os.path.join(sysconfig.get_path("purelib") or "", "modules"),
    ]
    seen = set()
    for path in candidates:
        if not path:
            continue
        resolved = os.path.abspath(path)
        if resolved not in seen:
            seen.add(resolved)
            yield resolved


def modules_dir():
    for path in _candidate_module_roots():
        if os.path.isdir(path):
            return path
    return os.path.join(REPO_ROOT, "modules")


MODULES_DIR = modules_dir()

# Each module: id, category, title, satisfies (match keywords), services, inputs, provides.
MODULES = [
    {
        "id": "storage-medallion-s3", "category": "storage",
        "title": "Medallion S3 storage (bronze/silver/gold) with KMS",
        "satisfies": ["data lake", "lakehouse", "medallion", "bronze silver gold",
                      "object storage", "s3", "raw curated", "tiered storage"],
        "services": ["Amazon S3", "AWS KMS"],
        "inputs": ["name_prefix", "tags", "zones", "retention_days"],
        "provides": ["bucket_names", "kms_key_arn"],
    },
    {
        "id": "orchestrator-mwaa", "category": "orchestration",
        "title": "Managed Airflow (Amazon MWAA) orchestration",
        "satisfies": ["airflow", "mwaa", "managed airflow", "dag", "workflow orchestration",
                      "apache airflow", "scheduler"],
        "services": ["Amazon MWAA", "Amazon S3", "AWS IAM"],
        "inputs": ["name_prefix", "tags", "dag_s3_bucket_arn", "subnet_ids", "security_group_ids"],
        "provides": ["airflow_environment", "execution_role_arn"],
    },
    {
        "id": "orchestrator-stepfunctions", "category": "orchestration",
        "title": "Step Functions state-machine orchestration",
        "satisfies": ["step functions", "state machine", "serverless orchestration",
                      "sequential workflow", "sfn"],
        "services": ["AWS Step Functions", "AWS IAM"],
        "inputs": ["name_prefix", "tags", "glue_job_names", "task_role_arns"],
        "provides": ["state_machine_arn", "role_arn"],
    },
    {
        "id": "compute-glue-etl", "category": "compute",
        "title": "AWS Glue Spark ETL jobs",
        "satisfies": ["glue", "spark", "etl", "batch transform", "pyspark", "batch compute"],
        "services": ["AWS Glue", "AWS IAM"],
        "inputs": ["name_prefix", "tags", "script_s3_bucket", "jobs", "worker_type", "number_of_workers", "alarm_sns_topic_arn", "enable_alarms"],
        "provides": ["glue_job_names", "glue_job_arns", "glue_role_arn"],
    },
    {
        "id": "speed-layer-kinesis", "category": "streaming",
        "title": "Streaming speed layer (Kinesis + Managed Flink)",
        "satisfies": ["lambda architecture", "kappa", "streaming", "real-time", "real time",
                      "speed layer", "kinesis", "flink", "sub-second", "low latency ingest", "events"],
        "services": ["Amazon Kinesis Data Streams", "Amazon Managed Service for Apache Flink"],
        "inputs": ["name_prefix", "tags", "shard_count", "retention_hours"],
        "provides": ["stream_arn", "stream_name"],
    },
    {
        "id": "dq-great-expectations", "category": "data-quality",
        "title": "Data-quality checks (Great Expectations on Glue)",
        "satisfies": ["data quality", "great expectations", "validation", "data validation",
                      "quality checks", "deequ", "expectations", "data tests"],
        "services": ["AWS Glue", "Amazon S3", "AWS IAM"],
        "inputs": ["name_prefix", "tags", "target_buckets", "fail_on_error"],
        "provides": ["dq_job_name", "dq_results_bucket"],
    },
    {
        "id": "schema-registry-glue", "category": "schema",
        "title": "Schema enforcement (Glue Schema Registry)",
        "satisfies": ["schema enforcement", "schema registry", "data contracts", "contracts",
                      "avro", "schema validation", "schema evolution", "compatibility"],
        "services": ["AWS Glue Schema Registry"],
        "inputs": ["name_prefix", "tags", "schemas", "compatibility"],
        "provides": ["registry_arn", "schema_arns"],
    },
    {
        "id": "query-athena", "category": "serving",
        "title": "Athena workgroup for SQL / BI access",
        "satisfies": ["athena", "sql", "ad-hoc query", "bi", "tableau", "powerbi",
                      "analyst access", "interactive query", "presto"],
        "services": ["Amazon Athena", "Amazon S3"],
        "inputs": ["name_prefix", "tags", "results_kms_key_arn", "bytes_scanned_cutoff"],
        "provides": ["workgroup_name", "results_bucket"],
    },
    {
        "id": "compaction-glue", "category": "optimization",
        "title": "Scheduled small-file compaction (Glue)",
        "satisfies": ["compaction", "small files", "compact", "file optimization",
                      "optimize storage layout", "many small objects", "tb scale"],
        "services": ["AWS Glue", "AWS IAM"],
        "inputs": ["name_prefix", "tags", "script_s3_bucket", "target_buckets", "schedule"],
        "provides": ["compaction_job_name"],
    },
    {
        "id": "table-format-iceberg", "category": "storage",
        "title": "Apache Iceberg table format on the curated zone",
        "satisfies": ["iceberg", "table format", "open table format", "acid tables", "time travel",
                      "delta format", "hudi", "petabyte", "snapshot isolation"],
        "services": ["AWS Glue Data Catalog", "Amazon S3"],
        "inputs": ["name_prefix", "tags", "table_bucket", "table_name", "columns"],
        "provides": ["database_name", "table_name", "table_location"],
    },
    {
        "id": "ingest-firehose", "category": "ingestion",
        "title": "Micro-batched streaming ingestion (Kinesis Data Firehose)",
        "satisfies": ["firehose", "streaming ingestion", "near real-time ingest", "event delivery",
                      "micro-batch", "continuous ingest", "clickstream", "log delivery"],
        "services": ["Amazon Kinesis Data Firehose", "Amazon S3", "AWS IAM"],
        "inputs": ["name_prefix", "tags", "destination_bucket_arn", "buffering_size_mb"],
        "provides": ["delivery_stream_name", "delivery_stream_arn"],
    },
    {
        "id": "compute-emr-serverless", "category": "compute",
        "title": "EMR Serverless Spark for sustained large-scale transforms",
        "satisfies": ["emr", "emr serverless", "large scale spark", "long-running jobs",
                      "heavy transform", "terabyte processing", "sustained compute"],
        "services": ["Amazon EMR Serverless", "AWS IAM"],
        "inputs": ["name_prefix", "tags", "release_label", "max_vcpu", "max_memory", "target_buckets"],
        "provides": ["application_id", "runtime_role_arn"],
    },
    {
        "id": "consumption-redshift-serverless", "category": "serving",
        "title": "Redshift Serverless for high-concurrency BI",
        "satisfies": ["redshift", "warehouse", "high concurrency", "many analysts",
                      "bi at scale", "dashboards at scale", "concurrent queries"],
        "services": ["Amazon Redshift Serverless"],
        "inputs": ["name_prefix", "tags", "base_capacity_rpu"],
        "provides": ["namespace_name", "workgroup_name"],
    },
    {
        "id": "governance-observability", "category": "governance",
        "title": "Budget guardrail + CloudWatch observability",
        "satisfies": ["budget", "cost guardrail", "monitoring", "observability", "alarms",
                      "cloudwatch", "alerting", "finops"],
        "services": ["AWS Budgets", "Amazon CloudWatch"],
        "inputs": ["name_prefix", "tags", "monthly_budget_usd", "alarm_sns_topic_arn"],
        "provides": ["budget_name", "alerts_topic_arn"],
    },
]

REQUIRED_FIELDS = ("id", "category", "title", "satisfies", "services", "inputs", "provides")
_WORD = re.compile(r"[a-z0-9]+")


def list_modules():
    return [dict(m) for m in MODULES]


def get_module(module_id):
    for m in MODULES:
        if m["id"] == module_id:
            return dict(m)
    return None


def categories():
    return sorted({m["category"] for m in MODULES})


def _tokens(text):
    return set(_WORD.findall((text or "").lower()))


def match_modules(requirements, min_score=1):
    """
    Score every module against free-text requirements by keyword overlap with its
    `satisfies` phrases, title, and services. Returns modules sorted best-first, each with a
    `score` and the `matched` keywords — so selection is explainable, not a black box.
    """
    req = (requirements or "").lower()
    req_tokens = _tokens(req)
    scored = []
    for m in MODULES:
        matched = []
        score = 0
        for phrase in m["satisfies"]:
            if phrase in req:                       # whole-phrase hit is strong signal
                score += 3
                matched.append(phrase)
            elif _tokens(phrase) & req_tokens:      # token overlap is a weak signal
                score += 1
                matched.append(phrase)
        for svc in m["services"]:
            if _tokens(svc) & req_tokens:
                score += 1
        # A selection must be explainable by a capability phrase — service-name token
        # overlap alone ("Data", "Amazon") is noise, it only boosts a real match.
        if matched and score >= min_score:
            scored.append({**m, "score": score, "matched": sorted(set(matched))})
    return sorted(scored, key=lambda x: x["score"], reverse=True)


def module_dir(module_id):
    for root in _candidate_module_roots():
        path = os.path.join(root, module_id)
        if os.path.isdir(path):
            return path
    return os.path.join(MODULES_DIR, module_id)


def validate_modules():
    """Return a list of registry errors (schema + that each module's Terraform exists)."""
    errors = []
    seen = set()
    for i, m in enumerate(MODULES):
        for f in REQUIRED_FIELDS:
            if f not in m:
                errors.append(f"modules[{i}] missing field: {f}")
        mid = m.get("id", f"<index {i}>")
        if mid in seen:
            errors.append(f"duplicate module id: {mid}")
        seen.add(mid)
        for listf in ("satisfies", "services", "inputs", "provides"):
            if listf in m and (not isinstance(m[listf], list) or not m[listf]):
                errors.append(f"{mid}.{listf} must be a non-empty list")
        main_tf = os.path.join(module_dir(mid), "main.tf")
        if not os.path.exists(main_tf):
            errors.append(f"{mid}: missing Terraform at modules/{mid}/main.tf")
    return errors


def main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Composable Terraform module registry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("validate")
    mp = sub.add_parser("match")
    mp.add_argument("requirements")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for m in MODULES:
            print(f"{m['id']:<28} {m['category']:<14} {m['title']}")
        return 0
    if args.cmd == "validate":
        errs = validate_modules()
        if errs:
            print("module registry INVALID:")
            for e in errs:
                print(f"  - {e}")
            return 1
        print(f"module registry OK: {len(MODULES)} modules")
        return 0
    if args.cmd == "match":
        for m in match_modules(args.requirements):
            print(f"[{m['score']:>2}] {m['id']:<28} matched: {', '.join(m['matched'])}")
        return 0
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
