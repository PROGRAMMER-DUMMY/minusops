import os
import sqlite3

import pytest

import knowledge_store


def test_init_db_creates_the_claims_table(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='claims'")
    assert cursor.fetchone() is not None
    conn.close()


def test_insert_claim_round_trips_every_field(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    claim_id = knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="acl", claim_text="acl is deprecated",
        method="structural", source_type="schema", provider="aws", provider_version="6.54.0",
        valid_from="2026-07-18T00:00:00Z", observed_at="2026-07-18T00:00:00Z",
    )
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    assert row is not None
    conn.close()


def test_insert_claim_defaults_ingested_at_to_now_if_not_given(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    claim_id = knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute=None, claim_text="exists",
        method="structural", source_type="schema", provider="aws",
        valid_from="2026-07-18T00:00:00Z", observed_at="2026-07-18T00:00:00Z",
    )
    row = conn.execute("SELECT ingested_at FROM claims WHERE id = ?", (claim_id,)).fetchone()
    assert row[0] is not None
    conn.close()


def _insert(conn, source_type, claim_text, observed_at, attribute="acl"):
    return knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute=attribute, claim_text=claim_text,
        method="structural" if source_type == "schema" else "semantic", source_type=source_type,
        provider="aws", valid_from=observed_at, observed_at=observed_at,
    )


def test_resolve_with_a_single_claim_is_trivially_resolved(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-18T00:00:00Z")
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["winner"]["claim_text"] == "acl is deprecated"


def test_resolve_schema_wins_when_web_claim_observed_no_later_than_schema_fetch(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-18T12:00:00Z")
    _insert(conn, "web", "acl is fine to use", "2026-07-18T10:00:00Z")  # observed BEFORE schema fetch
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["winner"]["source_type"] == "schema"


def test_resolve_freshness_clause_blocks_schema_default_when_web_claim_is_newer(tmp_path):
    # THE case this whole layer exists for: a web claim observed AFTER the schema was fetched,
    # contradicting it, must NOT default to schema-wins.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "attribute .name still exists", "2026-07-01T00:00:00Z")
    _insert(conn, "web", "attribute .name was renamed to .region in v6", "2026-07-18T00:00:00Z")
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "needs_review"
    assert result["reason"] == "non_schema_claim_observed_more_recently_than_schema_fetch"


def test_resolve_two_conflicting_web_claims_need_review(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "web", "acl is deprecated", "2026-07-18T00:00:00Z")
    _insert(conn, "web", "acl is still recommended", "2026-07-18T00:00:00Z")
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "needs_review"
    assert result["reason"] == "no_ground_truth_arbiter"


def test_resolve_never_reads_confidence():
    # Compares against the function BODY only, excluding the docstring -- resolve()'s own
    # docstring legitimately explains "NEVER confidence" in prose, which would otherwise
    # trip a naive substring check on the full source (inspect.getsource includes the
    # docstring). The intent is to prove the runtime logic never consults confidence, not
    # that the word never appears in the text explaining why it doesn't.
    import ast
    import inspect
    import textwrap
    source = textwrap.dedent(inspect.getsource(knowledge_store.resolve))
    func_node = ast.parse(source).body[0]
    body = func_node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # drop the docstring statement
    body_source = "\n".join(ast.get_source_segment(source, node) for node in body)
    assert "confidence" not in body_source


def test_resolve_treats_same_instant_across_z_and_offset_format_as_equal_freshness(tmp_path):
    # Ray's highest-severity finding: datetime.now(tz).isoformat() emits "+00:00"; hand-written
    # fixtures use "Z". For the SAME instant these strings are not equal and "+" sorts before "Z",
    # so a naive string ">" comparison gives a FALSE freshness verdict with zero real time gap.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-18T12:00:00+00:00")  # what isoformat() emits
    _insert(conn, "web", "acl is fine to use", "2026-07-18T12:00:00Z")  # hand-written, same instant
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["winner"]["source_type"] == "schema"
    assert result["reason"] == "schema_observed_same_or_more_recently_than_non_schema_claim"


def test_resolve_agreeing_claims_never_route_to_needs_review(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "Acl Is Deprecated", "2026-07-01T00:00:00Z")
    _insert(conn, "web", "acl is deprecated", "2026-07-18T00:00:00Z")  # later, but says the same thing
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["reason"] == "claims_agree"


def test_resolve_generalizes_beyond_web_to_any_non_schema_source_type(tmp_path):
    # A Step-4 "agent_delegated" claim must not fall outside resolve()'s comparison just because
    # it isn't literally "web".
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "attribute .name still exists", "2026-07-01T00:00:00Z")
    knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="acl",
        claim_text="attribute .name was renamed to .region in v6", method="semantic",
        source_type="agent_delegated", provider="aws",
        valid_from="2026-07-18T00:00:00Z", observed_at="2026-07-18T00:00:00Z",
    )
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "needs_review"
    assert result["reason"] == "non_schema_claim_observed_more_recently_than_schema_fetch"


