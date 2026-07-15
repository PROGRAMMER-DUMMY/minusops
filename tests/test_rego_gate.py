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
import re
import shutil
import subprocess

import pytest

import modules as module_registry
import rego_gate
import test_destructive_change_gate as dcg
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
# SEC-05 (extended, docs/g6_iam_extension_scope.md) -- aws_iam_role.assume_role_policy set
# directly as raw JSON, not only via data.aws_iam_policy_document. Real shape verified live
# before writing this (a genuine terraform plan, not assumed): a jsonencode()'d trust policy
# resolves as a plain JSON string in after.assume_role_policy, same json.unmarshal path as
# SEC-02's managed-resource case.
# ---------------------------------------------------------------------------

def _assume_role_policy(statement):
    return json.dumps({"Version": "2012-10-17", "Statement": [statement]})


def test_sec05_raw_flags_wildcard_principal():
    policy = _assume_role_policy({
        "Effect": "Allow", "Principal": {"AWS": "*"}, "Action": "sts:AssumeRole",
    })
    plan = _plan(resource_changes=[_rc("managed", "aws_iam_role", "aws_iam_role.bad",
                                        after={"assume_role_policy": policy})])
    result = rego_gate.evaluate(plan)
    findings = _findings_by_id(result, "SEC-05")
    assert len(findings) == 1
    assert "Wildcard Principal" in findings[0]["title"]


def test_sec05_raw_flags_cross_account_missing_external_id():
    """The verify-first item this scope required before coding: same-account-vs-cross-account
    cannot be determined by resolving data.aws_caller_identity (confirmed live -- it's a real
    STS call that fails under this repo's own dummy-credential testing), so this falls back to
    literal-ARN matching: any 12-digit-account-shaped ARN is treated as external."""
    policy = _assume_role_policy({
        "Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::999999999999:root"},
        "Action": "sts:AssumeRole",
    })
    plan = _plan(resource_changes=[_rc("managed", "aws_iam_role", "aws_iam_role.cross",
                                        after={"assume_role_policy": policy})])
    result = rego_gate.evaluate(plan)
    findings = _findings_by_id(result, "SEC-05")
    assert len(findings) == 1
    assert "Missing External ID" in findings[0]["title"]


def test_sec05_raw_clean_for_service_principal_and_scoped_cross_account_with_external_id():
    service_policy = _assume_role_policy({
        "Effect": "Allow", "Principal": {"Service": "glue.amazonaws.com"}, "Action": "sts:AssumeRole",
    })
    cross_account_ok = _assume_role_policy({
        "Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::999999999999:root"},
        "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"sts:ExternalId": "abc123"}},
    })
    plan = _plan(resource_changes=[
        _rc("managed", "aws_iam_role", "aws_iam_role.svc", after={"assume_role_policy": service_policy}),
        _rc("managed", "aws_iam_role", "aws_iam_role.cross_ok", after={"assume_role_policy": cross_account_ok}),
    ])
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "SEC-05") == []


def test_sec05_raw_unknown_assume_role_policy_routes_to_field_unresolved():
    plan = _plan(resource_changes=[_rc("managed", "aws_iam_role", "aws_iam_role.unknown",
                                        after_unknown={"assume_role_policy": True})])
    result = rego_gate.evaluate(plan)
    findings = _findings_by_id(result, "SEC-05")
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "field_unresolved"


# ---------------------------------------------------------------------------
# SEC-06 (new, docs/g6_iam_extension_scope.md) -- KMS key policy wide open
# ---------------------------------------------------------------------------

def test_sec06_flags_wildcard_principal_and_action():
    policy = json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": "*", "Action": "kms:*", "Resource": "*"},
    ]})
    plan = _plan(resource_changes=[_rc("managed", "aws_kms_key", "aws_kms_key.bad",
                                        after={"policy": policy})])
    result = rego_gate.evaluate(plan)
    assert len(_findings_by_id(result, "SEC-06")) == 1


