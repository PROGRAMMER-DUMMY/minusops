"""
PRD v8: governance and semantic-layer modules, Redshift ceilings, partition projection.

This exists because grill-me Pillars 12 and 13 interviewed operators about capabilities the
catalog could not build. A requirement captured and never consumed is worse than one never
asked about: it reads as a commitment in requirements.json and silently produces nothing at
synthesis.

The security tests here are the load-bearing ones, and two of them encode gotchas rather than
schemas:

  * Lake Formation registered while `IAMAllowedPrincipals` still holds default permissions is
    LF-TBAC that silently does nothing. Every tag grant is bypassed and the console shows
    green.
  * A cross-account consumer role without an `sts:ExternalId` condition is the confused-deputy
    problem: any principal who learns the role ARN can assume it.

Depends on: core/generation/modules.py, modules/*/main.tf
Shells out to: nothing (no terraform, no AWS)
Used by: nothing (pytest entry point)
"""
import os
import re

import pytest

import modules

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NEW_MODULES = ("governance-lakeformation", "security-iam-scoped",
               "dbt-semantic-layer", "cube-semantic-layer")


def _hcl(module_id, filename="main.tf"):
    with open(os.path.join(ROOT, "modules", module_id, filename), encoding="utf-8") as f:
        return f.read()


# --- FR-05: the catalog can build what the interview asks about -----------------------

@pytest.mark.parametrize("module_id", NEW_MODULES)
def test_the_module_exists_on_disk(module_id):
    assert os.path.exists(os.path.join(ROOT, "modules", module_id, "main.tf"))


@pytest.mark.parametrize("module_id", NEW_MODULES)
def test_the_module_is_registered(module_id):
    assert modules.get_module(module_id) is not None


def test_the_registry_is_still_valid():
    assert modules.validate_modules() == []


def test_every_registered_module_is_packaged_into_the_wheel():
    """The general guard, not four one-off assertions. pyproject's data-files list is
    hand-maintained and does not auto-discover; that is exactly how 5 of 14 modules went
    missing from a wheel before, and the module worked perfectly from a source checkout the
    whole time."""
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        pyproject = f.read()

    missing = [m["id"] for m in modules.MODULES
               if f'"modules/{m["id"]}"' not in pyproject]
    assert not missing, f"registered but not in [tool.setuptools.data-files]: {missing}"


def test_lake_formation_and_dbt_are_matched_together():
    """AC-01. The exact sentence Pillar 12 and 13 produce between them."""
    matched = [m["id"] for m in modules.match_modules(
        "Lake Formation row level security with dbt semantic layer")]

    assert "governance-lakeformation" in matched
    assert "dbt-semantic-layer" in matched


def test_pii_column_masking_finds_the_governance_module():
    matched = [m["id"] for m in modules.match_modules(
        "PII column masking and row filters on the gold tables")]
    assert "governance-lakeformation" in matched


def test_a_headless_semantic_layer_finds_cube():
    matched = [m["id"] for m in modules.match_modules(
        "headless semantic layer with a REST and GraphQL metrics API")]
    assert "cube-semantic-layer" in matched


def test_cross_account_analytics_access_finds_the_scoped_iam_module():
    matched = [m["id"] for m in modules.match_modules(
        "cross account read only access for the BI team with least privilege")]
    assert "security-iam-scoped" in matched


def test_a_plain_lakehouse_request_does_not_outrank_what_it_asked_for():
    """`match_modules` is a weak-signal RANKER, not a selector: any token overlap scores +1,
    which is why `databricks-workspace` also shows up here on "delta lake" overlapping
    "data lake". Absence is therefore the wrong assertion. What must hold is ORDER -- a
    request that names Glue and Athena must not rank a governance module it never mentioned
    above them, or the ranking stops being worth reading."""
    ranked = [m["id"] for m in modules.match_modules(
        "medallion data lake with glue etl and athena")]

    for named in ("compute-glue-etl", "storage-medallion-s3", "query-athena"):
        assert ranked.index(named) < ranked.index("governance-lakeformation")
    assert "cube-semantic-layer" not in ranked


def test_governance_outranks_the_lakehouse_modules_when_actually_asked_for():
    """The other direction of the same property."""
    ranked = [m["id"] for m in modules.match_modules(
        "medallion lakehouse with PII column masking and row level security on gold")]

    assert ranked[0] == "governance-lakeformation"


