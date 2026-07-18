# Knowledge-Layer Spine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task (chosen execution model — fresh subagent per task, review between tasks; the checkpoint between steps is where the `observed_at`/`valid_from` bug in this design was actually caught, so per-task isolation is deliberate, not just a style preference). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Status:** Step 2 built, all 5 tasks task-reviewed clean, final whole-branch review complete ("With fixes" — two Important boundary-guard findings + one Minor, all fixed in a follow-up commit). A subsequent implementation-level review (reading the shipped code against the advertised interface, not re-reading this plan) found a THIRD comparison-correctness bug in `resolve()` — `_active_claims()` pooled claims across unrelated attributes when `attribute=None` — fixed with 6 new branch-surface tests. Step 2 is complete and merged into this branch. Design-level fixes folded into Task 2 below: timestamp parsing, agreeing-claims short-circuit, non-schema generalization, version-stale deferral (all from ray's plan-level review), plus `attribute IS NULL` isolation and the AST import-guard hardening (from implementation-level review, both shown inline below).

**Goal:** Build the first, small-scale slice of MinusOps' knowledge layer — a bi-temporal SQLite store of claims about Terraform provider schemas, a deterministic structural-diff engine that reuses `schema_watch.py`'s live-fetch machinery, and a resolution function with a working freshness clause — proven against exactly one real resource type (`aws_s3_bucket`, from `storage-medallion-s3`) before any expansion.

**Architecture:** Two-path audit. The structural path (this plan, Step 2) is pure stdlib: live schema fetch → claim → deterministic diff → default-and-disclose resolution, with an explicit freshness clause that refuses to let a stale schema fetch silently outrank a more-recently-observed contradicting claim. The semantic path (delegated to whatever agentic CLI is driving MinusOps, Step 4) and an optional offline NLI fallback (never built in this plan) are out of scope here except for the boundary that must hold: core imports nothing beyond stdlib + `schema_watch`, enforced by a real test, not a comment.

**Tech Stack:** Python stdlib only (`sqlite3`, `ast`, `hashlib`, `datetime`, `json`, `os`) + this repo's own `schema_watch.get_type_schema()`. No third-party dependency, no framework, no vector/graph DB.

## Global Constraints

- No local LLM. No research framework. No vector DB in core. No graph DB.
- Structural path = deterministic stdlib diff against live `terraform providers schema -json`. Zero model involvement.
- Semantic path (cross-claim adjudication with no schema arbiter) is delegated to the driving agentic CLI — MinusOps never adjudicates prose-vs-prose itself.
- Optional NLI fallback is vendored later, if ever, strictly outside core, never imported at module level by any core file.
- Conflicting claims coexist — every insert is an `INSERT`, never an `UPDATE` of an existing claim's text. Resolution is a read-only query, never a mutation.
- Bi-temporal: `valid_from` (when a fact became true in the real world) is distinct from `observed_at` (when a source was actually fetched/read) is distinct from `ingested_at` (when the row was written to the store). The freshness clause reads `observed_at` only — never `valid_from`, never `confidence`.
- Freshness clause is non-negotiable: a web claim observed more recently than the schema was fetched, disagreeing with schema, does NOT default to schema-wins — it routes to `needs_review`.
- Do not touch `synthesizer.py`, `compose()`, or any catalog module. This spine runs alongside, reading `schema_watch`'s engine; it is never called from and never calls into the composition path.
- First and only resource type in this build: `aws_s3_bucket` (from `storage-medallion-s3`, already confirmed G2-clean, no disclosed exception).
- Flat modules in `core/generation/`, matching this project's existing convention — no new subpackages.

---

## Red-Team Review Focus (for ray)

Step 1's design (this plan's schema + `resolve()`) already went through one correction round: the freshness discriminator originally compared `valid_from` instead of `observed_at`, which would have made the clause auto-suppress stale-schema discoveries — the exact failure it exists to prevent. That bug passed a first read and only broke under close inspection, which is why this plan is going to an independent reviewer before Step 2 is built rather than proceeding straight to implementation. Three specific questions, not a general review:

**1. Is every failure mode of the freshness clause fail-safe (errs toward `needs_review`), never toward silent schema-wins?**

Look specifically at `resolve()` in Task 2 (line ~253): it compares `newest_web["observed_at"]` against `newest_schema["observed_at"]` with no reference to `provider_version`. Construct the case where a web claim is *observed* recently but is *about* a stale/older provider version, while the stored schema claim is about the current version. Does observation-recency alone let that version-stale web claim either (a) incorrectly win, or (b) incorrectly force a review that a version-aware comparison would have resolved cleanly? If provider-version context matters here, does it belong in this clause now, or is it acceptable to defer (and if deferred, is the failure mode in the meantime fail-safe or fail-open)?

**2. Does the freshness clause reintroduce the review-queue noise problem from an earlier round of this design?**

