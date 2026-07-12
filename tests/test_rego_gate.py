"""
rego_gate.py (G6, docs/g6_scope.md) evaluates SEC-*/COST-* rules over real Terraform plan JSON
via OPA/Rego (policy/g6/rules.rego), in shadow mode alongside optimize_analyzer.py's existing
regex-over-HCL rules -- see plan_gate.py's stage_plan()._g6_shadow_eval.

Classification tests use hand-constructed plan-JSON fixtures matching shapes verified live
against real Terraform before this file was written (not memorized/assumed): a real AWS
provider run confirmed `encrypted` on aws_redshift_cluster is declared type "string" (not
bool) so real plan JSON carries the STRING "true"/"false"; an unset encryption_info block
resolves to an empty LIST, never a missing key; after_unknown is a SPARSE structure (present,
true, only for genuinely unknown leaves); data sources live in `prior_state.values.root_module.
resources`, never in `resource_changes`; `resource_changes` itself is OMITTED entirely (not an
empty list) whenever a real plan has zero managed-resource changes -- caught by the first real-
terraform integration test below, which is why rego_gate.evaluate() treats an absent
`resource_changes` as "nothing managed to check," not malformed, and only blocks on a present-
but-wrong-typed one; and aws_iam_policy_document exposes a structured `.statement` field, not
just a `.json` string. These tests still require the real `opa`
binary -- Rego evaluation has no meaningful mock, same as G2/G5 needed the real terraform
binary for anything touching actual schema/plan behavior -- but not `terraform`, since the
plan-JSON shape itself is fixed data here. The real-terraform integration tests at the bottom
prove those hand-built shapes actually match what Terraform produces, the same "trust the
stub, prove it once against reality" split established throughout this session.
"""
import json
import os
import subprocess

import pytest

import rego_gate
import toolpath

OPA = toolpath.find_tool("opa")
TERRAFORM = toolpath.find_tool("terraform")

pytestmark = pytest.mark.skipif(OPA is None, reason="opa CLI not installed")


def _rc(mode, type_, address, after=None, after_unknown=None, actions=("create",)):
    return {
        "address": address, "mode": mode, "type": type_,
        "change": {"actions": list(actions), "after": after or {}, "after_unknown": after_unknown or {}},
    }


def _cfg_resource(address, type_, expressions=None, for_each_expression=None):
    cfg = {"address": address, "type": type_, "expressions": expressions or {}}
    if for_each_expression is not None:
        cfg["for_each_expression"] = for_each_expression
    return cfg


def _plan(resource_changes=(), config_resources=(), prior_state_resources=()):
    return {
        "resource_changes": list(resource_changes),
        "configuration": {"root_module": {"resources": list(config_resources)}},
        "prior_state": {"values": {"root_module": {"resources": list(prior_state_resources)}}},
    }


def _data_resource(address, type_, values):
    return {"address": address, "mode": "data", "type": type_, "values": values}


def _findings_by_id(result, rule_id):
    return [f for f in result["findings"] if f["id"] == rule_id]


# ---------------------------------------------------------------------------
# Fail-closed sweep -- Python-level input validation, no opa binary needed for these
# specifically (they short-circuit before ever invoking opa), but the module-level
# pytestmark still gates the whole file since most tests below do need it.
# ---------------------------------------------------------------------------

def test_evaluate_blocks_on_non_dict_input():
    result = rego_gate.evaluate("not a dict")
    assert result["evaluation_failed"] is True
    assert result["reason"] == "plan_malformed"


def test_evaluate_treats_absent_resource_changes_as_zero_managed_changes():
    # Verified live against real `terraform show -json`: the key is entirely OMITTED whenever
    # there are zero managed-resource changes (a data-source-only or genuine no-op plan), never
    # emitted as an empty list. Treating "absent" as malformed would over-block that common,
    # legitimate case -- only a wrong-typed `resource_changes` is a real malformed-shape signal.
    result = rego_gate.evaluate({"foo": "bar"})
    assert result["evaluation_failed"] is False
    assert result["findings"] == []


def test_evaluate_blocks_on_resource_changes_not_a_list():
    result = rego_gate.evaluate({"resource_changes": "oops"})
    assert result["evaluation_failed"] is True
    assert result["reason"] == "plan_malformed"