@pytest.mark.parametrize("module_id", NEW_MODULES)
def test_no_emoji_in_the_new_modules(module_id):
    """NFR-01."""
    directory = os.path.join(ROOT, "modules", module_id)
    for base, _dirs, files in os.walk(directory):
        if ".terraform" in base:
            continue
        for name in files:
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            assert all(ord(ch) < 0x2190 for ch in text), f"non-ASCII symbol in {name}"


# --- FR-01A: Lake Formation -----------------------------------------------------------

def test_lake_formation_declares_the_four_specified_resources():
    hcl = _hcl("governance-lakeformation")
    for resource in ("aws_lakeformation_data_lake_settings", "aws_lakeformation_resource",
                     "aws_lakeformation_lf_tag", "aws_lakeformation_permissions"):
        assert f'resource "{resource}"' in hcl


def test_default_permissions_are_emptied_or_tbac_does_nothing():
    """THE Lake Formation footgun. While `IAMAllowedPrincipals` holds ALL on new databases
    and tables, every LF-Tag grant is bypassed: IAM alone still opens the data, the console
    shows the tags attached, and nobody discovers it until an audit."""
    hcl = _hcl("governance-lakeformation")

    settings = hcl.split('resource "aws_lakeformation_data_lake_settings"')[1]
    settings = settings.split("\nresource ")[0]

    # Present AND empty. Populated grants the compatibility principal something; absent
    # leaves the AWS default in place. Only `{}` revokes. Checked structurally rather than by
    # searching for the string "IAMAllowedPrincipals", which appears legitimately in the
    # comment explaining why these blocks are here.
    for block in ("create_database_default_permissions", "create_table_default_permissions"):
        assert re.search(block + r"\s*\{\s*\}", settings), f"{block} must be present and empty"


def test_lake_formation_takes_the_gold_bucket_and_admins_as_inputs():
    hcl = _hcl("governance-lakeformation")
    for variable in ("gold_bucket_arn", "admin_iam_role_arns", "lf_tags", "tags"):
        assert f'variable "{variable}"' in hcl


def test_the_registered_resource_uses_a_service_linked_or_named_role():
    """Registering with `use_service_linked_role = false` and no role_arn is a plan that
    applies and a lake that cannot be read."""
    hcl = _hcl("governance-lakeformation")
    assert "use_service_linked_role" in hcl or "role_arn" in hcl


# --- FR-01B: scoped IAM ---------------------------------------------------------------

def test_the_consumer_role_requires_an_external_id():
    """Confused deputy. Without this condition, anyone who learns the role ARN can assume
    it -- and role ARNs are not secrets; they appear in logs and error messages."""
    hcl = _hcl("security-iam-scoped")
    assert "sts:ExternalId" in hcl


def test_the_policy_grants_no_wildcard_resource():
    """No statement may name `*` as its RESOURCE. `gold_prefixes = ["*"]` is a different
    thing -- a key prefix inside one named bucket -- and conflating them would forbid the
    only sane default for "all objects in the bucket you gave me"."""
    hcl = _hcl("security-iam-scoped")

    for line in hcl.splitlines():
        stripped = line.strip()
        if stripped.startswith("resources"):
            assert '"*"' not in stripped, f"wildcard resource: {stripped}"
            assert "var.gold_bucket_arn" in stripped or "var." in stripped or "local." in stripped


def test_kms_decrypt_is_scoped_to_the_given_key():
    hcl = _hcl("security-iam-scoped")
    assert "kms:Decrypt" in hcl
    assert "var.kms_key_arn" in hcl


def test_scoped_iam_takes_the_specified_inputs():
    hcl = _hcl("security-iam-scoped")
    for variable in ("name_prefix", "gold_bucket_arn", "kms_key_arn",
                     "trusted_external_principals"):
        assert f'variable "{variable}"' in hcl


# --- FR-02: semantic layers -----------------------------------------------------------

def test_dbt_scaffolds_the_specified_manifests():
    directory = os.path.join(ROOT, "modules", "dbt-semantic-layer")
    for artifact in ("models/semantic_models.yml", "models/metrics.yml", "dbt_project.yml"):
        assert os.path.exists(os.path.join(directory, *artifact.split("/"))), artifact