The clause in Task 2 routes to `needs_review` whenever `newest_web["observed_at"] > newest_schema["observed_at"]` — i.e., any time a web claim was fetched even slightly more recently than the cached schema fetch, regardless of whether the web claim asserts an actual attribute-level change. If schema claims are fetched once and cached (per `schema_watch.py`'s documented ~30s-per-call cost) while web claims are fetched live per query, nearly every conflict will satisfy `newer web observed_at`, routing to review by default rather than the exception. This appears to cut against the earlier principle (raised in prior review) against manufacturing a review-queue bottleneck. Should the freshness clause require materiality — i.e., only route to review when the web claim's `claim_text` asserts an actual change to the attribute (not just any recency delta) — or is unconditional recency-based routing the correct conservative default here, with materiality filtering left to whatever consumes the `needs_review` queue (Step 4)? Reconcile explicitly against the earlier "don't manufacture a bottleneck" guidance rather than treating them as unrelated.

**3. Do the Step 3-5 interface sketches (below) reveal any assumption Step 2's schema/functions hardcode that would make Steps 3-5 awkward to build later?**

In particular: the `(resource_type, attribute)` cross-version linking decision (Step 3), the `source_type` value for delegated verdicts (Step 4, currently leaning toward a dedicated `"agent_delegated"` value rather than folding into `"web"`), and whether the budget knob (Step 5) belongs in the knowledge-layer core at all. Flag anything in Task 1's `claims` table (line ~99) or Task 2's `resolve()` return shape (line ~253) that would need a breaking change to support any of Steps 3-5 as sketched.

### Ray's findings — accepted in full, folded into Task 2 below

Ray's review surfaced two bugs neither Step 1's design pass nor the questions above anticipated, both now fixed in Task 2's implementation and tests:

1. **Timestamp format bug (high severity, ray's most severe finding).** `resolve()`'s original comparison used raw string `>` on `observed_at`. `datetime.now(tz).isoformat()` emits a `+00:00` suffix; every hand-written test fixture used `Z`. For the identical instant these two strings are not equal, and `+` (0x2B) sorts before `Z` (0x5A) — so a schema claim's auto-generated timestamp could be judged *older* than a same-instant hand-written web claim purely from suffix formatting, corrupting the one comparison this entire layer exists to get right. Invisible to the original test suite, since every fixture used `Z` consistently. **Fix:** parse both sides to `datetime` via a `_parse_ts()` helper before comparing; never compare the raw strings. **Regression test:** `test_resolve_treats_same_instant_across_z_and_offset_format_as_equal_freshness`.
2. **Agreeing-claims noise.** The original `if schema_claims and web_claims:` branch fired even when the newest schema and web claims said the identical thing, either silently discarding a confirming observation or routing pure agreement to `needs_review`. **Fix:** an exact (case/whitespace-insensitive) `claim_text` equality short-circuit runs before the freshness comparison — a literal string check, not semantic judgment; semantic agreement stays delegated to the driving agent. **Regression test:** `test_resolve_agreeing_claims_never_route_to_needs_review`.
3. **`"web"` hardcode (Q3 follow-through).** `web_claims = [c for c in claims if c["source_type"] == "web"]` meant a future Step-4 `"agent_delegated"` claim would fall into neither bucket, silently skipping the freshness clause and forcing permanent `needs_review` regardless of actual recency. **Fix:** generalized to `non_schema_claims = [c for c in claims if c["source_type"] != "schema"]`, fixed now while it costs nothing (only two source_types exist today). **Coverage:** `test_resolve_generalizes_beyond_web_to_any_non_schema_source_type`.
4. **Version-stale web claim (Q1) — deferral confirmed fail-safe, no code change.** A web claim observed recently but describing a stale provider version routes to `needs_review` (precision loss, not a safety violation) since `resolve()` has no `provider_version` awareness. Adding version-awareness to the freshness clause would require judging "which version is current," which is semantic judgment, not a stdlib fact — same boundary Q2 draws. Left as a disclosed limitation; `provider_version` is already carried on every claim row and surfaced to Step 4's review contract for a human to see immediately.

**Materiality (Q2) reconciliation — accepted as the governing principle.** Materiality does NOT belong in `resolve()`. "Did the answer actually change" is a semantic judgment; encoding it in stdlib core would smuggle semantic adjudication back into the one place this design forbids it — a worse boundary violation than a noisy queue. `resolve()` performs only deterministic literal comparison (finding 2's exact-text short-circuit). Materiality filtering relocates to **insertion time, on the semantic side (Step 4)**: the delegating agent checks `resolve()`'s current winner before writing a new claim, and inserts only when its own semantic judgment says the answer changed. "Core never fakes semantic judgment" wins at the `resolve()` layer; "don't manufacture a bottleneck" is honestly enforceable at Step 4 instead.

**Carried forward (not Step 2 changes, tracked so they aren't lost):**
- Step 3 (below) is reprioritized from "someday" to a **near-term dependency of Step 2's production health** — it's what bounds the schema-cached-vs-web-live recency asymmetry that makes finding 2 (and Q2's noise concern) worse over time if schema's `observed_at` is never refreshed.
- Step 4's sketch (below) is corrected: `source_type` is plain `TEXT NOT NULL` with no `CHECK` constraint — `"agent_delegated"` is not schema-enforced/"reserved," it just requires no migration to start using.

---

## Step 2 (build now) — structural-diff path

### Task 1: `knowledge_store.py` — schema + `init_db` + `insert_claim`

**Files:**
- Create: `core/generation/knowledge_store.py`
- Test: `tests/test_knowledge_store.py`

**Interfaces:**
- Produces: `init_db(path: str) -> sqlite3.Connection`, `insert_claim(conn, *, resource_type: str, attribute: str | None, claim_text: str, method: str, source_type: str, provider: str, source_url: str | None = None, provider_version: str | None = None, confidence: float | None = None, valid_from: str, observed_at: str, ingested_at: str | None = None) -> int`

- [ ] **Step 1: Write the failing test**

```python
import os
import sqlite3

import pytest

import knowledge_store


def test_init_db_creates_the_claims_table(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='claims'")
    assert cursor.fetchone() is not None
    conn.close()


def test_insert_claim_round_trips_every_field(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    claim_id = knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="acl", claim_text="acl is deprecated",
        method="structural", source_type="schema", provider="aws", provider_version="6.54.0",
        valid_from="2026-07-18T00:00:00Z", observed_at="2026-07-18T00:00:00Z",
    )
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    assert row is not None
    conn.close()


def test_insert_claim_defaults_ingested_at_to_now_if_not_given(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    claim_id = knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute=None, claim_text="exists",
        method="structural", source_type="schema", provider="aws",
        valid_from="2026-07-18T00:00:00Z", observed_at="2026-07-18T00:00:00Z",
    )
    row = conn.execute("SELECT ingested_at FROM claims WHERE id = ?", (claim_id,)).fetchone()
    assert row[0] is not None
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_knowledge_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'knowledge_store'`

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_knowledge_store.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/generation/knowledge_store.py tests/test_knowledge_store.py
git commit -m "feat: knowledge-layer spine -- bi-temporal claims store, schema + insert_claim"
```

---

### Task 2: `knowledge_store.py` — `_active_claims` + `resolve()` with the freshness clause

**Files:**
- Modify: `core/generation/knowledge_store.py`
- Test: `tests/test_knowledge_store.py`

**Interfaces:**
- Consumes: the `claims` table from Task 1.
- Produces: `resolve(conn, resource_type: str, attribute: str | None = None) -> dict` returning `{"status": "resolved"|"needs_review", "winner": dict|None, "claims": list[dict], "reason": str}`.

- [ ] **Step 1: Write the failing test**

```python
def _insert(conn, source_type, claim_text, observed_at, attribute="acl"):
    return knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute=attribute, claim_text=claim_text,
        method="structural" if source_type == "schema" else "semantic", source_type=source_type,
        provider="aws", valid_from=observed_at, observed_at=observed_at,
    )


def test_resolve_with_a_single_claim_is_trivially_resolved(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-18T00:00:00Z")
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["winner"]["claim_text"] == "acl is deprecated"


def test_resolve_schema_wins_when_web_claim_observed_no_later_than_schema_fetch(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-18T12:00:00Z")
    _insert(conn, "web", "acl is fine to use", "2026-07-18T10:00:00Z")  # observed BEFORE schema fetch
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["winner"]["source_type"] == "schema"


def test_resolve_freshness_clause_blocks_schema_default_when_web_claim_is_newer(tmp_path):
    # THE case this whole layer exists for: a web claim observed AFTER the schema was fetched,
    # contradicting it, must NOT default to schema-wins.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "attribute .name still exists", "2026-07-01T00:00:00Z")
    _insert(conn, "web", "attribute .name was renamed to .region in v6", "2026-07-18T00:00:00Z")
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "needs_review"
    assert result["reason"] == "non_schema_claim_observed_more_recently_than_schema_fetch"


def test_resolve_two_conflicting_web_claims_need_review(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "web", "acl is deprecated", "2026-07-18T00:00:00Z")
    _insert(conn, "web", "acl is still recommended", "2026-07-18T00:00:00Z")
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "needs_review"
    assert result["reason"] == "no_ground_truth_arbiter"


def test_resolve_never_reads_confidence():
    # Compares against the function BODY only, excluding the docstring -- resolve()'s own
    # docstring legitimately explains "NEVER confidence" in prose, which would otherwise
    # trip a naive substring check on the full source (inspect.getsource includes the
    # docstring). The intent is to prove the runtime logic never consults confidence, not
    # that the word never appears in the text explaining why it doesn't.
    import ast
    import inspect
    import textwrap
    source = textwrap.dedent(inspect.getsource(knowledge_store.resolve))
    func_node = ast.parse(source).body[0]
    body = func_node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # drop the docstring statement
    body_source = "\n".join(ast.get_source_segment(source, node) for node in body)
    assert "confidence" not in body_source


def test_resolve_treats_same_instant_across_z_and_offset_format_as_equal_freshness(tmp_path):
    # Ray's highest-severity finding: datetime.now(tz).isoformat() emits "+00:00"; hand-written
    # fixtures use "Z". For the SAME instant these strings are not equal and "+" sorts before "Z",
    # so a naive string ">" comparison gives a FALSE freshness verdict with zero real time gap.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-18T12:00:00+00:00")  # what isoformat() emits
    _insert(conn, "web", "acl is fine to use", "2026-07-18T12:00:00Z")  # hand-written, same instant
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["winner"]["source_type"] == "schema"
    assert result["reason"] == "schema_observed_same_or_more_recently_than_non_schema_claim"


def test_resolve_agreeing_claims_never_route_to_needs_review(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "Acl Is Deprecated", "2026-07-01T00:00:00Z")
    _insert(conn, "web", "acl is deprecated", "2026-07-18T00:00:00Z")  # later, but says the same thing
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["reason"] == "claims_agree"


def test_resolve_generalizes_beyond_web_to_any_non_schema_source_type(tmp_path):
    # A Step-4 "agent_delegated" claim must not fall outside resolve()'s comparison just because
    # it isn't literally "web".
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "attribute .name still exists", "2026-07-01T00:00:00Z")
    knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="acl",
        claim_text="attribute .name was renamed to .region in v6", method="semantic",
        source_type="agent_delegated", provider="aws",
        valid_from="2026-07-18T00:00:00Z", observed_at="2026-07-18T00:00:00Z",
    )
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "needs_review"
    assert result["reason"] == "non_schema_claim_observed_more_recently_than_schema_fetch"


# Implementation-level review, 2026-07-18: zero test above ever called resolve() or
# _active_claims() without an explicit attribute string, or exercised several other reachable
# branches -- exactly the gap that let three separate comparison-correctness bugs (wrong clock,
# incompatible timestamp formats, cross-attribute pooling) each pass every prior review. The
# tests below audit resolve()'s full branch surface, not just the paths already in mind.
# ("only-non-schema claims, no schema present" is already covered above by
# test_resolve_two_conflicting_web_claims_need_review -- not duplicated here.)

def test_resolve_without_attribute_only_pools_resource_level_claims(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl: deprecated", "2026-07-18T00:00:00Z", attribute="acl")  # decoy
    knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute=None,
        claim_text="aws_s3_bucket is a stable resource type", method="structural",
        source_type="schema", provider="aws",
        valid_from="2026-07-01T00:00:00Z", observed_at="2026-07-01T00:00:00Z",
    )
    knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute=None,
        claim_text="aws_s3_bucket is being deprecated resource-wide", method="semantic",
        source_type="web", provider="aws",
        valid_from="2026-06-01T00:00:00Z", observed_at="2026-06-01T00:00:00Z",  # OLDER than schema
    )
    result = knowledge_store.resolve(conn, "aws_s3_bucket")  # no attribute -- resource level
    assert len(result["claims"]) == 2  # only the two resource-level claims, NOT the "acl" decoy
    assert all(c["attribute"] is None for c in result["claims"])
    assert result["status"] == "resolved"
    assert result["winner"]["source_type"] == "schema"


def test_resolve_with_zero_claims_returns_none_winner(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "nonexistent_attribute")
    assert result["status"] == "resolved"
    assert result["winner"] is None
    assert result["reason"] == "single_or_no_claim"
    assert result["claims"] == []


def test_resolve_exact_tie_in_observed_at_same_format_favors_schema(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-18T12:00:00Z")
    _insert(conn, "web", "acl is fine to use", "2026-07-18T12:00:00Z")  # identical string
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["winner"]["source_type"] == "schema"
    assert result["reason"] == "schema_observed_same_or_more_recently_than_non_schema_claim"


def test_resolve_uses_the_truly_newest_among_multiple_schema_claims(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl: older schema read", "2026-06-01T00:00:00Z")
    _insert(conn, "web", "acl: web claim between the two schema reads", "2026-07-01T00:00:00Z")
    _insert(conn, "schema", "acl: newer schema read", "2026-07-15T00:00:00Z")  # inserted last
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["winner"]["claim_text"] == "acl: newer schema read"


def test_resolve_agreeing_claims_leading_trailing_whitespace_still_agrees(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "  acl is deprecated  ", "2026-07-01T00:00:00Z")
    _insert(conn, "web", "acl is deprecated", "2026-07-18T00:00:00Z")
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["reason"] == "claims_agree"


def test_resolve_agreeing_claims_internal_whitespace_difference_does_not_agree(tmp_path):
    # Documents a known, disclosed limitation: the short-circuit normalizes leading/trailing
    # whitespace and case, but NOT internal whitespace. Locked in as a deliberate regression
    # test, not a silent gap.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl  is deprecated", "2026-07-01T00:00:00Z")  # double space
    _insert(conn, "web", "acl is deprecated", "2026-07-18T00:00:00Z")  # single space, observed later
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["reason"] != "claims_agree"
    assert result["status"] == "needs_review"
    assert result["reason"] == "non_schema_claim_observed_more_recently_than_schema_fetch"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_knowledge_store.py -v -k resolve`
Expected: FAIL with `AttributeError: module 'knowledge_store' has no attribute 'resolve'`

- [ ] **Step 3: Write minimal implementation**

```python
def _active_claims(conn, resource_type, attribute=None):
    if attribute is None:
        # Resource-level claims ONLY (attribute IS NULL), NOT every claim regardless of
        # attribute -- insert_claim() genuinely supports attribute=None for claims about the
        # resource type as a whole, and that is the only sensible meaning of
        # resolve(conn, resource_type) with no attribute. Omitting the attribute filter here
        # was a real, shipped bug (implementation-level review, 2026-07-18): it pooled claims
        # from unrelated attributes into one comparison, letting resolve() return a confident,
        # silently wrong verdict -- the third comparison-correctness bug in resolve(), after
        # the wrong-clock and format-coupling bugs above. Fix verified by reverting and
        # confirming the regression test below genuinely fails without this clause.
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_knowledge_store.py -v`
Expected: PASS (17 passed) -- 3 from Task 1 + 14 from Task 2 (5 original + 3 from ray's design-level review [timestamp-format equality, agreeing-claims short-circuit, non-schema generalization] + 6 from the implementation-level branch-surface audit that found and fixed the attribute=None pooling bug [resource-level isolation, zero claims, exact tie, multi-schema-claim max() correctness, whitespace short-circuit boundaries])

- [ ] **Step 5: Commit**

```bash
git add core/generation/knowledge_store.py tests/test_knowledge_store.py
git commit -m "feat: knowledge-layer spine -- resolve() with the freshness clause

Folds in ray's red-team review: parse observed_at via datetime, not raw string
comparison (Z vs +00:00 suffix mismatch gave false freshness verdicts); exact-text
agreement short-circuit before the freshness branch; generalize web_claims to
non_schema_claims so a future agent_delegated source isn't silently excluded."
```

---

### Task 3: import-guard test — core boundary enforced in code

**Files:**
- Create: `tests/test_knowledge_core_boundary.py`

**Interfaces:**
- Consumes: `core/generation/knowledge_store.py`, `core/generation/knowledge_diff.py` (Task 4).

Note: this is a regression lock on a property the code already has, not new behavior to implement -- the cycle here is write-the-test / run-it / confirm-PASS, not red-green.

- [ ] **Step 1: Write the test**

```python
import ast
import glob
import os
import sys

# Repo-root-anchored, NOT cwd-relative (final whole-branch review, 2026-07-18): a cwd-relative
# path silently returns an empty glob if pytest is ever invoked from outside the repo root,
# which would make both tests below vacuously pass having asserted nothing -- a guard that can
# silently test nothing is worse than no guard. Same anchoring pattern as tests/conftest.py's
# own ROOT computation.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_CORE_DIR = os.path.join(_REPO_ROOT, "core", "generation")
_FILES = sorted(os.path.basename(p) for p in glob.glob(os.path.join(_CORE_DIR, "knowledge_*.py")))
_ALLOWED = set(sys.stdlib_module_names) | {"schema_watch", "modules", "module_registry"}


# Scope, disclosed: this guard checks each knowledge_*.py file's OWN direct imports only, not
# the transitive closure of what an _ALLOWED dependency (schema_watch/modules/module_registry)
# imports internally. A re-export path -- e.g. schema_watch.py importing knowledge_verifier_nli
# and knowledge_store.py accessing it as schema_watch.knowledge_verifier_nli -- would not be
# caught by this file at all, since the literal name never appears as an import in the checked
# files. Treated as out of scope: the plan's own boundary is "core imports nothing beyond
# stdlib + schema_watch" (a first-order check), not a recursive audit of schema_watch's own
# dependency graph. Extending to a transitive check is a materially bigger feature, not a fix.


def _module_level_nodes(nodes):
    """Yields every AST node reachable at module level: the given statements plus everything
    nested inside them (try/except, if/elif, with, for/while, match/case, ...), EXCEPT bodies
    of FunctionDef/AsyncFunctionDef/ClassDef -- lazy imports inside a function are the
    sanctioned escape hatch this project relies on elsewhere and must stay invisible to this
    guard. Walks via ast.iter_fields (a generic field-walk) rather than a hardcoded list of
    compound-statement types, so no statement kind is silently missed -- the only invariant to
    verify is "never descends into def/class bodies," not "did I enumerate every syntax form"
    (final whole-branch review, 2026-07-18: the original version used ast.iter_child_nodes(),
    which only saw DIRECT children of Module and silently missed any import one level deeper,
    including the try/except-wrapped case this guard exists to catch)."""
    for node in nodes:
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for _, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            children = [c for c in children if isinstance(c, ast.AST)]
            if children:
                yield from _module_level_nodes(children)


def _module_level_imports(tree_body):
    for node in _module_level_nodes(tree_body):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def _module_level_dynamic_import_calls(tree_body):
    """Yields __import__(...) / importlib.import_module(...) call sites at module level. These
    bypass Import/ImportFrom detection entirely since the imported name is an arbitrary runtime
    expression (string concatenation, a variable, ...), not a parseable identifier -- a precise
    "did this dynamically import knowledge_verifier_nli specifically" check is undecidable by
    static analysis. Instead of trying, this bans the CALL FORM itself at module level in these
    core files: a stdlib-only, disclosed-boundary design has no legitimate reason to reach for
    dynamic imports here at all."""
    for node in _module_level_nodes(tree_body):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "__import__":
            yield node
        elif isinstance(func, ast.Attribute) and func.attr == "import_module" and \
                isinstance(func.value, ast.Name) and func.value.id == "importlib":
            yield node


def test_module_level_imports_descends_into_try_except_but_not_into_function_defs():
    # Proves the fix directly: a try/except-wrapped import at module level IS caught (this is
    # the exact bypass pattern the final review found), while a lazy import inside a function
    # body is NOT caught (that remains the sanctioned escape hatch -- over-catching it would
    # make this guard reject normal, allowed code). The general field-walk also covers an
    # `if TYPE_CHECKING:`-style conditional import with no special-casing needed, since it's
    # just another ast.If under the same walk.
    source = (
        "try:\n"
        "    import knowledge_verifier_nli\n"
        "except ImportError:\n"
        "    knowledge_verifier_nli = None\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    import knowledge_verifier_nli\n"
        "\n"
        "def _lazy():\n"
        "    import knowledge_verifier_nli\n"
    )
    tree = ast.parse(source)
    names = []
    for node in _module_level_imports(tree.body):
        names += [node.module] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names]
    assert names.count("knowledge_verifier_nli") == 2  # try/except + TYPE_CHECKING, not _lazy()


def test_module_level_dynamic_import_calls_detects_both_import_forms():
    # __import__(...) and importlib.import_module(...) are both banned call forms at module
    # level; a call to either buried inside a function body stays outside this guard's scope,
    # same rule as ordinary imports.
    source = (
        "x = __import__('knowledge_verifier_nli')\n"
        "import importlib\n"
        "y = importlib.import_module('knowledge_verifier_nli')\n"
        "\n"
        "def _lazy():\n"
        "    return __import__('knowledge_verifier_nli')\n"
    )
    tree = ast.parse(source)
    calls = list(_module_level_dynamic_import_calls(tree.body))
    assert len(calls) == 2


def test_knowledge_core_imports_nothing_beyond_stdlib_and_schema_watch():
    assert _FILES, "boundary test discovered no core files -- _CORE_DIR resolution or glob pattern broke"
    for filename in _FILES:
        with open(os.path.join(_CORE_DIR, filename), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filename)
        for node in _module_level_imports(tree.body):
            names = [node.module] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names]
            for name in names:
                top = (name or "").split(".")[0]
                assert top in _ALLOWED, f"{filename} imports non-stdlib {name!r} at module level"


def test_knowledge_verifier_nli_is_never_module_level_imported_by_core():
    assert _FILES, "boundary test discovered no core files -- _CORE_DIR resolution or glob pattern broke"
    for filename in _FILES:
        with open(os.path.join(_CORE_DIR, filename), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filename)
        for node in _module_level_imports(tree.body):
            names = [node.module] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names]
            for name in names:
                assert (name or "").split(".")[0] != "knowledge_verifier_nli", (
                    f"{filename} imports knowledge_verifier_nli at module level -- "
                    "the verifier must be absent-able, not just optional by convention")


def test_knowledge_core_has_no_dynamic_import_calls_at_module_level():
    assert _FILES, "boundary test discovered no core files -- _CORE_DIR resolution or glob pattern broke"
    for filename in _FILES:
        with open(os.path.join(_CORE_DIR, filename), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filename)
        for node in _module_level_dynamic_import_calls(tree.body):
            raise AssertionError(
                f"{filename}:{node.lineno} uses a dynamic import call (__import__/"
                "importlib.import_module) at module level -- these bypass static "
                "Import/ImportFrom detection and are banned outright in core")
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_knowledge_core_boundary.py -v`
Expected: PASS (5 passed) -- `_FILES` is discovered via a repo-root-anchored `glob.glob(.../"knowledge_*.py")`, so at this point it matches only `knowledge_store.py` (Task 1/2), confirming it already satisfies the boundary; the two synthetic-source tests (`test_module_level_imports_descends_into_try_except_but_not_into_function_defs`, `test_module_level_dynamic_import_calls_detects_both_import_forms`) are self-contained and don't depend on `_FILES` at all. No edit to this test file is needed later: once Task 4 adds `knowledge_diff.py`, the same glob picks it up automatically on the next run.

- [ ] **Step 3: Commit**

```bash
git add tests/test_knowledge_core_boundary.py
git commit -m "test: enforce knowledge-layer core/verifier boundary via AST import guard"
```

---

### Task 4: `knowledge_diff.py` — real live-schema structural claims

**Files:**
- Create: `core/generation/knowledge_diff.py`
- Test: `tests/test_knowledge_diff.py`

**Interfaces:**
- Consumes: `schema_watch.get_type_schema(provider, type_name, kind="resource") -> dict | None` (existing, `core/generation/schema_watch.py:135-166`).
- Produces: `schema_claims_for_type(provider: str, resource_type: str, observed_at: str | None = None) -> list[dict]` -- each dict has every keyword `knowledge_store.insert_claim` expects except `ingested_at`.

- [ ] **Step 1: Write the failing test (real fetch, terraform-gated)**

```python
"""
knowledge_diff.py tests -- real live-schema fetch against the real AWS provider, no mocks,
same discipline as test_schema_watch.py. Skips if terraform isn't installed.
"""
import pytest

import knowledge_diff
import toolpath

TERRAFORM = toolpath.find_tool("terraform")


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_schema_claims_for_aws_s3_bucket_includes_a_real_required_attribute():
    claims = knowledge_diff.schema_claims_for_type("aws", "aws_s3_bucket")
    by_attr = {c["attribute"]: c for c in claims}
    assert "bucket" in by_attr or "bucket_prefix" in by_attr  # real schema, not asserted blind
    assert all(c["source_type"] == "schema" for c in claims)
    assert all(c["method"] == "structural" for c in claims)
    assert all(c["provider_version"] for c in claims)


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_schema_claims_for_an_unknown_type_returns_empty_not_an_exception():
    claims = knowledge_diff.schema_claims_for_type("aws", "aws_totally_made_up_type")
    assert claims == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_knowledge_diff.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'knowledge_diff'`

- [ ] **Step 3: Write minimal implementation**

```python
"""
knowledge_diff.py -- the structural-diff path: live provider schema -> a set of deterministic
'schema' claims, ready to insert into knowledge_store. Reuses schema_watch.get_type_schema()
(core/generation/schema_watch.py:135-166), which itself reuses _fetch_schema() -- no second
fetch mechanism.
"""
import datetime

import schema_watch


def schema_claims_for_type(provider, resource_type, observed_at=None):
    observed_at = observed_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
    block = schema_watch.get_type_schema(provider, resource_type)
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
            "provider_version": None,  # populated once schema_watch exposes it per-call; see note below
            "valid_from": observed_at, "observed_at": observed_at,
        })
    return claims
