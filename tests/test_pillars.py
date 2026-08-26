"""
The 18 pillars, and the arithmetic that makes each later question specific.

The tests that matter here are the REFUSALS and the BANDS.

Refusals, because the failure this module exists to prevent is a confident recommendation
built on a number nobody supplied. A worker count derived from an absent volume looks exactly
like one derived from a real volume, and the operator cannot tell them apart -- so every
derivation must return `determinable: False` rather than a plausible default.

Bands, because the first version of object_size_plan reported 341 MB as "inside the 128-256
MB target". It was not inside it. The verdict was right and the sentence was wrong, which on
this surface is the worse of the two: the operator reads the sentence.

Depends on: core/architecture/pillars.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import json
import subprocess
import sys

import pytest

import pillars


# --- The catalogue ----------------------------------------------------------------------

def test_there_are_eighteen_pillars_numbered_one_to_eighteen():
    assert len(pillars.PILLARS) == 18
    assert pillars.PILLAR_IDS == tuple(range(1, 19))


def test_every_pillar_carries_depth_follow_ups():
    """The flat list was the defect. A pillar with no depth is a pillar back to being a form."""
    without = [p["key"] for p in pillars.PILLARS if not p["depth"]]
    assert not without, f"pillars with no follow-ups: {without}"


def test_every_pillar_names_what_is_usually_forgotten():
    without = [p["key"] for p in pillars.PILLARS if not p["forgotten"]]
    assert not without, f"pillars that name no omission: {without}"


def test_the_catalogue_is_ascii_only():
    """A cp1252 terminal raises UnicodeEncodeError rather than degrading, and this text is
    printed to one."""
    source = open(pillars.__file__, encoding="utf-8").read()
    offenders = sorted({c for c in source if ord(c) > 127})
    assert not offenders, f"non-ASCII characters: {offenders}"


def test_option_specific_depth_replaces_the_shared_depth():
    """A follow-up that fits every answer belongs at the top level, not inside one branch."""
    both = pillars.depth_for("data_quality", "Fail fast: abort the whole run on any "
                                             "assertion failure")
    assert both
    assert all("quarantine bucket" not in item for item in both), \
        "the quarantine follow-ups must not be asked of someone who chose fail-fast"


# --- Refusals ---------------------------------------------------------------------------

@pytest.mark.parametrize("volume", [None, "", 0])
def test_no_volume_means_no_worker_count(volume):
    result = pillars.glue_worker_plan(volume)
    assert result["determinable"] is False
    assert "volume" in result["reason"]
    assert "worker_type" not in result, "an absent input must not yield a sized answer"


def test_no_volume_means_no_object_size_verdict():
    assert pillars.object_size_plan(None, 24)["determinable"] is False


def test_no_partitioning_means_no_object_size_verdict():
    assert pillars.object_size_plan(50, None)["determinable"] is False


def test_a_stream_needs_both_rate_and_record_size():
    """One alone cannot size a shard: 1 MB/s and 1,000 records/s are different limits."""
    assert pillars.kinesis_shard_plan(5000, None)["determinable"] is False
    assert pillars.kinesis_shard_plan(None, 2)["determinable"] is False


def test_an_unknown_transform_shape_is_refused_not_guessed():
    result = pillars.engine_recommendation(500, "whatever")
    assert result["determinable"] is False


def test_spark_without_a_volume_names_the_gap_rather_than_sizing_it():
    result = pillars.engine_recommendation(None, "spark")
    assert result["determinable"] is False
    assert "sizing" not in result


# --- Object-size bands -------------------------------------------------------------------

def test_hourly_partitions_on_a_small_feed_are_reported_as_too_small():
    """The case the flat questionnaire could not catch: 8 GB/day hourly is 341 MB, but
    2 GB/day hourly is 85 MB -- the small-file problem, designed in at the partition key."""
    result = pillars.object_size_plan(2, 24, "mixed")
    assert result["verdict"] == "TOO_SMALL"
    assert result["per_partition_mb"] == pytest.approx(85.3, abs=0.2)
    assert result["options"], "a refusal must carry a way forward"


def test_an_object_inside_the_target_says_inside_and_is_inside():
    result = pillars.object_size_plan(3, 16, "mixed")           # 192 MB
    assert result["verdict"] == "OK"
    floor, ceiling = result["target_mb"]
    assert floor <= result["per_partition_mb"] <= ceiling, \
        "the OK band must actually contain the measured value"


def test_above_the_target_is_not_reported_as_inside_it():
    """341 MB with a 128-256 target was previously described as sitting inside it."""
    result = pillars.object_size_plan(8, 24, "mixed")
    assert result["per_partition_mb"] == pytest.approx(341.3, abs=0.2)
    assert result["verdict"] == "ABOVE_TARGET"
    assert "inside" not in result["because"]


def test_a_single_partition_for_a_large_feed_is_too_large():
    result = pillars.object_size_plan(500, 1, "mixed")
    assert result["verdict"] == "TOO_LARGE"


def test_the_write_heavy_floor_is_not_the_parquet_block_size():
    """64-128 MB is the write-heavy target, so 100 MB is fine there and small elsewhere."""
    assert pillars.object_size_plan(2.4, 24, "write_heavy")["verdict"] == "OK"
    assert pillars.object_size_plan(2.4, 24, "mixed")["verdict"] == "TOO_SMALL"


# --- Sizing arithmetic --------------------------------------------------------------------

def test_worker_memory_covers_the_working_set_times_the_shuffle_factor():
    result = pillars.glue_worker_plan(100, shuffle="wide")
    _name, _vcpu, memory_gb, _disk, _dpu = next(
        w for w in pillars.GLUE_WORKERS if w[0] == result["worker_type"])
    assert result["number_of_workers"] * memory_gb >= result["memory_target_gb"]


def test_a_narrow_transform_is_sized_smaller_than_a_wide_one():
    wide = pillars.glue_worker_plan(200, shuffle="wide")
    narrow = pillars.glue_worker_plan(200, shuffle="narrow")
    assert narrow["total_dpu"] < wide["total_dpu"]


def test_runs_per_day_divides_the_working_set():
    """Sizing from daily volume rather than per-run volume over-provisions every run."""
    once = pillars.glue_worker_plan(240, runs_per_day=1)
    hourly = pillars.glue_worker_plan(240, runs_per_day=24)
    assert hourly["per_run_gb"] == pytest.approx(10)
    assert hourly["total_dpu"] < once["total_dpu"]


def test_the_binding_kinesis_limit_is_named():
    """Many tiny records hit the 1,000/s record limit; few large ones hit 1 MB/s. The fix
    differs, so the answer says which one bound."""
    tiny = pillars.kinesis_shard_plan(events_per_sec=5000, avg_record_kb=0.1)
    assert tiny["binding_limit"] == "record count"
    assert tiny["shards"] == 5

    large = pillars.kinesis_shard_plan(events_per_sec=100, avg_record_kb=64)
    assert large["binding_limit"] == "throughput"
    assert large["shards"] == 7


def test_sql_only_does_not_get_a_spark_cluster_however_large_the_volume():
    result = pillars.engine_recommendation(50000, "sql_only")
    assert result["engine"] == "dbt-on-Athena"
    assert "compute-glue-etl" not in result["maps_to"]


def test_a_large_spark_job_is_told_to_price_emr_against_it():
    result = pillars.engine_recommendation(4000, "spark")
    assert "compute-emr-serverless" in result["maps_to"]
    assert "revisit_if" in result


# --- Sources ------------------------------------------------------------------------------

def test_every_published_capacity_carries_a_source():
    """A number without a citation is a remembered rule of thumb wearing a fact's clothes."""
    for plan in (pillars.glue_worker_plan(50),
                 pillars.object_size_plan(50, 24),
                 pillars.kinesis_shard_plan(1000, 1)):
        assert plan["source"].startswith("https://"), plan


