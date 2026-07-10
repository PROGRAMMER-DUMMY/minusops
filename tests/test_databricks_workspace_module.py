"""
Real-terraform proof for the databricks-workspace module (Phase 2, docs/project_plan.md Phase E
addendum): the existing_metastore_id toggle actually changes whether a new
databricks_metastore gets created -- the behavior the module's region-scoped-metastore design
hinges on (a metastore is one-per-region and shareable; a second workspace in the same region
must attach to the first one's metastore, not create a second). Uses Terraform's native test
framework with both aws and databricks providers mocked -- no credentials, no live calls.

Phase 2b adds catalog_name / create_sql_warehouse toggles (databricks_catalog /
databricks_sql_endpoint), tested the same way: both default off, both provably turn on only
when their variable is supplied.
"""
import os
import shutil
import subprocess

import pytest

import toolpath

TERRAFORM = toolpath.find_tool("terraform")

pytestmark = pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")

_REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
MODULE_SRC = os.path.join(_REPO_ROOT, "modules", "databricks-workspace")

TEST_HCL = """
mock_provider "aws" {}
mock_provider "databricks" {}

variables {
  name_prefix            = "test-dbx"
  tags                   = {}
  databricks_account_id  = "11111111-1111-1111-1111-111111111111"
  vpc_id                 = "vpc-mock"
  subnet_ids             = ["subnet-mock-a", "subnet-mock-b"]
  security_group_ids     = ["sg-mock"]
}

# mock_provider can't synthesize a valid JSON policy document for these three data sources'
# computed .json attribute (same issue hit by dq-great-expectations' aws_iam_policy_document
# data sources) -- override with a minimal but valid JSON object so the resources consuming
# .json (aws_iam_role, aws_iam_role_policy, aws_s3_bucket_policy) don't fail plan-time
# JSON validation.
override_data {
  target = data.databricks_aws_assume_role_policy.this
  values = {
    json = jsonencode({ Version = "2012-10-17", Statement = [] })
  }
}

override_data {
  target = data.databricks_aws_crossaccount_policy.this
  values = {
    json = jsonencode({ Version = "2012-10-17", Statement = [] })
  }
}

override_data {
  target = data.databricks_aws_bucket_policy.this
  values = {
    json = jsonencode({ Version = "2012-10-17", Statement = [] })
  }
}

run "creates_new_metastore_by_default" {
  command = plan

  assert {
    condition     = length(databricks_metastore.this) == 1
    error_message = "a new metastore should be created when existing_metastore_id is not supplied"
  }
}

run "attaches_to_existing_metastore_when_supplied" {
  command = plan

  variables {
    existing_metastore_id = "existing-metastore-id"
  }

  assert {
    condition     = length(databricks_metastore.this) == 0
    error_message = "no new metastore should be created when existing_metastore_id is supplied"
  }

  assert {
    condition     = databricks_metastore_assignment.this.metastore_id == "existing-metastore-id"
    error_message = "metastore_assignment should reference the existing metastore id, not a new one"
  }
}

run "no_catalog_or_warehouse_by_default" {
  command = plan

  assert {
    condition     = length(databricks_catalog.this) == 0
    error_message = "no catalog should be created unless catalog_name is supplied"
  }

  assert {
    condition     = length(databricks_sql_endpoint.this) == 0
    error_message = "no SQL warehouse should be created unless create_sql_warehouse is true"
  }
}

run "catalog_created_when_named" {
  command = plan

  variables {
    catalog_name = "healthdata"
  }

  assert {
    condition     = length(databricks_catalog.this) == 1
    error_message = "a catalog should be created when catalog_name is supplied"
  }

  assert {
    condition     = databricks_catalog.this[0].name == "healthdata"
    error_message = "the created catalog should use the supplied name"
  }
}

run "sql_warehouse_created_when_enabled" {
  command = plan

  variables {
    create_sql_warehouse = true
  }

  assert {
    condition     = length(databricks_sql_endpoint.this) == 1
    error_message = "a SQL warehouse should be created when create_sql_warehouse is true"
  }

  assert {
    condition     = databricks_sql_endpoint.this[0].cluster_size == "2X-Small"
    error_message = "cluster_size should default to the smallest tier"
  }
}
"""


def _cached_plugin_dir():
    """Reuse a provider already downloaded under runs/ if one exists, so the test doesn't need
    network access on every run. Falls back to a normal (networked) terraform init otherwise."""
    runs_dir = os.path.join(_REPO_ROOT, "runs")
    if not os.path.isdir(runs_dir):
        return None
    for entry in sorted(os.listdir(runs_dir), reverse=True):
        candidate = os.path.join(runs_dir, entry, "terraform", ".terraform", "providers")
        if os.path.isdir(candidate):
            return candidate
    return None


@pytest.fixture
def module_copy(tmp_path):
    dst = tmp_path / "databricks-workspace"
    shutil.copytree(MODULE_SRC, dst)
    (dst / "tests").mkdir()
    (dst / "tests" / "metastore_toggle.tftest.hcl").write_text(TEST_HCL, encoding="utf-8")
    return dst


def test_existing_metastore_id_toggle_behavior(module_copy):
    env = dict(os.environ)
    cache = _cached_plugin_dir()
    if cache:
        env["TF_PLUGIN_CACHE_DIR"] = cache
    init = subprocess.run([TERRAFORM, f"-chdir={module_copy}", "init", "-input=false"],
                          capture_output=True, text=True, env=env)
    assert init.returncode == 0, init.stdout + init.stderr
    result = subprocess.run([TERRAFORM, f"-chdir={module_copy}", "test"],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
