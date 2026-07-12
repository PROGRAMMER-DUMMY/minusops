"""
intent_assertions.py (Phase 4, docs/phase4_scope.md) is ADVISORY ONLY -- nothing here blocks
generation, plan, or apply; see plan_gate.py/report wiring for confirmation nothing calls these
findings as a gate.

Two kinds of proof, per the approved scope's condition 2 ("prove it both directions, like G6's
rules"): for every mapped blueprint control, a clean-case fixture proving the check PASSES when
the control is satisfied, AND a deliberately-broken fixture proving it FIRES when violated. A
mapping that can't catch a real violation is decoration, not a control.

The real demo blueprint's own generated Terraform (terraform_generator.generate_aws_data_
pipeline) already gives real, non-hypothetical evidence for both directions across the six
mapped controls combined -- verified live against a real plan (dummy AWS credentials,
aws_sfn_state_machine neutralized since it needs real STS access for its own plan-time
validation call, same disclosed limitation as orchestrator-stepfunctions, unrelated to any of
these six checks): four controls (SSE-KMS, public access blocks, versioning/lifecycle) pass
genuinely; two (CloudWatch alarms+log retention, budget+anomaly detection) genuinely violate
(the blueprint's own claim over-promises what's actually generated -- a real, previously
invisible gap this checker exists to surface); and the "Per-service IAM roles with scoped
resource permissions" control is genuinely UNRESOLVABLE from that plan alone, because the
policy JSON references a not-yet-created bucket's ARN (after_unknown.policy == True) -- a real
bug caught in this file's own first draft (silently falling through to "satisfied" when a
policy's content was merely unresolved, not actually checked) before it ever shipped.
"""
import json

import pytest

import intent_assertions
import blueprints
import toolpath

TERRAFORM = toolpath.find_tool("terraform")


def _rc(rtype, name, after=None, after_unknown=None, module_address=None, address=None):
    addr = address or f"{rtype}.{name}"
    if module_address:
        addr = f"{module_address}.{addr}"
    return {
        "address": addr, "mode": "managed", "type": rtype, "name": name,
        "module_address": module_address,
        "change": {"actions": ["create"], "after": after or {}, "after_unknown": after_unknown or {}},
    }


def _cfg(address, rtype, expressions=None, for_each_expression=None):
    cfg = {"address": address, "type": rtype, "expressions": expressions or {}}
    if for_each_expression is not None:
        cfg["for_each_expression"] = for_each_expression
    return cfg


def _plan(resource_changes=(), config_resources=()):
    return {
        "resource_changes": list(resource_changes),
        "configuration": {"root_module": {"resources": list(config_resources)}},
    }


def _by_kind(findings, control):
    for f in findings:
        if f["resource"] == control:
            return f["finding_kind"]
    return None


# ---------------------------------------------------------------------------
# 1. Module presence
# ---------------------------------------------------------------------------

def test_module_presence_flags_missing_selected_module():
    plan = _plan(resource_changes=[_rc("aws_s3_bucket", "zone", module_address="module.storage_medallion_s3")])
    decision = {"selected_modules": ["storage-medallion-s3", "compaction-glue"]}
    findings = intent_assertions.check_module_presence(decision, plan)
    assert len(findings) == 1
    assert findings[0]["resource"] == "compaction-glue"


def test_module_presence_clean_when_all_selected_modules_present():
    plan = _plan(resource_changes=[
        _rc("aws_s3_bucket", "zone", module_address="module.storage_medallion_s3"),
        _rc("aws_iam_role", "compact", module_address="module.compaction_glue"),
    ])
    decision = {"selected_modules": ["storage-medallion-s3", "compaction-glue"]}
    assert intent_assertions.check_module_presence(decision, plan) == []


def test_module_presence_handles_malformed_plan_fail_closed():
    findings = intent_assertions.check_module_presence({"selected_modules": ["x"]}, {"resource_changes": "oops"})
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "evaluation_failed"


