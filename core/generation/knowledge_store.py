"""
knowledge_store.py -- bi-temporal SQLite store for the knowledge-layer spine.

Claims coexist; nothing is ever overwritten. valid_from (when a fact became true in the real
world), observed_at (when a source was actually fetched/read), and ingested_at (when the row
was written here) are three DISTINCT timestamps -- collapsing any two of them breaks either the
bi-temporal model or the freshness clause resolve() depends on (see resolve()'s own docstring).
"""
import datetime
import hashlib
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_type   TEXT NOT NULL,
    attribute       TEXT,
    claim_text      TEXT NOT NULL,
    method          TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    source_url      TEXT,
    provider        TEXT NOT NULL,
    provider_version TEXT,
    confidence      REAL,
    valid_from      TEXT NOT NULL,
    valid_until     TEXT,
    observed_at     TEXT NOT NULL,
    ingested_at     TEXT NOT NULL,
    invalidated_at  TEXT,
    invalidated_by  INTEGER REFERENCES claims(id),
    content_hash    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_lookup ON claims(resource_type, attribute);
"""


def init_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _content_hash(resource_type, attribute, claim_text, provider_version):
    payload = f"{resource_type}|{attribute or ''}|{claim_text}|{provider_version or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def insert_claim(conn, *, resource_type, attribute, claim_text, method, source_type, provider,
                  valid_from, observed_at, source_url=None, provider_version=None,
                  confidence=None, ingested_at=None):
    ingested_at = ingested_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
    content_hash = _content_hash(resource_type, attribute, claim_text, provider_version)
    cursor = conn.execute(
        """INSERT INTO claims
           (resource_type, attribute, claim_text, method, source_type, source_url, provider,
            provider_version, confidence, valid_from, observed_at, ingested_at, content_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (resource_type, attribute, claim_text, method, source_type, source_url, provider,
         provider_version, confidence, valid_from, observed_at, ingested_at, content_hash),
    )
    conn.commit()
    return cursor.lastrowid


def _active_claims(conn, resource_type, attribute=None):
    if attribute is None:
        # Resource-level claims ONLY (attribute IS NULL), NOT every claim regardless of
        # attribute -- insert_claim() genuinely supports attribute=None for claims about the
        # resource type as a whole (e.g. "this resource type is deprecated entirely"), and
        # that is the only sensible meaning of resolve(conn, resource_type) with no attribute.
        # Omitting the attribute filter here previously pooled claims from unrelated attributes
        # into one comparison, letting resolve() return a confident, silently wrong verdict --
        # exactly the failure category the freshness clause exists to prevent (implementation-
        # level review, 2026-07-18).
        rows = conn.execute(
            "SELECT * FROM claims WHERE resource_type = ? AND attribute IS NULL AND valid_until IS NULL",
            (resource_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM claims WHERE resource_type = ? AND attribute = ? AND valid_until IS NULL",
            (resource_type, attribute),
        ).fetchall()
    return [dict(row) for row in rows]


def _parse_ts(ts):
    """Normalize 'Z'-suffixed and '+00:00'-suffixed ISO timestamps to comparable datetime
    objects. Raw string '>' is NOT safe here: datetime.now(tz).isoformat() emits '+00:00' while
    hand-written/external timestamps often use 'Z' -- for the identical instant these two strings
    are not equal and don't even sort correctly ('+' < 'Z'), which silently corrupts the one
    comparison resolve()'s freshness clause depends on (ray's review, 2026-07-18)."""
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def resolve(conn, resource_type, attribute=None):
    """Returns {"status": "resolved"|"needs_review", "winner": claim_or_None, "claims": [...],
    "reason": str}. Never writes -- pure read, same discipline as every gate in this project.

    Freshness clause compares observed_at (when EACH SOURCE was actually fetched/read) --
    NEVER valid_from (when the underlying real-world fact became true) and NEVER confidence.
    A schema fetch and a web claim can share the identical valid_from (both describe the same
    provider release) while differing sharply in observed_at (schema fetched weeks ago; web
    claim read just now) -- valid_from cannot discriminate this; only observed_at can.

    Comparison always goes through _parse_ts() into datetime objects, never raw string ">" --
    see _parse_ts()'s own docstring for why that distinction is load-bearing.

    Before comparing recency, an exact case/whitespace-insensitive claim_text match between the
    newest schema claim and the newest non-schema claim short-circuits to "resolved, schema wins,
    claims_agree" -- agreeing claims carry no conflict to adjudicate, so routing them into the
    freshness comparison (or silently discarding one) is noise, not signal. This is a literal
    string comparison, not semantic judgment -- semantic agreement/disagreement stays delegated
    to the driving agent (Step 4), never adjudicated here.

    Any non-schema source_type is treated uniformly (not just "web") -- a Step-4
    "agent_delegated" claim must not silently skip the freshness clause just because it isn't
    literally "web"."""
    claims = _active_claims(conn, resource_type, attribute)
    if len(claims) <= 1:
        return {"status": "resolved", "winner": claims[0] if claims else None,
                "claims": claims, "reason": "single_or_no_claim"}
    schema_claims = [c for c in claims if c["source_type"] == "schema"]
    non_schema_claims = [c for c in claims if c["source_type"] != "schema"]
    if schema_claims and non_schema_claims:
        newest_schema = max(schema_claims, key=lambda c: _parse_ts(c["observed_at"]))
        newest_other = max(non_schema_claims, key=lambda c: _parse_ts(c["observed_at"]))
        if newest_schema["claim_text"].strip().lower() == newest_other["claim_text"].strip().lower():
            return {"status": "resolved", "winner": newest_schema, "claims": claims,
                    "reason": "claims_agree"}
        if _parse_ts(newest_other["observed_at"]) > _parse_ts(newest_schema["observed_at"]):
            return {"status": "needs_review", "winner": None, "claims": claims,
                    "reason": "non_schema_claim_observed_more_recently_than_schema_fetch"}
        return {"status": "resolved", "winner": newest_schema, "claims": claims,
                "reason": "schema_observed_same_or_more_recently_than_non_schema_claim"}
    return {"status": "needs_review", "winner": None, "claims": claims,
            "reason": "no_ground_truth_arbiter"}