```

Note for the implementer: `get_type_schema()` currently discards the resolved provider version internally (`schema_watch.py:159-160` unpacks `schema, _resolved_version = _fetch_schema(...)` and throws the version away). This task's test asserts `provider_version` is truthy, so either (a) call `schema_watch._fetch_schema()` directly here instead of `get_type_schema()` to get both the schema and the version, keeping the type-lookup logic duplicated in three lines, or (b) file a one-line change to `get_type_schema()`'s own return shape. Prefer (a) for this task -- it touches zero existing files, matching "don't touch what you don't have to."

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_knowledge_diff.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add core/generation/knowledge_diff.py tests/test_knowledge_diff.py
git commit -m "feat: knowledge-layer spine -- structural claims from live provider schema"
```

---

### Task 5: end-to-end proof — `aws_s3_bucket`, real fetch, both resolve() outcomes

**Files:**
- Modify: `tests/test_knowledge_diff.py`

**Interfaces:**
- Consumes: `knowledge_diff.schema_claims_for_type` (Task 4), `knowledge_store.init_db` / `insert_claim` / `resolve` (Tasks 1-2).

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_end_to_end_real_schema_claim_beats_an_older_contradicting_web_claim(tmp_path):
    import knowledge_store
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_claims = knowledge_diff.schema_claims_for_type(
        "aws", "aws_s3_bucket", observed_at="2026-07-18T12:00:00Z")
    for c in schema_claims:
        knowledge_store.insert_claim(conn, **c)
    acl_claim = next((c for c in schema_claims if c["attribute"] == "acl"), None)
    assert acl_claim is not None
    knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="acl",
        claim_text="acl works fine, no deprecation", method="semantic", source_type="web",
        provider="aws", valid_from="2026-07-10T00:00:00Z", observed_at="2026-07-10T00:00:00Z",
    )  # observed BEFORE the schema fetch above
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["winner"]["source_type"] == "schema"


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_end_to_end_newer_web_claim_forces_review_not_schema_default(tmp_path):
    import knowledge_store
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_claims = knowledge_diff.schema_claims_for_type(
        "aws", "aws_s3_bucket", observed_at="2026-06-01T00:00:00Z")  # fetched weeks ago
    for c in schema_claims:
        knowledge_store.insert_claim(conn, **c)
    knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="acl",
        claim_text="acl was removed in the latest provider release", method="semantic",
        source_type="web", provider="aws",
        valid_from="2026-07-18T00:00:00Z", observed_at="2026-07-18T00:00:00Z",  # observed just now
    )
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "needs_review"
    assert result["reason"] == "non_schema_claim_observed_more_recently_than_schema_fetch"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_knowledge_diff.py -v -k end_to_end`
Expected: FAIL if any wiring gap exists between Tasks 1-4 (e.g. a keyword mismatch between `schema_claims_for_type`'s dict shape and `insert_claim`'s signature) -- this task exists specifically to catch that kind of seam bug before calling Step 2 done.

- [ ] **Step 3: Fix any wiring gap found**

No new production code expected if Tasks 1-4 were implemented as specified; fix whatever the real run surfaces.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_knowledge_diff.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_knowledge_diff.py
git commit -m "test: knowledge-layer spine -- end-to-end proof on aws_s3_bucket, both resolve() outcomes"
```

