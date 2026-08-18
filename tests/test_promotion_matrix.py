"""
Step 6 (MINUS-114/130/131/132): environment promotion, SIEM, DR, mandatory tags.

Fast: renderers and generated text, not Terraform. The composed stack is validated by the
slow tests.
"""
import os
import re

import synthesizer

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _norm(text):
    """Collapse runs of spaces. `terraform fmt` aligns `=` inside a generated workspace but
    the raw renderer does not, and the tests care about the values, not the column widths."""
    return re.sub(" +", " ", text)  # spaces only: \s would eat the newlines block-slicing needs


def _assignments(tfvars):
    """The actual `key = value` lines, ignoring comments -- a word appearing in prose must
    not read as a setting."""
    return [_norm(line).strip() for line in tfvars.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def _tfvars(env, budget=0):
    return synthesizer._render_env_tfvars(env, "demo", "data-platform", "run1",
                                          monthly_budget_usd=budget)


def test_every_environment_gets_a_var_file(tmp_path):
    written = synthesizer.write_env_tfvars(str(tmp_path), "demo", owner="data-platform")
    assert sorted(os.path.basename(p) for p in written) == [
        "dev.tfvars", "prod.tfvars", "staging.tfvars"]


def test_force_destroy_is_not_settable_from_a_var_file():
    """The load-bearing safety property of the promotion matrix. main.tf derives
    force_destroy from `var.environment == "dev"`, so no tfvars file can turn it on for prod.
    If someone adds it to _ENV_MATRIX as a convenience, this fails."""
    for env in ("dev", "staging", "prod"):
        assert not any(line.startswith("force_destroy") for line in _assignments(_tfvars(env)))


def test_prod_scales_up_and_retains_longer_than_dev():
    dev, prod = _assignments(_tfvars("dev")), _assignments(_tfvars("prod"))
    assert "glue_number_of_workers = 2" in dev
    assert "retention_days = 30" in dev
    assert "glue_number_of_workers = 10" in prod
    assert "retention_days = 365" in prod


def test_mandatory_tags_are_demanded_only_where_they_are_enforced():
    """staging/prod must carry them (the check block reports when unset); dev leaves them
    commented so a throwaway run is not blocked on a cost centre that does not exist yet."""
    assert 'cost_center = "REVIEW_REQUIRED"' in _assignments(_tfvars("prod"))
    assert 'data_classification = "REVIEW_REQUIRED"' in _assignments(_tfvars("staging"))
    assert not any(line.startswith("cost_center") for line in _assignments(_tfvars("dev")))


def test_budget_scales_with_the_tier_not_copied_flat():
    """A single declared ceiling applied identically to dev and prod means the prod alarm is
    tuned for dev traffic, or the dev alarm never fires."""
    assert "monthly_budget_usd = 125" in _assignments(_tfvars("dev", budget=500))
    assert "monthly_budget_usd = 250" in _assignments(_tfvars("staging", budget=500))
    assert "monthly_budget_usd = 500" in _assignments(_tfvars("prod", budget=500))
    # Undeclared stays undeclared rather than being invented.
    assert not any(line.startswith("monthly_budget_usd") for line in _assignments(_tfvars("prod")))


def test_default_tags_carry_the_mandatory_set():
    providers = synthesizer._render_providers({"storage-medallion-s3"})
    for tag in ("managed_by", "owner", "environment", "run_id"):
        assert tag in providers
    # Empty values are worse than absent ones: they look allocated in Cost Explorer.
    assert 'var.cost_center == "" ? {} :' in providers
    assert 'var.data_classification == "" ? {} :' in providers


def test_mandatory_tag_check_exists_and_is_scoped_to_promoted_environments():
    variables = synthesizer._render_variables({"storage-medallion-s3"})
    assert 'check "mandatory_tags_present"' in variables
    assert 'contains(["staging", "prod"], var.environment)' in variables


def test_replication_destination_is_per_zone():
    """S3 replication preserves the object key exactly and cannot add a prefix, so three
    zones replicating into one destination bucket would overwrite each other. The input is a
    map for that reason -- a regression to a single string ARN reintroduces the collision."""
    hcl = open(os.path.join(_ROOT, "modules", "storage-medallion-s3", "main.tf"),
               encoding="utf-8").read()
    assert 'variable "replication_destination_bucket_arns"' in hcl
    assert "type        = map(string)" in hcl
    assert "var.replication_destination_bucket_arns[each.key]" in hcl


def test_audit_bucket_is_never_force_destroyable():
    """An audit trail an operator can delete by re-running destroy is not an audit trail."""
    hcl = open(os.path.join(_ROOT, "modules", "governance-observability", "main.tf"),
               encoding="utf-8").read()
    audit = _norm(hcl[hcl.index('resource "aws_s3_bucket" "audit"'):])
    block = audit[:audit.index(chr(10) + "}")]
    assert "force_destroy = false" in block
    assert "object_lock_enabled = true" in block