def test_check_controls_fails_closed_on_malformed_plan():
    """A real gap the fail-closed sweep itself caught: check_controls originally had no
    malformed-plan guard at all -- a wrong-typed resource_changes would just read as an empty
    plan (managed_only([]) via the `rc or []` fallback), silently treating "couldn't check" as
    "nothing to check" rather than blocking the assertion pass. Fixed via the shared
    _plan_malformed_finding() guard, same as check_module_presence already had."""
    findings = intent_assertions.check_controls(
        _blueprint_with_controls(["SSE-KMS for storage and logs"]), {"resource_changes": "oops"})
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "evaluation_failed"


def test_check_numerics_fails_closed_on_malformed_plan():
    requirements = {"non_functional": {"budget": "$500/month"}}
    findings = intent_assertions.check_numerics(requirements, {"resource_changes": "oops"})
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "evaluation_failed"


def test_evaluate_reports_evaluation_failed_once_not_per_claim_class():
    """evaluate() checks plan validity ONCE upfront rather than surfacing the same
    evaluation_failed finding three times (once per claim class) when all three inputs are
    present -- a single, unambiguous verdict, same shape discipline as rego_gate.py."""
    result = intent_assertions.evaluate(
        requirements={"non_functional": {"budget": "$1/month"}},
        architecture_decision={"selected_modules": ["x"]},
        blueprint=_blueprint_with_controls(["SSE-KMS for storage and logs"]),
        plan_json={"resource_changes": "oops"},
    )
    assert result["evaluation_failed"] is True
    assert len(result["findings"]) == 1
    assert result["findings"][0]["finding_kind"] == "evaluation_failed"


def test_evaluate_reports_evaluation_failed_false_on_a_genuine_clean_plan():
    result = intent_assertions.evaluate(plan_json=_plan())
    assert result["evaluation_failed"] is False
    assert result["advisory"] is True


# ---------------------------------------------------------------------------
# 2. Control mapping -- both directions, per control
# ---------------------------------------------------------------------------

_DEMO_CONTROLS = [
    "SSE-KMS for storage and logs",
    "S3 public access blocks",
    "Versioning and lifecycle policies",
    "Per-service IAM roles with scoped resource permissions",
    "CloudWatch alarms and log retention",
    "Budget and anomaly detection hooks",
    "Terraform plan hash approval before apply",
]


def _blueprint_with_controls(controls):
    return {"controls": controls}


def test_sse_kms_clean_when_kms_and_sse_present():
    plan = _plan(resource_changes=[
        _rc("aws_s3_bucket", "b"),
        _rc("aws_kms_key", "k"),
        _rc("aws_s3_bucket_server_side_encryption_configuration", "b"),
    ])
    findings = intent_assertions.check_controls(_blueprint_with_controls(["SSE-KMS for storage and logs"]), plan)
    assert findings == []


def test_sse_kms_violated_when_no_kms():
    plan = _plan(resource_changes=[_rc("aws_s3_bucket", "b")])
    findings = intent_assertions.check_controls(_blueprint_with_controls(["SSE-KMS for storage and logs"]), plan)
    assert len(findings) == 1 and findings[0]["finding_kind"] == "standard"


def test_sse_kms_not_applicable_with_no_storage():
    plan = _plan(resource_changes=[_rc("aws_glue_job", "j")])
    assert intent_assertions.check_controls(_blueprint_with_controls(["SSE-KMS for storage and logs"]), plan) == []


def test_public_access_block_clean_when_sibling_references_bucket():
    plan = _plan(
        resource_changes=[_rc("aws_s3_bucket", "b"), _rc("aws_s3_bucket_public_access_block", "b")],
        config_resources=[
            _cfg("aws_s3_bucket.b", "aws_s3_bucket"),
            _cfg("aws_s3_bucket_public_access_block.b", "aws_s3_bucket_public_access_block",
                 expressions={"bucket": {"references": ["aws_s3_bucket.b.id", "aws_s3_bucket.b"]}}),
        ],
    )
    findings = intent_assertions.check_controls(_blueprint_with_controls(["S3 public access blocks"]), plan)
    assert findings == []


def test_public_access_block_violated_when_no_sibling():
    plan = _plan(resource_changes=[_rc("aws_s3_bucket", "b")], config_resources=[_cfg("aws_s3_bucket.b", "aws_s3_bucket")])
    findings = intent_assertions.check_controls(_blueprint_with_controls(["S3 public access blocks"]), plan)
    assert len(findings) == 1


