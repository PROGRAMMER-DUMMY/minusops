"""
Real-terraform proof for the networking-vpc module (Phase 1, docs/project_plan.md Phase E
addendum): subnet/AZ count follows az_count, and the single_nat_gateway toggle actually changes
NAT gateway count (1 shared vs. one per AZ) -- the two behaviors the module's whole design
hinges on. Uses Terraform's native test framework (`terraform test`) with a fully mocked AWS
provider -- no credentials, no live AWS calls. aws_availability_zones/aws_region are overridden
via override_data because the module indexes into them by az_count, and mock_provider's own
auto-generated mock values aren't guaranteed to have enough entries.
"""
import os
import shutil
import subprocess

import pytest

import toolpath

TERRAFORM = toolpath.find_tool("terraform")

pytestmark = pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")

_REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
MODULE_SRC = os.path.join(_REPO_ROOT, "modules", "networking-vpc")

TEST_HCL = """
mock_provider "aws" {}

variables {
  name_prefix = "test-net"
  tags        = {}
}

override_data {
  target = data.aws_availability_zones.available
  values = {
    names = ["us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d"]
  }
}

override_data {
  target = data.aws_region.current
  values = {
    region = "us-east-1"
  }
}

run "single_nat_default" {
  command = plan

  variables {
    az_count = 2
  }

  assert {
    condition     = length(output.private_subnet_ids) == 2
    error_message = "private_subnet_ids should have az_count entries"
  }

  assert {
    condition     = length(output.public_subnet_ids) == 2
    error_message = "public_subnet_ids should have az_count entries"
  }

  assert {
    condition     = length(output.nat_gateway_ids) == 1
    error_message = "single_nat_gateway defaults true -- exactly one NAT gateway expected"
  }
}

run "per_az_nat" {
  command = plan

  variables {
    az_count           = 3
    single_nat_gateway = false
  }

  assert {
    condition     = length(output.nat_gateway_ids) == 3
    error_message = "single_nat_gateway=false should create one NAT gateway per AZ"
  }

  assert {
    condition     = length(output.public_subnet_ids) == 3
    error_message = "public subnets should match az_count"
  }

  assert {
    condition     = length(output.private_subnet_ids) == 3
    error_message = "private subnets should match az_count"
  }
}

run "optional_endpoints_off_by_default" {
  command = plan

  assert {
    condition     = aws_vpc_endpoint.sts == []
    error_message = "sts endpoint should not be created unless enable_sts_endpoint is true"
  }

  assert {
    condition     = aws_vpc_endpoint.kinesis == []
    error_message = "kinesis endpoint should not be created unless enable_kinesis_endpoint is true"
  }
}

run "optional_endpoints_can_be_enabled" {
  command = plan

  variables {
    enable_sts_endpoint     = true
    enable_kinesis_endpoint = true
  }

  assert {
    condition     = length(aws_vpc_endpoint.sts) == 1
    error_message = "sts endpoint should be created when enable_sts_endpoint is true"
  }

  assert {
    condition     = length(aws_vpc_endpoint.kinesis) == 1
    error_message = "kinesis endpoint should be created when enable_kinesis_endpoint is true"
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
    dst = tmp_path / "networking-vpc"
    shutil.copytree(MODULE_SRC, dst)
    (dst / "tests").mkdir()
    (dst / "tests" / "az_and_nat_behavior.tftest.hcl").write_text(TEST_HCL, encoding="utf-8")
    return dst


def test_az_count_and_nat_gateway_toggle_behavior(module_copy):
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
