"""
Sprint 3: Snowflake, Unity Catalog/Delta, MWAA, MSK, Iceberg maintenance.

All five were validated against the installed provider schemas (AWS v6.60.0, databricks) during
development. What is asserted here is the security and correctness properties that are easy to
regress silently -- the confused-deputy conditions, the auth choices, and the defaults that
decide whether a job can quietly destroy data.
"""
import os

import modules

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NEW = ("warehouse-snowflake-aws", "compute-databricks-delta", "streaming-msk-kafka")


def _hcl(module_id, filename="main.tf"):
    return open(os.path.join(_ROOT, "modules", module_id, filename), encoding="utf-8").read()


def _directives(module_id, filename="main.tf"):
    """HCL with comments stripped. Needed whenever the assertion is about a token being
    ABSENT: these modules explain why a risky block is omitted, and the explanation contains
    the very word the test looks for."""
    return chr(10).join(line for line in _hcl(module_id, filename).splitlines()
                        if not line.lstrip().startswith("#"))


def test_registry_stays_valid_with_the_new_modules():
    assert modules.validate_modules() == []
    assert set(_NEW) <= {m["id"] for m in modules.list_modules()}


def test_each_new_module_is_reachable_by_keyword():
    for phrasing, expected in [
        ("snowflake external stage over our gold zone", "warehouse-snowflake-aws"),
        ("managed kafka cluster with consumer groups", "streaming-msk-kafka"),
        ("unity catalog external location and delta sharing", "compute-databricks-delta"),
    ]:
        ids = [m["id"] for m in modules.match_modules(phrasing)]
        assert expected in ids[:3], f"{phrasing!r} -> {ids[:3]}"


# --- Snowflake ------------------------------------------------------------------------------

def test_snowflake_trust_requires_an_external_id():
    """SEC-05. Snowflake's AWS account is shared across its customers, so the principal alone
    authenticates Snowflake-the-company, never your Snowflake account. Without the external id
    the trust policy admits every other Snowflake customer."""
    hcl = _hcl("warehouse-snowflake-aws")
    assert 'variable = "sts:ExternalId"' in hcl
    assert "snowflake_external_id" in hcl


def test_snowflake_trusts_nobody_before_the_handshake():
    """A placeholder principal would be a role that exists with an unconstrained trust policy
    -- precisely the window this is meant to close."""
    hcl = _hcl("warehouse-snowflake-aws")
    assert "handshake_complete" in hcl
    assert ':root"' in hcl  # account root is the tightest no-op principal


def test_snowflake_stage_access_is_prefix_scoped():
    """An external stage that can read every prefix can read Bronze, which is where the
    un-redacted data is."""
    hcl = _hcl("warehouse-snowflake-aws")
    assert 'variable = "s3:prefix"' in hcl
    assert "stage_prefixes" in hcl


def test_snowpipe_queue_only_accepts_the_stage_bucket():
    """An unconditioned s3.amazonaws.com grant lets any bucket in any account publish here."""
    hcl = _hcl("warehouse-snowflake-aws")
    assert 'variable = "aws:SourceArn"' in hcl


def test_snowflake_module_provisions_no_snowflake_objects():
    """The snowflake provider would need account credentials, and the handshake is two-sided
    -- doing both halves in one apply is a cycle, not a shortcut."""
    directives = _directives("warehouse-snowflake-aws")
    assert "snowflake_storage_integration" not in directives
    assert 'provider "snowflake"' not in directives


# --- Unity Catalog / Delta Sharing ----------------------------------------------------------

def test_external_locations_are_per_zone_not_bucket_root():
    """Governance is granted on the location, so one root location would make a Gold grant a
    Bronze grant too."""
    hcl = _hcl("compute-databricks-delta")
    assert 'resource "databricks_external_location" "zone"' in hcl
    assert "for_each        = local.zones" in hcl or "for_each = local.zones" in hcl


def test_only_gold_is_exposed_by_default():
    """Silver and Bronze hold pre-redaction data; exposing them through the catalog makes
    every workspace user a reader of it."""
    hcl = _hcl("compute-databricks-delta")
    assert 'default     = ["gold"]' in hcl


def test_unity_catalog_role_uses_an_external_id_too():
    """Same confused-deputy shape as Snowflake: the Databricks account is shared."""
    hcl = _hcl("compute-databricks-delta")
    assert 'variable = "sts:ExternalId"' in hcl
    assert "databricks_account_id" in hcl


def test_delta_share_is_select_only_and_needs_explicit_tables():
    """A share is a one-way publication; anything writable is a pipeline. And a schema-level
    share silently includes every table added to it later."""
    hcl = _hcl("compute-databricks-delta")
    assert 'privileges = ["SELECT"]' in hcl
    assert "shared_tables" in hcl
    assert 'data_object_type = "TABLE"' in hcl


def test_no_share_is_created_without_recipients():
    """A share created "ready for later" is an object someone grants access to without
    revisiting what is in it."""
    hcl = _hcl("compute-databricks-delta")
    assert "length(var.delta_share_recipients) > 0 && length(var.shared_tables) > 0" in hcl