def test_public_access_block_clean_for_each(tmp_path=None):
    """The for_each shape verified live this session (G6's own caught bug): the reference lives
    in for_each_expression, not expressions.bucket.references (which only holds each.value)."""
    plan = _plan(
        resource_changes=[_rc("aws_s3_bucket", "zone", address='aws_s3_bucket.zone["bronze"]'),
                           _rc("aws_s3_bucket_public_access_block", "zone", address='aws_s3_bucket_public_access_block.zone["bronze"]')],
        config_resources=[
            _cfg("aws_s3_bucket.zone", "aws_s3_bucket"),
            _cfg("aws_s3_bucket_public_access_block.zone", "aws_s3_bucket_public_access_block",
                 expressions={"bucket": {"references": ["each.value.id", "each.value"]}},
                 for_each_expression={"references": ["aws_s3_bucket.zone"]}),
        ],
    )
    findings = intent_assertions.check_controls(_blueprint_with_controls(["S3 public access blocks"]), plan)
    assert findings == []


def test_versioning_lifecycle_clean_when_both_present():
    plan = _plan(
        resource_changes=[_rc("aws_s3_bucket", "b"), _rc("aws_s3_bucket_versioning", "b"),
                           _rc("aws_s3_bucket_lifecycle_configuration", "b")],
        config_resources=[
            _cfg("aws_s3_bucket.b", "aws_s3_bucket"),
            _cfg("aws_s3_bucket_versioning.b", "aws_s3_bucket_versioning",
                 expressions={"bucket": {"references": ["aws_s3_bucket.b.id", "aws_s3_bucket.b"]}}),
            _cfg("aws_s3_bucket_lifecycle_configuration.b", "aws_s3_bucket_lifecycle_configuration",
                 expressions={"bucket": {"references": ["aws_s3_bucket.b.id", "aws_s3_bucket.b"]}}),
        ],
    )
    findings = intent_assertions.check_controls(_blueprint_with_controls(["Versioning and lifecycle policies"]), plan)
    assert findings == []


def test_versioning_lifecycle_violated_when_lifecycle_missing():
    plan = _plan(
        resource_changes=[_rc("aws_s3_bucket", "b"), _rc("aws_s3_bucket_versioning", "b")],
        config_resources=[
            _cfg("aws_s3_bucket.b", "aws_s3_bucket"),
            _cfg("aws_s3_bucket_versioning.b", "aws_s3_bucket_versioning",
                 expressions={"bucket": {"references": ["aws_s3_bucket.b.id", "aws_s3_bucket.b"]}}),
        ],
    )
    findings = intent_assertions.check_controls(_blueprint_with_controls(["Versioning and lifecycle policies"]), plan)
    assert len(findings) == 1


def test_scoped_iam_clean_when_policy_resolved_and_scoped():
    policy = json.dumps({"Statement": [{"Effect": "Allow", "Action": "s3:GetObject",
                                          "Resource": "arn:aws:s3:::bucket/*"}]})
    plan = _plan(resource_changes=[
        _rc("aws_iam_role", "r"),
        _rc("aws_iam_role_policy", "p", after={"policy": policy}),
    ])
    findings = intent_assertions.check_controls(
        _blueprint_with_controls(["Per-service IAM roles with scoped resource permissions"]), plan)
    assert findings == []


def test_scoped_iam_violated_when_policy_resolved_with_wildcard():
    policy = json.dumps({"Statement": [{"Effect": "Allow", "Action": "s3:*", "Resource": "*"}]})
    plan = _plan(resource_changes=[
        _rc("aws_iam_role", "r"),
        _rc("aws_iam_role_policy", "p", after={"policy": policy}),
    ])
    findings = intent_assertions.check_controls(
        _blueprint_with_controls(["Per-service IAM roles with scoped resource permissions"]), plan)
    assert len(findings) == 1 and findings[0]["finding_kind"] == "standard"


def test_scoped_iam_unresolved_when_policy_unknown_until_apply():
    """The real bug this session caught in its own first draft: a policy that references a
    not-yet-created resource's ARN has after_unknown.policy == True and after.policy absent
    entirely -- must be its own distinct finding kind, never silently treated as satisfied."""
    plan = _plan(resource_changes=[
        _rc("aws_iam_role", "r"),
        _rc("aws_iam_role_policy", "p", after={"name": "p"}, after_unknown={"policy": True}),
    ])
    findings = intent_assertions.check_controls(
        _blueprint_with_controls(["Per-service IAM roles with scoped resource permissions"]), plan)
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "control_unresolved"