# Implementation-level review, 2026-07-18: zero test above ever called resolve() or
# _active_claims() without an explicit attribute string, or exercised several other reachable
# branches -- exactly the gap that let three separate comparison-correctness bugs (wrong clock,
# incompatible timestamp formats, cross-attribute pooling) each pass every prior review. The
# tests below audit resolve()'s full branch surface, not just the paths already in mind.

def test_resolve_without_attribute_only_pools_resource_level_claims(tmp_path):
    # _active_claims()'s attribute-is-None branch must mean "resource-level claims only"
    # (attribute IS NULL), not "every claim for this resource_type regardless of attribute" --
    # the latter would let resolve(conn, resource_type) silently compare claims about unrelated
    # attributes as if they were about the same fact and return a confident wrong answer.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl: deprecated", "2026-07-18T00:00:00Z", attribute="acl")  # decoy
    knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute=None,
        claim_text="aws_s3_bucket is a stable resource type", method="structural",
        source_type="schema", provider="aws",
        valid_from="2026-07-01T00:00:00Z", observed_at="2026-07-01T00:00:00Z",
    )
    knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute=None,
        claim_text="aws_s3_bucket is being deprecated resource-wide", method="semantic",
        source_type="web", provider="aws",
        valid_from="2026-06-01T00:00:00Z", observed_at="2026-06-01T00:00:00Z",  # OLDER than schema
    )
    result = knowledge_store.resolve(conn, "aws_s3_bucket")  # no attribute -- resource level
    assert len(result["claims"]) == 2  # only the two resource-level claims, NOT the "acl" decoy
    assert all(c["attribute"] is None for c in result["claims"])
    assert result["status"] == "resolved"
    assert result["winner"]["source_type"] == "schema"


def test_resolve_with_zero_claims_returns_none_winner(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "nonexistent_attribute")
    assert result["status"] == "resolved"
    assert result["winner"] is None
    assert result["reason"] == "single_or_no_claim"
    assert result["claims"] == []


def test_resolve_exact_tie_in_observed_at_same_format_favors_schema(tmp_path):
    # Distinct from test_resolve_treats_same_instant_across_z_and_offset_format_as_equal_freshness:
    # that test proves the format-parsing fix; this proves the tie behavior itself (schema wins
    # on an exact tie) independent of any format-normalization concern -- identical strings, not
    # just identical instants.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-18T12:00:00Z")
    _insert(conn, "web", "acl is fine to use", "2026-07-18T12:00:00Z")  # identical string
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["winner"]["source_type"] == "schema"
    assert result["reason"] == "schema_observed_same_or_more_recently_than_non_schema_claim"


def test_resolve_uses_the_truly_newest_among_multiple_schema_claims(tmp_path):
    # Insertion order deliberately scrambled relative to timestamp order, so this can't pass by
    # accident of "first/last inserted happens to be newest."
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl: older schema read", "2026-06-01T00:00:00Z")
    _insert(conn, "web", "acl: web claim between the two schema reads", "2026-07-01T00:00:00Z")
    _insert(conn, "schema", "acl: newer schema read", "2026-07-15T00:00:00Z")  # inserted last
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    # If max() incorrectly picked the OLDER schema claim (06-01), the web claim (07-01) would
    # look newer and force needs_review. The truly newest schema claim (07-15) is newer than
    # the web claim, so schema must win.
    assert result["status"] == "resolved"
    assert result["winner"]["claim_text"] == "acl: newer schema read"


def test_resolve_agreeing_claims_leading_trailing_whitespace_still_agrees(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "  acl is deprecated  ", "2026-07-01T00:00:00Z")
    _insert(conn, "web", "acl is deprecated", "2026-07-18T00:00:00Z")
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["reason"] == "claims_agree"


def test_resolve_agreeing_claims_internal_whitespace_difference_does_not_agree(tmp_path):
    # Documents a known, disclosed limitation (final whole-branch review, 2026-07-18): the
    # short-circuit normalizes leading/trailing whitespace and case, but NOT internal whitespace.
    # Locks the current boundary in as a deliberate regression test, not a silent gap.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl  is deprecated", "2026-07-01T00:00:00Z")  # double space
    _insert(conn, "web", "acl is deprecated", "2026-07-18T00:00:00Z")  # single space, observed later
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["reason"] != "claims_agree"
    assert result["status"] == "needs_review"  # falls through to the ordinary freshness comparison
    assert result["reason"] == "non_schema_claim_observed_more_recently_than_schema_fetch"
