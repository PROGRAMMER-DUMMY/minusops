"""Structural-diff path: live provider schema -> deterministic 'schema' claims for knowledge_store.

Calls `schema_watch._fetch_schema()` directly rather than the tidier
`schema_watch.get_type_schema()`, on purpose: get_type_schema() throws away the resolved provider
version (`schema, _resolved_version = _fetch_schema(...)`), and knowledge_store's claims schema
requires provider_version to be populated. The cost is duplicating get_type_schema()'s three-line
type-table lookup below; the gain is one fetch mechanism, not two, with schema_watch.py untouched.

Depends on: core/generation/schema_watch.py
Shells out to: terraform (transitively, via schema_watch._fetch_schema -> `terraform init` +
    `terraform providers schema -json`)
Used by: core/generation/knowledge_degradation.py, tests/test_knowledge_diff.py
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
