import json
import os

import bcm_pricing_calculator as bcm


PLAN = {
    "resource_changes": [
        {
            "address": "aws_s3_bucket.bronze",
            "mode": "managed",
            "type": "aws_s3_bucket",
            "change": {"actions": ["create"]},
        },
        {
            "address": "aws_glue_job.transform",
            "mode": "managed",
            "type": "aws_glue_job",
            "change": {"actions": ["create"]},
        },
        {
            "address": "data.aws_caller_identity.current",
            "mode": "data",
            "type": "aws_caller_identity",
            "change": {"actions": ["read"]},
        },
    ],
}


def test_prepare_writes_reviewable_payloads(tmp_path):
    report = tmp_path / "reports" / "abc123"
    report.mkdir(parents=True)
    (report / "plan.json").write_text(json.dumps(PLAN), encoding="utf-8")
    (report / "manifest.json").write_text(json.dumps({
        "short": "abc123",
        "template": "aws-data-pipeline-standard",
    }), encoding="utf-8")

    paths = bcm.prepare(str(report), account_id="123456789012")

    assert os.path.exists(paths["usage"])
    assert os.path.exists(paths["create"])
    assert os.path.exists(paths["commands"])
    usage = json.loads((report / "bcm-usage.json").read_text(encoding="utf-8"))
    assumptions = json.loads((report / "bcm-assumptions.json").read_text(encoding="utf-8"))
    assert len(usage) == 2
    assert {entry["key"] for entry in usage} == {"U000001", "U000002"}
    assert "aws_glue_job" in assumptions["terraform_resource_inventory"]
    assert assumptions["usage_line_map"]["U000001"]["terraformResourceType"] == "aws_glue_job"
    assert usage[0]["usageAccountId"] == "123456789012"
    assert bcm.validate_usage(usage)


def test_validate_usage_rejects_placeholders():
    usage = bcm.build_usage(PLAN, account_id="", region="us-east-1")
    errors = bcm.validate_usage(usage)
    assert errors
    assert any("REVIEW_REQUIRED" in e for e in errors)


def test_validate_usage_accepts_resolved_required_fields():
    usage = [{
        "serviceCode": "AWSGlue",
        "usageType": "USE1-Example",
        "operation": "ExampleOperation",
        "key": "GLUE1",
        "usageAccountId": "123456789012",
        "amount": 12.5,
    }]
    assert bcm.validate_usage(usage) == []


def test_derive_usage_fills_amounts_from_inputs_no_placeholders():
    plan = {
        "variables": {"daily_data_gb": {"value": 10}},
        "resource_changes": [
            {"address": "aws_glue_job.x", "mode": "managed", "type": "aws_glue_job", "change": {"actions": ["create"]}},
            {"address": "aws_s3_bucket.x", "mode": "managed", "type": "aws_s3_bucket", "change": {"actions": ["create"]}},
            {"address": "aws_athena_workgroup.x", "mode": "managed", "type": "aws_athena_workgroup", "change": {"actions": ["create"]}},
        ],
    }
    profile = {"usage": [
        {"serviceCode": "AmazonS3", "usageType": "USE1-TimedStorage-ByteHrs", "operation": "StandardStorage"},
        {"serviceCode": "AWSGlue", "usageType": "USE1-ETL-DPU-Hour", "operation": "Spark"},
        {"serviceCode": "AmazonAthena", "usageType": "USE1-DataScannedInTB", "operation": "Athena"},
    ]}
    usage, A = bcm.derive_usage(plan, "123456789012", "us-east-1", profile)
    by = {u["serviceCode"]: u for u in usage}
    assert set(by) == {"AWSGlue", "AmazonS3", "AmazonAthena"}
    assert by["AmazonS3"]["amount"] == round(10 * A["s3_storage_retention_factor"], 2)   # derived from input
    assert by["AWSGlue"]["usageType"] == "USE1-ETL-DPU-Hour"                              # catalog from profile
    assert all(u["usageAccountId"] == "123456789012" for u in usage)
    assert bcm.validate_usage(usage) == []                                               # runnable, no placeholders


def test_assume_override_changes_amount():
    plan = {"variables": {"daily_data_gb": {"value": 10}},
            "resource_changes": [{"address": "aws_s3_bucket.x", "mode": "managed",
                                  "type": "aws_s3_bucket", "change": {"actions": ["create"]}}]}
    prof = {"usage": [{"serviceCode": "AmazonS3", "usageType": "U", "operation": "O"}]}
    base, _ = bcm.derive_usage(plan, "1", "us-east-1", prof)
    bumped, _ = bcm.derive_usage(plan, "1", "us-east-1", prof, {"s3_storage_retention_factor": 60})
    assert bumped[0]["amount"] == base[0]["amount"] * 2


