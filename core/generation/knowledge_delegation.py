"""
knowledge_delegation.py -- the agent-delegation contract for the semantic path: packages a
needs_review resolve() result into a structured hand-off for the driving agent, and records the
agent's verdict back as a new claim (Task 2). No local model anywhere in this path -- the driving
agent does the adjudication; this module only packages the question and records the answer.

Materiality (whether a new observation is worth recording at all) is deliberately NOT decided
here -- that is the driving agent's job, checking resolve()'s current winner before ever calling
record_delegation_verdict. Materiality must never live in resolve() or any stdlib-only core
module (ray's Q2 reconciliation).
"""
import knowledge_store


def build_delegation_request(conn, resource_type, attribute):
    result = knowledge_store.resolve(conn, resource_type, attribute)
    if result["status"] != "needs_review":
        return None
    # claims is asserted non-empty by its own dedicated test
    # (test_build_delegation_request_claims_list_is_never_empty) rather than left as an inference
    # from resolve()'s len(claims) <= 1 early return living in a different file.
    ordered = sorted(
        result["claims"], key=lambda c: knowledge_store._parse_ts(c["observed_at"]), reverse=True)
    return {
        "resource_type": resource_type,
        "attribute": attribute,
        "reason": result["reason"],
        "claims": [
            {
                "id": c["id"], "claim_text": c["claim_text"], "source_type": c["source_type"],
                "source_url": c["source_url"], "provider": c["provider"],
                "provider_version": c["provider_version"], "observed_at": c["observed_at"],
                "valid_from": c["valid_from"],
            }
            for c in ordered
        ],
    }
