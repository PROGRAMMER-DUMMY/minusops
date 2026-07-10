"""
Real-terraform proof for the run_id-hash fix (2026-07-04 audit follow-up): two runs sharing
the same name_prefix must not collide on bucket name. Uses Terraform's native test framework
(`terraform test`) with a fully mocked AWS provider -- no credentials, no live AWS calls. The
account_id mock is pinned via override_data so the assertions isolate the run_id-driven part of
the name (the specific bug this fix prevents) rather than mock_provider's own per-run
randomization of unset computed attributes.
"""
import os
import shutil
import subprocess

import pytest

import toolpath

TERRAFORM = toolpath.find_tool("terraform")

pytestmark = pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")

_REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
MODULE_SRC = os.path.join(_REPO_ROOT, "modules", "storage-medallion-s3")

TEST_HCL = """
mock_provider "aws" {}

variables {
  name_prefix = "sandbox-dev"
  zones       = ["gold"]
  tags        = {}
}

override_data {
  target = data.aws_caller_identity.current
  values = {
    account_id = "123456789012"
  }
}

run "run_a" {
  command = plan
  variables {
    run_id = "20260704-020012-requirements-first"
  }
}

run "run_b_same_prefix_different_run" {
  command = plan
  variables {
    run_id = "20260704-030500-requirements-first"
  }

  assert {
    condition     = output.bucket_names["gold"] != run.run_a.bucket_names["gold"]
    error_message = "two runs sharing the same name_prefix but different run_id must not collide on bucket name"
  }
}

run "run_a_replan_is_deterministic" {
  command = plan
  variables {
    run_id = "20260704-020012-requirements-first"
  }

  assert {
    condition     = output.bucket_names["gold"] == run.run_a.bucket_names["gold"]
    error_message = "re-planning the same run must produce the same bucket name (idempotent)"
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
    dst = tmp_path / "storage-medallion-s3"
    shutil.copytree(MODULE_SRC, dst)
    (dst / "tests").mkdir()
    (dst / "tests" / "run_id_uniqueness.tftest.hcl").write_text(TEST_HCL, encoding="utf-8")
    return dst


def test_run_id_prevents_same_prefix_bucket_collision(module_copy):
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
