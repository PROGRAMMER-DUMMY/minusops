"""
Increment 4: author-context hands the driving agent what MinusOps already verified.

Without this the agent re-researches every type from scratch every run, which is the
whole reason the knowledge layer existed unwired.
"""
import knowledge_store as ks
import synthesizer

_TS = "2026-07-26T00:00:00Z"


def _store(tmp_path, **over):
    conn = ks.init_db(str(tmp_path / "c.db"))
    kwargs = dict(
        resource_type="aws_dynamodb_table", attribute="billing_mode",
        claim_text="PAY_PER_REQUEST avoids provisioned capacity charges",
        method="semantic", source_type="agent_delegated", provider="aws",
        source_url="https://docs.aws.amazon.com/x", valid_from=_TS, observed_at=_TS,
    )
    kwargs.update(over)
    ks.insert_claim(conn, **kwargs)
    return conn


def test_context_carries_claims_for_the_resource_type(tmp_path, monkeypatch):
    conn = _store(tmp_path)
    monkeypatch.setattr(synthesizer, "_claims_conn", lambda: conn)
    monkeypatch.setattr("schema_watch.get_type_schema", lambda p, t, kind="resource": {"attributes": {}})

    ctx = synthesizer.assemble_authoring_context("aws_dynamodb_table", "j", "a data pipeline")

    assert "claims" in ctx
    texts = [c["claim_text"] for c in ctx["claims"]]
    assert "PAY_PER_REQUEST avoids provisioned capacity charges" in texts
    got = ctx["claims"][0]
    assert got["source_type"] == "agent_delegated"
    assert got["source_url"] == "https://docs.aws.amazon.com/x"
    assert got["observed_at"] == _TS
    conn.close()


def test_cross_cutting_architecture_claims_are_included(tmp_path, monkeypatch):
    """Architecture/practice knowledge has no resource_type, but it is exactly the
    'best architectures / dev practices' grounding an authoring agent needs."""
    conn = _store(tmp_path, scope="architecture", resource_type=None, attribute=None,
                  claim_text="sub-second latency -> Kinesis, not batch Glue")
    monkeypatch.setattr(synthesizer, "_claims_conn", lambda: conn)
    monkeypatch.setattr("schema_watch.get_type_schema", lambda p, t, kind="resource": {"attributes": {}})

    ctx = synthesizer.assemble_authoring_context("aws_dynamodb_table", "j", "streaming")

    assert any("Kinesis" in c["claim_text"] for c in ctx["claims"])
    conn.close()


def test_context_degrades_to_empty_claims_when_no_store_exists(tmp_path, monkeypatch):
    """An adopter with no corpus must still get schema + grounding, never a crash."""
    monkeypatch.setattr(synthesizer, "_claims_conn", lambda: None)
    monkeypatch.setattr("schema_watch.get_type_schema", lambda p, t, kind="resource": {"attributes": {}})

    ctx = synthesizer.assemble_authoring_context("aws_dynamodb_table", "j", "x")

    assert ctx["claims"] == []
    assert ctx["blocked"] is False


def test_blocked_context_still_reports_claims_key(tmp_path, monkeypatch):
    """A nonexistent type blocks before authoring; the shape must stay stable so callers
    never KeyError on the failure path."""
    monkeypatch.setattr(synthesizer, "_claims_conn", lambda: None)
    monkeypatch.setattr("schema_watch.get_type_schema", lambda p, t, kind="resource": None)

    ctx = synthesizer.assemble_authoring_context("aws_not_a_thing", "j", "x")

    assert ctx["blocked"] is True
    assert ctx["claims"] == []
