"""
knowledge_diff.py tests -- real live-schema fetch against the real AWS provider, no mocks,
same discipline as test_schema_watch.py. Skips if terraform isn't installed.
"""
import pytest

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
