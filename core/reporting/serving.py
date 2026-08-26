"""
Serving-layer endpoints: where Gold data is actually consumed from.

Four archetypes, each with a concrete address an analyst can paste:

  1. ad_hoc_sql     -- Athena JDBC, for exploration and BI tools
  2. data_warehouse -- Redshift Serverless, for high-concurrency dashboards
  3. semantic_layer -- the governed metric definitions, for BI and LLM agents
  4. reverse_etl    -- the S3 stage operational syncs read from

THE RULE THIS MODULE TURNS ON: an endpoint is emitted only when the stack actually
provisioned it, and only when every part of its address is known. A Redshift connection
string for a stack with no Redshift is a credential-shaped string that fails at connect time,
and the analyst blames the tool rather than the stack. A half-built URL like
`jdbc:awsathena://AwsRegion=None;Workgroup=x` is worse still -- it looks plausible enough to
paste. `_require()` is what enforces both: any missing part drops the whole endpoint.

Nothing here is reconstructed from a name_prefix. Every value comes from `terraform
output`-derived JSON, the same reasoning `seed.read_outputs()` carries: re-deriving a bucket
name by string surgery is how you seed the wrong bucket and report success.

Depends on: nothing (stdlib only)
Shells out to: nothing
Used by: core/cli/commands/runs.py (the spec card), core/reporting/export.py (the domain-repo
    connection scaffold)
"""
ARCHETYPES = ("ad_hoc_sql", "data_warehouse", "semantic_layer", "reverse_etl")

# Module id -> the semantic-layer artifact a consumer points at.
_SEMANTIC_ARTIFACTS = {
    "dbt-semantic-layer": ("dbt MetricFlow", "models/semantic_models.yml"),
    "cube-semantic-layer": ("Cube SQL API", "cube/schema/"),
}

REDSHIFT_PORT = 5439


def _require(*values):
    """All values present and non-empty, or None. A partially-known address is not an
    address."""
    for value in values:
        if value in (None, "", [], {}):
            return None
    return values


def _gold_bucket(outputs):
    buckets = outputs.get("bucket_names")
    if isinstance(buckets, dict):
        return buckets.get("gold")
    return outputs.get("gold_bucket")


def endpoints(outputs, modules=()):
    """Concrete serving endpoints for a stack, best-first. Empty when nothing is served."""
    outputs = outputs or {}
    modules = tuple(modules or ())
    found = []

    region = outputs.get("region")

    parts = _require(region, outputs.get("athena_workgroup"))
    if parts:
        found.append({
            "archetype": "ad_hoc_sql",
            "label": "Ad-Hoc SQL (Athena)",
            "connection": f"jdbc:awsathena://AwsRegion={parts[0]};Workgroup={parts[1]}",
            "catalog": outputs.get("glue_catalog_database"),
            "note": "Analyst exploration, Tableau, Superset. Billed per byte scanned.",
        })

    parts = _require(outputs.get("redshift_workgroup"), outputs.get("account_id"), region,
                     outputs.get("redshift_database"))
    if parts:
        workgroup, account, rs_region, database = parts
        found.append({
            "archetype": "data_warehouse",
            "label": "Data Warehouse (Redshift)",
            "connection": (f"{workgroup}.{account}.{rs_region}.redshift-serverless"
                           f".amazonaws.com:{REDSHIFT_PORT}/{database}"),
            "catalog": database,
            "note": "High-concurrency executive dashboards. Bounded by max_capacity.",
        })

    for module_id, (label, artifact) in _SEMANTIC_ARTIFACTS.items():
        if module_id in modules:
            found.append({
                "archetype": "semantic_layer",
                "label": f"Semantic Layer ({label})",
                "connection": artifact,
                "catalog": None,
                "note": "Governed metrics for BI tools and LLM agents. Edit in the domain repo.",
            })

    quarantine = outputs.get("quarantine_bucket")
    if quarantine:
        found.append({
            "archetype": "reverse_etl",
            "label": "Quarantine Stage",
            "connection": f"s3://{quarantine}/invalid_records/",
            "catalog": None,
            "note": "Rows that failed validation. Read before raising a quality threshold.",
        })

    gold = _gold_bucket(outputs)
    if gold:
        found.append({
            "archetype": "reverse_etl",
            "label": "Gold Stage (reverse ETL)",
            "connection": f"s3://{gold}/",
            "catalog": None,
            "note": "Source for AppFlow / Snowflake stage syncs to operational systems.",
        })

    return found


def as_yaml(endpoints_list, pipeline_name=""):
    """A hand-rolled YAML document -- flat, quoted, and dependency-free.

    Written by hand rather than through PyYAML because `core/` has no runtime dependencies and
    this is a fixed, flat shape (the same reasoning `cicd.parse_feed` carries). It is
    committed to a domain repository, so it carries addresses only: never a key, a token or a
    password. Authentication is the consumer's IAM role.
    """
    lines = [
        "# Serving endpoints for this pipeline, generated by MinusOps `minusctl export`.",
        "#",
        "# Addresses only. Authentication is your IAM role or SSO session -- nothing here is",
        "# a credential, and nothing here should become one. Regenerate with a fresh export",
        "# after the stack changes rather than editing by hand.",
        "",
    ]
    if pipeline_name:
        lines.append(f"pipeline: \"{pipeline_name}\"")
    lines.append("endpoints:")
    if not endpoints_list:
        lines.append("  []")
        return "\n".join(lines) + "\n"

    for endpoint in endpoints_list:
        lines.append(f"  - archetype: \"{endpoint['archetype']}\"")
        lines.append(f"    label: \"{endpoint['label']}\"")
        lines.append(f"    connection: \"{endpoint['connection']}\"")
        if endpoint.get("catalog"):
            lines.append(f"    catalog: \"{endpoint['catalog']}\"")
        lines.append(f"    note: \"{endpoint['note']}\"")
    return "\n".join(lines) + "\n"


def sample_queries(outputs, table="customer_events"):
    """A file an analyst can open and run. Names the catalog the stack actually created."""
    database = (outputs or {}).get("glue_catalog_database")
    if not database:
        return None
    return f"""-- Sample queries against the Gold zone, generated by MinusOps `minusctl export`.
--
-- Athena bills by BYTES SCANNED, so every query below filters on the partition key first.
-- Dropping that filter turns a cent into a full-table scan; on a multi-year lake that is the
-- difference an analyst notices on the invoice rather than in the console.

-- 1. Does the table have data, and for which days?
SELECT "date", count(*) AS row_count
FROM "{database}"."{table}"
GROUP BY "date"
ORDER BY "date" DESC
LIMIT 30;

-- 2. One day, fully scanned. The partition filter is what keeps this cheap.
SELECT *
FROM "{database}"."{table}"
WHERE "date" = '2026/08/22'
LIMIT 100;

-- 3. Freshness: how far behind is the latest partition?
SELECT max("date") AS latest_partition
FROM "{database}"."{table}";

-- 4. Row counts by day, for a dashboard's trend line.
SELECT "date", count(*) AS events
FROM "{database}"."{table}"
WHERE "date" >= '2026/08/01'
GROUP BY "date"
ORDER BY "date";
"""
