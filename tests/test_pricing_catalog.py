import json

import pricing_catalog as pc


def test_resolve_resource_type_glue_verified():
    entry = pc.resolve_resource_type("aws_glue_job")
    assert entry["service_code"] == "AWSGlue"
    assert entry["verified"] is True


def test_resolve_resource_type_unknown_is_none():
    assert pc.resolve_resource_type("aws_totally_made_up_thing") is None


def test_longest_prefix_wins_for_overlapping_kinesis_names():
    # aws_kinesis_firehose_delivery_stream, aws_kinesisanalyticsv2_application, and
    # aws_kinesis_stream all start with "aws_kinesis" -- the generic entry must not
    # swallow the two more specific services.
    firehose = pc.resolve_resource_type("aws_kinesis_firehose_delivery_stream")
    analytics = pc.resolve_resource_type("aws_kinesisanalyticsv2_application")
    streams = pc.resolve_resource_type("aws_kinesis_stream")
    assert firehose["service_code"] == "AmazonKinesisFirehose"
    assert analytics["service_code"] == "AmazonKinesisAnalytics"
    assert streams["service_code"] == "AmazonKinesis"


def test_previously_missing_services_are_now_mapped():
    # These were the concrete gaps found in the coverage audit: absent from every one of the
    # three old hand-maintained tables even though real modules create these resources.
    for rtype, expected_code in (
        ("aws_mwaa_environment", "AmazonMWAA"),
        ("aws_sns_topic", "AmazonSNS"),
    ):
        entry = pc.resolve_resource_type(rtype)
        assert entry is not None, f"{rtype} should now resolve to a serviceCode"
        assert entry["service_code"] == expected_code


def test_confirmed_free_security_group_and_iam():
    sg = pc.confirmed_free("aws_security_group")
    role = pc.confirmed_free("aws_iam_role")
    assert sg and sg["display_name"] == "Amazon VPC"
    assert role and role["display_name"] == "AWS IAM"


def test_confirmed_free_does_not_match_billable_types():
    # A resource that IS billable must never appear in the free registry.
    assert pc.confirmed_free("aws_glue_job") is None
    assert pc.confirmed_free("aws_kinesis_stream") is None


def test_service_display_name_falls_back_to_other():
    assert pc.service_display_name("aws_glue_job") == "AWS Glue"
    assert pc.service_display_name("aws_security_group") == "Amazon VPC"
    assert pc.service_display_name("aws_totally_made_up_thing") == "Other"


def test_file_hint_for_s3_object_is_more_specific_than_generic_s3():
    # Old FILE_HINTS list order made this entry unreachable (aws_s3_ matched first); the
    # longest-prefix-match here fixes that.
    assert pc.file_hint("aws_s3_object.script") == "scripts.tf"
    assert pc.file_hint("aws_s3_bucket.zone") == "s3.tf"


def test_list_service_codes_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(pc, "_aws_cli", lambda: "aws")
    calls = []

    def fake_run_json(cmd, timeout=30):
        calls.append(cmd)
        return {"Services": [{"ServiceCode": "AWSGlue", "AttributeNames": ["usagetype"]}]}

    monkeypatch.setattr(pc, "_run_json", fake_run_json)

    first = pc.list_service_codes()
    second = pc.list_service_codes()  # should hit the cache, not call _run_json again
    assert first == second
    assert len(calls) == 1
    assert (tmp_path / "aws_service_codes.json").exists()


def test_lookup_dimensions_writes_cache_per_service_region(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(pc, "_aws_cli", lambda: "aws")

    def fake_run_json(cmd, timeout=30):
        if "usagetype" in cmd:
            return {"AttributeValues": [{"Value": "USE1-ETL-DPU-Hour"}]}
        return {"AttributeValues": [{"Value": "Jobrun"}]}

    monkeypatch.setattr(pc, "_run_json", fake_run_json)
    dims = pc.lookup_dimensions("AWSGlue", region="us-east-1")
    assert "USE1-ETL-DPU-Hour" in dims["usagetype"]
    assert "Jobrun" in dims["operation"]
    cached = json.loads((tmp_path / "pricing_dims_AWSGlue_us-east-1.json").read_text(encoding="utf-8"))
    assert cached["region_hint"] == "us-east-1"