def test_delta_module_declares_its_own_provider_source():
    """Terraform infers the nonexistent hashicorp/databricks otherwise, and the root/child
    provider addresses disagree -- caught by terraform validate during development."""
    assert 'source  = "databricks/databricks"' in _hcl("compute-databricks-delta")


# --- MWAA -----------------------------------------------------------------------------------

def test_dag_bucket_is_versioned():
    """MWAA identifies DAG updates by S3 object version; an unversioned source bucket fails
    environment creation with a message that does not say so."""
    hcl = _hcl("orchestrator-mwaa")
    assert 'resource "aws_s3_bucket_versioning" "dags"' in hcl


def test_exactly_one_dag_bucket_source_is_accepted():
    """Two sources of truth for where DAGs live is how an environment ends up reading an
    empty bucket."""
    hcl = _hcl("orchestrator-mwaa")
    assert "!(var.create_dag_bucket && var.dag_s3_bucket_arn != \"\")" in hcl
    assert 'var.create_dag_bucket || var.dag_s3_bucket_arn != ""' in hcl


def test_webserver_is_private_by_default():
    """The Airflow UI can trigger every DAG in the environment."""
    hcl = _hcl("orchestrator-mwaa")
    assert 'default     = "PRIVATE_ONLY"' in hcl


def test_all_five_log_streams_are_enabled():
    """Scheduler and worker logs are where a DAG that never runs explains itself."""
    hcl = _hcl("orchestrator-mwaa")
    for stream in ("dag_processing_logs", "scheduler_logs", "task_logs", "webserver_logs",
                   "worker_logs"):
        assert stream in hcl, stream


# --- MSK ------------------------------------------------------------------------------------

def test_msk_uses_iam_auth_and_no_anonymous_access():
    """SCRAM would mean a password in a variable; unauthenticated Kafka has no read-only
    notion -- an anonymous client can produce and consume on any allowed topic."""
    assert "iam = true" in _hcl("streaming-msk-kafka")
    assert "unauthenticated" not in _directives("streaming-msk-kafka")


def test_msk_encrypts_in_transit_both_ways():
    """PLAINTEXT is offered by the API and is how a "private subnet, it's fine" cluster ships
    topic data in clear across AZs."""
    hcl = _hcl("streaming-msk-kafka")
    assert 'client_broker = "TLS"' in hcl
    assert "in_cluster    = true" in hcl


def test_msk_requires_multi_az():
    hcl = _hcl("streaming-msk-kafka")
    assert "length(var.subnet_ids) >= 2" in hcl


def test_connector_topic_access_is_scoped_to_this_cluster():
    """A wildcard cluster resource lets an IAM-authenticated client read every topic in the
    account's other clusters."""
    assert "resources = [aws_msk_cluster.this.arn]" in _hcl("streaming-msk-kafka")
    assert '"kafka-cluster:*"' not in _directives("streaming-msk-kafka")


def test_no_connector_role_without_a_sink_bucket():
    """An unused principal still shows up in every IAM review."""
    hcl = _hcl("streaming-msk-kafka")
    assert 'count              = var.sink_bucket == "" ? 0 : 1' in hcl


# --- Iceberg maintenance --------------------------------------------------------------------

def test_iceberg_maintenance_is_off_by_default():
    """Compaction rewrites data files. A maintenance job nobody asked for that rewrites Gold
    at 2am is not a helpful default."""
    hcl = _hcl("query-athena", "iceberg_maintenance.tf")
    assert "iceberg_maintenance_tables" in hcl
    assert "default     = []" in hcl
    assert "length(var.iceberg_maintenance_tables) > 0" in hcl


def test_tables_are_named_not_discovered():
    """A catalog scan would sweep in Hive tables, where OPTIMIZE is a syntax error, and the
    non-Iceberg failures would mask the real ones."""
    hcl = _hcl("query-athena", "iceberg_maintenance.tf")
    assert "glue:GetTables" in hcl          # read for validation
    assert "for table in TABLES" in hcl     # but the list comes from config


def test_vacuum_runs_after_optimize():
    """Compaction creates a new snapshot; expiring first would leave the pre-compaction files
    behind until the next run."""
    hcl = _hcl("query-athena", "iceberg_maintenance.tf")
    assert hcl.index("OPTIMIZE {table}") < hcl.index("VACUUM {table}")


def test_snapshot_retention_has_a_floor():
    """Expiring a snapshot mid-query fails the query and the files are already gone."""
    hcl = _hcl("query-athena", "iceberg_maintenance.tf")
    assert "var.iceberg_snapshot_retention_days >= 1" in hcl


def test_athena_permission_is_scoped_to_this_workgroup():
    """A wildcard would let the maintenance role run arbitrary SQL in every workgroup in the
    account, including ones with no scan limit."""
    hcl = _hcl("query-athena", "iceberg_maintenance.tf")
    assert "resources = [aws_athena_workgroup.this.arn]" in hcl


def test_one_failed_table_does_not_stop_the_rest_but_still_raises():
    """A silent partial run is worse than a loud one."""
    hcl = _hcl("query-athena", "iceberg_maintenance.tf")
    assert "failures.append" in hcl
    assert "raise RuntimeError" in hcl
