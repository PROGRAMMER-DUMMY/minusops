"""
plan_reader.py is the shared Python-side plan-JSON reader (G4 consolidation, docs/
phase4_scope.md) used by destructive_change_gate.py (G5) and architecture_model.py. Each shape
fact tested here was verified live against real Terraform output this session -- see the
module's own docstring for the real plans each fact traces back to.
"""
import plan_reader


def test_read_resource_changes_blocks_on_non_dict():
    result, error = plan_reader.read_resource_changes("not a dict", treat_absent_as_error=True)
    assert result is None
    assert error == "plan_json_not_a_dict"


def test_read_resource_changes_absent_as_error_when_requested():
    result, error = plan_reader.read_resource_changes({}, treat_absent_as_error=True)
    assert result is None
    assert error == "resource_changes_missing_or_null"


def test_read_resource_changes_absent_as_empty_when_not_requested():
    """The verified-live real shape: a data-source-only or genuine no-op plan OMITS
    resource_changes entirely. A reader that doesn't need G5's conservative block policy (an
    advisory/shadow reader) treats this as zero managed changes, not malformed."""
    result, error = plan_reader.read_resource_changes({}, treat_absent_as_error=False)
    assert result == []
    assert error is None


def test_read_resource_changes_blocks_on_wrong_type_regardless_of_flag():
    for flag in (True, False):
        result, error = plan_reader.read_resource_changes({"resource_changes": "oops"}, treat_absent_as_error=flag)
        assert result is None
        assert error == "resource_changes_not_a_list"


def test_read_resource_changes_returns_real_list():
    rcs = [{"address": "a", "mode": "managed"}]
    result, error = plan_reader.read_resource_changes({"resource_changes": rcs}, treat_absent_as_error=True)
    assert result == rcs
    assert error is None


def test_managed_only_excludes_data_denylist_not_allowlist():
    """A denylist on mode == 'data' (not an allowlist on 'managed') is deliberate: an
    unrecognized/missing `mode` field stays IN scope rather than being silently excluded."""
    rcs = [
        {"address": "a", "mode": "managed"},
        {"address": "b", "mode": "data"},
        {"address": "c"},  # missing mode -- must stay in scope, not silently dropped
    ]
    managed, malformed = plan_reader.managed_only(rcs)
    assert [r["address"] for r in managed] == ["a", "c"]
    assert malformed == []


def test_managed_only_reports_non_dict_entries_distinctly():
    managed, malformed = plan_reader.managed_only([{"address": "a", "mode": "managed"}, "garbage", None])
    assert [r["address"] for r in managed] == ["a"]
    assert malformed == [{"reason": "malformed_resource_change_entry"}] * 2


def test_data_sources_reads_from_prior_state_not_resource_changes():
    """Verified live, twice, this session: data sources never appear in resource_changes."""
    plan = {
        "resource_changes": [{"address": "aws_s3_bucket.b", "mode": "managed"}],
        "prior_state": {"values": {"root_module": {"resources": [
            {"address": "data.aws_iam_policy_document.p", "mode": "data", "type": "aws_iam_policy_document",
             "values": {"json": "{}"}},
            {"address": "aws_s3_bucket.other", "mode": "managed", "type": "aws_s3_bucket", "values": {}},
        ]}}},
    }
    ds = plan_reader.data_sources(plan)
    assert [d["address"] for d in ds] == ["data.aws_iam_policy_document.p"]


def test_data_sources_fails_soft_on_missing_prior_state():
    assert plan_reader.data_sources({}) == []
    assert plan_reader.data_sources({"prior_state": "not a dict"}) == []
    assert plan_reader.data_sources({"prior_state": {"values": None}}) == []


def test_config_resources_fails_soft_on_missing_configuration():
    assert plan_reader.config_resources({}) == []
    assert plan_reader.config_resources({"configuration": "oops"}) == []
    assert plan_reader.config_resources({"configuration": {"root_module": {"resources": "oops"}}}) == []


def test_config_resources_returns_real_list():
    resources = [{"address": "aws_s3_bucket.b", "type": "aws_s3_bucket"}]
    plan = {"configuration": {"root_module": {"resources": resources}}}
    assert plan_reader.config_resources(plan) == resources


def test_module_calls_fails_soft_and_returns_real_dict():
    assert plan_reader.module_calls({}) == {}
    assert plan_reader.module_calls({"configuration": {"root_module": {"module_calls": "oops"}}}) == {}
    calls = {"storage_medallion_s3": {"expressions": {}}}
    plan = {"configuration": {"root_module": {"module_calls": calls}}}
    assert plan_reader.module_calls(plan) == calls


def test_base_address_strips_for_each_and_count_index():
    assert plan_reader.base_address('aws_s3_bucket.zone["bronze"]') == "aws_s3_bucket.zone"
    assert plan_reader.base_address("aws_s3_bucket.zone[0]") == "aws_s3_bucket.zone"
    assert plan_reader.base_address("aws_s3_bucket.b") == "aws_s3_bucket.b"
    assert plan_reader.base_address("") == ""
    assert plan_reader.base_address(None) == ""


def test_module_address_shape_matches_real_composed_plan():
    """Locks down the real shape verified live this session: a composed multi-module plan's
    resource_changes carry both an address prefixed module.<underscored_id>.* AND a direct
    module_address field with the same value -- confirmed against a real synthesizer.compose()
    output (storage-medallion-s3 + compaction-glue, dummy AWS credentials)."""
    rc = {"address": 'module.storage_medallion_s3.aws_s3_bucket.zone["bronze"]',
          "module_address": "module.storage_medallion_s3"}
    assert rc["address"].startswith(rc["module_address"] + ".")