def test_sec06_clean_when_principal_is_scoped():
    policy = json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::000000000000:root"}, "Action": "kms:*", "Resource": "*"},
    ]})
    plan = _plan(resource_changes=[_rc("managed", "aws_kms_key", "aws_kms_key.ok",
                                        after={"policy": policy})])
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "SEC-06") == []


def test_sec06_unset_policy_routes_to_field_unresolved_not_silent_pass():
    """The load-bearing, live-verified finding this whole rule design is built around
    (docs/g6_iam_extension_scope.md section 2): aws_kms_key.policy is schema computed=true, so
    a module that doesn't set it at all (the common real pattern -- storage-medallion-s3 does
    exactly this) resolves as after_unknown.policy=True, not a knowable "no policy" default.
    Must never be silently read as safe."""
    plan = _plan(resource_changes=[_rc("managed", "aws_kms_key", "aws_kms_key.unset",
                                        after_unknown={"policy": True})])
    result = rego_gate.evaluate(plan)
    findings = _findings_by_id(result, "SEC-06")
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "field_unresolved"


# ---------------------------------------------------------------------------
# SEC-07 (new, docs/g6_iam_extension_scope.md) -- S3 bucket policy allows public access
# ---------------------------------------------------------------------------

def test_sec07_flags_public_allow_statement():
    policy = json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": "*", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::b/*"},
    ]})
    plan = _plan(resource_changes=[_rc("managed", "aws_s3_bucket_policy", "aws_s3_bucket_policy.bad",
                                        after={"policy": policy})])
    result = rego_gate.evaluate(plan)
    assert len(_findings_by_id(result, "SEC-07")) == 1


