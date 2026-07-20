"""
tests for knowledge_delegation.py -- Step 4 of the knowledge-layer spine, the agent-delegation
contract for the semantic path.
"""
import datetime
import sqlite3

import pytest

import knowledge_delegation
import knowledge_store


def _insert(conn, source_type, claim_text, observed_at, attribute="acl"):
    return knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute=attribute, claim_text=claim_text,
        method="structural" if source_type == "schema" else "semantic", source_type=source_type,
        provider="aws", valid_from=observed_at, observed_at=observed_at,
    )


def test_build_delegation_request_returns_none_when_single_claim_resolved(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    assert knowledge_delegation.build_delegation_request(conn, "aws_s3_bucket", "acl") is None


def test_build_delegation_request_returns_none_when_schema_wins_via_freshness(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "web", "acl is fine", "2026-07-01T00:00:00Z")
    _insert(conn, "schema", "acl is deprecated", "2026-07-05T00:00:00Z")
    assert knowledge_delegation.build_delegation_request(conn, "aws_s3_bucket", "acl") is None


def test_build_delegation_request_returns_well_formed_dict_when_needs_review(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    web_id = _insert(conn, "web", "acl is fine", "2026-07-05T00:00:00Z")
    req = knowledge_delegation.build_delegation_request(conn, "aws_s3_bucket", "acl")
    assert req["resource_type"] == "aws_s3_bucket"
    assert req["attribute"] == "acl"
    assert req["reason"] == "non_schema_claim_observed_more_recently_than_schema_fetch"
    ids = {c["id"] for c in req["claims"]}
    assert ids == {schema_id, web_id}
    for c in req["claims"]:
        assert set(c) == {"id", "claim_text", "source_type", "source_url", "provider",
                           "provider_version", "observed_at", "valid_from"}


def test_build_delegation_request_orders_claims_newest_first(tmp_path):
    # Insertion order deliberately scrambled against the EXPECTED (sorted, newest-first) order,
    # not just "the older one is inserted second" -- inserting the older claim first means
    # SQLite's rowid/insertion order is [older_id, newer_id], which DIVERGES from the expected
    # [newer_id, older_id] assertion below. A bug that just returns resolve()'s claims list
    # unsorted, in whatever (insertion/rowid) order it came back in, is caught by this
    # divergence -- it would return [older_id, newer_id], not matching. (An earlier version of
    # this test inserted the NEWER claim first, which coincidentally matches the expected sorted
    # order and would pass even with no sort at all -- caught by task review, 2026-07-20.)
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    older_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    newer_id = _insert(conn, "web", "acl is fine", "2026-07-10T00:00:00Z")
    req = knowledge_delegation.build_delegation_request(conn, "aws_s3_bucket", "acl")
    assert [c["id"] for c in req["claims"]] == [newer_id, older_id]


def test_build_delegation_request_claims_list_is_never_empty(tmp_path):
    # Asserted directly, not inferred from resolve()'s len(claims) <= 1 early return living in a
    # different file -- same "assert don't infer" discipline as Task 3's empty-set test.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    _insert(conn, "web", "acl is fine", "2026-07-05T00:00:00Z")
    req = knowledge_delegation.build_delegation_request(conn, "aws_s3_bucket", "acl")
    assert len(req["claims"]) >= 2


def _count_claims(conn):
    return conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]


def test_record_delegation_verdict_inserts_claim_with_correct_fields(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    web_id = _insert(conn, "web", "acl is fine", "2026-07-05T00:00:00Z")
    verdict_id = knowledge_delegation.record_delegation_verdict(
        conn, "aws_s3_bucket", "acl", claim_text="acl is deprecated, per agent review",
        valid_from="2026-07-10T00:00:00Z", observed_at="2026-07-10T00:00:00Z",
        provider="aws", adjudicated_ids=[schema_id, web_id], confidence=0.9,
    )
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (verdict_id,)).fetchone()
    assert row["claim_text"] == "acl is deprecated, per agent review"
    assert row["provider"] == "aws"
    assert row["confidence"] == 0.9
    assert row["valid_from"] == "2026-07-10T00:00:00Z"


def test_record_delegation_verdict_always_sets_agent_delegated_and_semantic(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    verdict_id = knowledge_delegation.record_delegation_verdict(
        conn, "aws_s3_bucket", "acl", claim_text="anything",
        valid_from="2026-07-10T00:00:00Z", provider="aws", adjudicated_ids=[schema_id],
    )
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (verdict_id,)).fetchone()
    assert row["source_type"] == "agent_delegated"
    assert row["method"] == "semantic"


def test_record_delegation_verdict_defaults_observed_at_to_now(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    before = datetime.datetime.now(datetime.timezone.utc)
    verdict_id = knowledge_delegation.record_delegation_verdict(
        conn, "aws_s3_bucket", "acl", claim_text="anything",
        valid_from="2026-07-01T00:00:00Z", provider="aws", adjudicated_ids=[schema_id],
    )
    after = datetime.datetime.now(datetime.timezone.utc)
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (verdict_id,)).fetchone()
    observed = knowledge_store._parse_ts(row["observed_at"])
    assert before <= observed <= after


def test_record_delegation_verdict_accepts_explicit_observed_at(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    verdict_id = knowledge_delegation.record_delegation_verdict(
        conn, "aws_s3_bucket", "acl", claim_text="anything",
        valid_from="2026-07-01T00:00:00Z", observed_at="2026-07-15T00:00:00Z",
        provider="aws", adjudicated_ids=[schema_id],
    )
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (verdict_id,)).fetchone()
    assert row["observed_at"] == "2026-07-15T00:00:00Z"


def test_record_delegation_verdict_rejects_unparseable_valid_from(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="not-a-timestamp", provider="aws", adjudicated_ids=[schema_id],
        )
    assert _count_claims(conn) == before


def test_record_delegation_verdict_rejects_unparseable_observed_at(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="2026-07-01T00:00:00Z", observed_at="not-a-timestamp",
            provider="aws", adjudicated_ids=[schema_id],
        )
    assert _count_claims(conn) == before


def test_record_delegation_verdict_rejects_valid_from_after_observed_at(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="2026-07-20T00:00:00Z", observed_at="2026-07-10T00:00:00Z",
            provider="aws", adjudicated_ids=[schema_id],
        )
    assert _count_claims(conn) == before


def test_record_delegation_verdict_rejects_empty_adjudicated_ids(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="2026-07-01T00:00:00Z", provider="aws", adjudicated_ids=[],
        )
    assert _count_claims(conn) == before


def test_record_delegation_verdict_rejects_nonexistent_adjudicated_id(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="2026-07-01T00:00:00Z", provider="aws", adjudicated_ids=[999999],
        )
    assert _count_claims(conn) == before


def test_record_delegation_verdict_rejects_inactive_adjudicated_id(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    knowledge_store.invalidate_claim(conn, schema_id, valid_until="2026-07-05T00:00:00Z")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="2026-07-10T00:00:00Z", provider="aws", adjudicated_ids=[schema_id],
        )
    assert _count_claims(conn) == before


def test_record_delegation_verdict_rejects_adjudicated_id_from_different_attribute(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    other_attr_id = _insert(conn, "schema", "bucket is optional", "2026-07-01T00:00:00Z",
                             attribute="bucket")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="2026-07-10T00:00:00Z", provider="aws", adjudicated_ids=[other_attr_id],
        )
    assert _count_claims(conn) == before


def test_record_delegation_verdict_rejects_duplicate_adjudicated_ids(tmp_path):
    # ray's round-3 review, 2026-07-19: without this check, set(adjudicated_ids) silently dedups
    # the input during validation (which passes), then the claim_adjudications INSERT hits its
    # own PRIMARY KEY on the second (verdict_id, schema_id) row -- but only AFTER insert_claim
    # has already committed the verdict claim. This test alone doesn't prove the orphan is
    # avoided; that's what the transactional-rollback test below proves independently.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="2026-07-10T00:00:00Z", provider="aws",
            adjudicated_ids=[schema_id, schema_id],
        )
    assert _count_claims(conn) == before


def test_record_delegation_verdict_rolls_back_the_verdict_claim_if_the_adjudication_write_fails(
        tmp_path, monkeypatch):
    # The transactional wrap (ray's round-3 review, 2026-07-19) is structural, not dependent on
    # catching every bad input in advance -- the duplicate-ids check above closes the ONE
    # currently-known trigger, but this proves the invariant holds even for a failure the input
    # validation doesn't anticipate. Forced via a class-level monkeypatch of
    # sqlite3.Connection.executemany (reverted automatically after the test) since there's no
    # other way to make the second write fail with valid, non-duplicate input.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    before = _count_claims(conn)

    def _boom(self, *a, **k):
        raise sqlite3.IntegrityError("forced failure")
    monkeypatch.setattr(sqlite3.Connection, "executemany", _boom)

    with pytest.raises(sqlite3.IntegrityError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="2026-07-10T00:00:00Z", provider="aws", adjudicated_ids=[schema_id],
        )
    assert _count_claims(conn) == before
