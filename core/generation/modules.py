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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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


def output_root():
    """Wheel-safe root for MinusOps' own generated governance artifacts (recent-changes/,
    upgrades/, .agents/logs/) -- deliberately never resolves into site-packages, unlike raw
    REPO_ROOT (plain dirname math off this file's location, correct for a source checkout,
    wrong for an installed wheel).

    MODULES_DIR's fallback chain works because it's looking for an *existing* modules/ dir
    (env var -> cwd -> REPO_ROOT -> sysconfig data/purelib, first one found wins). An output
    root doesn't need to already exist, it just needs to be writable and predictable, so the
    fallback here is: an explicit override, then the first of (cwd, REPO_ROOT) that actually
    looks like a MinusOps source checkout, then a guaranteed-writable per-user directory --
    never a location inside the installed package."""
    override = os.environ.get("MINUSOPS_OUTPUT_DIR")
    if override:
        return os.path.abspath(override)
    for candidate in (os.getcwd(), REPO_ROOT):
        looks_like_checkout = (os.path.isdir(os.path.join(candidate, "modules"))
                                or os.path.isfile(os.path.join(candidate, "pyproject.toml")))
        if looks_like_checkout:
            return os.path.abspath(candidate)
    return os.path.join(os.path.expanduser("~"), ".minusops")


