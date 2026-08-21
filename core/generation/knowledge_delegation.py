"""Agent-delegation contract for the semantic path: package a needs_review, record the verdict.

Turns a `needs_review` resolve() result into a structured hand-off for the driving agent, then
records that agent's verdict back as a new claim. There is no local model anywhere in this path:
the driving agent adjudicates, this module only packages the question and stores the answer.

Materiality -- whether a new observation is worth recording at all -- is deliberately NOT decided
here. That is the driving agent's job, checking resolve()'s current winner before it ever calls
record_delegation_verdict. Materiality must never migrate into resolve() or any stdlib-only core
module.

Depends on: core/generation/knowledge_store.py
Shells out to: nothing
Used by: nothing in core/ or app/ — tests/test_knowledge_delegation.py, tests/test_knowledge_jsonl.py
"""
import datetime

import knowledge_store


def build_delegation_request(conn, resource_type, attribute):
    result = knowledge_store.resolve(conn, resource_type, attribute)
    if result["status"] != "needs_review":
        return None
    # No empty-list guard here on purpose: resolve() cannot return needs_review with fewer than
    # two claims. That invariant lives in another file, so
    # test_build_delegation_request_claims_list_is_never_empty pins it rather than leaving it
    # as an inference.
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

    This is the external boundary of the two-clock model: an arbitrary driving agent supplies
    valid_from/observed_at. Both must parse via knowledge_store._parse_ts (rejecting garbage and
    wrong types) AND be timezone-AWARE. The awareness check is not redundant with parsing. A
    well-formed but naive ISO string such as "2026-07-10T00:00:00" inserts fine, and then the
    NEXT resolve() on that resource_type/attribute dies inside its max()-by-observed_at with
    "TypeError: can't compare offset-naive and offset-aware datetimes", bricking the attribute
    until the row is manually invalidated. Every other timestamp in this store is aware
    (schema/web claims always emit "Z" or "+00:00"); _parse_ts guards format, not awareness.
    The awareness check must stay immediately after parsing and before the valid_from >
    observed_at comparison below -- a naive valid_from reaching that comparison raises a raw
    TypeError, escaping this function's documented ValueError contract. valid_from must not be
    AFTER observed_at (a fact cannot be observed before it became true). observed_at defaults to
    now, always aware via datetime.now(timezone.utc); valid_from has no default, matching
    invalidate_claim's valid_until.

    adjudicated_ids is the second external-input boundary: required, non-empty, duplicate-free,
    and every id must reference a currently-active claim for this exact resource_type/attribute.
    A stale, fabricated, or duplicated id would silently void resolve()'s coverage test. All
    validation runs BEFORE any write, and the verdict claim insert shares one transaction with
    its claim_adjudications rows (rolled back together on any failure), so "no partial claim row
    on rejection" does not rest on this function's validation having anticipated every failure
    mode -- insert_claim's own commit() would otherwise make the verdict row permanent before a
    claim_adjudications write could fail on it."""
    observed_at = observed_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        parsed_valid_from = knowledge_store._parse_ts(valid_from)
        parsed_observed_at = knowledge_store._parse_ts(observed_at)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(
            f"record_delegation_verdict: valid_from/observed_at must be parseable ISO "
            f"timestamps -- got valid_from={valid_from!r}, observed_at={observed_at!r}"
        ) from exc
    knowledge_store._require_aware(
        parsed_valid_from, f"record_delegation_verdict: valid_from={valid_from!r}")
    knowledge_store._require_aware(
        parsed_observed_at, f"record_delegation_verdict: observed_at={observed_at!r}")
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
