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
import json
import os
import sys

import knowledge_diff
import knowledge_store
import modules


def _default_db_path():
    return os.path.join(modules.output_root(), "knowledge", "claims.db")


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

    removed_attributes = []
    skipped_removed_attribute_check = False
    if not fresh_claims:
        # Type-not-found guard (ray's review, 2026-07-19): schema_claims_for_type() returns []
        # both when the resource type genuinely doesn't exist in the live schema (a typo, or the
        # wrong `kind` -- aws_s3_bucket is a real name under BOTH "resource" and "data") and when
        # it exists with zero attributes. Collapsing these would mark EVERY previously-tracked
        # attribute "removed" on an ordinary caller mistake, not a real removal -- a confident
        # wrong verdict, same family as resolve()'s three. Do NOT touch schema_claims_for_type()'s
        # own already-locked (three review rounds) contract to disambiguate the two cases;
        # instead, skip the removed-attribute pass entirely and say so in the summary rather than
        # silently trusting emptiness as confirmed removal. Only True when there was something at
        # stake (previously-active claims this function declined to touch); an empty fetch for a
        # type that was never tracked has nothing to silently get wrong.
        skipped_removed_attribute_check = bool(previously_active_by_attr)
    else:
        # An attribute with a previously-active schema claim absent from the fresh fetch would
        # otherwise stay "active" forever, asserting something about an attribute that no longer
        # exists -- a silent-stale-answer bug in the same family as resolve()'s three
        # (implementation-level review, 2026-07-19). Gives resolve() a real current belief instead
        # of a silent absence, which the store has no other way to represent.
        removed_provider_version = fresh_claims[0]["provider_version"]
        removed_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for attr, old in previously_active_by_attr.items():
            if attr in fresh_attributes:
                continue
            removed_id = knowledge_store.insert_claim(
                conn, resource_type=resource_type, attribute=attr,
                claim_text=f"{attr}: removed from live schema", method="structural",
                source_type="schema", provider=provider, provider_version=removed_provider_version,
                valid_from=removed_ts, observed_at=removed_ts,
            )
            knowledge_store.invalidate_claim(conn, old["id"], valid_until=removed_ts, invalidated_by=removed_id)
            inserted.append(removed_id)
            invalidated.append(old["id"])
            removed_attributes.append(attr)

    return {"resource_type": resource_type, "provider": provider,
            "inserted": inserted, "invalidated": invalidated, "removed_attributes": removed_attributes,
            "skipped_removed_attribute_check": skipped_removed_attribute_check}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Knowledge-layer schema degradation check")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("provider")
    c.add_argument("resource_type")
    c.add_argument("--kind", default="resource", choices=["resource", "data"])
    c.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    db_path = args.db or _default_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = knowledge_store.init_db(db_path)
    summary = check_and_refresh(conn, args.provider, args.resource_type, kind=args.kind)
    conn.close()
    print(json.dumps(summary, indent=2))
    if summary.get("skipped_removed_attribute_check"):
        # Loud, not just a summary field nobody reads (ray's review, 2026-07-19). A mistyped
        # resource_type or --kind must not quietly no-op and report success.
        print(
            f"[knowledge_degradation] WARNING: no live schema found for "
            f"{args.provider}:{args.resource_type} (kind={args.kind}), but previously-active "
            f"claims exist for it -- check resource_type/--kind for a typo before trusting this "
            f"as a real removal. Removed-attribute detection was skipped, not silently applied.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
