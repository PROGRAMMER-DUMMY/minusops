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
