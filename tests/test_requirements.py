import pytest

import requirements as reqgate

COMPLETE = {
    "goal": "serve curated analytics",
    "system_class": "data-pipeline",
    "functional": ["analysts query gold tables"],
    "non_functional": {
        "latency": "hourly", "scale": "50 GB/day", "availability": "99.9%",
        "retention": "archive after 90d", "security": "KMS", "budget": "$500/mo",
    },
}


def test_complete_record_passes():
    ok, missing = reqgate.validate(COMPLETE)
    assert ok and missing == []


def test_missing_fields_are_reported():
    ok, missing = reqgate.validate({"goal": "x", "functional": [], "non_functional": {"latency": "1s"}})
    assert not ok
    assert "system_class" in missing
    assert "functional (at least one capability)" in missing
    assert "non_functional.budget" in missing       # unanswered NFR axes are named


def test_explicit_deferral_counts_as_answered():
    spec = {**COMPLETE, "non_functional": {**COMPLETE["non_functional"], "budget": "deferred: set in finance review"}}
    ok, missing = reqgate.validate(spec)
    assert ok
    assert "budget" in reqgate.deferred_axes(spec)   # deferral is recorded, not silent


def test_require_raises_with_the_missing_list():
    with pytest.raises(reqgate.RequirementsIncomplete) as exc:
        reqgate.require({"goal": "x"})
    assert "system_class" in exc.value.missing


def test_template_is_a_valid_blank_skeleton():
    t = reqgate.template()
    assert set(t["non_functional"]) == set(reqgate.REQUIRED_NFR)
    ok, missing = reqgate.validate(t)
    assert not ok                                    # blank template is intentionally incomplete


def test_template_includes_data_pipeline_profile():
    t = reqgate.template()
    assert set(t["data_pipeline"]) == set(reqgate.DATA_FIELDS)


def test_is_data_pipeline_detection():
    assert reqgate.is_data_pipeline({"system_class": "data-pipeline"}) is True
    assert reqgate.is_data_pipeline({"goal": "build a lakehouse for analytics"}) is True
    assert reqgate.is_data_pipeline({"system_class": "web-app", "goal": "a todo app"}) is False
    # a populated data_pipeline block signals a data workload even without keyword
    assert reqgate.is_data_pipeline({"system_class": "svc", "data_pipeline": {"sources": "kafka"}}) is True


def test_validate_data_pipeline_reports_missing_and_accepts_deferral():
    ok, missing = reqgate.validate_data_pipeline({"data_pipeline": {"sources": "kafka"}})
    assert not ok
    assert "data_pipeline.storage_zones" in missing
    assert "data_pipeline.data_quality" in missing

    complete = {f: "specified" for f in reqgate.DATA_FIELDS}
    complete["freshness_sla"] = "deferred: set after profiling"   # deferral counts as answered
    ok2, missing2 = reqgate.validate_data_pipeline({"data_pipeline": complete})
    assert ok2 and missing2 == []


def test_generic_validate_unaffected_by_data_profile():
    # A complete generic record with no data_pipeline block still passes the generic gate.
    ok, missing = reqgate.validate(COMPLETE)
    assert ok and missing == []


def test_write_and_load_roundtrip(tmp_path):
    path = reqgate.write(str(tmp_path), COMPLETE, gathered_by="alice")
    assert path.endswith("requirements.json")
    loaded = reqgate.load(str(tmp_path))             # load by directory resolves the file
    assert loaded["gathered_by"] == "alice" and loaded["gathered_at"]
