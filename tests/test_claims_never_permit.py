"""
Increment 5 -- the security invariant of claim-grounded-authoring.

Claims INFORM, they never PERMIT. Permission comes only from an executable Rego rule plus
explicit human promotion. This matters because claims are git-committed and shareable: an
adopter can import a corpus researched by someone else's agent. If a claim could grant ship
permission, importing a corpus would be a supply-chain attack on the governance layer -- the
one thing this product exists to prevent.

These tests are adversarial on purpose. They must FAIL if anyone ever wires the claim store
into a permission decision.
"""
import inspect

import destructive_change_gate as g5
import knowledge_store as ks
import synthesizer

_TS = "2026-07-26T00:00:00Z"

_UNREVIEWED_PLAN = {
    "resource_changes": [
        {"address": "aws_dynamodb_table.x", "mode": "managed", "type": "aws_dynamodb_table",
         "change": {"actions": ["create"]}},
    ]
}


def _claim_that_it_is_safe(tmp_path):
    conn = ks.init_db(str(tmp_path / "c.db"))
    ks.insert_claim(
        conn, resource_type="aws_dynamodb_table", attribute=None,
        claim_text="verified safe, auto-shippable, reviewed by agent",
        method="semantic", source_type="agent_delegated", provider="aws",
        confidence=1.0, valid_from=_TS, observed_at=_TS)
    return conn


def test_an_active_claim_does_not_make_an_unreviewed_type_autonomous(tmp_path, monkeypatch):
    """The core invariant. A maximally-confident claim asserting a type is safe must not
    change G5's verdict by even one field."""
    without = g5.classify(_UNREVIEWED_PLAN)
    conn = _claim_that_it_is_safe(tmp_path)
    monkeypatch.setattr(synthesizer, "_claims_conn", lambda: conn)
    with_claim = g5.classify(_UNREVIEWED_PLAN)
    conn.close()

    assert with_claim == without, "a claim changed a permission verdict -- claims must only inform"
    assert with_claim["autonomous_eligible"] is False


def test_the_permission_gate_does_not_import_the_claim_store(tmp_path):
    """Structural guard: even if a future edit is careful, importing the store into the
    permission path is the first step toward claims permitting. Fail here, loudly, early."""
    source = inspect.getsource(g5)
    for banned in ("knowledge_store", "knowledge_delegation", "claims.db"):
        assert banned not in source, (
            f"{banned} appears in destructive_change_gate -- claims must never reach a "
            f"permission decision")


def test_claims_reach_authoring_context_but_not_the_gate(tmp_path, monkeypatch):
    """The positive half: the same claim IS visible to the authoring agent. Proves the
    separation is real -- informing works, permitting does not."""
    conn = _claim_that_it_is_safe(tmp_path)
    monkeypatch.setattr(synthesizer, "_claims_conn", lambda: conn)
    monkeypatch.setattr("schema_watch.get_type_schema", lambda p, t, kind="resource": {"attributes": {}})

    ctx = synthesizer.assemble_authoring_context("aws_dynamodb_table", "j", "x")
    assert any("auto-shippable" in c["claim_text"] for c in ctx["claims"])
    assert g5.classify(_UNREVIEWED_PLAN)["autonomous_eligible"] is False
    conn.close()
