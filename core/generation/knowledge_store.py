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
CREATE TABLE IF NOT EXISTS claim_adjudications (
    verdict_claim_id     INTEGER NOT NULL REFERENCES claims(id),
    adjudicated_claim_id INTEGER NOT NULL REFERENCES claims(id),
    PRIMARY KEY (verdict_claim_id, adjudicated_claim_id)
);
"""


def init_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _content_hash(resource_type, attribute, claim_text, provider_version):
    payload = f"{resource_type}|{attribute or ''}|{claim_text}|{provider_version or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def insert_claim(conn, *, resource_type, attribute, claim_text, method, source_type, provider,
                  valid_from, observed_at, source_url=None, provider_version=None,
                  confidence=None, ingested_at=None, commit=True):
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
    if commit:
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


def _active_schema_claims_for_resource(conn, resource_type):
    rows = conn.execute(
        "SELECT * FROM claims WHERE resource_type = ? AND source_type = 'schema' "
        "AND attribute IS NOT NULL AND valid_until IS NULL",
        (resource_type,),
    ).fetchall()
    return [dict(row) for row in rows]


def invalidate_claim(conn, claim_id, *, valid_until, invalidated_by=None, invalidated_at=None):
    """Bookkeeping only -- never touches claim_text/observed_at/any content column, same
    'claims coexist, nothing is overwritten' discipline as insert_claim(). valid_until and
    invalidated_at are deliberately two different clocks on two different axes: valid_until is
    fact-validity time (caller-supplied, semantically the superseding claim's valid_from -- NOT
    its observed_at; valid_until must track the same axis valid_from does, the fact's own
    timeline, not when we happened to notice the change, or it silently misrepresents the window
    by however large the gap between the real change and the recheck is) and invalidated_at is
    write-time audit time (defaults to now, same pattern as insert_claim()'s own ingested_at).
    valid_until has NO default specifically so a caller can never accidentally use wall-clock time
    for both (the exact wrong-clock shape that produced resolve()'s bug #1; using observed_at here
    instead of valid_from would have been a second, subtler instance of the same family -- ray's
    review, 2026-07-19)."""
    invalidated_at = invalidated_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn.execute(
        "UPDATE claims SET valid_until = ?, invalidated_at = ?, invalidated_by = ? WHERE id = ?",
        (valid_until, invalidated_at, invalidated_by, claim_id),
    )
    conn.commit()


def _parse_ts(ts):
    """Normalize 'Z'-suffixed and '+00:00'-suffixed ISO timestamps to comparable datetime
    objects. Raw string '>' is NOT safe here: datetime.now(tz).isoformat() emits '+00:00' while
    hand-written/external timestamps often use 'Z' -- for the identical instant these two strings
    are not equal and don't even sort correctly ('+' < 'Z'), which silently corrupts the one
    comparison resolve()'s freshness clause depends on (ray's review, 2026-07-18).

    FORMAT only -- a well-formed ISO string with no timezone designator parses cleanly here but
    produces a naive datetime. Awareness is a separate check: see _require_aware."""
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _require_aware(parsed, label):
    """Reject a naive datetime that already parsed cleanly via _parse_ts. A well-formed ISO
    string with no timezone designator (e.g. "2026-07-10T00:00:00") is fine by FORMAT but
    produces a naive datetime, and comparing that against the aware timestamps everywhere else
    in this store raises TypeError deep inside resolve() (bug #9, final whole-step review,
    2026-07-20). Call this on every timestamp entering the store from outside its own control,
    after _parse_ts, before it's used in any comparison."""
    if parsed.tzinfo is None:
        raise ValueError(
            f"{label} must be timezone-aware -- every other timestamp in this store is aware, "
            f"and comparing a naive value against them raises TypeError deep inside resolve()")


