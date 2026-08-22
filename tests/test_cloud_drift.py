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


# --- FR-06: telemetry correlation (PRD-ARCH-2026-005) ---------------------------------
#
# Drift tells a reviewer that a Glue job was resized outside Terraform. It does not tell
# them WHY, so the honest options are "revert it" and "leave it", with no way to choose.
# CloudTrail names who did it and CloudWatch shows the OutOfMemoryError that preceded it,
# which turns the same finding into a decision. Every part of this is advisory: it runs on
# ambient credentials that a dry-run box does not have, so it must never be the reason a
# gate fails.

GLUE_DRIFT = {
    "resource_drift": [
        {"address": "aws_glue_job.etl", "mode": "managed", "type": "aws_glue_job",
         "change": {"actions": ["update"],
                    "before": {"worker_type": "G.1X"},
                    "after": {"worker_type": "G.2X"}}},
    ],
    "resource_changes": [
        {"address": "aws_glue_job.etl", "mode": "managed", "type": "aws_glue_job",
         "change": {"actions": ["update"],
                    "before": {"worker_type": "G.2X"},
                    "after": {"worker_type": "G.1X"}}},
    ],
}


def _telemetry(address, resource_type):
    return {"identity": "john.doe@acme.com",
            "errors": ["java.lang.OutOfMemoryError: Java heap space"]}


def test_telemetry_is_off_unless_a_lookup_is_supplied():
    """The default path stays offline and free. Ambient AWS calls from a plan classifier
    would make an offline `plan` slower and, on a misconfigured box, fail."""
    result = cloud_drift.classify(GLUE_DRIFT)
    assert result["telemetry_available"] is False
    assert result["telemetry_evidence"] == []


def test_a_supplied_lookup_attaches_identity_and_error_signature():
    result = cloud_drift.classify(GLUE_DRIFT, telemetry=_telemetry)

    assert result["telemetry_available"] is True
    evidence = result["telemetry_evidence"][0]
    assert evidence["address"] == "aws_glue_job.etl"
    assert evidence["identity"] == "john.doe@acme.com"
    assert "OutOfMemoryError" in evidence["errors"][0]


def test_correlated_evidence_reaches_the_operator_summary():
    text = cloud_drift.format_result(cloud_drift.classify(GLUE_DRIFT, telemetry=_telemetry))

    assert "john.doe@acme.com" in text
    assert "OutOfMemoryError" in text


def test_a_lookup_that_raises_never_takes_down_the_classifier():
    """It runs subprocess AWS CLI calls against a possibly-unreachable account. Failing
    closed here would let an unrelated network problem block every plan."""
    def _explode(address, resource_type):
        raise RuntimeError("no credentials")

    result = cloud_drift.classify(GLUE_DRIFT, telemetry=_explode)

    assert result["drift_count"] == 1
    assert result["reverts_out_of_band_changes"] is True
    assert result["telemetry_available"] is False


def test_a_lookup_returning_nothing_is_not_evidence():
    """No CloudTrail hit means we do not know who changed it. Emitting an empty row would
    read as "correlated" when nothing was."""
    result = cloud_drift.classify(GLUE_DRIFT, telemetry=lambda a, t: None)

    assert result["telemetry_evidence"] == []
    assert result["telemetry_available"] is False


def test_telemetry_never_changes_the_revert_verdict():
    """An explained change is still a change the plan is about to undo. Correlation informs
    the reviewer; it does not grant permission."""
    plain = cloud_drift.classify(GLUE_DRIFT)
    correlated = cloud_drift.classify(GLUE_DRIFT, telemetry=_telemetry)

    assert plain["reverted"] == correlated["reverted"]
    assert correlated["reverts_out_of_band_changes"] is True


def test_the_lookup_is_asked_only_about_drifted_resources():
    asked = []

    def _record(address, resource_type):
        asked.append((address, resource_type))
        return None

    cloud_drift.classify(GLUE_DRIFT, telemetry=_record)

    assert asked == [("aws_glue_job.etl", "aws_glue_job")]


def test_aws_telemetry_returns_none_without_credentials(monkeypatch):
    import aws as aws_provider

    monkeypatch.setattr(aws_provider, "run_aws", lambda args, timeout=20: (False, None, "boom"))

    assert cloud_drift.aws_telemetry("aws_glue_job.etl", "aws_glue_job") is None


def test_aws_telemetry_is_not_attempted_for_unsupported_types():
    """Only Glue and EMR expose a job-run history worth correlating. Calling CloudTrail for
    every drifted S3 bucket would add a round trip per resource for nothing."""
    assert cloud_drift.aws_telemetry("aws_s3_bucket.data", "aws_s3_bucket") is None