def test_run_pulls_per_service_line_items(tmp_path, monkeypatch):
    (tmp_path / "bcm-usage.json").write_text(json.dumps(
        [{"serviceCode": "AWSGlue", "usageType": "X", "operation": "Y", "key": "U1",
          "usageAccountId": "123456789012", "amount": 12.5}]), encoding="utf-8")
    (tmp_path / "bcm-create-workload-estimate.json").write_text(json.dumps({"name": "x"}), encoding="utf-8")
    monkeypatch.setattr(bcm, "request_approval", lambda *a, **k: True)
    monkeypatch.setattr(bcm, "_aws_cli", lambda: "aws")
    calls = []

    def fake(cmd, cwd):
        calls.append(cmd[2])
        return {
            "create-workload-estimate": {"id": "WE1"},
            "batch-create-workload-estimate-usage": {"items": []},
            "get-workload-estimate": {"id": "WE1", "totalCost": {"amount": "123.45"}},
            "list-workload-estimate-usage": {"items": [{"serviceCode": "AWSGlue", "cost": {"amount": "80.00"}}]},
        }.get(cmd[2], {})

    monkeypatch.setattr(bcm, "_run_json", fake)
    import reporter
    monkeypatch.setattr(reporter, "refresh_cost", lambda d: None)

    assert bcm.run(str(tmp_path), mode="auto-approve") is True
    assert "list-workload-estimate-usage" in calls
    est = json.loads((tmp_path / "bcm-estimate.json").read_text(encoding="utf-8"))
    assert est["usage_lines"]["items"][0]["serviceCode"] == "AWSGlue"


