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


def test_write_and_load_roundtrip(tmp_path):
    path = reqgate.write(str(tmp_path), COMPLETE, gathered_by="alice")
    assert path.endswith("requirements.json")
    loaded = reqgate.load(str(tmp_path))             # load by directory resolves the file
    assert loaded["gathered_by"] == "alice" and loaded["gathered_at"]