def _adjudicated_ids(conn, verdict_claim_id):
    rows = conn.execute(
        "SELECT adjudicated_claim_id FROM claim_adjudications WHERE verdict_claim_id = ?",
        (verdict_claim_id,),
    ).fetchall()
    return {row["adjudicated_claim_id"] for row in rows}


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
    to the driving agent, never adjudicated here.

    Any non-schema source_type is treated uniformly (not just "web") -- an "agent_delegated"
    claim does not silently skip the freshness clause just because it isn't literally "web".

    SCOPED AUTHORITY (Step 4): a delegated verdict (source_type="agent_delegated") becomes the
    resolved winner via a dedicated early-return, BEFORE the schema/non-schema comparison below,
    when it is the UNIQUE newest-observed active claim AND the other currently-active claims are
    a subset-or-equal of what it adjudicated (recorded via record_delegation_verdict's
    claim_adjudications rows, read through _adjudicated_ids()). Subset-or-equal, not exact-match,
    deliberately (the code uses <=, not a strict "proper subset" -- the normal covering case is
    exact equality between other_active_ids and adjudicated_ids, not a strict subset of it):
    coverage means nothing NEW has appeared since the verdict, not that the active
    set is frozen at exactly what was adjudicated. A verdict is NEVER permanently authoritative --
    it is only ever as good as its coverage of the CURRENT active set, checked fresh on every
    call. This is deliberately not "the newest agent_delegated claim always wins": that would let
    a stale verdict outrank fresh evidence forever, the exact failure mode this freshness clause
    exists to prevent, through a new door.
    """
    claims = _active_claims(conn, resource_type, attribute)
    if len(claims) <= 1:
        return {"status": "resolved", "winner": claims[0] if claims else None,
                "claims": claims, "reason": "single_or_no_claim"}
    # Unique-max requirement (not a plain max()) is load-bearing: if a delegated verdict is ever
    # tied for newest-observed with one of its OWN adjudicated claims, a plain max() can silently
    # return either one depending on unspecified row order, changing whether authority gets
    # granted for the exact same underlying state (see the tie-breaking test).
    newest_ts = max(_parse_ts(c["observed_at"]) for c in claims)
    newest_claims = [c for c in claims if _parse_ts(c["observed_at"]) == newest_ts]
    if len(newest_claims) == 1 and newest_claims[0]["source_type"] == "agent_delegated":
        verdict = newest_claims[0]
        adjudicated_ids = _adjudicated_ids(conn, verdict["id"])
        other_active_ids = {c["id"] for c in claims if c["id"] != verdict["id"]}
        if other_active_ids <= adjudicated_ids:
            return {"status": "resolved", "winner": verdict, "claims": claims,
                    "reason": "delegated_verdict_covers_active_claims"}
    schema_claims = [c for c in claims if c["source_type"] == "schema"]
    non_schema_claims = [c for c in claims if c["source_type"] != "schema"]
    if schema_claims and non_schema_claims:
        # Bug #8 fix (ray's round-3 review, 2026-07-19): each side's "newest" used to be picked
        # via max(..., key=parse_ts), a SINGLE row -- on a tie, max() returns whichever row is
        # first in unspecified SQL row order. Now every claim tied for newest on EITHER side is
        # collected into a cohort, and agreement requires ALL of them (not one arbitrarily-picked
        # row per side) to share one identical normalized text. A single disagreeing claim inside
        # a tied cohort now correctly falls through to the freshness comparison instead of being
        # silently outvoted by whichever claim the row-order pick happened to favor.
        newest_schema_ts = max(_parse_ts(c["observed_at"]) for c in schema_claims)
        newest_schema_claims = [c for c in schema_claims
                                 if _parse_ts(c["observed_at"]) == newest_schema_ts]
        newest_other_ts = max(_parse_ts(c["observed_at"]) for c in non_schema_claims)
        newest_other_claims = [c for c in non_schema_claims
                                if _parse_ts(c["observed_at"]) == newest_other_ts]
        # Deterministic pick for the WINNER claim object (id order, never SQL row order) -- when
        # claims_agree fires, every member of both cohorts shares identical text by construction,
        # so which specific row is returned doesn't change the answer; this only makes the choice
        # reproducible.
        newest_schema = min(newest_schema_claims, key=lambda c: c["id"])
        all_newest_texts = {c["claim_text"].strip().lower()
                             for c in newest_schema_claims + newest_other_claims}
        if len(all_newest_texts) == 1:
            return {"status": "resolved", "winner": newest_schema, "claims": claims,
                    "reason": "claims_agree"}
        if newest_other_ts > newest_schema_ts:
            return {"status": "needs_review", "winner": None, "claims": claims,
                    "reason": "non_schema_claim_observed_more_recently_than_schema_fetch"}
        return {"status": "resolved", "winner": newest_schema, "claims": claims,
                "reason": "schema_observed_same_or_more_recently_than_non_schema_claim"}
    return {"status": "needs_review", "winner": None, "claims": claims,
            "reason": "no_ground_truth_arbiter"}
