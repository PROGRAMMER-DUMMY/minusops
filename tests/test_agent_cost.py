"""Token economics for the AGENTS COST view (PRD v14 WP-01).

The load-bearing property under test is not the arithmetic, it is the absence handling:
a missing transcript, a step with no token_usage, and a genuinely free stdlib step must
each produce a distinguishable result. Two of those are "no number exists"; only the
third is a real zero, and a report that renders them identically is a fabricated total.
"""
import json
import os

import pytest

import agent_cost_calculator as acc


# --- fixtures ---------------------------------------------------------------------------

def _write_transcript(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    return path


def _step(index, created_at, model="pro", prompt=1000, completion=100, cached=0, **extra):
    record = {
        "step_index": index,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "created_at": created_at,
        "thinking": f"reasoning for step {index}",
        "tool_calls": [],
        "model": model,
        "token_usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "cached_tokens": cached,
        },
    }
    record.update(extra)
    return record


@pytest.fixture
def transcript(tmp_path):
    """A three-step run: pro model, stdlib gate, haiku notifier."""
    return _write_transcript(str(tmp_path / "transcript.jsonl"), [
        _step(1, "2026-08-23T10:00:00Z", model="pro",
              prompt=14200, completion=1450, cached=84000,
              tool_calls=[{"name": "search_modules", "arguments": {"q": "glue"},
                           "toolAction": "READ", "toolSummary": "matched 3 modules"}]),
        _step(2, "2026-08-23T10:00:06Z", model="stdlib", prompt=0, completion=0, cached=0),
        _step(3, "2026-08-23T10:00:10Z", model="haiku", prompt=1200, completion=180, cached=0),
    ])


# --- FR-01 pricing matrix ---------------------------------------------------------------

def test_pro_tier_rates_match_the_pricing_matrix():
    # One million of each unit, so the result IS the published rate. Any transposed or
    # mistyped rate shows up here as a wrong dollar figure rather than a rounding wobble.
    priced = acc.price_step("pro", 1_000_000, 1_000_000, 1_000_000)
    assert priced["input_usd"] == pytest.approx(1.25)
    assert priced["output_usd"] == pytest.approx(10.00)
    assert priced["cached_usd"] == pytest.approx(0.30)
    assert priced["total_usd"] == pytest.approx(11.55)


def test_flash_and_haiku_share_the_cheap_tier_rates():
    flash = acc.price_step("flash", 1_000_000, 1_000_000, 1_000_000)
    haiku = acc.price_step("haiku", 1_000_000, 1_000_000, 1_000_000)
    assert flash["input_usd"] == pytest.approx(0.10)
    assert flash["output_usd"] == pytest.approx(0.40)
    assert flash["cached_usd"] == pytest.approx(0.025)
    assert flash["total_usd"] == pytest.approx(0.525)
    assert haiku == flash


def test_stdlib_steps_cost_zero_and_that_zero_is_real():
    priced = acc.price_step("stdlib", 1_000_000, 1_000_000, 1_000_000)
    assert priced["available"] is True
    assert priced["total_usd"] == 0.0


def test_step_costs_are_computed_from_the_transcript(transcript):
    run = acc.analyse_run(transcript)
    pro, gate, haiku = run["steps"]
    assert pro["cost"]["input_usd"] == pytest.approx(14200 / 1e6 * 1.25)
    assert pro["cost"]["output_usd"] == pytest.approx(1450 / 1e6 * 10.00)
    assert pro["cost"]["cached_usd"] == pytest.approx(84000 / 1e6 * 0.30)
    assert pro["cost"]["total_usd"] == pytest.approx(0.05745)
    assert gate["cost"]["total_usd"] == 0.0
    assert haiku["cost"]["total_usd"] == pytest.approx(0.000192)


# --- parsing and latency ----------------------------------------------------------------

def test_parsed_steps_carry_the_declared_schema_fields(transcript):
    run = acc.analyse_run(transcript)
    first = run["steps"][0]
    assert first["step_index"] == 1
    assert first["source"] == "MODEL"
    assert first["type"] == "PLANNER_RESPONSE"
    assert first["created_at"] == "2026-08-23T10:00:00Z"
    assert first["thinking"] == "reasoning for step 1"
    assert first["tool_calls"][0]["name"] == "search_modules"
    assert first["token_usage"]["prompt_tokens"] == 14200
    assert first["token_usage"]["completion_tokens"] == 1450
    assert first["token_usage"]["cached_tokens"] == 84000
    assert first["tier"] == "pro"


def test_latency_is_the_delta_between_consecutive_timestamps(transcript):
    run = acc.analyse_run(transcript)
    latencies = [step["latency_seconds"] for step in run["steps"]]
    # The first step has no predecessor, so its latency is unknown -- not zero.
    assert latencies[0] is None
    assert latencies[1] == pytest.approx(6.0)
    assert latencies[2] == pytest.approx(4.0)
    assert run["summary"]["total_latency_seconds"] == pytest.approx(10.0)


