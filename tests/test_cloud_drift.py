"""
Issue #1 / decision #13 -- out-of-band changes must be visible.

Terraform records objects changed outside Terraform in a top-level `resource_drift` array.
Nothing read it, so someone's console fix got silently reverted by the next plan and the
report called it a routine update.

The dangerous case is specifically a REVERT: the plan setting an attribute back to what it
was before a human changed it. Drift that the plan leaves alone is informational.
"""
import plan_reader
import cloud_drift

# Someone widened a bucket's versioning in the console; the plan would set it back.
REVERTING = {
    "resource_drift": [
        {"address": "aws_s3_bucket.data", "mode": "managed", "type": "aws_s3_bucket",
         "change": {"actions": ["update"],
                    "before": {"versioning": "Disabled", "tags": {"a": "1"}},
                    "after": {"versioning": "Enabled", "tags": {"a": "1"}}}},
    ],
    "resource_changes": [
        {"address": "aws_s3_bucket.data", "mode": "managed", "type": "aws_s3_bucket",
         "change": {"actions": ["update"],
                    "before": {"versioning": "Enabled"},
                    "after": {"versioning": "Disabled"}}},
    ],
}

# Drift exists, but the plan does not touch the drifted attribute.
NOT_REVERTING = {
    "resource_drift": [
        {"address": "aws_s3_bucket.data", "mode": "managed", "type": "aws_s3_bucket",
         "change": {"actions": ["update"],
                    "before": {"tags": {}}, "after": {"tags": {"owner": "alice"}}}},
    ],
    "resource_changes": [
        {"address": "aws_s3_bucket.data", "mode": "managed", "type": "aws_s3_bucket",
         "change": {"actions": ["update"],
                    "before": {"versioning": "Disabled"},
                    "after": {"versioning": "Enabled"}}},
    ],
}


def test_plan_reader_exposes_resource_drift():
    drift = plan_reader.resource_drift(REVERTING)
    assert len(drift) == 1
    assert drift[0]["address"] == "aws_s3_bucket.data"


def test_absent_resource_drift_is_empty_not_an_error():
    assert plan_reader.resource_drift({"resource_changes": []}) == []
    assert plan_reader.resource_drift({}) == []


def test_a_plan_that_undoes_a_console_change_is_flagged_with_the_attribute():
    result = cloud_drift.classify(REVERTING)
    assert result["reverts_out_of_band_changes"] is True
    assert result["reverted_count"] == 1
    row = result["reverted"][0]
    assert row["address"] == "aws_s3_bucket.data"
    assert "versioning" in row["attributes"]


def test_drift_the_plan_leaves_alone_is_reported_but_not_a_revert():
    result = cloud_drift.classify(NOT_REVERTING)
    assert result["drift_count"] == 1
    assert result["reverts_out_of_band_changes"] is False
    assert result["reverted"] == []


def test_no_drift_is_clean():
    result = cloud_drift.classify({"resource_changes": [], "resource_drift": []})
    assert result["drift_count"] == 0
    assert result["reverts_out_of_band_changes"] is False


def test_malformed_drift_entries_do_not_crash_the_gate():
    """Fail-visible, not fail-crash: a weird drift entry must not take down a plan."""
    result = cloud_drift.classify({"resource_drift": ["nonsense", {}], "resource_changes": []})
    assert result["malformed_count"] == 2
    assert result["reverts_out_of_band_changes"] is False


# --- the enforcement half: a revert must not slip through auto-approve -------------
import plan_gate


def test_auto_approve_is_refused_when_the_plan_reverts_an_out_of_band_change(tmp_path, capsys):
    drift = cloud_drift.classify(REVERTING)
    blocked = plan_gate._reject_if_reverts_out_of_band_and_auto_approve(
        str(tmp_path), "auto-approve", drift, False)
    assert blocked is True
    err = capsys.readouterr().err
    assert "reverts changes made outside Terraform" in err
    assert "aws_s3_bucket.data" in err
    assert "versioning" in err


def test_gatekeeper_mode_is_never_blocked_by_drift():
    """A human is already in the loop at approve time -- that IS the review this routes to."""
    drift = cloud_drift.classify(REVERTING)
    assert plan_gate._reject_if_reverts_out_of_band_and_auto_approve(
        ".", "gatekeeper", drift, False) is False


def test_non_reverting_drift_does_not_block_auto_approve():
    """Drift the plan leaves alone is advisory. Blocking on it would train operators to
    ignore the check."""
    drift = cloud_drift.classify(NOT_REVERTING)
    assert plan_gate._reject_if_reverts_out_of_band_and_auto_approve(
        ".", "auto-approve", drift, False) is False


def test_missing_drift_record_does_not_block(tmp_path):
    """An approval recorded before this check existed has no cloud_drift key. It must not
    hard-block every legacy approval."""
    assert plan_gate._reject_if_reverts_out_of_band_and_auto_approve(
        str(tmp_path), "auto-approve", {}, False) is False
