"""
Increment 1 of claim-grounded-authoring: the claims table must hold knowledge that is NOT
about a single resource type -- architecture choices, developer practices, templates.

Before this, `resource_type TEXT NOT NULL` made every claim per-resource-type, so
"for sub-second latency use Kinesis, not batch Glue" had nowhere to live.
"""
import pytest

import knowledge_store

_TS = "2026-07-26T00:00:00Z"


def _claim(conn, **over):
    kwargs = dict(
        resource_type="aws_s3_bucket", attribute="acl", claim_text="acl is deprecated",
        method="structural", source_type="schema", provider="aws",
        valid_from=_TS, observed_at=_TS,
    )
    kwargs.update(over)
    return knowledge_store.insert_claim(conn, **kwargs)


def test_scope_defaults_to_schema_for_existing_callers(tmp_path):
    """Every current call site omits scope; those claims must keep working and read as 'schema'."""
    conn = knowledge_store.init_db(str(tmp_path / "c.db"))
    cid = _claim(conn)
    assert conn.execute("SELECT scope FROM claims WHERE id=?", (cid,)).fetchone()["scope"] == "schema"
    conn.close()


def test_architecture_claim_needs_no_resource_type(tmp_path):
    """The whole point: cross-cutting knowledge with resource_type=None must insert."""
    conn = knowledge_store.init_db(str(tmp_path / "c.db"))
    cid = _claim(
        conn, scope="architecture", resource_type=None, attribute=None,
        claim_text="sub-second latency -> Kinesis + Managed Flink, not batch Glue",
        method="semantic", source_type="agent_delegated",
    )
    row = conn.execute("SELECT * FROM claims WHERE id=?", (cid,)).fetchone()
    assert row["resource_type"] is None
    assert row["scope"] == "architecture"
    conn.close()


def test_practice_and_template_scopes_are_accepted(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "c.db"))
    for scope in ("practice", "template"):
        cid = _claim(conn, scope=scope, resource_type=None, attribute=None,
                     claim_text=f"a {scope} claim", method="semantic",
                     source_type="agent_delegated")
        assert conn.execute("SELECT scope FROM claims WHERE id=?", (cid,)).fetchone()["scope"] == scope
    conn.close()


def test_pricing_map_is_resource_scoped_not_cross_cutting(tmp_path):
    """A pricing_map claim maps a tf_type to a serviceCode, so it inherently HAS a resource
    type -- it belongs with schema, not with the cross-cutting scopes."""
    conn = knowledge_store.init_db(str(tmp_path / "c.db"))
    cid = _claim(conn, scope="pricing_map", attribute=None,
                 claim_text="aws_s3_bucket -> AmazonS3", method="semantic",
                 source_type="agent_delegated")
    assert conn.execute("SELECT scope FROM claims WHERE id=?", (cid,)).fetchone()["scope"] == "pricing_map"
    with pytest.raises(ValueError):
        _claim(conn, scope="pricing_map", resource_type=None, attribute=None)
    conn.close()


def test_unknown_scope_is_rejected_not_silently_stored(tmp_path):
    """A typo'd scope must fail loudly -- a claim filed under a scope nothing queries is
    invisible knowledge, which is worse than no claim."""
    conn = knowledge_store.init_db(str(tmp_path / "c.db"))
    with pytest.raises(ValueError):
        _claim(conn, scope="architecure", resource_type=None, attribute=None)
    conn.close()


def test_schema_scope_still_requires_a_resource_type(tmp_path):
    """Nullable resource_type is for cross-cutting scopes only. A schema claim about
    nothing in particular is incoherent and must not be storable."""
    conn = knowledge_store.init_db(str(tmp_path / "c.db"))
    with pytest.raises(ValueError):
        _claim(conn, scope="schema", resource_type=None)
    conn.close()