def test_the_spark_factor_is_labelled_as_this_projects_assumption():
    source = open(pillars.__file__, encoding="utf-8").read()
    # The comment wraps, so compare on collapsed whitespace with the comment markers gone.
    preamble = " ".join(source.split("SPARK_MEMORY_FACTOR =")[0].replace("#", " ").split())
    assert "not a published Amazon ratio" in preamble, \
        "a heuristic presented as vendor guidance is the fabrication this repo bans"


# --- Interview flow ------------------------------------------------------------------------

def test_next_pillar_walks_in_order_and_ends():
    assert pillars.next_pillar([])["key"] == "ingestion_source"
    assert pillars.next_pillar(["ingestion_source"])["key"] == "storage_format"
    assert pillars.next_pillar(pillars.PILLAR_KEYS) is None


def test_next_pillar_carries_the_derivation_the_facts_already_support():
    rendered = pillars.next_pillar(
        [k for k in pillars.PILLAR_KEYS if k != "worker_sizing"],
        {"daily_gb": 100, "shuffle": "wide"})
    assert rendered["key"] == "worker_sizing"
    assert rendered["derived"]["determinable"] is True
    assert rendered["derived"]["worker_type"]


def test_missing_facts_names_what_is_still_unstated():
    assert "daily_gb" in pillars.missing_facts({})
    assert "daily_gb" not in pillars.missing_facts({"daily_gb": 50})


# --- CLI ------------------------------------------------------------------------------------

def _run(*args):
    return subprocess.run([sys.executable, pillars.__file__, *args],
                          capture_output=True, text=True)


def test_the_cli_derives_from_key_value_facts():
    done = _run("derive", "daily_gb=2", "partitions_per_day=24", "--json")
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["derived"]["partitioning"]["verdict"] == "TOO_SMALL"


def test_the_cli_refuses_an_unknown_fact_rather_than_ignoring_it():
    """Silently dropping a typo'd fact would derive from less than the operator supplied."""
    done = _run("derive", "dailygb=50")
    assert done.returncode != 0
    assert "unknown fact" in (done.stderr + done.stdout)


def test_the_cli_lists_all_eighteen():
    done = _run("list", "--json")
    assert done.returncode == 0, done.stderr
    assert len(json.loads(done.stdout)) == 18
