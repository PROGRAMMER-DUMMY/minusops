import json

import coverage_audit as ca
import providers.base as pb


def _change(address, rtype, after=None):
    return {
        "address": address, "mode": "managed", "type": rtype,
        "change": {"actions": ["create"], "after": after or {}},
    }


PLAN = {
    "variables": {"daily_data_gb": {"value": 50}},
    "resource_changes": [
        _change("aws_glue_job.transform", "aws_glue_job"),
        _change("aws_mwaa_environment.this", "aws_mwaa_environment", {"environment_class": "mw1.small"}),
        _change("aws_kinesis_stream.this", "aws_kinesis_stream", {"shard_count": 3}),
        _change("aws_sns_topic.alerts", "aws_sns_topic"),
        _change("aws_security_group.eg", "aws_security_group"),
        _change("aws_iam_role.eg", "aws_iam_role"),
        _change("aws_totally_new_resource.thing", "aws_totally_new_resource"),
        # a data source must never show up in any bucket
        {"address": "data.aws_caller_identity.current", "mode": "data",
         "type": "aws_caller_identity", "change": {"actions": ["read"]}},
    ],
}


def test_classify_auto_prices_verified_services_with_derivable_amount():
    coverage = ca.classify(PLAN)
    auto = {row["resource_type"] for row in coverage["auto_priced"]}
    assert "aws_glue_job" in auto  # verified catalog fields + a derivable amount


def test_classify_maps_but_flags_unverified_catalog_for_new_services():
    coverage = ca.classify(PLAN)
    mapped = {row["resource_type"]: row for row in coverage["catalog_mapped_needs_usage"]}
    assert "aws_mwaa_environment" in mapped
    assert "aws_kinesis_stream" in mapped
    assert "aws_sns_topic" in mapped
    # MWAA and Kinesis have real plan-derived quantities even though the catalog fields
    # (usageType/operation) still need a reviewed profile.
    assert mapped["aws_mwaa_environment"]["amount_derivable"] is True
    assert mapped["aws_kinesis_stream"]["amount_derivable"] is True
    # SNS has no plan-derivable usage driver at all (notification volume is unknowable).
    assert mapped["aws_sns_topic"]["amount_derivable"] is False
    assert all(not row["catalog_verified"] for row in mapped.values())


def test_classify_confirms_free_resources():
    coverage = ca.classify(PLAN)
    free = {row["resource_type"] for row in coverage["confirmed_free"]}
    assert "aws_security_group" in free
    assert "aws_iam_role" in free


def test_classify_flags_genuinely_unresolved_types_instead_of_dropping_them():
    coverage = ca.classify(PLAN)
    unresolved = {row["resource_type"] for row in coverage["unresolved"]}
    assert "aws_totally_new_resource" in unresolved


def test_classify_ignores_data_sources():
    coverage = ca.classify(PLAN)
    all_types = set()
    for bucket in ("auto_priced", "catalog_mapped_needs_usage", "confirmed_free", "unresolved"):
        all_types |= {row["resource_type"] for row in coverage[bucket]}
    assert "aws_caller_identity" not in all_types


def test_audit_writes_bcm_coverage_json_with_summary(tmp_path):
    report = tmp_path / "reports" / "abc123"
    report.mkdir(parents=True)
    (report / "plan.json").write_text(json.dumps(PLAN), encoding="utf-8")

    coverage = ca.audit(str(report))

    written = json.loads((report / "bcm-coverage.json").read_text(encoding="utf-8"))
    assert written["summary"]["unresolved"] == 1
    assert written["summary"]["confirmed_free"] == 2
    assert coverage["generated_at"]


def test_classify_goes_through_cloudprovider_not_pricing_catalog_directly():
    # Audit finding 2026-07-03: this file used to import pricing_catalog directly, bypassing
    # the CloudProvider contract entirely, which made "multi-cloud" aspirational rather than
    # real. Confirm classify() actually calls through the provider it's given.
    calls = []

    class FakeProvider:
        name = "fake"
        status = "implemented"

        def confirmed_free(self, tf_type):
            calls.append(("confirmed_free", tf_type))
            return None

        def resolve_resource_type(self, tf_type):
            calls.append(("resolve_resource_type", tf_type))
            return None

    coverage = ca.classify(PLAN, provider=FakeProvider())
    resource_types = {c["type"] for c in PLAN["resource_changes"] if c.get("mode") == "managed"}
    called_types = {t for _, t in calls if _ == "resolve_resource_type"}
    assert called_types == resource_types
    # A provider with nothing resolved means everything is unresolved -- not silently AWS-priced.
    assert len(coverage["unresolved"]) == len(resource_types)


