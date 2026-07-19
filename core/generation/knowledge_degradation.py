"""
knowledge_degradation.py -- the bi-temporal degradation check: re-fetches a resource type's live
schema and reconciles it against what the store currently believes, generalizing schema_watch.py's
drift pattern into the knowledge layer's own claims/resolve() model instead of schema_watch's
separate snapshot-diff files.

Always inserts, never dedups by content hash -- a re-check that finds nothing changed still
inserts a fresh claim and invalidates the old one, bumping observed_at. This is load-bearing for
resolve()'s freshness clause, not just "evidence has value": without it, schema claims go
permanently stale relative to live-fetched non-schema claims, and every future conflict trivially
looks "web/agent_delegated is newer" -- the exact noise-queue asymmetry ray's Q2 review named.
"""
import datetime

import knowledge_diff
import knowledge_store


def check_and_refresh(conn, provider, resource_type, kind="resource"):
    fresh_claims = knowledge_diff.schema_claims_for_type(provider, resource_type, kind=kind)
    fresh_attributes = {c["attribute"] for c in fresh_claims}
    # Grouped, then reduced via _parse_ts()'s max() -- NOT a plain {attr: claim} dict
    # comprehension. A dict comprehension keeps whichever row SQLite happens to return LAST for
    # a given attribute (unspecified order), which is only ever safe if at most one active schema
    # claim can exist per attribute. That is NOT guaranteed: a DB used before this step existed
    # (Step 2's own tests/proof runs never invalidated anything) can already hold more than one.
    # resolve() itself already handles this correctly via its own max()-by-observed_at (Step 2,
    # test_resolve_uses_the_truly_newest_among_multiple_schema_claims) -- this mirrors that same
    # discipline here so this function's OWN bookkeeping (which row is "old," what invalidated_by
    # points at) can't silently reference the wrong duplicate.
    _by_attr = {}
    for c in knowledge_store._active_schema_claims_for_resource(conn, resource_type):
        existing = _by_attr.get(c["attribute"])
        if existing is None or knowledge_store._parse_ts(c["observed_at"]) > knowledge_store._parse_ts(existing["observed_at"]):
            _by_attr[c["attribute"]] = c
    previously_active_by_attr = _by_attr

    inserted, invalidated = [], []
    for claim in fresh_claims:
        old = previously_active_by_attr.get(claim["attribute"])
        new_id = knowledge_store.insert_claim(conn, **claim)
        inserted.append(new_id)
        if old is not None:
            # valid_until = the NEW claim's valid_from, NOT its observed_at (ray's review,
            # 2026-07-19) -- valid_until must track the same axis valid_from does (the fact's own
            # timeline), not when this check happened to notice the change. Only appears
            # interchangeable today because knowledge_diff.py's schema claims currently set
            # valid_from == observed_at (a disclosed Step 2 placeholder); this is correct
            # regardless of whether that placeholder is ever resolved.
            knowledge_store.invalidate_claim(
                conn, old["id"], valid_until=claim["valid_from"], invalidated_by=new_id)
            invalidated.append(old["id"])

    return {"resource_type": resource_type, "provider": provider,
            "inserted": inserted, "invalidated": invalidated, "removed_attributes": []}