def test_evaluate_dir_blocks_when_terraform_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(rego_gate.toolpath, "find_tool", lambda name: None)
    result = rego_gate.evaluate_dir(str(tmp_path))
    assert result["evaluation_failed"] is True
    assert result["reason"] == "terraform_not_found"


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_evaluate_dir_blocks_when_no_saved_plan_exists(tmp_path):
    # No `terraform show -json tfplan` target in an uninitialized empty dir -- `terraform show`
    # itself fails (non-zero exit), which must surface as a block, not a silent empty result.
    result = rego_gate.evaluate_dir(str(tmp_path))
    assert result["evaluation_failed"] is True
    assert result["reason"] == "terraform_show_failed"


def test_evaluate_blocks_when_opa_not_found():
    result = rego_gate.evaluate({"resource_changes": []}, opa_bin=None)
    # opa_bin=None with nothing on PATH under test isolation -- force it explicitly instead of
    # relying on the real environment's PATH one way or the other.
    result = rego_gate.evaluate({"resource_changes": []}, opa_bin="/definitely/not/a/real/opa")
    assert result["evaluation_failed"] is True
    assert result["reason"] in ("opa_eval_failed", "opa_invocation_failed")


def test_evaluate_blocks_on_missing_policy_file(tmp_path):
    fake_policy = str(tmp_path / "does_not_exist.rego")
    result = rego_gate.evaluate({"resource_changes": []}, opa_bin=OPA or "opa", policy_path=fake_policy)
    assert result["evaluation_failed"] is True
    assert result["reason"] == "policy_not_found"


# ---------------------------------------------------------------------------
# SEC-01 / COST-01 -- S3 bucket missing public-access-block / lifecycle policy
# ---------------------------------------------------------------------------

def test_sec01_cost01_flag_unprotected_bucket():
    plan = _plan(resource_changes=[_rc("managed", "aws_s3_bucket", "aws_s3_bucket.b")])
    result = rego_gate.evaluate(plan)
    assert result["evaluation_failed"] is False
    assert len(_findings_by_id(result, "SEC-01")) == 1
    assert len(_findings_by_id(result, "COST-01")) == 1


def test_sec01_cost01_clean_when_siblings_reference_the_bucket():
    plan = _plan(
        resource_changes=[
            _rc("managed", "aws_s3_bucket", "aws_s3_bucket.b"),
            _rc("managed", "aws_s3_bucket_public_access_block", "aws_s3_bucket_public_access_block.b"),
            _rc("managed", "aws_s3_bucket_lifecycle_configuration", "aws_s3_bucket_lifecycle_configuration.b"),
        ],
        config_resources=[
            _cfg_resource("aws_s3_bucket.b", "aws_s3_bucket"),
            _cfg_resource("aws_s3_bucket_public_access_block.b", "aws_s3_bucket_public_access_block",
                          {"bucket": {"references": ["aws_s3_bucket.b.id", "aws_s3_bucket.b"]}}),
            _cfg_resource("aws_s3_bucket_lifecycle_configuration.b", "aws_s3_bucket_lifecycle_configuration",
                          {"bucket": {"references": ["aws_s3_bucket.b.id", "aws_s3_bucket.b"]}}),
        ],
    )
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "SEC-01") == []
    assert _findings_by_id(result, "COST-01") == []


def test_sec01_cost01_clean_for_for_each_siblings():
    """Real bug caught by the 16-module parity pass against storage-medallion-s3 (a genuine,
    correctly-configured for_each S3 module) and fixed in policy/g6/rules.rego: a for_each
    sibling's `bucket = each.value.id` attribute never resolves to the bucket's address inside
    `expressions.bucket.references` -- only the symbolic `each.value`/`each.value.id`. The real
    reference lives in the sibling config resource's own `for_each_expression.references`, and
    the bucket's OWN resource_changes address carries an index suffix (`aws_s3_bucket.zone
    ["bronze"]`) that must be stripped to compare against the base config address
    (`aws_s3_bucket.zone`) referenced there. Before the fix, this exact shape false-positived
    SEC-01/COST-01 on every for_each bucket despite genuinely correct siblings."""
    plan = _plan(
        resource_changes=[
            _rc("managed", "aws_s3_bucket", 'aws_s3_bucket.zone["bronze"]'),
            _rc("managed", "aws_s3_bucket_public_access_block", 'aws_s3_bucket_public_access_block.zone["bronze"]'),
            _rc("managed", "aws_s3_bucket_lifecycle_configuration", 'aws_s3_bucket_lifecycle_configuration.zone["bronze"]'),
        ],
        config_resources=[
            _cfg_resource("aws_s3_bucket.zone", "aws_s3_bucket"),
            _cfg_resource("aws_s3_bucket_public_access_block.zone", "aws_s3_bucket_public_access_block",
                          expressions={"bucket": {"references": ["each.value.id", "each.value"]}},
                          for_each_expression={"references": ["aws_s3_bucket.zone"]}),
            _cfg_resource("aws_s3_bucket_lifecycle_configuration.zone", "aws_s3_bucket_lifecycle_configuration",
                          expressions={"bucket": {"references": ["each.value.id", "each.value"]}},
                          for_each_expression={"references": ["aws_s3_bucket.zone"]}),
        ],
    )
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "SEC-01") == []
    assert _findings_by_id(result, "COST-01") == []


