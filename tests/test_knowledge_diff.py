"""
knowledge_diff.py tests -- real live-schema fetch against the real AWS provider, no mocks,
same discipline as test_schema_watch.py. Skips if terraform isn't installed.
"""
import pytest


# Live Terraform/provider-schema access: minutes, not seconds. Deselected from the
# default run (see pyproject addopts) and executed explicitly with `pytest -m slow`.
pytestmark = pytest.mark.slow

import knowledge_diff
import toolpath

TERRAFORM = toolpath.find_tool("terraform")


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_schema_claims_for_aws_s3_bucket_includes_a_real_required_attribute():
    claims = knowledge_diff.schema_claims_for_type("aws", "aws_s3_bucket")
    by_attr = {c["attribute"]: c for c in claims}
    assert "bucket" in by_attr or "bucket_prefix" in by_attr  # real schema, not asserted blind
    assert all(c["source_type"] == "schema" for c in claims)
    assert all(c["method"] == "structural" for c in claims)
    assert all(c["provider_version"] for c in claims)


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_schema_claims_for_an_unknown_type_returns_empty_not_an_exception():
    claims = knowledge_diff.schema_claims_for_type("aws", "aws_totally_made_up_type")
    assert claims == []


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_end_to_end_real_schema_claim_beats_an_older_contradicting_web_claim(tmp_path):
    import knowledge_store
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_claims = knowledge_diff.schema_claims_for_type(
        "aws", "aws_s3_bucket", observed_at="2026-07-18T12:00:00Z")
    for c in schema_claims:
        knowledge_store.insert_claim(conn, **c)
    acl_claim = next((c for c in schema_claims if c["attribute"] == "acl"), None)
    assert acl_claim is not None
    knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="acl",
        claim_text="acl works fine, no deprecation", method="semantic", source_type="web",
        provider="aws", valid_from="2026-07-10T00:00:00Z", observed_at="2026-07-10T00:00:00Z",
    )  # observed BEFORE the schema fetch above
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["winner"]["source_type"] == "schema"
    conn.close()


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_end_to_end_newer_web_claim_forces_review_not_schema_default(tmp_path):
    import knowledge_store
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_claims = knowledge_diff.schema_claims_for_type(
        "aws", "aws_s3_bucket", observed_at="2026-06-01T00:00:00Z")  # fetched weeks ago
    for c in schema_claims:
        knowledge_store.insert_claim(conn, **c)
    knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="acl",
        claim_text="acl was removed in the latest provider release", method="semantic",
        source_type="web", provider="aws",
        valid_from="2026-07-18T00:00:00Z", observed_at="2026-07-18T00:00:00Z",  # observed just now
    )
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "needs_review"
    assert result["reason"] == "non_schema_claim_observed_more_recently_than_schema_fetch"
    conn.close()