# Each module: id, category, title, satisfies (match keywords), services, inputs, provides.
MODULES = [
    {
        "id": "networking-vpc", "category": "networking",
        "title": "Customer-managed VPC (private subnets, NAT, minimal endpoints)",
        "satisfies": ["vpc", "networking", "customer managed vpc", "private subnets",
                      "byo vpc", "mwaa network", "databricks network"],
        "services": ["Amazon VPC", "NAT Gateway", "VPC Endpoints"],
        "inputs": ["name_prefix", "tags", "az_count", "single_nat_gateway"],
        "provides": ["vpc_id", "private_subnet_ids", "public_subnet_ids", "default_security_group_id"],
    },
    {
        "id": "databricks-workspace", "category": "compute",
        "title": "Databricks workspace on AWS (classic, customer-managed VPC)",
        "satisfies": ["databricks", "spark", "notebooks", "unity catalog",
                      "databricks workspace", "delta lake"],
        "services": ["Databricks", "Amazon S3", "AWS IAM"],
        "inputs": ["name_prefix", "tags", "databricks_account_id", "vpc_id", "subnet_ids",
                   "security_group_ids", "existing_metastore_id", "catalog_name",
                   "create_sql_warehouse", "sql_warehouse_cluster_size",
                   "sql_warehouse_auto_stop_mins"],
        "provides": ["workspace_id", "workspace_url", "metastore_id", "catalog_name",
                     "sql_warehouse_id", "sql_warehouse_jdbc_url"],
    },
    {
        "id": "storage-medallion-s3", "category": "storage",
        "title": "Medallion S3 storage (bronze/silver/gold) with KMS",
        "satisfies": ["data lake", "lakehouse", "medallion", "bronze silver gold",
                      "object storage", "s3", "raw curated", "tiered storage"],
        "services": ["Amazon S3", "AWS KMS"],
        "inputs": ["name_prefix", "tags", "zones", "retention_days", "run_id", "force_destroy",
                   "replication_destination_bucket_arns", "replication_destination_kms_key_arn",
                   "multi_region_kms"],
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
        "inputs": ["name_prefix", "tags", "glue_job_names", "task_role_arns", "schedule_expression"],
        "provides": ["state_machine_arn", "role_arn"],
    },
    {
        "id": "compute-glue-etl", "category": "compute",
        "title": "AWS Glue Spark ETL jobs",
        "satisfies": ["glue", "spark", "etl", "batch transform", "pyspark", "batch compute"],
        "services": ["AWS Glue", "AWS IAM"],
        "inputs": ["name_prefix", "tags", "script_s3_bucket", "jobs", "data_buckets", "kms_key_arn",
                   "source_bucket", "target_bucket", "source_format", "target_format",
                   "worker_type", "number_of_workers",
                   "alarm_sns_topic_arn", "enable_alarms"],
        "provides": ["glue_job_names", "glue_job_arns", "glue_role_arn"],
    },
    {
        "id": "compute-emr-ec2-spot", "category": "compute",
        "title": "EMR on EC2 (Graviton + Spot task fleets)",
        "satisfies": ["emr cluster", "petabyte", "multi-terabyte", "sustained spark",
                      "spot instances", "graviton", "instance fleet", "very large scale spark"],
        "services": ["Amazon EMR", "Amazon EC2", "AWS IAM"],
        "inputs": ["name_prefix", "tags", "subnet_ids", "release_label", "target_buckets",
                   "kms_key_arn", "master_instance_types", "core_instance_types",
                   "task_instance_types", "core_target_capacity", "task_target_spot_capacity",
                   "spot_timeout_minutes", "idle_timeout_seconds"],
        "provides": ["cluster_id", "instance_role_arn"],
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
        "inputs": ["name_prefix", "tags", "target_buckets", "fail_on_error", "run_id",
                   "script_s3_bucket", "script_s3_key", "quarantine_kms_key_arn",
                   "alert_topic_arn"],
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
        "inputs": ["name_prefix", "tags", "results_kms_key_arn", "bytes_scanned_cutoff", "run_id", "gold_bucket"],
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
        "inputs": ["name_prefix", "tags", "monthly_budget_usd", "alarm_sns_topic_arn",
                   "notification_emails", "enable_siem_trail", "siem_data_bucket_arns",
                   "siem_kms_key_arn", "siem_retention_days"],
        "provides": ["budget_name", "alerts_topic_arn"],
    },
    # --- Upstream ingestion (MINUS-123..127). The 2026-08-17 run created empty landing
    # buckets because nothing in the catalog answered "where does the data come from".
    {
        "id": "ingestion-dms", "category": "ingestion",
        "title": "Database CDC ingestion (AWS DMS)",
        "satisfies": ["cdc", "change data capture", "rds", "postgres", "mysql", "oracle",
                      "sql server", "on-premise database", "operational database",
                      "replicate database", "transactional source", "database sync"],
        "services": ["AWS DMS", "Amazon S3", "AWS Secrets Manager", "AWS IAM"],
        "inputs": ["name_prefix", "tags", "subnet_ids", "vpc_security_group_ids",
                   "source_engine_name", "source_secret_arn", "target_bucket",
                   "target_bucket_kms_key_arn", "table_mappings_json",
                   "replication_instance_class", "migration_type"],
        "provides": ["replication_task_arn", "dms_role_arn"],
    },
    {
        "id": "ingestion-appflow", "category": "ingestion",
        "title": "SaaS ingestion (Amazon AppFlow)",
        "satisfies": ["saas", "salesforce", "zendesk", "servicenow", "marketo", "stripe",
                      "google analytics", "crm", "third party api", "saas connector"],
        "services": ["Amazon AppFlow", "Amazon S3"],
        "inputs": ["name_prefix", "tags", "connector_profile_name", "connector_type",
                   "source_object", "target_bucket", "target_prefix", "schedule_expression",
                   "mapped_fields"],
        "provides": ["flow_name", "flow_arn"],
    },
    {
        "id": "ingestion-sftp", "category": "ingestion",
        "title": "Partner file drops (AWS Transfer Family SFTP)",
        "satisfies": ["sftp", "ftp", "file drop", "partner files", "external partner",
                      "managed file transfer", "vendor feed", "file upload"],
        "services": ["AWS Transfer Family", "Amazon S3", "AWS IAM"],
        "inputs": ["name_prefix", "tags", "target_bucket", "target_bucket_kms_key_arn",
                   "users", "security_policy_name"],
        "provides": ["sftp_endpoint", "sftp_server_id"],
    },
    {
        "id": "ingestion-webhook", "category": "ingestion",
        "title": "Webhook receiver (API Gateway + SQS)",
        "satisfies": ["webhook", "http push", "event push", "callback url", "inbound events",
                      "real-time push", "api endpoint", "third party events"],
        "services": ["Amazon API Gateway", "Amazon SQS", "AWS Secrets Manager", "AWS IAM"],
        "inputs": ["name_prefix", "tags", "route_key", "message_retention_seconds",
                   "visibility_timeout_seconds", "throttling_burst_limit",
                   "throttling_rate_limit"],
        "provides": ["webhook_url", "queue_url", "queue_arn", "hmac_secret_arn"],
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


# Tokens that appear in so many modules' `satisfies` phrases that a single-token hit on them
# carries no signal. "data" is the worst offender -- it is inside "change data capture",
# "data quality", "data lake", "data contracts", so a weak-overlap hit on it made every
# module look partly relevant to every data-pipeline request. Discovered when adding the
# ingestion modules silently pulled `ingestion-dms` into an Airflow-lakehouse match and broke
# pattern reuse (test_patterns.py). WHOLE-PHRASE matches are unaffected: "change data capture"
# appearing verbatim is still the strong 3-point signal it always was.
_WEAK_STOPWORDS = frozenset({
    "data", "aws", "amazon", "managed", "the", "and", "for", "with", "from", "into", "our",
})


def _tokens(text):
    return set(_WORD.findall((text or "").lower()))


def _signal_tokens(text):
    return _tokens(text) - _WEAK_STOPWORDS


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
            elif _signal_tokens(phrase) & req_tokens:  # token overlap is a weak signal
                score += 1
                matched.append(phrase)
        for svc in m["services"]:
            if _signal_tokens(svc) & req_tokens:
                score += 1
        # A selection must be explainable by a capability phrase — service-name token
        # overlap alone ("Data", "Amazon") is noise, it only boosts a real match.
        if matched and score >= min_score:
            scored.append({**m, "score": score, "matched": sorted(set(matched))})
    return sorted(scored, key=lambda x: x["score"], reverse=True)


def retrieve_grounding_examples(requirements, top_n=3, min_score=1):
    """Repurposes `match_modules()`'s scoring toward retrieval-for-grounding (docs/
    phase6_scope.md section 2.1, docs/phase6_step5_teardown_scope.md section 3) -- additive, not
    a rewrite: the scorer itself is untouched and still used exactly as-is by every existing
    caller (`synthesizer.select_modules()`, `patterns.py`) for final catalog-pick selection.

    This is a DIFFERENT consumer of the same ranking: given a requirement, return the top-N
    ranked modules as real, human-reviewed reference examples -- their actual `main.tf` content,
    not just metadata -- for whatever authors new HCL to ground against (real wiring patterns,
    attribute shapes, cross-resource conventions), architecturally the retrieval step of a RAG
    pipeline. Never a selection decision by itself: nothing calls this to decide what to compose;
    `architecture_decision.json`'s human-reviewed record stays the only path to that (docs/
    phase6_scope.md section 2.1's own explicit non-negotiable).

    Returns a list of {id, title, services, score, matched, content} dicts, best-first. `content`
    is the module's real, current `main.tf` text -- read fresh every call, never cached, so a
    grounding example is never staler than the catalog itself (the same "re-verify live, don't
    trust from history" principle this session's `module_provenance.py` retirement established
    for content review applies here too, for content RETRIEVAL)."""
    ranked = match_modules(requirements, min_score=min_score)
    examples = []
    for m in ranked[:top_n]:
        main_tf_path = os.path.join(module_dir(m["id"]), "main.tf")
        try:
            with open(main_tf_path, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            content = None
        examples.append({
            "id": m["id"], "title": m["title"], "services": m["services"],
            "score": m["score"], "matched": m["matched"], "content": content,
        })
    return examples


# Phase 7 Item 3 (docs/phase7_generation_engine_plan.md), scoped to requirements.py's own named
# carve-out (see that module's docstring): non_functional.latency and data_pipeline.consumption/
# orchestration/catalog are enumerable answers grill-me already gathers and nothing downstream
# reads today. Deliberately NOT storage/compute/data-quality (sources, storage_zones, transforms,
# data_quality) -- those need real text understanding beyond a closed enumerable answer and stay
# on match_modules()'s free-text path.
_ENUMERABLE_FIELD_MODULES = {
    "consumption": ["query-athena", "consumption-redshift-serverless"],
    "orchestration": ["orchestrator-mwaa", "orchestrator-stepfunctions"],
    "catalog": ["schema-registry-glue"],
}
_LATENCY_STREAMING_MODULES = ["speed-layer-kinesis", "ingest-firehose"]


def _is_deferred_or_blank(value):
    text = str(value or "").strip()
    return (not text) or text.lower().startswith("deferred")


def _match_score(text, module_id):
    """Count of module_id's own `satisfies` phrases the text hits (whole-phrase substring OR
    every one of a phrase's tokens present) -- 0 means no match. Used both as a yes/no check
    (score > 0) and, within one enumerable field's candidate group, as a specificity signal so
    the single best-matching alternative wins instead of every independently-matching one. On a
    genuine tie (two alternatives scoring equally), derive_module_ids() surfaces both, labeled as
    mutually-exclusive alternatives for a human to pick between -- it never silently drops one."""
    module = get_module(module_id)
    if module is None:
        return 0
    text_tokens = _tokens(text)
    score = 0
    for phrase in module["satisfies"]:
        if phrase in text:
            score += 1
        else:
            phrase_tokens = _tokens(phrase)
            if phrase_tokens and phrase_tokens <= text_tokens:
                score += 1
    return score


# MINUS-128. Volume decides the engine; the SLA decides whether it can run on discounted
# capacity. Thresholds are the crossover points where the cheaper option stops being cheaper:
#
#   < 1 TB/day   Glue. Per-DPU-second billing with no cluster to idle. Below this, EMR's
#                startup and idle time costs more than Glue's premium.
#   1-5 TB/day   EMR Serverless on Graviton. Spark dynamic allocation without cluster ops,
#                and past ~1 TB Glue's per-DPU rate stops competing.
#   >= 5 TB/day  EMR on EC2 with Graviton + Spot task fleets. Only here does running an
#                actual cluster beat serverless, and only because Spot task capacity is
#                roughly 70% off -- which is also why it needs an interruption-tolerant job.
#
# Advisory, like every other recommendation in this module: returned for a human to review
# into architecture_decision.json, never auto-applied.
_TB = 1024.0

_TIER_TABLE = (
    (_TB, "compute-glue-etl",
     "under 1 TB/day: per-DPU-second billing with no cluster to idle beats EMR startup cost"),
    (5 * _TB, "compute-emr-serverless",
     "1-5 TB/day: Spark dynamic allocation on Graviton without cluster operations"),
    (float("inf"), "compute-emr-ec2-spot",
     "5+ TB/day: sustained scale is the only point where an actual cluster with Spot task "
     "capacity beats serverless"),
)

# Phrases that mean "a person is NOT waiting on this run", which is the precondition for FLEX
# (spare capacity, unpredictable start, possible interruption, ~35% off).
_FLEX_TOLERANT = ("nightly", "overnight", "daily", "batch", "hourly", "hours", "next day",
                  "end of day", "not time sensitive", "not time-sensitive")
_FLEX_INTOLERANT = ("real-time", "real time", "streaming", "sub-second", "subsecond",
                    "interactive", "minutes", "near real")


def compute_tier(daily_gb, latency_text=""):
    """Recommend a compute module and execution class for a volume and an SLA.

    Returns {"module_id", "reason", "execution_class", "daily_gb"}. `execution_class` is
    meaningful only for Glue; it is None for the EMR tiers.

    daily_gb of 0 means "undeclared" -- the caller gets the Glue tier with a reason saying so
    rather than a guess at scale, because recommending an EMR cluster off no evidence is how
    a $40/month pipeline acquires a $4,000/month bill.
    """
    latency = (latency_text or "").lower()
    for ceiling, module_id, reason in _TIER_TABLE:
        if daily_gb < ceiling:
            break
    if not daily_gb:
        module_id, reason = ("compute-glue-etl",
                             "volume undeclared: defaulting to the smallest tier rather than "
                             "guessing at scale -- restate once data_volume is answered")

    execution_class = None
    if module_id == "compute-glue-etl":
        tolerant = any(p in latency for p in _FLEX_TOLERANT)
        intolerant = any(p in latency for p in _FLEX_INTOLERANT)
        # Intolerant wins a tie: "hourly batch feeding a real-time dashboard" must not get FLEX.
        execution_class = "FLEX" if (tolerant and not intolerant) else "STANDARD"
        if execution_class == "FLEX":
            reason += "; FLEX execution class (~35% off) because the stated SLA tolerates an "
            reason += "unpredictable start"
    return {"module_id": module_id, "reason": reason,
            "execution_class": execution_class, "daily_gb": daily_gb}


def derive_module_ids(requirements_data):
    """Read exactly requirements.py's named-carve-out fields and recommend module ids --
    deterministic and explainable, never a keyword score against one accumulated free-text
    blob. Returns a list of {module_id, reason, source_field} dicts, for a human/agent to
    review into architecture_decision.json's `selected_modules` -- never auto-applied, same
    discipline as match_modules()'s own callers.

    Within data_pipeline.consumption/orchestration/catalog, each field's candidate modules are
    mutually-exclusive alternatives (e.g. Athena vs. Redshift Serverless) -- only the
    highest-specificity match(es) (by matched-phrase count) are recommended, never a weaker
    candidate alongside a stronger one. On a genuine tie between two or more alternatives, ALL
    tied candidates are surfaced (never silently narrowed to one arbitrary pick), and each tied
    pick's `reason` is annotated to say so explicitly -- the human reviewing must choose, this
    function never guesses on their behalf. non_functional.latency's streaming modules are
    complementary, not alternatives (a real streaming architecture commonly uses both Kinesis
    and Firehose together), so both are included independently whenever they match -- no
    tie-break there.

    Known disclosed limitation (latency path only): some of speed-layer-kinesis's `satisfies`
    phrases are bare generic single words ("events", "streaming") -- a batch-cadence answer that
    happens to mention one of those words in a non-streaming sense (e.g. "process events in
    hourly batches") can still trigger a false-positive streaming recommendation. This is the
    same class of weak-single-token-match issue the enumerable-field tie-break above was built
    to avoid, but is not fixed on the latency path in this pass: the two streaming modules'
    complementary (non-exclusive) semantics mean a specificity threshold that fixes this would
    also risk dropping ingest-firehose's own legitimate weaker single-phrase matches (e.g. "near
    real-time ingest" scoring only 1) for genuinely-streaming answers. Recommendations here are
    advisory only -- exactly like every other pick this function returns -- so a human reviewing
    before it reaches architecture_decision.json is the actual safety net for this residual gap,
    not a claim that the latency path is airtight."""
    data_pipeline = (requirements_data or {}).get("data_pipeline") or {}
    non_functional = (requirements_data or {}).get("non_functional") or {}
    picks = []

    for field, candidate_ids in _ENUMERABLE_FIELD_MODULES.items():
        value = str(data_pipeline.get(field) or "")
        if _is_deferred_or_blank(value):
            continue
        text = value.lower()
        scores = {module_id: _match_score(text, module_id) for module_id in candidate_ids}
        best = max(scores.values())
        if best == 0:
            continue
        tied = [module_id for module_id in candidate_ids if scores[module_id] == best]
        for module_id in tied:
            reason = f"data_pipeline.{field} = {value!r}"
            if len(tied) > 1:
                others = ", ".join(m for m in tied if m != module_id)
                reason += (f" -- tied with {others}: mutually-exclusive alternatives, "
                           f"a human must pick one")
            picks.append({
                "module_id": module_id,
                "reason": reason,
                "source_field": f"data_pipeline.{field}",
            })

    latency = str(non_functional.get("latency") or "")
    if not _is_deferred_or_blank(latency):
        text = latency.lower()
        for module_id in _LATENCY_STREAMING_MODULES:
            if _match_score(text, module_id) > 0:
                picks.append({
                    "module_id": module_id,
                    "reason": f"non_functional.latency = {latency!r}",
                    "source_field": "non_functional.latency",
                })

    return picks


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
    pp = sub.add_parser(
        "preplan", help="recommend module ids from a requirements.json's enumerable fields")
    pp.add_argument("requirements_file")
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
    if args.cmd == "preplan":
        with open(args.requirements_file, encoding="utf-8") as f:
            data = json.load(f)
        picks = derive_module_ids(data)
        if not picks:
            print("[preplan] no enumerable-field recommendations "
                  "(fields blank/deferred, or no keyword hit)")
            return 0
        for p in picks:
            print(f"{p['module_id']:<28} <- {p['source_field']:<24} {p['reason']}")
        return 0
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