# ---------------------------------------------------------------------------
# SEC-03 -- Redshift `encrypted` is schema-typed STRING, not bool (verified live)
# ---------------------------------------------------------------------------

def test_sec03_flags_string_false():
    plan = _plan(resource_changes=[_rc("managed", "aws_redshift_cluster", "aws_redshift_cluster.c",
                                        after={"encrypted": "false"})])
    result = rego_gate.evaluate(plan)
    assert len(_findings_by_id(result, "SEC-03")) == 1
    assert _findings_by_id(result, "SEC-03")[0]["finding_kind"] == "standard"


def test_sec03_clean_on_string_true_not_boolean_true():
    # The real bug this regression locks down: a rule comparing against the Rego boolean
    # `true` would wrongly flag this, since real plan JSON carries the STRING "true".
    plan = _plan(resource_changes=[_rc("managed", "aws_redshift_cluster", "aws_redshift_cluster.c",
                                        after={"encrypted": "true"})])
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "SEC-03") == []


def test_sec03_unknown_value_routes_to_field_unresolved_not_silent_pass():
    plan = _plan(resource_changes=[_rc("managed", "aws_redshift_cluster", "aws_redshift_cluster.c",
                                        after={"encrypted": None}, after_unknown={"encrypted": True})])
    result = rego_gate.evaluate(plan)
    findings = _findings_by_id(result, "SEC-03")
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "field_unresolved"


# ---------------------------------------------------------------------------
# SEC-04 -- MSK: unset encryption_info is an empty LIST, never a missing key (verified live)
# ---------------------------------------------------------------------------

def test_sec04_flags_empty_encryption_info():
    plan = _plan(resource_changes=[_rc("managed", "aws_msk_cluster", "aws_msk_cluster.m",
                                        after={"encryption_info": []})])
    result = rego_gate.evaluate(plan)
    assert len(_findings_by_id(result, "SEC-04")) == 1


def test_sec04_clean_when_encryption_info_present():
    plan = _plan(resource_changes=[_rc("managed", "aws_msk_cluster", "aws_msk_cluster.m",
                                        after={"encryption_info": [{"encryption_at_rest_kms_key_arn": "arn:aws:kms:x"}]})])
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "SEC-04") == []


def test_sec04_unknown_routes_to_field_unresolved():
    plan = _plan(resource_changes=[_rc("managed", "aws_msk_cluster", "aws_msk_cluster.m",
                                        after_unknown={"encryption_info": True})])
    result = rego_gate.evaluate(plan)
    findings = _findings_by_id(result, "SEC-04")
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "field_unresolved"


# ---------------------------------------------------------------------------
# COST-02 -- Databricks cluster missing auto-termination
# ---------------------------------------------------------------------------

def test_cost02_flags_missing_autotermination():
    plan = _plan(resource_changes=[_rc("managed", "databricks_cluster", "databricks_cluster.d", after={})])
    result = rego_gate.evaluate(plan)
    assert len(_findings_by_id(result, "COST-02")) == 1


def test_cost02_clean_when_set():
    plan = _plan(resource_changes=[_rc("managed", "databricks_cluster", "databricks_cluster.d",
                                        after={"autotermination_minutes": 20})])
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "COST-02") == []