def test_sec07_clean_when_principal_is_scoped():
    policy = json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::000000000000:root"}, "Action": "s3:GetObject", "Resource": "arn:aws:s3:::b/*"},
    ]})
    plan = _plan(resource_changes=[_rc("managed", "aws_s3_bucket_policy", "aws_s3_bucket_policy.ok",
                                        after={"policy": policy})])
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "SEC-07") == []


def test_sec07_unknown_policy_routes_to_field_unresolved_not_silent_pass():
    """Mirrors SEC-06's own load-bearing case: a bucket policy that interpolates its own
    bucket's ARN (the common create-together pattern) is unknown until apply -- proven live
    against a real plan in the integration test below, not just this hand-built fixture."""
    plan = _plan(resource_changes=[_rc("managed", "aws_s3_bucket_policy", "aws_s3_bucket_policy.unknown",
                                        after_unknown={"policy": True})])
    result = rego_gate.evaluate(plan)
    findings = _findings_by_id(result, "SEC-07")
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "field_unresolved"


# ---------------------------------------------------------------------------
# SEC-08 (new, docs/phase6_step1_authoring_scope.md section 4.2) -- Redshift Serverless
# workgroup publicly accessible
# ---------------------------------------------------------------------------

def test_sec08_flags_publicly_accessible_true():
    plan = _plan(resource_changes=[_rc("managed", "aws_redshiftserverless_workgroup", "aws_redshiftserverless_workgroup.bad",
                                        after={"publicly_accessible": True})])
    result = rego_gate.evaluate(plan)
    assert len(_findings_by_id(result, "SEC-08")) == 1


def test_sec08_clean_when_false():
    plan = _plan(resource_changes=[_rc("managed", "aws_redshiftserverless_workgroup", "aws_redshiftserverless_workgroup.ok",
                                        after={"publicly_accessible": False})])
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "SEC-08") == []


def test_sec08_clean_when_omitted_resolves_to_known_null_not_unresolved():
    """Live-verified (not assumed): publicly_accessible is optional but NOT computed, so an
    omitted attribute resolves to a KNOWN null in `after`, never after_unknown -- unlike SEC-06/
    SEC-07's policy fields. No field_unresolved case exists for this rule."""
    plan = _plan(resource_changes=[_rc("managed", "aws_redshiftserverless_workgroup", "aws_redshiftserverless_workgroup.unset",
                                        after={"publicly_accessible": None})])
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "SEC-08") == []


# ---------------------------------------------------------------------------
# SEC-09 (new, docs/phase6_step1_authoring_scope.md section 4.2) -- Subnet auto-assigns
# public IPs
# ---------------------------------------------------------------------------

def test_sec09_flags_map_public_ip_on_launch_true():
    plan = _plan(resource_changes=[_rc("managed", "aws_subnet", "aws_subnet.bad",
                                        after={"map_public_ip_on_launch": True})])
    result = rego_gate.evaluate(plan)
    assert len(_findings_by_id(result, "SEC-09")) == 1


def test_sec09_clean_when_omitted_resolves_to_known_false():
    """Live-verified: map_public_ip_on_launch is optional, NOT computed -- an omitted
    attribute resolves to a KNOWN false in `after`, never after_unknown."""
    plan = _plan(resource_changes=[_rc("managed", "aws_subnet", "aws_subnet.ok",
                                        after={"map_public_ip_on_launch": False})])
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "SEC-09") == []


# ---------------------------------------------------------------------------
# SEC-10 (new, docs/phase6_step1_authoring_scope.md section 4.2) -- S3 object ACL public
# ---------------------------------------------------------------------------

def test_sec10_flags_public_read_acl():
    plan = _plan(resource_changes=[_rc("managed", "aws_s3_object", "aws_s3_object.bad",
                                        after={"acl": "public-read"})])
    result = rego_gate.evaluate(plan)
    assert len(_findings_by_id(result, "SEC-10")) == 1


def test_sec10_flags_authenticated_read_acl():
    plan = _plan(resource_changes=[_rc("managed", "aws_s3_object", "aws_s3_object.bad2",
                                        after={"acl": "authenticated-read"})])
    result = rego_gate.evaluate(plan)
    assert len(_findings_by_id(result, "SEC-10")) == 1


def test_sec10_clean_when_private():
    plan = _plan(resource_changes=[_rc("managed", "aws_s3_object", "aws_s3_object.ok",
                                        after={"acl": "private"})])
    result = rego_gate.evaluate(plan)
    assert _findings_by_id(result, "SEC-10") == []


def test_sec10_unset_acl_routes_to_field_unresolved_not_silent_pass():
    """Live-verified: acl is schema optional AND computed -- an omitted attribute resolves to
    after_unknown.acl=True in a real plan, the same shape SEC-06/SEC-07 established for
    aws_kms_key.policy/aws_s3_bucket_policy.policy. Never silently read as "no ACL, private,
    safe" -- AWS's documented default being safe is not something this rule can verify from
    plan JSON alone."""
    plan = _plan(resource_changes=[_rc("managed", "aws_s3_object", "aws_s3_object.unset",
                                        after_unknown={"acl": True})])
    result = rego_gate.evaluate(plan)
    findings = _findings_by_id(result, "SEC-10")
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "field_unresolved"


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


# ---------------------------------------------------------------------------
# SEC-02 (extended, docs/g6_iam_extension_scope.md) -- Action == "*" alongside the pre-existing
# Resource == "*" check, same two statement shapes.
# ---------------------------------------------------------------------------

def test_sec02_flags_wildcard_action_in_data_source_statement():
    plan = _plan(prior_state_resources=[
        _data_resource("data.aws_iam_policy_document.p", "aws_iam_policy_document", {
            "statement": [{"actions": ["*"], "resources": ["arn:aws:s3:::bucket/*"]}],
        }),
    ])
    result = rego_gate.evaluate(plan)
    findings = _findings_by_id(result, "SEC-02")
    assert len(findings) == 1
    assert "Action" in findings[0]["title"]


def test_sec02_flags_wildcard_action_in_managed_policy_json():
    policy = json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "*", "Resource": "arn:aws:s3:::bucket/*"},
    ]})
    plan = _plan(resource_changes=[_rc("managed", "aws_iam_role_policy", "aws_iam_role_policy.p",
                                        after={"policy": policy})])
    result = rego_gate.evaluate(plan)
    findings = _findings_by_id(result, "SEC-02")
    assert len(findings) == 1
    assert "Action" in findings[0]["title"]


def test_sec02_clean_when_action_is_scoped():
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


@pytest.mark.skipif(TERRAFORM is None or OPA is None, reason="terraform and/or opa CLI not installed")
def test_real_plan_unset_kms_policy_is_unknown_not_a_knowable_default(tmp_path):
    """docs/g6_iam_extension_scope.md section 2's own load-bearing, live-verified finding: an
    aws_kms_key with no `policy` argument at all (schema computed=true; storage-medallion-s3
    does exactly this in this repo's real catalog) resolves as after.policy=None,
    after_unknown.policy=True -- AWS assigns a default at apply time this rule cannot see at
    plan time. Confirmed live here, not assumed from the schema alone, and asserted to route
    to BLOCK (field_unresolved), never read as "no policy set, nothing to check.\""""
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
''', encoding="utf-8")
    subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "init", "-input=false"],
                   capture_output=True, text=True, check=True)
    subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "plan", "-out=tfplan"],
                   capture_output=True, text=True, check=True)
    show = subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "show", "-json", "tfplan"],
                          capture_output=True, text=True, check=True)
    plan_json = json.loads(show.stdout)
    kms = next(rc for rc in plan_json["resource_changes"] if rc["type"] == "aws_kms_key")
    assert kms["change"]["after"].get("policy") is None
    assert kms["change"]["after_unknown"].get("policy") is True

    result = rego_gate.evaluate(plan_json, opa_bin=OPA)
    assert result["evaluation_failed"] is False
    findings = _findings_by_id(result, "SEC-06")
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "field_unresolved"


@pytest.mark.skipif(TERRAFORM is None or OPA is None, reason="terraform and/or opa CLI not installed")
def test_real_plan_s3_bucket_policy_both_ways_fresh_create_vs_preexisting_bucket(tmp_path):
    """Proof-bar item required by review before this could be called done: SEC-07 proven BOTH
    ways against real Terraform, not just hand-built fixtures.

    Fresh-create (the majority real pattern -- a bucket policy referencing the ARN of a bucket
    created in the SAME plan): docs/g6_iam_extension_scope.md section 2's own finding,
    confirmed live -- the whole assembled policy string is unknown until apply, because one of
    its interpolated inputs (the bucket's own ARN) is. Routes to field_unresolved, not a
    silent pass, even though every literal piece of the policy is fully known.

    Pre-existing bucket (a policy written against a bucket referenced by a literal ARN/name,
    not this plan's own resource attribute -- the real pattern once a bucket already exists in
    state, or is referenced by fixed convention): the policy resolves fully at plan time, and
    SEC-07 produces a real content verdict, not field_unresolved -- proven here with a genuine
    public-Allow statement so the "real verdict" path actually fires the finding, not just
    "didn't block."
    """
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

resource "aws_s3_bucket" "fresh" {
  bucket = "g6-probe-fresh-bucket"
}

resource "aws_s3_bucket_policy" "fresh_policy" {
  bucket = aws_s3_bucket.fresh.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = "*", Action = "s3:GetObject"
      Resource = "${aws_s3_bucket.fresh.arn}/*"
    }]
  })
}

resource "aws_s3_bucket_policy" "preexisting_policy" {
  bucket = "g6-probe-already-existing-bucket"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = "*", Action = "s3:GetObject"
      Resource = "arn:aws:s3:::g6-probe-already-existing-bucket/*"
    }]
  })
}
''', encoding="utf-8")
    subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "init", "-input=false"],
                   capture_output=True, text=True, check=True)
    subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "plan", "-out=tfplan"],
                   capture_output=True, text=True, check=True)
    show = subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "show", "-json", "tfplan"],
                          capture_output=True, text=True, check=True)
    plan_json = json.loads(show.stdout)

    fresh = next(rc for rc in plan_json["resource_changes"]
                 if rc["address"] == "aws_s3_bucket_policy.fresh_policy")
    assert fresh["change"]["after"].get("policy") is None
    assert fresh["change"]["after_unknown"].get("policy") is True

    preexisting = next(rc for rc in plan_json["resource_changes"]
                        if rc["address"] == "aws_s3_bucket_policy.preexisting_policy")
    assert preexisting["change"]["after_unknown"].get("policy") is not True
    assert preexisting["change"]["after"].get("policy") is not None

    result = rego_gate.evaluate(plan_json, opa_bin=OPA)
    assert result["evaluation_failed"] is False
    findings = _findings_by_id(result, "SEC-07")
    by_address = {f["resource"]: f for f in findings}
    assert len(findings) == 2
    assert by_address["aws_s3_bucket_policy.fresh_policy"]["finding_kind"] == "field_unresolved"
    assert by_address["aws_s3_bucket_policy.preexisting_policy"]["finding_kind"] == "standard"


# ---------------------------------------------------------------------------
# G6 16-module zero-false-positive proof, CODIFIED (docs/phase6_step5_teardown_scope.md section
# 1.3): `docs/g6_iam_extension_scope.md` section 7.2 proved this once, by hand, in prose -- real
# evidence, but never re-verified by CI the way G5's and G2's 16-module proofs are. This makes it
# a real, automated, parametrized regression test, same shape as those two, using the exact
# methodology section 7.2 named: real `terraform plan` + `show -json` with dummy AWS/Databricks
# credentials, NOT `mock_provider`/`terraform test` -- mock_provider supplies synthetic computed
# values that misrepresent the real after_unknown behavior SEC-06/07/08/09/10's fail-closed
# design depends on (confirmed live this session, not assumed).
# ---------------------------------------------------------------------------

_CALLER_IDENTITY_BLOCK_RE = re.compile(
    r'data\s+"aws_caller_identity"\s+"[A-Za-z0-9_]+"\s*\{\s*\}\s*\n?')
_CALLER_IDENTITY_REF_RE = re.compile(r'data\.aws_caller_identity\.[A-Za-z0-9_]+\.account_id')

_DUMMY_AWS_PROVIDER = '''
provider "aws" {
  region                       = "us-east-1"
  access_key                   = "test"
  secret_key                   = "test"
  skip_credentials_validation  = true
  skip_requesting_account_id   = true
  skip_metadata_api_check      = true
}
'''

_DUMMY_DATABRICKS_PROVIDER = '''
provider "databricks" {
  host       = "https://accounts.cloud.databricks.com"
  account_id = "00000000-0000-0000-0000-000000000000"
}
'''

# Real, disclosed, pre-existing gaps, not introduced by this test: each of these modules
# declares a data source that makes a genuine AWS API call at plan time no dummy credential can
# satisfy (confirmed live, not assumed) -- neither module can be planned standalone this way.
_CANNOT_PLAN_STANDALONE = {
    "orchestrator-stepfunctions": (
        "aws_sfn_state_machine triggers a real ValidateStateMachineDefinition AWS API call at "
        "plan time that dummy credentials cannot satisfy -- disclosed, pre-existing gap, not "
        "introduced by this test (docs/g6_iam_extension_scope.md section 7.2)."
    ),
    "networking-vpc": (
        "data.aws_availability_zones.available triggers a real EC2 DescribeAvailabilityZones "
        "API call at plan time -- confirmed live, dummy credentials get a real 401 AuthFailure "
        "from the actual AWS endpoint, not a local/offline failure. Real, disclosed gap found "
        "while codifying this test, not a new one this test introduces."
    ),
}

# Real, known, pre-existing findings this test's own first run confirmed against the actual
# catalog content (not asserted from prose) -- distinguishes "zero FALSE positives" (the real
# claim docs/g6_iam_extension_scope.md section 7.2 made) from "zero findings at all" (a
# stricter, wrong claim this test must not silently encode). Each entry is a real, explained,
# non-regression finding; a module producing anything ELSE (or missing one of these) is a real
# regression this test must catch, not paper over.
_KNOWN_REAL_FINDINGS = {
    "databricks-workspace": {
        # Pre-existing since Phase 3 (docs/g6_scope.md), independent of this session's IAM/KMS/S3
        # extension -- databricks-workspace's own root storage bucket has no lifecycle policy.
        ("COST-01", "aws_s3_bucket.root_storage_bucket"),
        # The exact finding docs/g6_iam_extension_scope.md section 7.2 predicted and confirmed:
        # a pre-existing Resource == "*" IAM finding, not a new false positive from the
        # Action == "*" extension.
        ("SEC-02", "aws_iam_role_policy.cross_account_role"),
    },
}


def _strip_caller_identity(content):
    """Terraform reads every declared data source at plan time regardless of whether its output
    is referenced -- data.aws_caller_identity is a real STS GetCallerIdentity call dummy
    credentials cannot satisfy, so the block itself must go, not just its references (the real
    bug docs/g6_iam_extension_scope.md section 7.2 found and fixed the first time this ran)."""
    content = _CALLER_IDENTITY_BLOCK_RE.sub("", content)
    content = _CALLER_IDENTITY_REF_RE.sub('"000000000000"', content)
    return content


def _uses_databricks(main_tf_content):
    return ('source  = "databricks/databricks"' in main_tf_content
            or 'source = "databricks/databricks"' in main_tf_content)


def _real_plan_for_module(module_id, tmp_path):
    """Real, standalone terraform plan for one catalog module, dummy AWS/Databricks credentials,
    no mock_provider. Returns (plan_json, None) on success, or (None, detail) if this module
    cannot be planned standalone this way."""
    src = os.path.join(dcg.MODULES_DIR, module_id)
    main_tf = open(os.path.join(src, "main.tf"), encoding="utf-8").read()
    dst = tmp_path / module_id
    shutil.copytree(src, dst)

    patched = _strip_caller_identity(main_tf)
    if patched != main_tf:
        (dst / "main.tf").write_text(patched, encoding="utf-8")

    var_lines = dcg._required_variable_lines(main_tf)
    (dst / "terraform.tfvars").write_text("\n".join(var_lines) + "\n", encoding="utf-8")

    providers = _DUMMY_AWS_PROVIDER
    if _uses_databricks(main_tf):
        providers += _DUMMY_DATABRICKS_PROVIDER
    (dst / "_test_providers.tf").write_text(providers, encoding="utf-8")

    init = subprocess.run([TERRAFORM, f"-chdir={dst}", "init", "-input=false"],
                          capture_output=True, text=True, timeout=120)
    if init.returncode != 0:
        return None, f"init failed: {(init.stderr or init.stdout).strip()[:2000]}"

    plan = subprocess.run([TERRAFORM, f"-chdir={dst}", "plan", "-out=tfplan", "-input=false"],
                         capture_output=True, text=True, timeout=120)
    if plan.returncode != 0:
        return None, f"plan failed: {(plan.stderr or plan.stdout).strip()[:2000]}"

    show = subprocess.run([TERRAFORM, f"-chdir={dst}", "show", "-json", "tfplan"],
                          capture_output=True, text=True, timeout=60)
    if show.returncode != 0:
        return None, f"show failed: {show.stderr.strip()[:2000]}"
    return json.loads(show.stdout), None


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
@pytest.mark.parametrize("module_id", [
    pytest.param(m["id"], marks=pytest.mark.skip(reason=_CANNOT_PLAN_STANDALONE[m["id"]]))
    if m["id"] in _CANNOT_PLAN_STANDALONE else m["id"]
    for m in module_registry.list_modules()
])
def test_g6_zero_false_positives_across_real_catalog(module_id, tmp_path):
    plan_json, err = _real_plan_for_module(module_id, tmp_path)
    assert plan_json is not None, f"{module_id}: could not produce a real standalone plan -- {err}"

    result = rego_gate.evaluate(plan_json)
    assert result["evaluation_failed"] is False, result

    non_unresolved = [f for f in result["findings"] if f.get("finding_kind") != "field_unresolved"]
    actual = {(f["id"], f["resource"]) for f in non_unresolved}
    expected = _KNOWN_REAL_FINDINGS.get(module_id, set())
    assert actual == expected, (
        f"{module_id}: G6's non-unresolved findings diverged from the known-real baseline -- "
        f"expected {expected}, got {actual}. New/missing entries here are a real regression "
        f"(a new false positive, or a previously-real finding that silently stopped firing), "
        f"not something to allowlist without re-verifying it against real catalog content."
    )
