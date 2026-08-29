"""
The requirements gate, and every way of getting past it that must not work.

Deferral is the interesting half. A real deferral carries a reason and counts as answered; a
bare "deferred", a lazy "TBD", or deferring every axis at once does not. Without that, the
gate is a formality anyone can satisfy by typing placeholder text, and the record it produces
reads as reviewed.

Depends on: core/architecture/requirements.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import copy
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


def test_bare_deferred_with_no_reason_does_not_count_as_answered():
    # Audit finding 2026-07-03: bare "deferred" (no reason) used to satisfy the gate.
    spec = {**COMPLETE, "non_functional": {**COMPLETE["non_functional"], "budget": "deferred"}}
    ok, missing = reqgate.validate(spec)
    assert not ok
    assert "non_functional.budget" in missing


def test_lazy_deferral_reason_does_not_count_as_answered():
    spec = {**COMPLETE, "non_functional": {**COMPLETE["non_functional"], "budget": "deferred: tbd"}}
    ok, missing = reqgate.validate(spec)
    assert not ok
    assert "non_functional.budget" in missing


def test_cannot_satisfy_the_gate_by_deferring_everything():
    # The exact loophole the audit flagged: six one-word "deferred" axes + minimal required
    # fields must NOT pass validate() cleanly.
    spec = {
        "goal": "x", "system_class": "x", "functional": ["x"],
        "non_functional": {axis: "deferred" for axis in reqgate.REQUIRED_NFR},
    }
    ok, missing = reqgate.validate(spec)
    assert not ok
    assert all(f"non_functional.{axis}" in missing for axis in reqgate.REQUIRED_NFR)


def test_more_than_two_real_deferrals_requires_signoff():
    real_deferrals = {axis: f"deferred: {axis} intentionally deferred pending review cycle"
                       for axis in reqgate.REQUIRED_NFR[:3]}
    remaining = {axis: "specified value" for axis in reqgate.REQUIRED_NFR[3:]}
    spec = {"goal": "x", "system_class": "x", "functional": ["x"],
            "non_functional": {**real_deferrals, **remaining}}
    ok, missing = reqgate.validate(spec)
    assert not ok
    assert any("deferral_signoff" in m for m in missing)

    spec["deferral_signoff"] = "approved by platform lead ahead of MVP scope cut"
    ok2, missing2 = reqgate.validate(spec)
    assert ok2 and missing2 == []


def test_two_or_fewer_real_deferrals_need_no_signoff():
    spec = {**COMPLETE, "non_functional": {
        **COMPLETE["non_functional"],
        "budget": "deferred: set in finance review",
        "retention": "deferred: pending legal review of data policy",
    }}
    ok, missing = reqgate.validate(spec)
    assert ok and missing == []


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


def test_parse_budget_usd_extracts_dollar_figure():
    spec = {"non_functional": {"budget": "sandbox account has a $1.00 hard budget alarm -- stay well under it"}}
    amount, source = reqgate.parse_budget_usd(spec)
    assert amount == 1.0
    assert "1.00" in source


def test_parse_budget_usd_takes_smallest_when_ambiguous():
    # Unlike parse_daily_gb's upper bound, a budget guardrail should err toward tripping
    # earlier -- take the smallest figure mentioned, not the largest.
    spec = {"non_functional": {"budget": "departmental cap is $500/mo but this pipeline should stay under $50"}}
    amount, _ = reqgate.parse_budget_usd(spec)
    assert amount == 50.0


def test_parse_budget_usd_never_guesses():
    for spec in ({"non_functional": {"budget": "deferred: pending finance approval"}}, {}, None):
        amount, source = reqgate.parse_budget_usd(spec)
        assert amount == 0 and source == ""


def _complete():
    """A deep copy, because these tests mutate nested blocks."""
    record = copy.deepcopy(COMPLETE)
    record.setdefault("data_pipeline", {k: "stated" for k in reqgate.DATA_FIELDS})
    return record



# --- The 19 pillars (additive; the interview's answers get a home) -----------------------

def test_the_template_has_a_slot_for_every_pillar():
    """18 asked, 16 slots, 2 driving generation was the gap. Every pillar now lands."""
    blank = reqgate.template()
    assert set(blank["pillars"]) == set(reqgate.PILLAR_KEYS)
    assert len(blank["pillars"]) == 19


def test_a_pillar_slot_carries_the_choice_and_the_operators_own_words():
    blank = reqgate.template()
    assert set(blank["pillars"]["ingestion_source"]) == {"choice", "notes"}


def test_pillar_validation_is_separate_from_the_generation_gate():
    """validate() gates generation and predates the pillars. Folding 18 new required fields
    into it would block every record written before today, so the pillar profile is its own
    check -- the same shape validate_data_pipeline() already uses."""
    record = _complete()
    ok, _missing = reqgate.validate(record)
    assert ok, "an existing complete record must not be invalidated by the new block"


def test_unanswered_pillars_names_what_the_interview_still_owes():
    record = _complete()
    record["pillars"] = {"ingestion_source": {"choice": "Batch files landing in S3", "notes": ""}}
    unanswered = reqgate.unanswered_pillars(record)
    assert "ingestion_source" not in unanswered
    assert "worker_sizing" in unanswered
    assert len(unanswered) == 18


def test_a_lazy_pillar_deferral_does_not_count_as_answered():
    """Same quality bar as the NFR axes: 'deferred: tbd' is not a decision."""
    record = _complete()
    record["pillars"] = {"proving": {"choice": "deferred: tbd", "notes": ""}}
    assert "proving" in reqgate.unanswered_pillars(record)


def test_a_real_pillar_deferral_does_count():
    record = _complete()
    record["pillars"] = {"proving": {
        "choice": "deferred: no pre-production account exists until Q3", "notes": ""}}
    assert "proving" not in reqgate.unanswered_pillars(record)


def test_pillar_facts_are_the_numbers_the_derivations_consume():
    record = _complete()
    record["pillar_facts"] = {"daily_gb": 50, "partitions_per_day": 24, "nonsense": 1}
    facts = reqgate.pillar_facts(record)
    assert facts["daily_gb"] == 50
    assert "nonsense" not in facts, "only known facts reach the arithmetic"


def test_daily_volume_falls_back_to_the_prose_answer():
    """parse_daily_gb already reads data_pipeline.data_volume. A record that answered it
    there must not have to repeat itself in pillar_facts."""
    record = _complete()
    record["data_pipeline"]["data_volume"] = "about 120 GB per day"
    assert reqgate.pillar_facts(record)["daily_gb"] == 120


def test_an_explicit_pillar_fact_beats_the_parsed_prose():
    record = _complete()
    record["data_pipeline"]["data_volume"] = "about 120 GB per day"
    record["pillar_facts"] = {"daily_gb": 40}
    assert reqgate.pillar_facts(record)["daily_gb"] == 40


def test_derived_sizing_computes_from_the_record_alone():
    record = _complete()
    record["pillar_facts"] = {"daily_gb": 2, "partitions_per_day": 24}
    derived = reqgate.derived_sizing(record)
    assert derived["partitioning"]["verdict"] == "TOO_SMALL"


def test_derived_sizing_refuses_rather_than_defaulting_when_the_record_is_silent():
    record = _complete()
    record["data_pipeline"]["data_volume"] = ""
    derived = reqgate.derived_sizing(record)
    assert derived["worker_sizing"]["determinable"] is False
    assert "worker_type" not in derived["worker_sizing"]


def test_an_unknown_pillar_key_in_a_record_is_reported_not_silently_kept():
    record = _complete()
    record["pillars"] = {"not_a_pillar": {"choice": "x", "notes": ""}}
    ok, problems = reqgate.validate_pillars(record)
    assert not ok
    assert any("not_a_pillar" in p for p in problems)