def test_cost02_unknown_routes_to_field_unresolved():
    plan = _plan(resource_changes=[_rc("managed", "databricks_cluster", "databricks_cluster.d",
                                        after_unknown={"autotermination_minutes": True})])
    result = rego_gate.evaluate(plan)
    findings = _findings_by_id(result, "COST-02")
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "field_unresolved"


# ---------------------------------------------------------------------------
# COST-03 -- EMR lacks Spot pricing; instance groups are LISTS of objects (verified live)
# ---------------------------------------------------------------------------

def test_cost03_flags_when_no_bid_price_anywhere():
    plan = _plan(resource_changes=[_rc("managed", "aws_emr_cluster", "aws_emr_cluster.e",
                                        after={"master_instance_group": [{"instance_type": "m5.xlarge", "bid_price": None}],
                                               "core_instance_group": [{"instance_type": "m5.xlarge", "bid_price": None}]})])
    result = rego_gate.evaluate(plan)
    assert len(_findings_by_id(result, "COST-03")) == 1


def test_cost03_clean_when_bid_price_set_on_any_group():
    plan = _plan(resource_changes=[_rc("managed", "aws_emr_cluster", "aws_emr_cluster.e",
                                        after={"master_instance_group": [{"instance_type": "m5.xlarge", "bid_price": None}],
                                               "core_instance_group": [{"instance_type": "m5.xlarge", "bid_price": "0.30"}]})])
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "COST-03") == []


def test_cost03_unknown_instance_group_routes_to_field_unresolved():
    plan = _plan(resource_changes=[_rc("managed", "aws_emr_cluster", "aws_emr_cluster.e",
                                        after={"master_instance_group": [{"instance_type": "m5.xlarge"}]},
                                        after_unknown={"core_instance_group": True})])
    result = rego_gate.evaluate(plan)
    findings = _findings_by_id(result, "COST-03")
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "field_unresolved"


# ---------------------------------------------------------------------------
# SEC-05a -- Databricks-canonical trust policy missing external_id (data source lives in
# prior_state, never resource_changes -- verified live)
# ---------------------------------------------------------------------------

def test_sec05a_flags_blank_external_id():
    plan = _plan(prior_state_resources=[
        _data_resource("data.databricks_aws_assume_role_policy.trust", "databricks_aws_assume_role_policy",
                       {"external_id": ""}),
    ])
    result = rego_gate.evaluate(plan)
    assert len(_findings_by_id(result, "SEC-05")) == 1


def test_sec05a_clean_when_external_id_supplied():
    plan = _plan(prior_state_resources=[
        _data_resource("data.databricks_aws_assume_role_policy.trust", "databricks_aws_assume_role_policy",
                       {"external_id": "real-id"}),
    ])
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "SEC-05") == []


# ---------------------------------------------------------------------------
# SEC-05b/c -- Hand-rolled cross-account trust policy: structured .statement (verified live,
# not the .json string)
# ---------------------------------------------------------------------------

def test_sec05b_flags_missing_external_id_condition():
    plan = _plan(prior_state_resources=[
        _data_resource("data.aws_iam_policy_document.cross_account", "aws_iam_policy_document", {
            "statement": [{
                "actions": ["sts:AssumeRole"],
                "principals": [{"type": "AWS", "identifiers": ["arn:aws:iam::123456789012:root"]}],
                "condition": [],
            }],
        }),
    ])
    result = rego_gate.evaluate(plan)
    assert len(_findings_by_id(result, "SEC-05")) == 1


def test_sec05c_flags_wildcard_principal():
    plan = _plan(prior_state_resources=[
        _data_resource("data.aws_iam_policy_document.cross_account", "aws_iam_policy_document", {
            "statement": [{
                "actions": ["sts:AssumeRole"],
                "principals": [{"type": "AWS", "identifiers": ["*"]}],
                "condition": [],
            }],
        }),
    ])
    result = rego_gate.evaluate(plan)
    # Both rules fire independently for a wildcard-with-no-condition statement -- matches the
    # original regex's own independent-checks behavior, not a bug.
    assert len(_findings_by_id(result, "SEC-05")) == 2


def test_sec05_clean_with_proper_condition_and_specific_principal():
    plan = _plan(prior_state_resources=[
        _data_resource("data.aws_iam_policy_document.cross_account", "aws_iam_policy_document", {
            "statement": [{
                "actions": ["sts:AssumeRole"],
                "principals": [{"type": "AWS", "identifiers": ["arn:aws:iam::123456789012:root"]}],
                "condition": [{"test": "StringEquals", "variable": "sts:ExternalId", "values": ["x"]}],
            }],
        }),
    ])
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "SEC-05") == []