---

## Steps 3-5 (outline only — NOT built until Step 2 lands and is reviewed)

These are sketched at the shape/interface level for red-team review of the overall design now, not bite-sized for execution yet. Per the project's own step-by-step gating: each step gets its own diff + tests + review before the next is even planned in detail.

**Priority note (post-red-team):** Step 3 is not indefinitely deferrable. It's what bounds the schema-cached-vs-web-live recency asymmetry that Q2's noise-queue concern and ray's finding 2 both trade on — if schema's `observed_at` never refreshes, every future web/agent_delegated claim will trivially look "newer," making `needs_review` the default outcome rather than the exception. Treat Step 3 as a near-term follow-up to Step 2, not a someday item.

### Step 3 — bi-temporal degradation check (generalizes `schema_watch.py`'s drift pattern)

- **New**: a `reverify_after` concept per claim (or a separate `degradation_check.py`) that re-fetches a resource type's live schema on a cadence, groups existing claims by `(resource_type, attribute)` (confirmed in Step 1's design review: no new linking column needed -- these two existing, already-indexed columns are the version-independent key; `content_hash` stays deliberately version-scoped for exact-duplicate detection only), and inserts a fresh `schema` claim when the live fetch disagrees with the last-stored one -- setting `valid_until`/`invalidated_at`/`invalidated_by` on the superseded row (never deleting it).
- **Consumes**: `knowledge_diff.schema_claims_for_type`, `knowledge_store.insert_claim`, a new `knowledge_store.invalidate_claim(conn, claim_id, invalidated_by)`.
- **Open question for ray**: content-hash-based dedup (skip re-inserting an identical observation) vs. always inserting and letting `resolve()`'s freshness clause do the work -- leaning toward always-insert-and-let-resolve-decide, since deduping by hash risks silently swallowing a "the world didn't change" re-confirmation that itself has evidentiary value (a schema re-checked and found unchanged is a fact worth an `observed_at` bump, not a no-op).