def test_the_dbt_semantic_model_declares_entities_dimensions_and_measures():
    """A semantic model without all three is not a semantic model; it is a view with
    aspirations, and MetricFlow will not compile it."""
    yaml = pytest.importorskip("yaml")
    with open(os.path.join(ROOT, "modules", "dbt-semantic-layer", "models",
                           "semantic_models.yml"), encoding="utf-8") as f:
        doc = yaml.safe_load(f)

    model = doc["semantic_models"][0]
    assert model["entities"] and model["dimensions"] and model["measures"]


def test_the_dbt_metrics_file_is_valid_yaml_with_a_named_metric():
    yaml = pytest.importorskip("yaml")
    with open(os.path.join(ROOT, "modules", "dbt-semantic-layer", "models", "metrics.yml"),
              encoding="utf-8") as f:
        doc = yaml.safe_load(f)

    assert doc["metrics"][0]["name"]
    assert doc["metrics"][0]["type"]


def test_cube_scaffolds_a_schema_and_a_config():
    directory = os.path.join(ROOT, "modules", "cube-semantic-layer")
    assert os.path.isdir(os.path.join(directory, "cube", "schema"))
    assert os.path.exists(os.path.join(directory, "cube", "cube.js"))


def test_cube_declares_a_pre_aggregation():
    """The reason to run Cube at all is the pre-aggregation cache. A schema without one is a
    proxy that re-scans the lake on every dashboard refresh."""
    schema_dir = os.path.join(ROOT, "modules", "cube-semantic-layer", "cube", "schema")
    text = "".join(open(os.path.join(schema_dir, n), encoding="utf-8").read()
                   for n in os.listdir(schema_dir))
    assert "preAggregations" in text or "pre_aggregations" in text


def test_the_semantic_modules_carry_terraform_too():
    """Every registered module must have main.tf -- validate_modules() enforces it, and a
    scaffold-only module would break the registry."""
    for module_id in ("dbt-semantic-layer", "cube-semantic-layer"):
        assert _hcl(module_id).strip()


# --- FR-03: Redshift ceilings ---------------------------------------------------------

def test_redshift_declares_a_capacity_ceiling():
    """AC-02. base_capacity is a floor. Without max_capacity the workgroup scales RPUs with
    no upper bound, which is the one unbounded spend knob in a repo whose cost doctrine is
    otherwise strict everywhere."""
    hcl = _hcl("consumption-redshift-serverless")
    assert 'variable "max_capacity"' in hcl
    # Structure, not spacing -- terraform fmt aligns `=` and the column shifts whenever a
    # longer attribute name is added next to it.
    assert re.search(r"max_capacity\s*=\s*var\.max_capacity", hcl)


def test_the_ceiling_cannot_be_set_below_the_floor():
    """A max below the base is a plan that applies and a workgroup that will not start."""
    hcl = _hcl("consumption-redshift-serverless")
    block = hcl.split('variable "max_capacity"')[1].split("\nvariable ")[0]
    assert "validation" in block
    assert "base_capacity_rpu" in block


def test_redshift_declares_a_usage_limit():
    hcl = _hcl("consumption-redshift-serverless")
    assert 'resource "aws_redshiftserverless_usage_limit"' in hcl


def test_the_usage_limit_does_not_silently_deactivate_by_default():
    """`deactivate` stops the warehouse mid-quarter-close. The default has to be the one
    that pages someone rather than the one that takes BI offline without warning."""
    hcl = _hcl("consumption-redshift-serverless")
    block = hcl.split('variable "usage_limit_breach_action"')[1].split("\nvariable ")[0]
    assert 'default     = "log"' in block or 'default = "log"' in block


def test_the_existing_redshift_inputs_are_unchanged():
    """base_capacity_rpu and publicly_accessible are already referenced by generated stacks."""
    hcl = _hcl("consumption-redshift-serverless")
    assert 'variable "base_capacity_rpu"' in hcl
    assert 'variable "publicly_accessible"' in hcl


def test_the_redshift_registry_entry_lists_the_new_inputs():
    entry = modules.get_module("consumption-redshift-serverless")
    assert "max_capacity" in entry["inputs"]


# --- FR-04: Athena partition projection -----------------------------------------------

def test_athena_declares_partition_projection():
    """AC-03. MSCK REPAIR TABLE gets slower with every partition added; projection resolves
    partitions in memory and never scans the prefix at all."""
    hcl = _hcl("query-athena")
    for prop in ("projection.enabled", "projection.date.type", "projection.date.range",
                 "projection.date.format", "storage.location.template"):
        assert prop in hcl, prop