def test_classify_degrades_honestly_for_a_roadmap_cloud_instead_of_crashing():
    for cloud in ("azure", "gcp"):
        provider = pb.get_provider(cloud)
        coverage = ca.classify(PLAN, provider=provider)  # must not raise
        assert coverage["provider"] == {"cloud": cloud, "status": "roadmap"}
        assert coverage["auto_priced"] == []
        assert coverage["confirmed_free"] == []
        # every managed resource type in PLAN lands in unresolved, not silently priced
        resource_types = {c["type"] for c in PLAN["resource_changes"] if c.get("mode") == "managed"}
        assert {row["resource_type"] for row in coverage["unresolved"]} == resource_types


def test_classify_defaults_to_the_active_provider(monkeypatch):
    monkeypatch.setenv("MINUS_CLOUD", "aws")
    coverage = ca.classify(PLAN)  # no provider passed -> resolves via providers.base.get_provider()
    assert coverage["provider"]["cloud"] == "aws"


def test_audit_raises_when_plan_json_missing(tmp_path):
    try:
        ca.audit(str(tmp_path))
        assert False, "expected FileNotFoundError"
    except FileNotFoundError as exc:
        assert "plan.json" in str(exc)


def test_cloudwatch_metric_alarm_verified_but_event_rule_is_not():
    # The generic aws_cloudwatch prefix covers event_rule/event_target too, but only the
    # metric_alarm's catalog fields (CW:AlarmMonitorUsage) were actually verified live
    # (2026-07-04) -- event rules/targets must not inherit that verified status.
    plan = {"resource_changes": [
        {"address": "a.event_rule", "mode": "managed", "type": "aws_cloudwatch_event_rule",
         "change": {"actions": ["create"], "after": {}}},
        {"address": "a.metric_alarm", "mode": "managed", "type": "aws_cloudwatch_metric_alarm",
         "change": {"actions": ["create"], "after": {}}},
    ]}
    coverage = ca.classify(plan)
    auto = {r["resource_type"] for r in coverage["auto_priced"]}
    needs_usage = {r["resource_type"] for r in coverage["catalog_mapped_needs_usage"]}
    assert "aws_cloudwatch_metric_alarm" in auto
    assert "aws_cloudwatch_event_rule" in needs_usage
    assert "aws_cloudwatch_event_rule" not in auto


def test_audit_schema_watch_status_is_none_with_no_snapshot(tmp_path, monkeypatch):
    # Regression proof for the schema_watch.py wiring: with no recent-changes/ snapshot present
    # (the state of every existing deployment today), the new field is just None -- the four
    # coverage buckets and the summary counts are completely unaffected.
    monkeypatch.setattr(ca.module_registry, "output_root", lambda: str(tmp_path))
    report = tmp_path / "reports" / "abc123"
    report.mkdir(parents=True)
    (report / "plan.json").write_text(json.dumps(PLAN), encoding="utf-8")

    coverage = ca.audit(str(report))

    assert coverage["schema_watch_status"] is None
    assert coverage["summary"]["unresolved"] == 1
    assert coverage["summary"]["confirmed_free"] == 2
    assert "schema_watch_status" not in coverage["summary"]


def test_audit_schema_watch_status_reflects_latest_report(tmp_path, monkeypatch):
    monkeypatch.setattr(ca.module_registry, "output_root", lambda: str(tmp_path))
    provider_dir = tmp_path / "recent-changes" / "aws"
    provider_dir.mkdir(parents=True)
    (provider_dir / "schema-snapshot.json").write_text("{}", encoding="utf-8")
    (provider_dir / "20260101T000000Z.json").write_text(json.dumps({
        "provider": "aws", "resolved_version": "6.1.0", "generated_at": "2026-01-01T00:00:00Z",
        "findings": [],
    }), encoding="utf-8")
    (provider_dir / "20260709T000000Z.json").write_text(json.dumps({
        "provider": "aws", "resolved_version": "6.54.0", "generated_at": "2026-07-09T00:00:00Z",
        "findings": [{"finding": "removed", "type": "resource:aws_x"}],
    }), encoding="utf-8")
    report = tmp_path / "reports" / "abc123"
    report.mkdir(parents=True)
    (report / "plan.json").write_text(json.dumps(PLAN), encoding="utf-8")

    coverage = ca.audit(str(report))

    status = coverage["schema_watch_status"]
    assert status["resolved_version"] == "6.54.0"  # the later timestamped report, not the earlier
    assert status["findings_count"] == 1


def test_kms_key_auto_priced_live_verified():
    plan = {"resource_changes": [
        {"address": "a.key", "mode": "managed", "type": "aws_kms_key",
         "change": {"actions": ["create"], "after": {}}},
    ]}
    coverage = ca.classify(plan)
    assert {r["resource_type"] for r in coverage["auto_priced"]} == {"aws_kms_key"}
