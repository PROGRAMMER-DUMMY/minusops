"""
knowledge_diff.py -- the structural-diff path: live provider schema -> a set of deterministic
'schema' claims, ready to insert into knowledge_store. Reuses schema_watch._fetch_schema()
(core/generation/schema_watch.py:90-132) directly rather than schema_watch.get_type_schema()
(schema_watch.py:135-166): get_type_schema() discards the resolved provider version it gets back
from _fetch_schema() (it unpacks `schema, _resolved_version = _fetch_schema(...)` and throws the
version away), but knowledge_store's claims schema requires provider_version to be populated.
Calling _fetch_schema() here means duplicating get_type_schema()'s three-line type-table lookup,
but it is still the same single fetch mechanism -- no second fetch path, schema_watch.py itself
untouched.
"""
import datetime
import tempfile

import schema_watch


def schema_claims_for_type(provider, resource_type, observed_at=None, kind="resource"):
    observed_at = observed_at or datetime.datetime.now(datetime.timezone.utc).isoformat()

    with tempfile.TemporaryDirectory() as workdir:
        schema, resolved_version = schema_watch._fetch_schema(provider, workdir)

    table = schema.get("resource_schemas" if kind == "resource" else "data_source_schemas", {})
    entry = table.get(resource_type)
    block = entry.get("block") if entry is not None else None
    if block is None:
        return []

    claims = []
    for name, attr in (block.get("attributes") or {}).items():
        if not isinstance(attr, dict):
            continue
        parts = []
        if attr.get("required"):
            parts.append("required")
        if attr.get("deprecated"):
            parts.append("deprecated")
        claim_text = f"{name}: " + (", ".join(parts) if parts else "optional, not deprecated")
        claims.append({
            "resource_type": resource_type, "attribute": name, "claim_text": claim_text,
            "method": "structural", "source_type": "schema", "provider": provider,
            "provider_version": resolved_version,
            "valid_from": observed_at, "observed_at": observed_at,
        })
    return claims