def test_run_bill_scenario_orchestrates_commitment_flow(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text(json.dumps({"short": "abc"}), encoding="utf-8")
    (tmp_path / "commit.json").write_text(json.dumps(
        {"commitmentModifications": [{"commitment": {"savingsPlans": {"hourlyCommitment": "5"}}}]}), encoding="utf-8")
    monkeypatch.setattr(bcm, "request_approval", lambda *a, **k: True)
    monkeypatch.setattr(bcm, "_aws_cli", lambda: "aws")
    calls = []

    def fake(cmd, cwd):
        calls.append(cmd[2])
        return {
            "create-bill-scenario": {"id": "BS-1"},
            "batch-create-bill-scenario-commitment-modification": {"items": []},
            "create-bill-estimate": {"id": "BE-1", "totalCost": {"amount": "250.00"}},
            "list-bill-estimate-line-items": {"items": [{"serviceCode": "AWSGlue", "cost": {"amount": "150"}}]},
            "list-bill-estimate-commitments": {"items": [{"commitmentType": "SavingsPlans"}]},
        }.get(cmd[2], {})

    monkeypatch.setattr(bcm, "_run_json", fake)
    import reporter
    monkeypatch.setattr(reporter, "refresh_cost", lambda d: None)

    assert bcm.run_bill_scenario(str(tmp_path), commitments=str(tmp_path / "commit.json"), mode="auto-approve") is True
    for op in ("create-bill-scenario", "batch-create-bill-scenario-commitment-modification",
               "create-bill-estimate", "list-bill-estimate-line-items", "list-bill-estimate-commitments"):
        assert op in calls
    out = json.loads((tmp_path / "bcm-scenario-estimate.json").read_text(encoding="utf-8"))
    assert out["bill_estimate"]["id"] == "BE-1"
    # report prefers the commitment-aware estimate
    cost = reporter.load_bcm_estimate(str(tmp_path))
    assert cost["pricing_source"] == "AWS BCM Bill Estimate (with commitments)"
    assert cost["line_items"][0]["serviceCode"] == "AWSGlue"


def test_fetch_actuals_writes_bcm_actuals_from_cost_explorer(tmp_path, monkeypatch):
    import providers.base as pb

    class _P:
        def cost_by_service(self, months_back=6):
            return {"ok": True, "error": "", "months": [
                {"month": "2026-04", "total": 50.0, "by_service": {"AWS Glue": 50.0}},
                {"month": "2026-05", "total": 110.0,
                 "by_service": {"AWS Glue": 92.0, "Amazon Simple Storage Service": 18.0}},
            ]}

    monkeypatch.setattr(pb, "get_provider", lambda *a, **k: _P())
    import reporter
    monkeypatch.setattr(reporter, "refresh_cost", lambda d: None)

    res = bcm.fetch_actuals(str(tmp_path))
    assert res["month"] == "2026-05"  # most recent month with spend
    written = json.loads((tmp_path / "bcm-actuals.json").read_text(encoding="utf-8"))
    assert written["AWS Glue"] == 92.0 and written["Amazon Simple Storage Service"] == 18.0


def test_fetch_actuals_raises_when_cost_explorer_unavailable(tmp_path, monkeypatch):
    import providers.base as pb

    class _P:
        def cost_by_service(self, months_back=6):
            return {"ok": False, "error": "AccessDenied", "months": []}

    monkeypatch.setattr(pb, "get_provider", lambda *a, **k: _P())
    try:
        bcm.fetch_actuals(str(tmp_path))
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "Cost Explorer unavailable" in str(exc)


def test_shipped_example_usage_profile_is_valid():
    # The committed example profile must be genuinely runnable: no REVIEW_REQUIRED
    # placeholders, all required fields present — so a client only swaps account+amounts.
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    profile = bcm._load_usage_profile(os.path.join(here, "examples", "bcm-usage-profile.example.json"))
    usage = bcm._profile_usage(profile)
    assert usage and bcm.validate_usage(usage) == []


def test_usage_profile_can_supply_reviewed_entries(tmp_path):
    profile = tmp_path / "usage-profile.json"
    profile.write_text(json.dumps({
        "name": "reviewed-internal-service-profile",
        "usage": [{
            "serviceCode": "AmazonS3",
            "usageType": "USE1-ExampleUsage",
            "operation": "ExampleOperation",
            "key": "S3USAGE1",
            "usageAccountId": "123456789012",
            "amount": 20,
        }],
    }), encoding="utf-8")

    usage = bcm.build_usage(
        PLAN,
        account_id="123456789012",
        region="us-east-1",
        usage_profile=bcm._load_usage_profile(str(profile)),
    )

    assert usage[0]["serviceCode"] == "AmazonS3"
    assert usage[0]["key"] == "S3USAGE1"
    assert bcm.validate_usage(usage) == []


# ---- auto_estimate: frictionless pricing (estimates need no human gate) ----
def _auto_report(tmp_path):
    report = tmp_path / "reports" / "auto1"
    report.mkdir(parents=True)
    plan = dict(PLAN)
    plan["variables"] = {"daily_data_gb": {"value": 50}, "region": {"value": "us-east-1"}}
    (report / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (report / "manifest.json").write_text(json.dumps({"short": "auto1", "template": "t"}), encoding="utf-8")
    return report


def test_auto_estimate_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MINUS_BCM_AUTO", "0")
    ok, note = bcm.auto_estimate(str(_auto_report(tmp_path)))
    assert not ok and "MINUS_BCM_AUTO" in note


def test_auto_estimate_degrades_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("MINUS_BCM_AUTO", "1")
    monkeypatch.setattr(bcm, "_sts_account_id", lambda: None)
    ok, note = bcm.auto_estimate(str(_auto_report(tmp_path)))
    assert not ok and "credentials" in note


def test_auto_estimate_submits_complete_lines_and_records_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("MINUS_BCM_AUTO", "1")
    monkeypatch.setattr(bcm, "_sts_account_id", lambda: "123456789012")
    ran = {}

    def fake_run(report_dir, mode="auto-approve"):
        ran["mode"] = mode
        ran["usage"] = json.loads((tmp_path / "reports" / "auto1" / "bcm-usage.json").read_text(encoding="utf-8"))
        return True

    monkeypatch.setattr(bcm, "run", fake_run)
    report = _auto_report(tmp_path)
    ok, note = bcm.auto_estimate(str(report), region="us-east-1")
    assert ok, note
    assert ran["mode"] == "auto-approve"
    # Only catalog-backed lines were submitted (Glue + S3 come from the shipped profile).
    assert ran["usage"] and all(not bcm._has_placeholder(u) for u in ran["usage"])
    codes = {u["serviceCode"] for u in ran["usage"]}
    assert "AWSGlue" in codes and "AmazonS3" in codes
    # Amounts are derived from run inputs + recorded assumptions (S3: 50 GB/day x factor).
    s3 = next(u for u in ran["usage"] if u["serviceCode"] == "AmazonS3")
    assert s3["amount"] == 50 * bcm.DEFAULT_ASSUMPTIONS["s3_storage_retention_factor"]


def test_auto_estimate_never_clobbers_reviewed_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("MINUS_BCM_AUTO", "1")
    monkeypatch.setattr(bcm, "_sts_account_id", lambda: "123456789012")
    monkeypatch.setattr(bcm, "run", lambda report_dir, mode="auto-approve": True)
    report = _auto_report(tmp_path)
    reviewed = [{"serviceCode": "AWSGlue", "usageType": "USE1-ETL-DPU-Hour", "operation": "Spark",
                 "key": "U000001", "usageAccountId": "999999999999", "amount": 7.5}]
    (report / "bcm-usage.json").write_text(json.dumps(reviewed), encoding="utf-8")
    ok, _ = bcm.auto_estimate(str(report))
    assert ok
    kept = json.loads((report / "bcm-usage.json").read_text(encoding="utf-8"))
    assert kept == reviewed          # a validating (reviewed) payload is used as-is


def test_derive_prices_every_glue_job_and_every_zone():
    # P0 review findings: one Glue line must cover ALL jobs; S3 retains a copy per zone.
    plan = {
        "variables": {"daily_data_gb": {"value": 100}},
        "resource_changes": [
            {"address": 'module.s.aws_s3_bucket.zone["bronze"]', "type": "aws_s3_bucket",
             "mode": "managed", "change": {"actions": ["create"]}},
            {"address": 'module.s.aws_s3_bucket.zone["silver"]', "type": "aws_s3_bucket",
             "mode": "managed", "change": {"actions": ["create"]}},
            {"address": 'module.s.aws_s3_bucket.zone["gold"]', "type": "aws_s3_bucket",
             "mode": "managed", "change": {"actions": ["create"]}},
            {"address": "module.s.aws_s3_bucket.results", "type": "aws_s3_bucket",
             "mode": "managed", "change": {"actions": ["create"]}},   # results bucket: NOT a zone
            {"address": "module.c.aws_glue_job.a", "type": "aws_glue_job",
             "mode": "managed", "change": {"actions": ["create"]}},
            {"address": "module.c.aws_glue_job.b", "type": "aws_glue_job",
             "mode": "managed", "change": {"actions": ["create"]}},
            {"address": "module.c.aws_glue_job.c", "type": "aws_glue_job",
             "mode": "managed", "change": {"actions": ["create"]}},
        ],
    }
    usage, A = bcm.derive_usage(plan, "123456789012", "us-east-1")
    assert A["glue_job_count"] == 3 and A["s3_retention_zones"] == 3
    by_code = {u["serviceCode"]: u for u in usage}
    per_job = (bcm.DEFAULT_ASSUMPTIONS["glue_workers"]
               * bcm.DEFAULT_ASSUMPTIONS["glue_minutes_per_run"] / 60.0
               * bcm.DEFAULT_ASSUMPTIONS["glue_runs_per_day"]
               * bcm.DEFAULT_ASSUMPTIONS["days_per_month"])
    assert by_code["AWSGlue"]["amount"] == round(per_job * 3, 2)          # 720, not 240
    assert by_code["AmazonS3"]["amount"] == 100 * 30 * 3                  # 9000 GB-Mo, not 3000
    # explicit overrides still win and are recorded
    usage2, A2 = bcm.derive_usage(plan, "123456789012", "us-east-1",
                                  assumptions={"glue_job_count": 1, "s3_retention_zones": 1})
    assert A2["glue_job_count"] == 1
    assert {u["serviceCode"]: u for u in usage2}["AWSGlue"]["amount"] == round(per_job, 2)


def _change(address, rtype, after=None):
    return {"address": address, "mode": "managed", "type": rtype,
            "change": {"actions": ["create"], "after": after or {}}}


def test_resource_count_dispatcher_prices_kms_key_not_alias():
    plan = {"resource_changes": [
        _change("aws_kms_key.lake", "aws_kms_key"),
        _change("aws_kms_alias.lake", "aws_kms_alias"),
    ]}
    A = dict(bcm.DEFAULT_ASSUMPTIONS)
    assert bcm._amount_for("awskms", {}, A, plan) == 1.0  # only the key counts, not the alias


def test_resource_count_dispatcher_matches_prior_mwaa_and_cloudwatch_behavior():
    # Regression guard for the amount_model refactor: MWAA/CloudWatch amounts must be
    # byte-identical to the hand-written branches they replaced.
    plan = {"resource_changes": [
        _change("aws_mwaa_environment.this", "aws_mwaa_environment"),
        _change("aws_cloudwatch_metric_alarm.spend", "aws_cloudwatch_metric_alarm"),
        _change("aws_cloudwatch_metric_alarm.spend2", "aws_cloudwatch_metric_alarm"),
    ]}
    A = dict(bcm.DEFAULT_ASSUMPTIONS)
    assert bcm._amount_for("AmazonMWAA", {}, A, plan) == A["hours_per_month"]
    assert bcm._amount_for("AmazonCloudWatch", {}, A, plan) == 2.0


def test_resource_count_dispatcher_returns_none_when_no_matching_resources():
    plan = {"resource_changes": [_change("aws_s3_bucket.x", "aws_s3_bucket")]}
    A = dict(bcm.DEFAULT_ASSUMPTIONS)
    assert bcm._amount_for("awskms", {}, A, plan) is None
    assert bcm._amount_for("AmazonMWAA", {}, A, plan) is None
    assert bcm._amount_for("AmazonCloudWatch", {}, A, plan) is None
