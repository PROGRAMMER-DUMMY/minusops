"""
knowledge_degradation.py tests -- the stubbed tests below fake schema_claims_for_type()'s return
to run fast with no live fetch. Their fake return shape is asserted, separately, against the REAL
function's actual return in test_schema_claims_for_type_shape_matches_the_degradation_stubs
(Task 4) -- a stub that silently drifts from reality is exactly how the +00:00/Z bug survived,
so that drift-guard is not optional.
"""
import pytest

import knowledge_degradation
import knowledge_store
import toolpath

TERRAFORM = toolpath.find_tool("terraform")

_FIXED_CLAIM = {
    "resource_type": "aws_s3_bucket", "attribute": "bucket",
    "claim_text": "bucket: optional, not deprecated", "method": "structural",
    "source_type": "schema", "provider": "aws", "provider_version": "6.54.0",
    "valid_from": "2026-07-19T00:00:00Z", "observed_at": "2026-07-19T00:00:00Z",
}


def test_check_and_refresh_inserts_when_no_prior_claim_exists(tmp_path, monkeypatch):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [dict(_FIXED_CLAIM)])
    summary = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    assert len(summary["inserted"]) == 1
    assert summary["invalidated"] == []
    assert summary["removed_attributes"] == []


def test_check_and_refresh_always_inserts_even_when_nothing_changed(tmp_path, monkeypatch):
    # THE decision this task locks in: no content-hash dedup. A re-check finding zero real
    # change still inserts a fresh claim and invalidates the old one -- the observed_at bump is
    # load-bearing for the freshness clause's own noise-queue reasoning (ray's Q2), not a no-op.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [dict(_FIXED_CLAIM)])
    summary1 = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    summary2 = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    assert len(summary2["inserted"]) == 1
    assert summary2["inserted"] != summary1["inserted"]  # a genuinely new row, not a no-op
    assert summary2["invalidated"] == summary1["inserted"]  # invalidates exactly the prior insert
    active = knowledge_store._active_schema_claims_for_resource(conn, "aws_s3_bucket")
    assert len(active) == 1  # exactly one active claim survives, not two accumulating
    assert active[0]["id"] == summary2["inserted"][0]


def test_check_and_refresh_invalidates_the_old_claim_with_the_new_claims_valid_from(tmp_path, monkeypatch):
    # Proves Task 1's two-clock discipline is actually wired correctly at the call site, not just
    # correct in isolation: valid_until on the OLD row must equal the NEW claim's valid_from,
    # NOT its observed_at (ray's review, 2026-07-19 -- valid_until must track the fact-validity
    # axis, not when this check happened to notice). valid_from and observed_at are set to
    # DIFFERENT, easily-distinguishable values here specifically so this can't pass by coincidence
    # if the two axes were conflated -- same standard as the invalidated_at/valid_until swap test.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [dict(_FIXED_CLAIM)])
    summary1 = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    old_id = summary1["inserted"][0]
    later_claim = dict(_FIXED_CLAIM, valid_from="2026-08-01T00:00:00Z", observed_at="2026-09-15T00:00:00Z")
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [dict(later_claim)])
    knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    old_row = conn.execute("SELECT * FROM claims WHERE id = ?", (old_id,)).fetchone()
    assert old_row["valid_until"] == "2026-08-01T00:00:00Z"  # the new claim's valid_from
    assert old_row["valid_until"] != "2026-09-15T00:00:00Z"  # NOT the new claim's observed_at


def test_check_and_refresh_targets_the_newest_of_multiple_pre_existing_active_claims(tmp_path, monkeypatch):
    # Simulates a DB used before this step's invalidate-on-insert discipline existed (Step 2's own
    # tests/proof runs never invalidated anything, so more than one active schema claim CAN
    # already exist for one attribute). check_and_refresh's bookkeeping must reference the NEWEST
    # of them, not whichever row SQLite happens to return last from _active_schema_claims_for_resource.
    #
    # Insertion order deliberately scrambled against timestamp order (the NEWER-by-observed_at
    # claim inserted FIRST, older SECOND) -- required by this plan's Global Constraints testing
    # convention. Ray's review, 2026-07-19: the original draft of this test inserted older-then-
    # newer, matching timestamp order, so SQLite's natural (unordered) row-return order happened
    # to coincide with the correct _parse_ts-based pick -- the buggy dict-comprehension version
    # would very likely have passed the exact same test. This ordering is what makes the two
    # implementations actually diverge; see Step 5 below for the required fail-first proof.
    #
    # Disclosed limit, not asserted here: the OTHER (older) pre-existing duplicate is left
    # untouched, still active -- this step guarantees correct behavior going forward, not
    # retroactive cleanup of duplicates that predate it.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    newer_id = knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="bucket", claim_text="bucket: optional, not deprecated",
        method="structural", source_type="schema", provider="aws", provider_version="6.54.0",
        valid_from="2026-06-01T00:00:00Z", observed_at="2026-06-01T00:00:00Z",
    )
    older_id = knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="bucket", claim_text="bucket: stale duplicate",
        method="structural", source_type="schema", provider="aws", provider_version="6.50.0",
        valid_from="2026-05-01T00:00:00Z", observed_at="2026-05-01T00:00:00Z",
    )
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [dict(_FIXED_CLAIM)])
    summary = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    assert newer_id in summary["invalidated"]
    assert older_id not in summary["invalidated"]