def test_projection_is_enabled_not_merely_configured():
    hcl = _hcl("query-athena")
    assert re.search(r'"projection\.enabled"\s*=\s*"true"', hcl)


def test_the_projected_table_is_opt_in():
    """A generic module cannot know a customer's columns. Creating a table unconditionally
    would emit a schema we invented and call it theirs."""
    hcl = _hcl("query-athena")
    table = hcl.split('resource "aws_glue_catalog_table"')[1].split("\nresource ")[0]
    assert "count" in table


def test_the_location_template_uses_the_gold_bucket_variable():
    """A hardcoded bucket in a location template points every generated stack at one lake."""
    hcl = _hcl("query-athena")
    assert "var.gold_bucket" in hcl.split("storage.location.template")[1][:200]


# --- FR-05: the interview points at the command that exists ---------------------------

def test_pillar_14_names_the_five_hop_proving_command():
    """AC-04. Pillar 14 describes DQ validation and quarantine checks, which is the five-hop
    harness; `minusctl seed --execute` is the older three-hop form and does neither."""
    with open(os.path.join(ROOT, ".agents", "skills", "grill-me", "SKILL.md"),
              encoding="utf-8") as f:
        skill = f.read()

    pillar = skill.split("| **14** |")[1].split("\n")[0]
    assert "minusctl prove --execute" in pillar
    assert "minusctl seed --execute" not in pillar


def test_every_module_the_interview_names_exists_in_the_catalog():
    """The defect this whole PRD closes: an interview that gathers requirements for modules
    synthesis cannot build writes a promise into requirements.json and produces nothing."""
    import re as _re

    with open(os.path.join(ROOT, ".agents", "skills", "grill-me", "SKILL.md"),
              encoding="utf-8") as f:
        skill = f.read()

    registered = {m["id"] for m in modules.MODULES}
    # Backticked identifiers in the pillar table's module column that look like module ids.
    named = set(_re.findall(r"`([a-z0-9]+(?:-[a-z0-9]+)+)`", skill))
    module_shaped = {n for n in named
                     if not n.endswith(".py") and "/" not in n and n.count("-") >= 1}

    unbuildable = sorted(n for n in module_shaped
                         if n not in registered and os.path.isdir(
                             os.path.join(ROOT, "modules")) and n.split("-")[0] in {
                                 "governance", "security", "dbt", "cube", "query", "storage",
                                 "compute", "orchestrator", "ingestion", "ingest", "streaming",
                                 "warehouse", "consumption", "dq", "schema", "table",
                                 "compaction", "metadata", "networking", "speed"})
    assert not unbuildable, f"grill-me names modules the catalog cannot build: {unbuildable}"


# --- Regression: the guard must be expressible Terraform ------------------------------

def test_the_external_id_precondition_references_real_configuration():
    """`condition = false` is rejected by `terraform validate`: "The condition expression
    must refer to at least one object from elsewhere in the configuration, or else its result
    would not be checking anything." Encoding the rule in `count` and leaving the condition
    constant produced HCL that passed every structural test here and failed on first plan."""
    hcl = _hcl("security-iam-scoped")

    block = hcl.split("precondition {")[1].split("}")[0]
    assert "var.external_id" in block
    assert "var.trusted_external_principals" in block
    assert "condition     = false" not in block


@pytest.mark.slow
@pytest.mark.parametrize("module_id", NEW_MODULES)
def test_the_module_is_valid_terraform(module_id, tmp_path):
    """The check that would have caught the precondition bug. Marked slow because it runs a
    real `terraform init`, which on Windows copies the provider rather than symlinking it."""
    import shutil
    import subprocess
    import sys

    sys.path.insert(0, os.path.join(ROOT, "core", "reporting"))
    import toolpath

    terraform = toolpath.find_tool("terraform")
    if not terraform:
        pytest.skip("terraform not on PATH")

    work = tmp_path / module_id
    shutil.copytree(os.path.join(ROOT, "modules", module_id), work,
                    ignore=shutil.ignore_patterns(".terraform*"))
    init = subprocess.run([terraform, f"-chdir={work}", "init", "-backend=false",
                           "-input=false", "-no-color"], capture_output=True, text=True)
    assert init.returncode == 0, init.stderr or init.stdout

    result = subprocess.run([terraform, f"-chdir={work}", "validate", "-no-color"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
