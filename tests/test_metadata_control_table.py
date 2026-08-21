"""
Phase 3 (tasks/implementation_plan_for_architect.md): metadata control table module + runtime
column-mapping helper.

Fast: reads the module HCL, the registry, and the standalone runtime script -- no Terraform, no
AWS. Mirrors tests/test_ingestion_modules.py's HCL-assertion style for the module half; the
runtime-helper half is the load-bearing part, since the whole point of the primary (read-
existing-table) path is that a caller's own, differently-named columns still resolve correctly.

Depends on: modules (core/generation/modules.py, via conftest.py's sys.path insert),
    modules/metadata-control-table/scripts/fetch_pipeline_config.py (loaded by file path --
    it deliberately is not an importable package, see that file's own docstring)
Shells out to: nothing
Used by: nothing (test module)
"""
import importlib.util
import os

import modules

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE_ID = "metadata-control-table"
_SCRIPT_PATH = os.path.join(_ROOT, "modules", _MODULE_ID, "scripts", "fetch_pipeline_config.py")


def _hcl():
    return open(os.path.join(_ROOT, "modules", _MODULE_ID, "main.tf"), encoding="utf-8").read()


def _load_script():
    spec = importlib.util.spec_from_file_location("fetch_pipeline_config", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fpc = _load_script()


# --- registry ---------------------------------------------------------------

def test_registry_stays_valid_with_the_new_module():
    assert modules.validate_modules() == []
    assert _MODULE_ID in {m["id"] for m in modules.list_modules()}


def test_module_reachable_by_a_realistic_phrasing_not_just_its_id():
    """A module nobody's words reach is a module that does not exist. Uses multi-word phrases
    deliberately -- match_modules() scores a whole-phrase hit at 3 vs. a single weak-token hit
    at 1, and generic single tokens like 'data'/'pipeline' are exactly what the catalog's own
    _WEAK_STOPWORDS carve-out warns against relying on."""
    ids = [m["id"] for m in modules.match_modules(
        "we need a dynamic dag configuration control table so Airflow reads cluster size and "
        "schedule from a database instead of hardcoding it")]
    assert _MODULE_ID in ids[:3], ids[:3]


# --- Terraform: fallback-creation module conventions -------------------------

def test_no_credential_taken_as_an_input():
    """TerraShark FM-02, same bar as tests/test_ingestion_modules.py applies to the ingestion
    catalog."""
    hcl = _hcl()
    for forbidden in ('variable "password"', 'variable "api_key"', 'variable "secret_key"',
                       'variable "token"', 'variable "access_key"', 'variable "client_secret"'):
        assert forbidden not in hcl, forbidden


def test_no_iam_resource_and_therefore_no_wildcard_resource_statement():
    """This module provisions no IAM at all -- readers (Airflow/Step Functions) use their own
    existing execution role. Asserted explicitly rather than assumed, so a future edit that adds
    an IAM policy here is forced to justify itself against the catalog's 'no Resource = *' bar
    rather than sliding one in unnoticed."""
    hcl = _hcl()
    assert 'resource "aws_iam_' not in hcl
    assert 'Resource = "*"' not in hcl
    assert "Resource = [\"*\"]" not in hcl


def test_encryption_at_rest_is_always_enabled():
    hcl = _hcl()
    assert "server_side_encryption {" in hcl
    assert "enabled     = true" in hcl


def test_table_name_defaults_to_a_run_hash_suffixed_name():
    """Same collision-avoidance shape as storage-medallion-s3's buckets and query-athena's
    results bucket: two runs sharing the same name_prefix must not collide."""
    hcl = _hcl()
    assert "substr(md5(var.run_id), 0, 8)" in hcl
    assert 'var.table_name != "" ? var.table_name :' in hcl


def test_key_attribute_names_are_inputs_not_hardcoded():
    """The whole point of the fallback path: even a greenfield table can be created under a
    company's own naming convention, not a MinusOps-invented one."""
    hcl = _hcl()
    assert 'variable "partition_key_name"' in hcl
    assert 'variable "sort_key_name"' in hcl
    assert "hash_key     = var.partition_key_name" in hcl


# --- runtime helper: the column-mapping indirection (the primary path) ------

def test_column_mapping_resolves_an_existing_table_with_completely_different_column_names():
    """The core claim this whole phase rests on: MinusOps reads an EXISTING enterprise control
    table under ITS OWN column names, never a fixed MinusOps schema. Two raw rows shaped like
    two unrelated companies' own tables must both resolve to the same normalized keys."""
    company_a_row = {
        "FeedID": {"S": "payer_feed"},
        "CronSchedule": {"S": "0 8 * * ? *"},
        "EngineType": {"S": "glue_spark"},
        "WorkerCount": {"N": "4"},
    }
    company_a_map = {
        "feed_id": "FeedID", "schedule_cron": "CronSchedule",
        "cluster_type": "EngineType", "dpu_workers": "WorkerCount",
    }
    company_b_row = {
        "pipeline_key": {"S": "payer_feed"},
        "cron_expr": {"S": "0 8 * * ? *"},
        "compute_engine": {"S": "glue_spark"},
        "workers": {"N": "4"},
    }
    company_b_map = {
        "feed_id": "pipeline_key", "schedule_cron": "cron_expr",
        "cluster_type": "compute_engine", "dpu_workers": "workers",
    }
    row_a = fpc.parse_control_row(company_a_row, company_a_map)
    row_b = fpc.parse_control_row(company_b_row, company_b_map)
    assert row_a == row_b == {
        "feed_id": "payer_feed", "schedule_cron": "0 8 * * ? *",
        "cluster_type": "glue_spark", "dpu_workers": 4,
    }


def test_mapped_column_absent_from_the_row_resolves_to_none_not_a_crash():
    row = fpc.parse_control_row({"FeedID": {"S": "x"}}, {"status": "Status"})
    assert row == {"status": None}


def test_unsupported_and_malformed_attribute_values_resolve_to_none():
    row = fpc.parse_control_row(
        {"nested": {"M": {"x": {"S": "y"}}}, "bad": "not-a-dict"},
        {"a": "nested", "b": "bad", "c": "missing"},
    )
    assert row == {"a": None, "b": None, "c": None}


def test_numeric_attribute_values_convert_to_int_or_float():
    row = fpc.parse_control_row(
        {"Workers": {"N": "4"}, "ScanCap": {"N": "10.5"}},
        {"workers": "Workers", "scan_cap": "ScanCap"},
    )
    assert row["workers"] == 4 and isinstance(row["workers"], int)
    assert row["scan_cap"] == 10.5 and isinstance(row["scan_cap"], float)


def test_self_check_demo_runs_clean():
    fpc._demo()  # asserts internally; a regression here raises, failing this test