def test_alarms_log_retention_clean_when_both_present():
    plan = _plan(resource_changes=[_rc("aws_cloudwatch_metric_alarm", "a"), _rc("aws_cloudwatch_log_group", "g")])
    findings = intent_assertions.check_controls(_blueprint_with_controls(["CloudWatch alarms and log retention"]), plan)
    assert findings == []


def test_alarms_log_retention_violated_when_log_group_missing():
    """The real, previously-invisible gap this checker exists to surface: the demo blueprint's
    own generated Terraform has an alarm but no log_group at all -- confirmed live by grep
    across every generated .tf file, not hypothetical."""
    plan = _plan(resource_changes=[_rc("aws_cloudwatch_metric_alarm", "a")])
    findings = intent_assertions.check_controls(_blueprint_with_controls(["CloudWatch alarms and log retention"]), plan)
    assert len(findings) == 1


def test_budget_anomaly_clean_when_both_present():
    plan = _plan(resource_changes=[_rc("aws_budgets_budget", "b"), _rc("aws_ce_anomaly_monitor", "m")])
    findings = intent_assertions.check_controls(_blueprint_with_controls(["Budget and anomaly detection hooks"]), plan)
    assert findings == []


def test_budget_anomaly_violated_when_anomaly_missing():
    """Also a real, previously-invisible gap confirmed against the demo blueprint's real
    output: aws_budgets_budget exists, no aws_ce_anomaly_* resource anywhere."""
    plan = _plan(resource_changes=[_rc("aws_budgets_budget", "b")])
    findings = intent_assertions.check_controls(_blueprint_with_controls(["Budget and anomaly detection hooks"]), plan)
    assert len(findings) == 1


def test_unmapped_control_logs_loudly_never_silently_skipped():
    findings = intent_assertions.check_controls(
        _blueprint_with_controls(["Terraform plan hash approval before apply"]), _plan())
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "control_unmapped"


def test_every_demo_blueprint_control_has_a_disposition():
    """No control is silently dropped from consideration: every one of the real blueprint's 7
    controls is either in CONTROL_CHECKS (6 of them) or is the one disclosed, deliberately
    unmapped process-level claim -- never a third, unaccounted-for outcome."""
    bp = blueprints.get_blueprint("aws-data-pipeline-standard")
    mapped = [c for c in bp["controls"] if c in intent_assertions.CONTROL_CHECKS]
    unmapped = [c for c in bp["controls"] if c not in intent_assertions.CONTROL_CHECKS]
    assert len(mapped) == 6
    assert unmapped == ["Terraform plan hash approval before apply"]

    # Direct proof: running check_controls against an empty plan produces exactly one finding
    # (the unmapped control) -- every mapped control against zero resources is correctly
    # not-applicable, not silently "passed".
    findings = intent_assertions.check_controls(bp, _plan())
    assert len(findings) == 1
    assert findings[0]["finding_kind"] == "control_unmapped"


# ---------------------------------------------------------------------------
# 3. Numeric ceilings
# ---------------------------------------------------------------------------

def test_budget_ceiling_flags_missing_budget_resource():
    requirements = {"non_functional": {"budget": "$500/month max"}}
    plan = _plan(resource_changes=[_rc("aws_s3_bucket", "b")])
    findings = intent_assertions.check_numerics(requirements, plan)
    assert len(findings) == 1
    assert findings[0]["id"] == "INTENT-BUDGET-MISSING"


def test_budget_ceiling_clean_when_budget_resource_present():
    requirements = {"non_functional": {"budget": "$500/month max"}}
    plan = _plan(resource_changes=[_rc("aws_budgets_budget", "b")])
    assert intent_assertions.check_numerics(requirements, plan) == []


def test_budget_ceiling_not_applicable_when_unparseable():
    """Matches parse_budget_usd's own 'never guess' contract: nothing parseable is
    not_applicable, never a pass or a block."""
    requirements = {"non_functional": {"budget": "deferred: no ceiling set yet"}}
    plan = _plan(resource_changes=[])
    assert intent_assertions.check_numerics(requirements, plan) == []