### Step 4 — agent-delegation contract for the semantic path

- **New**: a function (name TBD, e.g. `build_delegation_request(conn, resource_type, attribute)`) that packages a `needs_review` result from `resolve()` into a structured hand-off: the conflicting claims, their provenance/URLs, resource_type/attribute, and a place for the driving agent to write back a verdict (`record_delegation_verdict(conn, resource_type, attribute, verdict_claim)`).
- **No local model anywhere in this path** -- the driving agent does the adjudication; MinusOps only packages the question and records the answer as a new claim (still an `INSERT`, never an `UPDATE`).
- **Resolved by red-team review**: `source_type` is a plain `TEXT NOT NULL` column with no `CHECK` constraint or enum -- `"agent_delegated"` is not schema-enforced/"reserved," it simply requires no migration to start using. `resolve()` (Task 2) already generalizes to any non-schema `source_type`, so a dedicated `"agent_delegated"` value is safe to introduce here without touching `knowledge_store.py` again: it will fall into `non_schema_claims` alongside `"web"` and participate in the freshness clause identically. Dedicated `source_type` confirmed as the right choice -- collapsing it into `"web"` would lose the distinction between "found on the internet" and "adjudicated by the agent given both sides," and `confidence` can be populated from whatever the agent reports without `resolve()` ever reading it.

