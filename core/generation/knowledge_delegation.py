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
import datetime

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


def record_delegation_verdict(conn, resource_type, attribute, *, claim_text, valid_from,
                                provider, adjudicated_ids, observed_at=None, source_url=None,
                                confidence=None, provider_version=None):
    """Records the driving agent's adjudication as a new claim -- an INSERT, never an UPDATE.
    source_type and method are fixed by this function (not caller-supplied): "agent_delegated"
    and "semantic".

    This is the external boundary of the two-clock model -- an arbitrary driving agent supplies
    valid_from/observed_at. Both are validated: each must parse via knowledge_store._parse_ts
    (rejects garbage/wrong-type input) AND must be timezone-AWARE (rejects a well-formed ISO
    string with no timezone designator, e.g. "2026-07-10T00:00:00") -- every other timestamp in
    this store is aware (schema/web claims always emit "Z" or "+00:00"; _parse_ts's own docstring
    is about format, not awareness), and a naive value that slips past validation here does not
    fail at insert time: it gets stored, and the NEXT resolve() call on this resource_type/
    attribute crashes with "TypeError: can't compare offset-naive and offset-aware datetimes"
    inside its max()-by-observed_at call, permanently bricking that attribute until the row is
    manually invalidated (found by the final whole-step review, 2026-07-20 -- the exact same
    hazard class _parse_ts exists to prevent for FORMAT, reintroduced for AWARENESS at precisely
    the new external-input boundary this step added). This check runs immediately after parsing,
    before the valid_from > observed_at comparison below -- a naive valid_from was previously
    able to raise a raw TypeError out of that comparison, escaping this function's own documented
    ValueError contract entirely. valid_from must not be AFTER observed_at (a fact can't be
    observed before it became true). observed_at defaults to now (always aware, via
    datetime.now(timezone.utc)); valid_from has no default (same no-default precedent as
    invalidate_claim's valid_until).

    adjudicated_ids is the second external-input boundary surface: required (no default), must
    be non-empty, free of duplicates, and every id must reference a currently-active claim for
    this exact resource_type/attribute -- a stale, fabricated, or duplicated id would make the
    coverage test in resolve() (Task 3) meaningless, silently. All validation runs BEFORE any
    write, AND the verdict claim insert and its claim_adjudications rows share a single
    transaction (rolled back on any failure) -- "no partial claim row on rejection" does not
    depend on this function's own validation having anticipated every possible failure mode
    (ray's round-3 review, 2026-07-19: insert_claim's own internal commit() used to make the
    verdict row permanent before the claim_adjudications write could fail on it, e.g. via a
    duplicate id slipping past a set()-based check)."""
    observed_at = observed_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        parsed_valid_from = knowledge_store._parse_ts(valid_from)
        parsed_observed_at = knowledge_store._parse_ts(observed_at)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(
            f"record_delegation_verdict: valid_from/observed_at must be parseable ISO "
            f"timestamps -- got valid_from={valid_from!r}, observed_at={observed_at!r}"
        ) from exc
    if parsed_valid_from.tzinfo is None or parsed_observed_at.tzinfo is None:
        raise ValueError(
            f"record_delegation_verdict: valid_from/observed_at must be timezone-aware -- got "
            f"valid_from={valid_from!r}, observed_at={observed_at!r} -- every other timestamp in "
            f"this store is timezone-aware, and comparing a naive value against them raises "
            f"TypeError deep inside resolve(), permanently bricking it for this "
            f"resource_type/attribute"
        )
    if parsed_valid_from > parsed_observed_at:
        raise ValueError(
            f"record_delegation_verdict: valid_from ({valid_from!r}) is after observed_at "
            f"({observed_at!r}) -- a fact cannot be observed before it became true"
        )

    adjudicated_ids = list(adjudicated_ids)
    if not adjudicated_ids:
        raise ValueError(
            "record_delegation_verdict: adjudicated_ids must be non-empty -- a verdict "
            "adjudicating nothing is incoherent"
        )
    if len(adjudicated_ids) != len(set(adjudicated_ids)):
        raise ValueError(
            f"record_delegation_verdict: adjudicated_ids contains duplicates -- "
            f"{sorted(adjudicated_ids)} -- a verdict adjudicating the same claim twice is "
            f"incoherent"
        )
    placeholders = ",".join("?" * len(adjudicated_ids))
    if attribute is None:
        rows = conn.execute(
            f"SELECT id FROM claims WHERE id IN ({placeholders}) AND resource_type = ? "
            f"AND attribute IS NULL AND valid_until IS NULL",
            (*adjudicated_ids, resource_type),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT id FROM claims WHERE id IN ({placeholders}) AND resource_type = ? "
            f"AND attribute = ? AND valid_until IS NULL",
            (*adjudicated_ids, resource_type, attribute),
        ).fetchall()
    found_ids = {row["id"] for row in rows}
    missing = set(adjudicated_ids) - found_ids
    if missing:
        raise ValueError(
            f"record_delegation_verdict: adjudicated_ids {sorted(missing)} do not reference "
            f"currently-active claims for {resource_type}/{attribute!r} -- stale or "
            f"fabricated id"
        )

    verdict_id = knowledge_store.insert_claim(
        conn, resource_type=resource_type, attribute=attribute, claim_text=claim_text,
        method="semantic", source_type="agent_delegated", provider=provider,
        provider_version=provider_version, source_url=source_url, confidence=confidence,
        valid_from=valid_from, observed_at=observed_at, commit=False,
    )
    try:
        conn.executemany(
            "INSERT INTO claim_adjudications (verdict_claim_id, adjudicated_claim_id) "
            "VALUES (?, ?)",
            [(verdict_id, aid) for aid in adjudicated_ids],
        )
    except Exception:
        # Structural, not input-dependent: whatever failed, the verdict claim insert above
        # (deferred via commit=False) is rolled back with it, so no orphaned claims row survives
        # a failed adjudication write, regardless of why it failed.
        conn.rollback()
        raise
    conn.commit()
    return verdict_id