# ---------------------------------------------------------------------------
# Fail-closed sweep
# ---------------------------------------------------------------------------

def test_evaluate_skips_missing_records_gracefully():
    result = intent_assertions.evaluate(plan_json=_plan())
    assert result["advisory"] is True
    assert result["findings"] == []


def test_evaluate_combines_all_three_claim_classes():
    requirements = {"non_functional": {"budget": "$100/month"}}
    decision = {"selected_modules": ["missing-module"]}
    bp = _blueprint_with_controls(["SSE-KMS for storage and logs"])
    plan = _plan(resource_changes=[_rc("aws_s3_bucket", "b")])
    result = intent_assertions.evaluate(requirements=requirements, architecture_decision=decision,
                                         blueprint=bp, plan_json=plan)
    ids = {f["id"] for f in result["findings"]}
    assert "INTENT-MODULE-MISSING" in ids
    assert "INTENT-CONTROL-VIOLATED" in ids
    assert "INTENT-BUDGET-MISSING" in ids
    assert result["advisory"] is True


# ---------------------------------------------------------------------------
# Real terraform integration: the demo blueprint's own generated Terraform, real plan.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_real_demo_blueprint_plan_matches_manually_verified_dispositions(tmp_path):
    """Regenerates the demo blueprint's real Terraform, plans it with dummy AWS credentials
    (aws_caller_identity patched out -- used only for bucket-name uniqueness, irrelevant to
    every control's content; aws_sfn_state_machine neutralized -- needs real STS access for its
    own plan-time validation, the same disclosed limitation as orchestrator-stepfunctions,
    unrelated to any of these six checks), and locks in the exact dispositions manually verified
    this session: 4 controls clean, 2 violated (real gaps), 1 unresolved (unknown-until-apply
    policy), 1 unmapped (a process-level claim, not a plan-JSON property)."""
    import subprocess
    import sys as _sys
    for sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
        _sys.path.insert(0, __import__("os").path.join(
            __import__("os").path.dirname(__import__("os").path.dirname(__file__)), "core", sub))
    import terraform_generator

    inputs = {"environment": "dev", "region": "us-east-1", "owner": "ci",
              "ingestion_mode": "batch", "daily_data_gb": 10}
    terraform_generator.generate_aws_data_pipeline(inputs, str(tmp_path))

    (tmp_path / "step_functions.tf").write_text("", encoding="utf-8")
    monitoring = tmp_path / "monitoring.tf"
    monitoring.write_text(
        monitoring.read_text(encoding="utf-8").replace(
            "StateMachineArn = aws_sfn_state_machine.pipeline.arn",
            'StateMachineArn = "arn:aws:states:us-east-1:123456789012:stateMachine:placeholder"'),
        encoding="utf-8")
    outputs = tmp_path / "outputs.tf"
    outputs.write_text(
        outputs.read_text(encoding="utf-8").replace(
            'output "step_function_arn" { value = aws_sfn_state_machine.pipeline.arn }',
            'output "step_function_arn" { value = "arn:aws:states:us-east-1:123456789012:stateMachine:placeholder" }'),
        encoding="utf-8")
    provider = tmp_path / "provider.tf"
    provider.write_text(
        provider.read_text(encoding="utf-8")
        .replace('provider "aws" {\n  region = var.region',
                 'provider "aws" {\n  region = var.region\n  access_key = "test"\n  secret_key = "test"\n'
                 '  skip_credentials_validation = true\n  skip_requesting_account_id = true\n'
                 '  skip_metadata_api_check = true')
        .replace('data "aws_caller_identity" "current" {}\n', ''),
        encoding="utf-8")
    for fname in ("iam.tf", "s3.tf"):
        fpath = tmp_path / fname
        fpath.write_text(
            fpath.read_text(encoding="utf-8").replace(
                "data.aws_caller_identity.current.account_id", '"123456789012"'),
            encoding="utf-8")

    subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "init", "-input=false"],
                   capture_output=True, text=True, check=True)
    subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "plan", "-out=tfplan", "-input=false"],
                   capture_output=True, text=True, check=True)
    show = subprocess.run([TERRAFORM, f"-chdir={tmp_path}", "show", "-json", "tfplan"],
                          capture_output=True, text=True, check=True)
    plan_json = json.loads(show.stdout)

    bp = blueprints.get_blueprint("aws-data-pipeline-standard")
    findings = intent_assertions.check_controls(bp, plan_json)
    by_control = {f["resource"]: f["finding_kind"] for f in findings}

    assert by_control.get("CloudWatch alarms and log retention") == "standard"
    assert by_control.get("Budget and anomaly detection hooks") == "standard"
    assert by_control.get("Per-service IAM roles with scoped resource permissions") == "control_unresolved"
    assert by_control.get("Terraform plan hash approval before apply") == "control_unmapped"
    for clean_control in ("SSE-KMS for storage and logs", "S3 public access blocks",
                          "Versioning and lifecycle policies"):
        assert clean_control not in by_control, f"{clean_control} unexpectedly flagged: {by_control}"


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_real_composed_plan_module_presence_across_catalog(tmp_path):
    """Proof-bar item (docs/phase4_scope.md section 6 item 1): a real multi-module composition
    (not the hand-built dicts above), dummy AWS credentials, three real modules from the
    catalog spanning storage/compute/consumption (storage-medallion-s3, compaction-glue,
    query-athena -- avoiding orchestrator-stepfunctions and databricks-workspace, both with
    already-disclosed real-credential plan-time constraints unrelated to this check). Confirms
    the module.<label>.* addressing shape holds for a 3-module real composition, and that a
    module named in selected_modules but never actually composed is correctly flagged missing
    against a genuinely real plan, not just a synthetic one."""
    import subprocess
    import sys as _sys
    import os as _os
    repo_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    for sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
        _sys.path.insert(0, _os.path.join(repo_root, "core", sub))
    import synthesizer

    out_dir = str(tmp_path / "composed")
    synthesizer.compose(["storage-medallion-s3", "compaction-glue", "query-athena"],
                         "probe", out_dir, owner="ci")

    # aws_caller_identity is used only for bucket-name uniqueness in these modules -- irrelevant
    # to module-presence, patched out the same way the parity pass and demo-blueprint test do.
    import re
    for module_id in ("storage-medallion-s3", "query-athena"):
        p = _os.path.join(out_dir, "modules", module_id, "main.tf")
        content = open(p, encoding="utf-8").read()
        content = re.sub(r'data\.aws_caller_identity\.current\.account_id', '"123456789012"', content)
        content = re.sub(r'data\s+"aws_caller_identity"\s+"current"\s*\{\s*\}\n?', '', content)
        open(p, "w", encoding="utf-8").write(content)

    providers_path = _os.path.join(out_dir, "providers.tf")
    providers = open(providers_path, encoding="utf-8").read()
    providers = providers.replace(
        'provider "aws" {\n  region = var.region',
        'provider "aws" {\n  region = var.region\n  access_key = "test"\n  secret_key = "test"\n'
        '  skip_credentials_validation = true\n  skip_requesting_account_id = true\n'
        '  skip_metadata_api_check = true')
    open(providers_path, "w", encoding="utf-8").write(providers)

    subprocess.run([TERRAFORM, f"-chdir={out_dir}", "init", "-input=false"],
                   capture_output=True, text=True, check=True)
    subprocess.run([TERRAFORM, f"-chdir={out_dir}", "plan", "-out=tfplan", "-input=false"],
                   capture_output=True, text=True, check=True)
    show = subprocess.run([TERRAFORM, f"-chdir={out_dir}", "show", "-json", "tfplan"],
                          capture_output=True, text=True, check=True)
    plan_json = json.loads(show.stdout)

    decision_all_present = {"selected_modules": ["storage-medallion-s3", "compaction-glue", "query-athena"]}
    assert intent_assertions.check_module_presence(decision_all_present, plan_json) == []

    decision_with_missing = {"selected_modules": ["storage-medallion-s3", "compaction-glue",
                                                    "query-athena", "dq-great-expectations"]}
    findings = intent_assertions.check_module_presence(decision_with_missing, plan_json)
    assert len(findings) == 1
    assert findings[0]["resource"] == "dq-great-expectations"