# --- run summary ------------------------------------------------------------------------

def test_summary_aggregates_tokens_cost_and_peak_context(transcript):
    summary = acc.analyse_run(transcript)["summary"]
    assert summary["input_tokens"] == 14200 + 1200
    assert summary["output_tokens"] == 1450 + 180
    assert summary["cached_tokens"] == 84000
    assert summary["total_usd"] == pytest.approx(0.05745 + 0.000192)
    assert summary["steps_total"] == 3
    assert summary["steps_priced"] == 3
    # Peak context is the busiest single step, not the run total.
    assert summary["peak_context_tokens"] == 14200 + 84000
    assert summary["context_ceiling"] == acc.DEFAULT_CONTEXT_CEILING
    assert summary["peak_context_fraction"] == pytest.approx(98200 / 1_000_000)
    assert summary["context_alert"] is False


def test_context_alert_trips_above_the_eighty_percent_ceiling(tmp_path):
    path = _write_transcript(str(tmp_path / "transcript.jsonl"), [
        _step(1, "2026-08-23T10:00:00Z", prompt=810_000, completion=10, cached=0),
    ])
    summary = acc.analyse_run(path)["summary"]
    assert summary["peak_context_fraction"] == pytest.approx(0.81)
    assert summary["context_alert"] is True


# --- absence: the three states that must never look alike -------------------------------

def test_a_missing_transcript_is_absent_rather_than_free(tmp_path):
    run = acc.analyse_run(str(tmp_path / "nope.jsonl"))
    assert run["available"] is False
    assert run["reason"]
    assert run["steps"] == []
    summary = run["summary"]
    # Every total is None. A dashboard reading 0 here would be asserting the run was free.
    for field in ("input_tokens", "output_tokens", "cached_tokens", "total_usd",
                  "total_latency_seconds", "peak_context_tokens", "peak_context_fraction"):
        assert summary[field] is None, field
    assert summary["context_alert"] is None


def test_a_step_without_token_usage_is_absent_not_zero(tmp_path):
    record = _step(1, "2026-08-23T10:00:00Z")
    del record["token_usage"]
    path = _write_transcript(str(tmp_path / "transcript.jsonl"), [record])
    step = acc.analyse_run(path)["steps"][0]
    assert step["token_usage"]["present"] is False
    assert step["token_usage"]["prompt_tokens"] is None
    assert step["cost"]["available"] is False
    assert step["cost"]["total_usd"] is None


def test_an_unmeasured_step_and_a_free_stdlib_step_are_distinguishable(tmp_path):
    unmeasured = _step(1, "2026-08-23T10:00:00Z")
    del unmeasured["token_usage"]
    path = _write_transcript(str(tmp_path / "transcript.jsonl"), [
        unmeasured,
        _step(2, "2026-08-23T10:00:01Z", model="stdlib", prompt=0, completion=0, cached=0),
    ])
    run = acc.analyse_run(path)
    absent, free = run["steps"]
    assert (absent["cost"]["total_usd"], free["cost"]["total_usd"]) == (None, 0.0)
    assert absent["token_usage"]["present"] is False
    assert free["token_usage"]["present"] is True
    assert run["summary"]["steps_missing_usage"] == 1
    assert run["summary"]["steps_priced"] == 1


def test_partial_token_usage_leaves_the_missing_field_absent(tmp_path):
    record = _step(1, "2026-08-23T10:00:00Z")
    del record["token_usage"]["cached_tokens"]
    path = _write_transcript(str(tmp_path / "transcript.jsonl"), [record])
    step = acc.analyse_run(path)["steps"][0]
    assert step["token_usage"]["cached_tokens"] is None
    assert step["cost"]["available"] is False


def test_a_run_with_no_measured_step_reports_no_total(tmp_path):
    record = _step(1, "2026-08-23T10:00:00Z")
    del record["token_usage"]
    path = _write_transcript(str(tmp_path / "transcript.jsonl"), [record])
    summary = acc.analyse_run(path)["summary"]
    assert summary["steps_total"] == 1
    assert summary["steps_priced"] == 0
    assert summary["total_usd"] is None
    assert summary["input_tokens"] is None
    assert summary["peak_context_fraction"] is None
    assert summary["context_alert"] is None


def test_an_empty_transcript_is_available_but_totals_nothing(tmp_path):
    path = _write_transcript(str(tmp_path / "transcript.jsonl"), [])
    run = acc.analyse_run(path)
    assert run["available"] is True
    assert run["steps"] == []
    assert run["summary"]["total_usd"] is None