# ---------------------------------------------------------------------------
# SEC-02 -- Wildcard IAM Resource: structured .statement.resources for data sources, and
# json.unmarshal of the .policy string for managed aws_iam_policy/aws_iam_role_policy
# ---------------------------------------------------------------------------

def test_sec02_flags_wildcard_resource_in_data_source_statement():
    plan = _plan(prior_state_resources=[
        _data_resource("data.aws_iam_policy_document.p", "aws_iam_policy_document", {
            "statement": [{"actions": ["s3:GetObject"], "resources": ["*"]}],
        }),
    ])
    result = rego_gate.evaluate(plan)
    assert len(_findings_by_id(result, "SEC-02")) == 1


def test_sec02_flags_wildcard_resource_in_managed_policy_json():
    policy = json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "s3:*", "Resource": "*"},
    ]})
    plan = _plan(resource_changes=[_rc("managed", "aws_iam_role_policy", "aws_iam_role_policy.p",
                                        after={"policy": policy})])
    result = rego_gate.evaluate(plan)
    assert len(_findings_by_id(result, "SEC-02")) == 1


def test_sec02_clean_when_resource_is_scoped():
    policy = json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::bucket/*"},
    ]})
    plan = _plan(resource_changes=[_rc("managed", "aws_iam_role_policy", "aws_iam_role_policy.p",
                                        after={"policy": policy})])
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "SEC-02") == []


def test_sec02_malformed_policy_json_fails_closed_not_silently_skipped():
    """The real fail-open risk this rule carries: json.unmarshal on a malformed .policy string
    is a Rego built-in error, which --strict-builtin-errors (required in rego_gate.py's opa
    eval invocation) turns into a hard evaluation failure instead of silently making this
    rule's match undefined for that resource. Proves the flag is actually load-bearing."""
    plan = _plan(resource_changes=[_rc("managed", "aws_iam_role_policy", "aws_iam_role_policy.p",
                                        after={"policy": "{not valid json"})])
    result = rego_gate.evaluate(plan)
    assert result["evaluation_failed"] is True
    assert result["reason"] == "opa_eval_failed"


def test_sec02_malformed_policy_json_WOULD_silently_pass_without_strict_builtin_errors():
    """Negative control proving the flag is doing real work, not decorative: the exact same
    malformed input, evaluated WITHOUT --strict-builtin-errors, produces zero findings instead
    of a failure -- the fail-open this session's whole discipline exists to catch, reproduced
    deliberately here so it can never come back silently if the flag is ever dropped."""
    import tempfile
    plan = _plan(resource_changes=[_rc("managed", "aws_iam_role_policy", "aws_iam_role_policy.p",
                                        after={"policy": "{not valid json"})])
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            json.dump(plan, tmp)
            tmp_path = tmp.name
        result = subprocess.run(
            [OPA, "eval", "-f", "json", "-i", tmp_path, "-d", rego_gate.POLICY_PATH, rego_gate.QUERY],
            capture_output=True, text=True,
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    findings = parsed["result"][0]["expressions"][0]["value"]
    assert findings == []  # the fail-open this repo's rego_gate.py deliberately closes


# ---------------------------------------------------------------------------
# Real terraform + opa integration: proves the hand-built shapes above actually match what
# Terraform produces, not just an assumption carried through the whole file.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(TERRAFORM is None or OPA is None, reason="terraform and/or opa CLI not installed")
def test_real_plan_encrypted_is_schema_typed_string_not_bool(tmp_path):
    (tmp_path / "main.tf").write_text('''
terraform {
  required_providers { aws = { source = "hashicorp/aws", version = ">= 5.0" } }
}
provider "aws" {
  region = "us-east-1"
  access_key = "test"
  secret_key = "test"
  skip_credentials_validation = true
  skip_requesting_account_id = true
  skip_metadata_api_check = true
  s3_use_path_style = true
}
resource "aws_redshift_cluster" "c" {
  cluster_identifier = "probe"
  database_name      = "probe"
  master_username    = "admin"
  master_password    = "ProbePassword123!"
  node_type          = "dc2.large"
  cluster_type       = "single-node"
  encrypted          = true
}
''', encoding="utf-8")
    subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "init", "-input=false"],
                   capture_output=True, text=True, check=True)
    subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "plan", "-out=tfplan"],
                   capture_output=True, text=True, check=True)
    show = subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "show", "-json", "tfplan"],
                          capture_output=True, text=True, check=True)
    plan_json = json.loads(show.stdout)
    result = rego_gate.evaluate(plan_json, opa_bin=OPA)
    assert result["evaluation_failed"] is False
    assert _findings_by_id(result, "SEC-03") == []  # encrypted=true must not be mis-flagged


@pytest.mark.skipif(TERRAFORM is None or OPA is None, reason="terraform and/or opa CLI not installed")
def test_real_plan_unknown_encrypted_routes_to_field_unresolved(tmp_path):
    """Proof-bar item 5 (docs/g6_scope.md section 4): a deliberately constructed real plan with
    a genuinely unknown-until-apply value, not a memorized/assumed shape. `encrypted` here is
    derived from a KMS key being created in the same plan, so its value cannot be known until
    apply -- confirmed live: this produces after.encrypted=None, after_unknown.encrypted=True,
    matching the sparse after_unknown structure assumed by rules.rego. Asserts the rule routes
    to BLOCK (field_unresolved), not a silent pass, exactly the case G2's own regressed-fixture
    proof-bar precedent (data.aws_region.current.name) was modeled on."""
    (tmp_path / "main.tf").write_text('''
terraform {
  required_providers { aws = { source = "hashicorp/aws", version = ">= 5.0" } }
}
provider "aws" {
  region = "us-east-1"
  access_key = "test"
  secret_key = "test"
  skip_credentials_validation = true
  skip_requesting_account_id = true
  skip_metadata_api_check = true
}
resource "aws_kms_key" "k" {
  description = "probe"
}
resource "aws_redshift_cluster" "c" {
  cluster_identifier = "probe"
  database_name      = "probe"
  master_username    = "admin"
  master_password    = "ProbePassword123!"
  node_type          = "dc2.large"
  cluster_type       = "single-node"
  encrypted          = length(aws_kms_key.k.key_id) > 0
}
''', encoding="utf-8")
    subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "init", "-input=false"],
                   capture_output=True, text=True, check=True)
    subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "plan", "-out=tfplan"],
                   capture_output=True, text=True, check=True)
    show = subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "show", "-json", "tfplan"],
                          capture_output=True, text=True, check=True)
    plan_json = json.loads(show.stdout)
    redshift = next(rc for rc in plan_json["resource_changes"] if rc["type"] == "aws_redshift_cluster")
    assert redshift["change"]["after"].get("encrypted") is None
    assert redshift["change"]["after_unknown"].get("encrypted") is True

    result = rego_gate.evaluate(plan_json, opa_bin=OPA)
    assert result["evaluation_failed"] is False
    findings = _findings_by_id(result, "SEC-03")
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "field_unresolved"


@pytest.mark.skipif(TERRAFORM is None or OPA is None, reason="terraform and/or opa CLI not installed")
def test_real_plan_data_sources_live_in_prior_state(tmp_path):
    (tmp_path / "main.tf").write_text('''
terraform {
  required_providers { aws = { source = "hashicorp/aws", version = ">= 5.0" } }
}
provider "aws" {
  region = "us-east-1"
  access_key = "test"
  secret_key = "test"
  skip_credentials_validation = true
  skip_requesting_account_id = true
  skip_metadata_api_check = true
  s3_use_path_style = true
}
data "aws_iam_policy_document" "wildcard" {
  statement {
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["*"]
  }
}
''', encoding="utf-8")
    subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "init", "-input=false"],
                   capture_output=True, text=True, check=True)
    subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "plan", "-out=tfplan"],
                   capture_output=True, text=True, check=True)
    show = subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "show", "-json", "tfplan"],
                          capture_output=True, text=True, check=True)
    plan_json = json.loads(show.stdout)
    assert plan_json.get("resource_changes", []) == []  # data-only plan: no resource_changes
    result = rego_gate.evaluate(plan_json, opa_bin=OPA)
    assert result["evaluation_failed"] is False
    assert len(_findings_by_id(result, "SEC-02")) == 1
