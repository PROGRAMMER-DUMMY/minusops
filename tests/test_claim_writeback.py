"""
P2.1 -- the missing half of the memory loop.

author-context READS claims. Nothing wrote them, so the store stayed empty forever and
"MinusOps remembers" was aspiration. knowledge_delegation.record_delegation_verdict() was
built and correct but had no CLI front-end, so a driving agent had no way to call it.

This is the write leg: agent researches -> `synthesizer.py remember` -> next author-context
starts from it.
"""
import json
import subprocess
import sys
import os

import knowledge_store as ks
import synthesizer

_TS = "2026-07-26T00:00:00Z"


def _conn(tmp_path):
    return ks.init_db(str(tmp_path / "claims.db"))


def test_remember_records_a_researched_claim(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    monkeypatch.setattr(synthesizer, "_claims_conn", lambda: conn)

    claim_id = synthesizer.remember_claim(
        resource_type="aws_dynamodb_table", attribute="billing_mode",
        claim_text="PAY_PER_REQUEST avoids provisioned capacity charges",
        source_url="https://docs.aws.amazon.com/amazondynamodb/x",
        source_type="vendor_docs", valid_from=_TS)

    assert claim_id
    row = conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
    assert row["claim_text"].startswith("PAY_PER_REQUEST")
    assert row["source_url"].startswith("https://")
    assert row["method"] == "semantic"
    assert row["scope"] == "schema"


def test_a_researched_claim_comes_back_out_of_author_context(tmp_path, monkeypatch):
    """The actual loop closing: write, then read it back where the agent will see it."""
    conn = _conn(tmp_path)
    monkeypatch.setattr(synthesizer, "_claims_conn", lambda: conn)
    monkeypatch.setattr("schema_watch.get_type_schema",
                        lambda p, t, kind="resource": {"attributes": {}})

    synthesizer.remember_claim(
        resource_type="aws_dynamodb_table", attribute="billing_mode",
        claim_text="PAY_PER_REQUEST avoids provisioned capacity charges",
        source_url="https://docs.aws.amazon.com/x", valid_from=_TS)

    ctx = synthesizer.assemble_authoring_context("aws_dynamodb_table", "j", "a pipeline")
    assert any("PAY_PER_REQUEST" in c["claim_text"] for c in ctx["claims"])
    conn.close()


def test_architecture_knowledge_needs_no_resource_type(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    monkeypatch.setattr(synthesizer, "_claims_conn", lambda: conn)
    cid = synthesizer.remember_claim(
        scope="architecture", resource_type=None, attribute=None,
        claim_text="sub-second latency -> Kinesis, not batch Glue",
        source_url="https://aws.amazon.com/blogs/x", valid_from=_TS)
    assert conn.execute("SELECT scope FROM claims WHERE id=?", (cid,)).fetchone()["scope"] == "architecture"
    conn.close()


def test_a_claim_without_a_source_is_refused(tmp_path, monkeypatch):
    """Provenance is the entire point. An unsourced claim is a rumour with a timestamp,
    and it would be indistinguishable from a verified one at read time."""
    conn = _conn(tmp_path)
    monkeypatch.setattr(synthesizer, "_claims_conn", lambda: conn)
    try:
        synthesizer.remember_claim(
            resource_type="aws_s3_bucket", attribute="acl",
            claim_text="something an agent believes", source_url="", valid_from=_TS)
        assert False, "expected ValueError for a sourceless claim"
    except ValueError as exc:
        assert "source_url" in str(exc)
    conn.close()


def test_cost_claims_may_map_but_never_price(tmp_path, monkeypatch):
    """Decision #19: agents contribute tf_type -> serviceCode mappings only. A rate or a
    free-ness assertion from an agent would violate 'never fabricate a number'."""
    conn = _conn(tmp_path)
    monkeypatch.setattr(synthesizer, "_claims_conn", lambda: conn)

    ok = synthesizer.remember_claim(
        scope="pricing_map", resource_type="aws_dynamodb_table", attribute=None,
        claim_text="aws_dynamodb_table -> AmazonDynamoDB",
        source_url="https://docs.aws.amazon.com/x", valid_from=_TS)
    assert ok

    for bad in ("$0.25 per GB-month", "this resource is free", "costs 0 USD"):
        try:
            synthesizer.remember_claim(
                scope="pricing_map", resource_type="aws_dynamodb_table", attribute=None,
                claim_text=bad, source_url="https://x", valid_from=_TS)
            assert False, f"expected refusal for priced claim: {bad}"
        except ValueError as exc:
            assert "price" in str(exc).lower() or "free" in str(exc).lower()
    conn.close()


def test_remember_cli_round_trips(tmp_path):
    """End to end through the actual CLI a driving agent would call."""
    env = dict(os.environ, MINUSOPS_OUTPUT_DIR=str(tmp_path))
    root = os.path.dirname(os.path.dirname(os.path.abspath(synthesizer.__file__)))
    cmd = [sys.executable, os.path.join(root, "generation", "synthesizer.py"), "remember",
           "--resource-type", "aws_s3_bucket", "--attribute", "acl",
           "--claim", "acl is deprecated, use aws_s3_bucket_acl",
           "--source-url", "https://registry.terraform.io/x",
           "--valid-from", _TS, "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["recorded"] is True
    assert payload["claim_id"]
    assert os.path.exists(os.path.join(str(tmp_path), "knowledge", "claims.db"))