def test_an_unknown_model_is_left_unpriced_not_guessed(tmp_path):
    path = _write_transcript(str(tmp_path / "transcript.jsonl"), [
        _step(1, "2026-08-23T10:00:00Z", model="some-unreleased-model", prompt=5000),
    ])
    run = acc.analyse_run(path)
    step = run["steps"][0]
    assert step["tier"] is None
    assert step["cost"]["available"] is False
    assert step["cost"]["total_usd"] is None
    # The tokens were really measured, so they are not the missing-usage case.
    assert step["token_usage"]["prompt_tokens"] == 5000
    assert run["summary"]["steps_unpriced_model"] == 1
    assert run["summary"]["total_usd"] is None


# --- fail-soft on malformed input -------------------------------------------------------

def test_a_malformed_line_is_counted_and_the_rest_still_parse(tmp_path):
    path = str(tmp_path / "transcript.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(_step(1, "2026-08-23T10:00:00Z", prompt=1000, completion=100)) + "\n")
        fh.write("{not json at all\n")
        fh.write("[1, 2, 3]\n")
        fh.write(json.dumps(_step(3, "2026-08-23T10:00:02Z", prompt=1000, completion=100)) + "\n")
    run = acc.analyse_run(path)
    assert len(run["steps"]) == 2
    assert run["summary"]["malformed_lines"] == 2
    # Counted, and visible enough that nobody has to diff line counts to notice.
    assert run["summary"]["total_usd"] is not None


def test_an_unreadable_timestamp_leaves_latency_absent(tmp_path):
    path = _write_transcript(str(tmp_path / "transcript.jsonl"), [
        _step(1, "2026-08-23T10:00:00Z"),
        _step(2, "not-a-timestamp"),
        _step(3, "2026-08-23T10:00:09Z"),
    ])
    run = acc.analyse_run(path)
    assert [s["latency_seconds"] for s in run["steps"]] == [None, None, None]
    assert run["summary"]["total_latency_seconds"] is None


# --- FR-03 / FR-07 secret redaction -----------------------------------------------------

SECRETS = [
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc123", "eyJhbGciOi"),
    ("token ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8 pushed", "ghp_A1b2C3"),
    ("slack xoxb-2461234567-2468012345678-AbCdEfGhIjKlMnOpQrStUvWx ok", "xoxb-2461"),
    ("aws_access_key_id = AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQ==\n-----END RSA PRIVATE KEY-----",
     "MIIEowIBAAKCAQ"),
    ('password: "hunter2-not-a-real-one"', "hunter2"),
    ("api_key=sk-live-9f8e7d6c5b4a3210", "sk-live-9f8e7d6c"),
]


@pytest.mark.parametrize("text,leak", SECRETS)
def test_known_secret_shapes_are_scrubbed(text, leak):
    scrubbed = acc.redact(text)
    assert leak not in scrubbed
    assert acc.REDACTION in scrubbed


def test_redaction_keeps_the_surrounding_text_readable():
    scrubbed = acc.redact("cloned repo with ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8 then failed")
    assert scrubbed == f"cloned repo with {acc.REDACTION} then failed"


def test_redaction_walks_nested_structures_and_leaves_numbers_alone():
    scrubbed = acc.redact({
        "tool_calls": [{"name": "push", "arguments": {"token": "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4"}}],
        "prompt_tokens": 14200,
        "nested": ["AKIAIOSFODNN7EXAMPLE", None, True],
    })
    assert scrubbed["tool_calls"][0]["arguments"]["token"] == acc.REDACTION
    assert scrubbed["tool_calls"][0]["name"] == "push"
    assert scrubbed["prompt_tokens"] == 14200
    assert scrubbed["nested"] == [acc.REDACTION, None, True]


def test_a_secret_bearing_key_is_redacted_whatever_its_value_looks_like():
    # The value has no recognisable token shape; the key name is the only signal there is.
    scrubbed = acc.redact({"db_password": "correct horse battery staple", "region": "eu-west-1"})
    assert scrubbed["db_password"] == acc.REDACTION
    assert scrubbed["region"] == "eu-west-1"


def test_raw_step_telemetry_is_redacted_before_it_is_returned(tmp_path):
    """FR-03: the raw JSON inspector must never be the thing that leaks the credential."""
    path = _write_transcript(str(tmp_path / "transcript.jsonl"), [
        _step(1, "2026-08-23T10:00:00Z",
              thinking="I will authenticate with ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
              tool_calls=[{"name": "http", "arguments": {"header": "Bearer sk-live-abcdef123456"},
                           "toolAction": "WRITE", "toolSummary": "posted"}]),
    ])
    step = acc.analyse_run(path)["steps"][0]
    blob = json.dumps(step)
    assert "ghp_A1b2C3" not in blob
    assert "sk-live-abcdef" not in blob
    assert acc.REDACTION in step["thinking"]
    assert acc.REDACTION in json.dumps(step["raw"])
    # Redaction must not have disturbed the numbers the ledger renders.
    assert step["token_usage"]["prompt_tokens"] == 1000
    assert step["cost"]["total_usd"] == pytest.approx(1000 / 1e6 * 1.25 + 100 / 1e6 * 10.0)