### Step 5 — breadth×depth budget knob (request-shaping contract, not a loop MinusOps runs)

- **New**: a plain parameter object (e.g. `ResearchBudget(breadth: int, depth: int)`) that MinusOps hands to the driving agent alongside a delegation request or an authoring-context request (`assemble_authoring_context()`, `core/generation/synthesizer.py:139`) -- purely descriptive, MinusOps never iterates or enforces it itself.
- **Open question for ray**: does this belong in `knowledge_store.py`/`knowledge_diff.py` at all, or is it purely a `synthesizer.py`-side concern that only *reads* the knowledge layer's `needs_review` queue depth to decide how much budget to hand out? Leaning toward the latter -- keeps the knowledge-layer core from acquiring an opinion about how the agent should spend its own research effort.

---

## Self-Review

**Spec coverage**: Step 2's four settled pieces (a: placement/reuse, b: schema, c: boundary, d: resource) each map to a task above (Tasks 1-2 = schema/store, Task 3 = boundary, Task 4 = diff engine reusing `get_type_schema`, all five tasks together = `aws_s3_bucket` end to end). Steps 3-5 are explicitly out of scope for this pass, sketched only for red-team input on shape.

**Placeholder scan**: no TBD/"add appropriate handling" in any Task 1-5 step; Steps 3-5's "open question for ray" markers are deliberate discussion points for the review this plan is headed to, not implementation gaps in tasks meant to be built now.

**Type consistency**: `insert_claim`'s keyword signature (Task 1) matches exactly what `schema_claims_for_type` (Task 4) produces per-dict and what the end-to-end test (Task 5) calls with `**c`; `resolve()`'s return shape (`status`/`winner`/`claims`/`reason`) is identical across Task 2's unit tests and Task 5's end-to-end tests. Post-red-team: the renamed `reason` string (`non_schema_claim_observed_more_recently_than_schema_fetch`, replacing `web_claim_observed_more_recently_than_schema_fetch`) was checked and updated in both places it appears -- Task 2's own test and Task 5's end-to-end test -- so the two don't drift.
