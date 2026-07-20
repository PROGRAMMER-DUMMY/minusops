"""
tests for knowledge_delegation.py -- Step 4 of the knowledge-layer spine, the agent-delegation
contract for the semantic path.
"""
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
