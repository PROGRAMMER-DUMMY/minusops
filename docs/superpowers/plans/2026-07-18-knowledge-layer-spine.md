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
- **Testing convention, binding on every task in this plan, present and future (established by Step 2's `test_resolve_uses_the_truly_newest_among_multiple_schema_claims`, restated here as a rule after it was violated once already in Step 3's own draft):** any test proving "the newest/correct row is picked from a set that could contain more than one" MUST insert rows with insertion order deliberately scrambled against timestamp order (the row that should win inserted earlier or later than a naive "last/first inserted wins" implementation would pick). A test where insertion order happens to match timestamp order can pass under both a correct and a buggy implementation and proves nothing — this is not a per-test reminder, it is a required property of the test before it can be trusted.

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

**Status (2026-07-19): bite-sized below, approved design after TWO ray review rounds, ready to build subagent-driven.**

Round 1 (design, before any code written): the original sketch (above, kept for history) plus a
reconciliation pass that resolved every open question, corrected the DB default path, and found
one thing neither the sketch nor the first pass caught (the removed-attribute case) plus one
near-miss caught before any code was written (SQL-level `ORDER BY observed_at` would have
reintroduced bug #2's exact shape — string comparison on a `Z`/`+00:00`-mixed column. All ordering
in this step goes through `_parse_ts()` in Python, never SQL). Self-review during that round also
caught the dict-comprehension bug (below).

Round 2 (implementation-level review against the *shipped* Step 2 code, same standard as the
`attribute IS NULL` catch) found a fourth-family bug of its own and a flaw in the test meant to
guard the third:
- **A fifth instance of "confident wrong answer": the type-not-found ambiguity** (Task 4). `schema_
  claims_for_type()` returns `[]` both when a resource type genuinely doesn't exist and when it
  exists with zero attributes — Step 2's own already-tested contract, not touched here. Left
  unguarded, an empty fetch would cascade into marking EVERY previously-tracked attribute
  "removed" — reachable by an ordinary typo or the wrong `--kind` (`aws_s3_bucket` is a real name
  under both `resource` and `data`), not an edge case. Fixed: `check_and_refresh` skips the
  removed-attribute pass entirely when the fetch is empty, surfaces `"skipped_removed_attribute_
  check"` in its summary, and the CLI (Task 5) warns on stderr and exits non-zero rather than
  quietly reporting success.
- **The regression test for bug #4 (duplicate active claims) was theater**: it inserted the older
  and newer claims in an order that happened to match their timestamps, so SQLite's natural
  (unordered) row-return order coincided with the correct `_parse_ts`-based pick — the buggy
  dict-comprehension version would very likely have passed the same test. Fixed by scrambling
  insertion order (Task 3), and the scrambling requirement is now a standing testing convention
  (see Global Constraints), not a fact to remember per-test.
- **`invalidate_claim`'s `valid_until` was set from the superseding claim's `observed_at`;
  corrected to `valid_from`.** `valid_until` must track the same axis as `valid_from` (the fact's
  own timeline) — using `observed_at` sets the window's end to when the check happened to notice,
  not when the fact actually changed, misrepresenting the window by however large the release-to-
  recheck gap is. This only "worked" today because of Step 2's own disclosed placeholder
  (`knowledge_diff.py` sets `valid_from == observed_at` for every schema claim) — fixed now, while
  free, rather than left as a second latent clock-conflation for whatever eventually reads
  `valid_until`'s value for real (Step 4, an audit view).

**Decisions locked in, restated from the sketch's open questions:**
- **Always-insert, no dedup** — confirmed as the design, not just defensible: a re-check that
  finds nothing changed still inserts a fresh claim and invalidates the old one, bumping
  `observed_at`. This is load-bearing for the freshness clause's own noise-queue reasoning (ray's
  Q2), not just "evidence has value" — it's what keeps schema claims from going permanently stale
  relative to live-fetched non-schema claims.
- **DB default path is `modules.output_root()/knowledge/claims.db`**, not a bare `.agents/`-
  relative path — `output_root()` (`core/generation/modules.py:50`) exists specifically to be
  wheel-safe (never resolves into `site-packages` when installed), and `modules` is already in the
  boundary guard's `_ALLOWED` set, so this costs nothing extra there. `--db` overrides.
- **`_ALLOWED` in `tests/test_knowledge_core_boundary.py` gains `"knowledge_store"`,
  `"knowledge_diff"`** — first-party same-family modules, not a loosening of the stdlib-only
  boundary against the outside world. Confirmed this does NOT touch the separate
  `test_knowledge_verifier_nli_is_never_module_level_imported_by_core` check (`test_knowledge_core_
  boundary.py:130-140`), which never reads `_ALLOWED` at all — it's a fully independent function
  scanning for one literal name.
- **Only one new read primitive**, not two: `_active_schema_claims_for_resource(conn, resource_type)`
  (plural, one query, dict-built in Python) serves both the per-attribute "what's currently active"
  lookup inside the main loop and the removed-attribute diff. A singular per-attribute lookup was
  in the original proposal but nothing in this step actually needs it (YAGNI) — dropped.
- **Removed-attribute case (found reading the shipped code, not in the original sketch):** an
  attribute with a previously-active schema claim that's absent from a fresh fetch would otherwise
  stay "active" forever, asserting something about an attribute that no longer exists — a
  silent-stale-answer bug in the same family as `resolve()`'s three. Fix: insert a synthetic
  `"<attr>: removed from live schema"` claim and invalidate the stale one against it, so `resolve()`
  has a real current belief instead of a silent absence.

**Consumes**: `knowledge_diff.schema_claims_for_type`, `knowledge_store.insert_claim`,
`knowledge_store._parse_ts` (reused, not reimplemented), plus the two new `knowledge_store.py`
functions built in Task 1 below.

**Red-Team focus for this step (for ray) — point at these two specifically, not a general pass:**

1. **The removed-attribute synthetic-claim logic** (Task 4 below). This is new belief-state
   semantics with no prior art in this codebase — nothing before this has ever had the store
   assert something on the store's own initiative rather than relaying an external observation
   verbatim. Does synthesizing a claim (vs. some other representation of "we don't know anymore")
   hold up under scrutiny? Does it interact correctly with `resolve()`'s existing agreement
   short-circuit or freshness comparison in any surprising way once it's just another schema claim
   in the table?
2. **`invalidate_claim`'s two-clock handling** (Task 1 below): `valid_until` (fact-validity end,
   caller-supplied, tied to the superseding claim's `observed_at`) vs `invalidated_at` (write-time
   audit stamp, defaults to now) — the exact wrong-clock shape that produced bug #1
   (`resolve()` originally compared `valid_from` instead of `observed_at`). Task 1's own test is
   built to fail if the two are swapped; does that test actually prove what it claims to, and is
   there a swap/confusion risk this design doesn't cover?

---

### Task 1: `knowledge_store.py` — `invalidate_claim()` + `_active_schema_claims_for_resource()`

**Files:**
- Modify: `core/generation/knowledge_store.py`
- Test: `tests/test_knowledge_store.py`

**Interfaces:**
- Consumes: `_parse_ts` (existing, this file).
- Produces: `invalidate_claim(conn, claim_id, *, valid_until, invalidated_by=None, invalidated_at=None) -> None`; `_active_schema_claims_for_resource(conn, resource_type) -> list[dict]` (active `source_type="schema"` claims with a non-null `attribute`, i.e. excludes resource-level schema claims — this function is specifically for per-attribute presence tracking).

- [ ] **Step 1: Write the failing tests**

```python
def test_active_schema_claims_for_resource_returns_only_active_attribute_scoped_schema_claims(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "bucket: optional", "2026-07-01T00:00:00Z", attribute="bucket")
    _insert(conn, "web", "acl: contested", "2026-07-01T00:00:00Z", attribute="acl")  # wrong source_type
    knowledge_store.insert_claim(  # resource-level (attribute=None), must be excluded
        conn, resource_type="aws_s3_bucket", attribute=None, claim_text="stable",
        method="structural", source_type="schema", provider="aws",
        valid_from="2026-07-01T00:00:00Z", observed_at="2026-07-01T00:00:00Z",
    )
    result = knowledge_store._active_schema_claims_for_resource(conn, "aws_s3_bucket")
    assert len(result) == 1
    assert result[0]["attribute"] == "bucket"
    assert result[0]["source_type"] == "schema"


def test_active_schema_claims_for_resource_excludes_invalidated_rows(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    old_id = _insert(conn, "schema", "bucket: optional", "2026-07-01T00:00:00Z", attribute="bucket")
    knowledge_store.invalidate_claim(conn, old_id, valid_until="2026-07-10T00:00:00Z")
    result = knowledge_store._active_schema_claims_for_resource(conn, "aws_s3_bucket")
    assert result == []


def test_invalidate_claim_sets_valid_until_and_invalidated_at_as_distinct_clocks(tmp_path):
    # valid_until = fact-validity end (caller-supplied, semantically tied to the superseding
    # claim's valid_from -- NOT its observed_at) vs invalidated_at = write-time audit stamp
    # (defaults to now) -- the exact wrong-clock shape that produced bug #1 (resolve() originally
    # compared valid_from instead of observed_at; this function's own docstring names the same
    # risk in reverse -- using observed_at here instead of valid_from). Deliberately far-apart,
    # easily-distinguishable values, so a swap between the two fields is caught, not just "some
    # value got set somewhere."
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    claim_id = _insert(conn, "schema", "acl: required", "2026-06-01T00:00:00Z", attribute="acl")
    knowledge_store.invalidate_claim(
        conn, claim_id, valid_until="2026-07-01T00:00:00Z",  # the superseding claim's valid_from
        invalidated_at="2099-01-01T00:00:00Z",  # deliberately absurd write-time stamp
        invalidated_by=999,
    )
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    assert row["valid_until"] == "2026-07-01T00:00:00Z"
    assert row["invalidated_at"] == "2099-01-01T00:00:00Z"
    assert row["invalidated_by"] == 999


def test_invalidate_claim_defaults_invalidated_at_to_now_but_never_defaults_valid_until(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    claim_id = _insert(conn, "schema", "acl: required", "2026-06-01T00:00:00Z", attribute="acl")
    knowledge_store.invalidate_claim(conn, claim_id, valid_until="2026-07-01T00:00:00Z")
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    assert row["valid_until"] == "2026-07-01T00:00:00Z"  # exactly what was passed, untouched
    assert row["invalidated_at"] is not None  # defaulted, but to a DIFFERENT clock than valid_until
    assert row["invalidated_at"] != row["valid_until"]


def test_invalidate_claim_requires_valid_until_explicitly():
    import pytest
    with pytest.raises(TypeError):
        knowledge_store.invalidate_claim(None, 1, invalidated_by=2)  # no valid_until -- must not silently default to now()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_knowledge_store.py -v -k "active_schema_claims_for_resource or invalidate_claim"`
Expected: FAIL with `AttributeError: module 'knowledge_store' has no attribute 'invalidate_claim'` (and `'_active_schema_claims_for_resource'`).

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_knowledge_store.py -v`
Expected: PASS (22 passed) -- 17 from Step 2 + 5 new above.

- [ ] **Step 5: Empirically verify the two-clock test actually catches a swap**

Not optional -- same standard as the `attribute IS NULL` fix. Temporarily swap the two values in
`invalidate_claim`'s `UPDATE` tuple (`(invalidated_at, valid_until, invalidated_by, claim_id)`
instead of `(valid_until, invalidated_at, invalidated_by, claim_id)`), run
`test_invalidate_claim_sets_valid_until_and_invalidated_at_as_distinct_clocks` alone, confirm it
FAILS, then revert and confirm it passes again. Paste both results in the report.

- [ ] **Step 6: Commit**

```bash
git add core/generation/knowledge_store.py tests/test_knowledge_store.py
git commit -m "feat: knowledge-layer spine Step 3 -- invalidate_claim + active-schema-claims read primitive

Two-clock design (valid_until vs invalidated_at) verified by deliberately
swapping the UPDATE tuple and confirming the distinguishing test fails,
then reverting -- same standard as Step 2's attribute-IS-NULL fix."
```

---

### Task 2: boundary-guard `_ALLOWED` update

**Files:**
- Modify: `tests/test_knowledge_core_boundary.py`

**Interfaces:** none -- this only widens what Task 3/4/5's new file is permitted to import.

- [ ] **Step 1: Apply the change**

```python
_ALLOWED = set(sys.stdlib_module_names) | {"schema_watch", "modules", "module_registry",
                                            "knowledge_store", "knowledge_diff"}
```

- [ ] **Step 2: Run the full boundary suite, confirm still green**

Run: `pytest tests/test_knowledge_core_boundary.py -v`
Expected: PASS (5 passed) -- unchanged count; this widens the allowlist, doesn't add a new test.
Confirm specifically that `test_knowledge_verifier_nli_is_never_module_level_imported_by_core`
still passes and is unaffected (it doesn't read `_ALLOWED`, but confirm anyway since this task
touches the same file).

- [ ] **Step 3: Commit**

```bash
git add tests/test_knowledge_core_boundary.py
git commit -m "feat: knowledge-layer spine Step 3 -- allow knowledge_store/knowledge_diff as intra-project imports

First-party same-family modules, not a loosening of the stdlib-only boundary
against the outside world. Does not touch the separate
knowledge_verifier_nli-never-imported check, which reads no allowlist."
```

---

### Task 3: `knowledge_degradation.py` — `check_and_refresh()` core loop (present attributes)

**Files:**
- Create: `core/generation/knowledge_degradation.py`
- Test: `tests/test_knowledge_degradation.py`

**Interfaces:**
- Consumes: `knowledge_diff.schema_claims_for_type`, `knowledge_store.insert_claim`,
  `knowledge_store.invalidate_claim`, `knowledge_store._active_schema_claims_for_resource`,
  `knowledge_store._parse_ts` (accessed as `knowledge_store._parse_ts`, not reimplemented -- picks
  the newest among possibly-multiple active schema claims per attribute; see the code below for
  why a plain dict comprehension over `_active_schema_claims_for_resource`'s result isn't safe).
- Produces: `check_and_refresh(conn, provider, resource_type, kind="resource") -> dict` returning
  `{"resource_type", "provider", "inserted": [ids], "invalidated": [ids], "removed_attributes": []}`.
  This task builds the present-attribute loop only; `removed_attributes` stays `[]` until Task 4.

- [ ] **Step 1: Write the failing tests (stubbed fetch -- fast, no live network)**

```python
"""
knowledge_degradation.py tests -- the stubbed tests below fake schema_claims_for_type()'s return
to run fast with no live fetch. Their fake return shape is asserted, separately, against the REAL
function's actual return in test_schema_claims_for_type_shape_matches_the_degradation_stubs
(Task 4) -- a stub that silently drifts from reality is exactly how the +00:00/Z bug survived,
so that drift-guard is not optional.
"""
import pytest

import knowledge_degradation
import knowledge_store
import toolpath

TERRAFORM = toolpath.find_tool("terraform")

_FIXED_CLAIM = {
    "resource_type": "aws_s3_bucket", "attribute": "bucket",
    "claim_text": "bucket: optional, not deprecated", "method": "structural",
    "source_type": "schema", "provider": "aws", "provider_version": "6.54.0",
    "valid_from": "2026-07-19T00:00:00Z", "observed_at": "2026-07-19T00:00:00Z",
}


def test_check_and_refresh_inserts_when_no_prior_claim_exists(tmp_path, monkeypatch):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [dict(_FIXED_CLAIM)])
    summary = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    assert len(summary["inserted"]) == 1
    assert summary["invalidated"] == []
    assert summary["removed_attributes"] == []


def test_check_and_refresh_always_inserts_even_when_nothing_changed(tmp_path, monkeypatch):
    # THE decision this task locks in: no content-hash dedup. A re-check finding zero real
    # change still inserts a fresh claim and invalidates the old one -- the observed_at bump is
    # load-bearing for the freshness clause's own noise-queue reasoning (ray's Q2), not a no-op.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [dict(_FIXED_CLAIM)])
    summary1 = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    summary2 = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    assert len(summary2["inserted"]) == 1
    assert summary2["inserted"] != summary1["inserted"]  # a genuinely new row, not a no-op
    assert summary2["invalidated"] == summary1["inserted"]  # invalidates exactly the prior insert
    active = knowledge_store._active_schema_claims_for_resource(conn, "aws_s3_bucket")
    assert len(active) == 1  # exactly one active claim survives, not two accumulating
    assert active[0]["id"] == summary2["inserted"][0]


def test_check_and_refresh_invalidates_the_old_claim_with_the_new_claims_valid_from(tmp_path, monkeypatch):
    # Proves Task 1's two-clock discipline is actually wired correctly at the call site, not just
    # correct in isolation: valid_until on the OLD row must equal the NEW claim's valid_from,
    # NOT its observed_at (ray's review, 2026-07-19 -- valid_until must track the fact-validity
    # axis, not when this check happened to notice). valid_from and observed_at are set to
    # DIFFERENT, easily-distinguishable values here specifically so this can't pass by coincidence
    # if the two axes were conflated -- same standard as the invalidated_at/valid_until swap test.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [dict(_FIXED_CLAIM)])
    summary1 = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    old_id = summary1["inserted"][0]
    later_claim = dict(_FIXED_CLAIM, valid_from="2026-08-01T00:00:00Z", observed_at="2026-09-15T00:00:00Z")
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [dict(later_claim)])
    knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    old_row = conn.execute("SELECT * FROM claims WHERE id = ?", (old_id,)).fetchone()
    assert old_row["valid_until"] == "2026-08-01T00:00:00Z"  # the new claim's valid_from
    assert old_row["valid_until"] != "2026-09-15T00:00:00Z"  # NOT the new claim's observed_at


def test_check_and_refresh_targets_the_newest_of_multiple_pre_existing_active_claims(tmp_path, monkeypatch):
    # Simulates a DB used before this step's invalidate-on-insert discipline existed (Step 2's own
    # tests/proof runs never invalidated anything, so more than one active schema claim CAN
    # already exist for one attribute). check_and_refresh's bookkeeping must reference the NEWEST
    # of them, not whichever row SQLite happens to return last from _active_schema_claims_for_resource.
    #
    # Insertion order deliberately scrambled against timestamp order (the NEWER-by-observed_at
    # claim inserted FIRST, older SECOND) -- required by this plan's Global Constraints testing
    # convention. Ray's review, 2026-07-19: the original draft of this test inserted older-then-
    # newer, matching timestamp order, so SQLite's natural (unordered) row-return order happened
    # to coincide with the correct _parse_ts-based pick -- the buggy dict-comprehension version
    # would very likely have passed the exact same test. This ordering is what makes the two
    # implementations actually diverge; see Step 5 below for the required fail-first proof.
    #
    # Disclosed limit, not asserted here: the OTHER (older) pre-existing duplicate is left
    # untouched, still active -- this step guarantees correct behavior going forward, not
    # retroactive cleanup of duplicates that predate it.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    newer_id = knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="bucket", claim_text="bucket: optional, not deprecated",
        method="structural", source_type="schema", provider="aws", provider_version="6.54.0",
        valid_from="2026-06-01T00:00:00Z", observed_at="2026-06-01T00:00:00Z",
    )
    older_id = knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="bucket", claim_text="bucket: stale duplicate",
        method="structural", source_type="schema", provider="aws", provider_version="6.50.0",
        valid_from="2026-05-01T00:00:00Z", observed_at="2026-05-01T00:00:00Z",
    )
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [dict(_FIXED_CLAIM)])
    summary = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    assert newer_id in summary["invalidated"]
    assert older_id not in summary["invalidated"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_knowledge_degradation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'knowledge_degradation'`

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_knowledge_degradation.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Empirically verify both comparison-sensitive tests actually catch a break**

Not optional -- same standard as Task 1's two-clock swap, applied here to two separate breaks:

1. **The duplicate-selection fix** (`test_check_and_refresh_targets_the_newest_of_multiple_pre_existing_active_claims`): temporarily replace the `_by_attr` reduction loop with the naive version --
   ```python
   previously_active_by_attr = {
       c["attribute"]: c
       for c in knowledge_store._active_schema_claims_for_resource(conn, resource_type)
   }
   ```
   Run this one test alone, confirm it FAILS (this is the proof that scrambling insertion order in the test actually made it discriminate -- the un-scrambled version from the first draft would NOT have failed here). Revert, confirm it passes again.
2. **The valid_from fix** (`test_check_and_refresh_invalidates_the_old_claim_with_the_new_claims_valid_from`): temporarily change `valid_until=claim["valid_from"]` back to `valid_until=claim["observed_at"]`. Run this one test alone, confirm it FAILS on the `!= "2026-09-15T00:00:00Z"` assertion. Revert, confirm it passes again.

Paste all four results (fail, pass, fail, pass) in the report.

- [ ] **Step 6: Commit**

```bash
git add core/generation/knowledge_degradation.py tests/test_knowledge_degradation.py
git commit -m "feat: knowledge-layer spine Step 3 -- check_and_refresh() present-attribute loop

Always-insert, no content-hash dedup -- load-bearing for resolve()'s
freshness clause per ray's Q2 noise-queue reasoning, not just evidentiary
value. Removed-attribute handling is Task 4.

Folds in ray's second review round: duplicate-active-claim selection goes
through _parse_ts (not a dict comprehension that keeps whichever row SQLite
returns last); its regression test uses scrambled insertion order so it
can't pass by coincidence, verified to fail against the naive version and
against un-scrambled insertion order; invalidate_claim's valid_until is set
from the superseding claim's valid_from, not observed_at -- both empirically
verified to fail pre-fix."
```

---

### Task 4: removed-attribute handling + the real-shape drift guard

**Files:**
- Modify: `core/generation/knowledge_degradation.py`
- Modify: `tests/test_knowledge_degradation.py`

**Interfaces:**
- Consumes: same as Task 3, no new dependency.
- Produces: `check_and_refresh()`'s `removed_attributes` field, now populated. Also adds
  `"skipped_removed_attribute_check": bool` to the return dict -- `True` only when a fresh fetch
  returned nothing AND previously-active claims existed for the type (nothing to signal if there
  was nothing at stake). The type-not-found guard: an empty fetch is never trusted as "every
  attribute removed" (ray's review, 2026-07-19).

- [ ] **Step 1: Write the failing tests**

```python
def test_check_and_refresh_marks_a_vanished_attribute_as_removed(tmp_path, monkeypatch):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    # Seed a previously-active claim for an attribute the "fresh fetch" below won't return --
    # simulating a provider release that dropped it. Can't force a real provider to do this, so
    # this is the one place in this file that stubs the fetch rather than using a live one; the
    # shape-match test below guards against this stub drifting from what the real function
    # actually returns.
    knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="acl", claim_text="acl: deprecated",
        method="structural", source_type="schema", provider="aws", provider_version="6.54.0",
        valid_from="2026-06-01T00:00:00Z", observed_at="2026-06-01T00:00:00Z",
    )
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [dict(_FIXED_CLAIM)])
    summary = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    assert summary["removed_attributes"] == ["acl"]
    # The synthetic claim must actually become resolve()'s current belief, not just exist as a
    # row -- proving this through resolve() itself, not just internal bookkeeping.
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["winner"]["claim_text"] == "acl: removed from live schema"


def test_check_and_refresh_does_not_flag_attributes_still_present(tmp_path, monkeypatch):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [dict(_FIXED_CLAIM)])
    summary = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    assert summary["removed_attributes"] == []


def test_check_and_refresh_skips_removed_attribute_check_when_type_not_found_but_claims_exist(tmp_path, monkeypatch):
    # THE bug ray's review found: schema_claims_for_type() returning [] must NOT be trusted as
    # "every previously-tracked attribute was removed" -- that's reachable by an ordinary typo or
    # the wrong --kind (aws_s3_bucket is a real name under BOTH "resource" and "data"), not just a
    # genuine full-type removal. Confirmed this test discriminates: against the pre-guard code,
    # fresh_attributes is an empty set, "acl" is not in it, and the removed-attribute loop would
    # have fired -- summary["removed_attributes"] would be ["acl"], not [].
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="acl", claim_text="acl: deprecated",
        method="structural", source_type="schema", provider="aws", provider_version="6.54.0",
        valid_from="2026-06-01T00:00:00Z", observed_at="2026-06-01T00:00:00Z",
    )
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [])
    summary = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    assert summary["removed_attributes"] == []  # NOT ["acl"] -- the false positive this guards against
    assert summary["skipped_removed_attribute_check"] is True
    # The pre-existing claim must be left untouched, still the active belief.
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["winner"]["claim_text"] == "acl: deprecated"


def test_check_and_refresh_does_not_report_skipped_when_nothing_was_at_stake(tmp_path, monkeypatch):
    # An empty fetch for a type with NO previously-active claims has nothing to silently get
    # wrong -- skipped_removed_attribute_check must not fire on every unknown/never-tracked type.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [])
    summary = knowledge_degradation.check_and_refresh(conn, "aws", "aws_totally_made_up_type")
    assert summary["skipped_removed_attribute_check"] is False
    assert summary["removed_attributes"] == []


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_schema_claims_for_type_shape_matches_the_degradation_stubs():
    # The removed-attribute stub above (and Task 3's stubs) encode an assumption about
    # schema_claims_for_type()'s real return shape -- can't force a real provider to drop an
    # attribute, so the stub can't be replaced with a live call there. This test guards against
    # that assumption silently drifting from reality: the exact defect class that let the
    # +00:00/Z bug survive (an assumption encoded once, never re-checked against a real fetch).
    import knowledge_diff
    claims = knowledge_diff.schema_claims_for_type("aws", "aws_s3_bucket")
    assert claims
    assert set(claims[0].keys()) == set(_FIXED_CLAIM.keys())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_knowledge_degradation.py -v -k "removed or shape_matches"`
Expected: FAIL -- `removed_attributes` stays `[]` (Task 3's implementation doesn't populate it yet).

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_knowledge_degradation.py -v`
Expected: PASS (9 passed) -- 4 from Task 3 + 5 new above.

- [ ] **Step 5: Empirically verify the type-not-found guard actually catches the false positive**

Temporarily remove the `if not fresh_claims:` guard (restore the unconditional removed-attribute
loop that treats any absent attribute as removed, no matter why `fresh_claims` is empty). Run
`test_check_and_refresh_skips_removed_attribute_check_when_type_not_found_but_claims_exist` alone,
confirm it FAILS (`removed_attributes` will be `["acl"]`, not `[]`). Revert, confirm it passes
again. Paste both results in the report.

- [ ] **Step 6: Commit**

```bash
git add core/generation/knowledge_degradation.py tests/test_knowledge_degradation.py
git commit -m "feat: knowledge-layer spine Step 3 -- removed-attribute synthetic claim + real-shape drift guard

An attribute absent from a fresh fetch but present in the last-active set
gets a synthetic 'removed from live schema' claim instead of leaving a
silently stale required/deprecated claim as resolve()'s answer. New belief-
state semantics -- flagged for ray's review, not built casually.

Also folds in ray's second review round: schema_claims_for_type() returning
[] is never trusted as 'every previously-tracked attribute was removed' --
reachable by an ordinary typo or the wrong --kind, not just a real removal.
Empty fetch skips the removed-attribute pass entirely and reports
skipped_removed_attribute_check in the summary instead of silently
cascading; verified to fail pre-fix. Louder CLI-level warning is Task 5."
```

---

### Task 5: CLI entrypoint + default DB path + real end-to-end proof

**Files:**
- Modify: `core/generation/knowledge_degradation.py`
- Modify: `tests/test_knowledge_degradation.py`

**Interfaces:**
- Consumes: `modules.output_root()` (existing, `core/generation/modules.py:50`).
- Produces: `knowledge_degradation.py`'s `main(argv=None)`, invocable as
  `python core/generation/knowledge_degradation.py check <provider> <resource_type> [--kind resource|data] [--db <path>]`.

- [ ] **Step 1: Write the failing test (real fetch, terraform-gated, no mocks -- same discipline as Step 2's Task 5)**

Add `import os` to `tests/test_knowledge_degradation.py`'s existing imports (not needed by Tasks
3-4's tests, needed by the two CLI tests below).

```python
@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_end_to_end_check_and_refresh_against_real_live_aws_s3_bucket_schema(tmp_path):
    db_path = str(tmp_path / "claims.db")
    conn = knowledge_store.init_db(db_path)
    summary1 = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    assert summary1["inserted"]
    assert summary1["invalidated"] == []  # first check, nothing to supersede

    summary2 = knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")
    assert len(summary2["inserted"]) == len(summary1["inserted"])
    assert sorted(summary2["invalidated"]) == sorted(summary1["inserted"])  # re-check invalidates the first pass

    active = knowledge_store._active_schema_claims_for_resource(conn, "aws_s3_bucket")
    assert len(active) == len(summary1["inserted"])  # exactly one active generation, not two accumulating
    conn.close()


def test_cli_check_writes_to_output_root_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_degradation.modules, "output_root", lambda: str(tmp_path))
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [dict(_FIXED_CLAIM)])
    rc = knowledge_degradation.main(["check", "aws", "aws_s3_bucket"])
    assert rc == 0
    assert os.path.exists(os.path.join(str(tmp_path), "knowledge", "claims.db"))


def test_cli_check_respects_explicit_db_override(tmp_path, monkeypatch):
    db_path = str(tmp_path / "custom" / "claims.db")
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [dict(_FIXED_CLAIM)])
    rc = knowledge_degradation.main(["check", "aws", "aws_s3_bucket", "--db", db_path])
    assert rc == 0
    assert os.path.exists(db_path)


def test_cli_check_warns_and_exits_nonzero_when_removed_attribute_check_is_skipped(tmp_path, monkeypatch, capsys):
    # Ray's review, 2026-07-19: a summary field alone is passive -- nobody reads those. A
    # mistyped resource_type/--kind must not quietly no-op and report success; the CLI path
    # needs its own loud signal, not just Task 4's summary flag.
    db_path = str(tmp_path / "claims.db")
    conn = knowledge_store.init_db(db_path)
    knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute="acl", claim_text="acl: deprecated",
        method="structural", source_type="schema", provider="aws", provider_version="6.54.0",
        valid_from="2026-06-01T00:00:00Z", observed_at="2026-06-01T00:00:00Z",
    )
    conn.close()
    monkeypatch.setattr(knowledge_degradation.knowledge_diff, "schema_claims_for_type",
                         lambda provider, resource_type, observed_at=None, kind="resource": [])
    rc = knowledge_degradation.main(["check", "aws", "aws_s3_bucket", "--db", db_path])
    captured = capsys.readouterr()
    assert rc != 0
    assert "WARNING" in captured.err
    assert "skipped" in captured.err.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_knowledge_degradation.py -v -k "end_to_end or cli_check"`
Expected: FAIL -- no `main()` yet, `modules`/`os` not imported in the test file yet.

- [ ] **Step 3: Write minimal implementation**

```python
# Added to core/generation/knowledge_degradation.py, top of file:
import json
import os
import sys

import modules


def _default_db_path():
    return os.path.join(modules.output_root(), "knowledge", "claims.db")


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
```

Note for the implementer: `test_cli_check_writes_to_output_root_by_default` monkeypatches
`knowledge_degradation.modules.output_root` directly (not `os.environ["MINUSOPS_OUTPUT_DIR"]`) --
either works since `output_root()` checks the env override first, but patching the function itself
is simpler and doesn't leak an env var across tests.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_knowledge_degradation.py -v`
Expected: PASS (13 passed) -- 9 from Tasks 3-4 + 4 new above.

- [ ] **Step 5: Run the full knowledge-layer suite once, confirm nothing regressed**

Run: `pytest tests/test_knowledge_store.py tests/test_knowledge_diff.py tests/test_knowledge_core_boundary.py tests/test_knowledge_degradation.py -v`
Expected: PASS (22 + 4 + 5 + 13 = 44 passed)

- [ ] **Step 6: Commit**

```bash
git add core/generation/knowledge_degradation.py tests/test_knowledge_degradation.py
git commit -m "feat: knowledge-layer spine Step 3 -- CLI entrypoint, output_root()-based default DB path

python core/generation/knowledge_degradation.py check <provider> <resource_type>
[--kind resource|data] [--db <path>]. Default DB path is
modules.output_root()/knowledge/claims.db -- wheel-safe, matching this
project's existing convention, not a bare cwd-relative path. Cadence/
scheduling deliberately out of scope, same precedent as schema_watch.py:
this is a standalone CLI for an external scheduler (cron/CI) to invoke, not
a self-scheduling daemon.

Also folds in ray's second review round: the CLI warns on stderr and exits
non-zero when the removed-attribute check was skipped (Task 4's
skipped_removed_attribute_check) -- a summary field alone is passive and a
mistyped resource_type/--kind must not quietly report success."
```

---

### Step 4 — agent-delegation contract for the semantic path

**Status (2026-07-19): bite-sized below, two design rounds completed with the human (no ray review yet) -- ready to send to ray before build.**

Round 1 (initial proposal, before any code written): proposed `build_delegation_request()` /
`record_delegation_verdict()`, with `source_type="agent_delegated"` falling into `resolve()`'s
existing `non_schema_claims` bucket exactly as the original sketch above (kept for history)
described. While designing this step's own end-to-end test, found a fundamental gap the sketch
never surfaced: `resolve()` has NO mechanism for an `agent_delegated` verdict to ever BECOME the
resolved winner, in either branch -- the schema-vs-non-schema branch is blocked by strict
exact-text `claims_agree` matching (a verdict's text is a paraphrase, not a copy), and the
no-schema-arbiter branch (`"no_ground_truth_arbiter"`) doesn't unblock at all regardless of what
adjudicates the conflict. Stopped and presented this to the human rather than building around it
or silently deciding how to fix it. Also folded in three items from an earlier human review pass
on the naive design: `record_delegation_verdict`'s clock handling (default `observed_at` to now,
require only `valid_from`, validate whatever's supplied through `_parse_ts` and reject
`valid_from > observed_at`), corrected test framing (assert `source_type`/`method` directly, not
via an "ignore" framing), and confirmed no queue-discovery function is needed (agreed, dropped --
`build_delegation_request` on a specific `resource_type`/`attribute` is enough; scanning for
*which* attributes need review is a driving-agent concern, not this module's).

Round 2 (scoped-authority design, "b′"): the human rejected a simpler fix ("the newest
`agent_delegated` claim always wins") on sight -- "that's the freshness clause's exact failure
mode through a new door," traced concretely: a July verdict adjudicating a schema-vs-web conflict
would still outrank a genuinely-changed September schema re-fetch, forever, under that design.
Proposed and refined **scoped authority** instead: a `claim_adjudications` join table records
exactly which claims a verdict adjudicated; `resolve()` grants a delegated verdict authority only
when the claims *currently* active (other than the verdict itself) are a subset-or-equal of what
it adjudicated -- coverage is checked fresh against the CURRENT active set on every call, never
cached, never permanent. Three follow-up decisions locked in this round:
1. The vacuous-subset case (verdict is the only active claim, so the empty set is trivially a
   subset of anything) is claimed structurally **unreachable** -- `resolve()`'s own
   `len(claims) <= 1` early return fires first and unconditionally. Asserted via its own dedicated
   test (Task 3) rather than left as an inference, so a future change that reorders or removes that
   early return fails this test loudly instead of silently opening the path.
2. `adjudicated_ids` is validated at `record_delegation_verdict`'s boundary with the same rigor as
   the clocks (Task 2): required, non-empty, and every id must reference a claim that is
   currently active for the EXACT `resource_type`/`attribute` being adjudicated -- a stale or
   fabricated id would make the coverage test in `resolve()` meaningless, silently.
3. Verdict-invalidation-on-new-verdict (explicitly considered: should recording a new verdict
   invalidate a still-active prior one?) was rejected -- approved as-is. A later verdict naturally
   covers a still-active prior verdict's claims via `build_delegation_request` reflecting current
   state; no separate invalidation mechanism is needed.

While designing Task 3's tie-breaking test (found proactively, before any code existed, not
discovered as a defect afterward -- see the resolve-comparison-bugs-pattern memory, now an
8th-instance candidate): a plain `max()` by `observed_at` can silently return an ARBITRARY one of
several tied-newest claims. If a delegated verdict is ever tied for newest-observed with ONE OF
ITS OWN adjudicated claims, an unspecified tie winner changes whether authority gets granted for
the identical underlying state. Fixed by requiring the newest claim be UNIQUE (`len(newest_claims)
== 1`), not just any `max()` result.

**Consumes**: `knowledge_store.resolve`, `knowledge_store._active_claims`, `knowledge_store.
_parse_ts`, `knowledge_store.insert_claim`, `knowledge_store.invalidate_claim` (all existing,
reused not reimplemented). No boundary-guard (`_ALLOWED`) change needed anywhere in this step:
the new `knowledge_delegation.py` imports only `datetime` (stdlib) and `knowledge_store` (already
in `_ALLOWED` since Step 3), and `tests/test_knowledge_core_boundary.py`'s `_FILES` is computed
via `glob(knowledge_*.py)`, so the new file is picked up automatically with zero edits to the
guard itself.

**Red-Team focus for this step (for ray) -- point at these four specifically, not a general pass:**

1. **The tie-breaking fix** (Task 3): `len(newest_claims) == 1` guards against granting authority
   off an ambiguous tie. Does the dedicated tie-breaking test (constructed by tying the verdict's
   `observed_at` against ONE OF ITS OWN adjudicated claims, not an unrelated new claim -- the
   latter construction was tried first and found to be theater, since it breaks the subset test
   regardless of which side wins the tie) actually discriminate a unique-max requirement from a
   plain `max()`? Is there a tie construction this doesn't cover?
2. **The proper-subset decision** (Task 3): `other_active_ids <= adjudicated_ids` uses
   subset-or-equal, not exact-match, deliberately -- coverage means nothing NEW has appeared, not
   that the active set is frozen at exactly what was adjudicated. Does this hold up, or does
   subset-or-equal open a gap exact-match would have closed?
3. **The structurally-unreachable-empty-set claim** (Task 3): the vacuous-subset case is claimed
   unreachable because `resolve()`'s `len(claims) <= 1` early return fires first. Verify it's
   ACTUALLY unreachable, not just apparently -- is there any path (future or present) that could
   reach the authority-check code with fewer than 2 active claims, bypassing that early return?
4. **The `adjudicated_ids` boundary validation** (Task 2), as the second external-input surface
   after the clocks: required, non-empty, every id must reference a currently-active claim for the
   exact `resource_type`/`attribute`. Same review standard as the clock validation -- does this
   close the boundary, or is there a gap (e.g. an id active for the right attribute but wrong
   `resource_type`, or a race between validation and insert)?

---

### Task 1: `knowledge_delegation.py` (new file) — `build_delegation_request()`

**Files:**
- Create: `core/generation/knowledge_delegation.py`
- Test: `tests/test_knowledge_delegation.py`

**Interfaces:**
- Consumes: `knowledge_store.resolve`, `knowledge_store._parse_ts` (both existing, reused not
  reimplemented).
- Produces: `build_delegation_request(conn, resource_type, attribute) -> dict | None` -- `None`
  when `resolve()` doesn't return `needs_review`; otherwise a dict with keys `resource_type`,
  `attribute`, `reason`, `claims` (list of provenance dicts, newest-`observed_at`-first).

- [ ] **Step 1: Write the failing tests**

```python
"""
tests for knowledge_delegation.py -- Step 4 of the knowledge-layer spine, the agent-delegation
contract for the semantic path.
"""
import knowledge_delegation
import knowledge_store


def _insert(conn, source_type, claim_text, observed_at, attribute="acl"):
    return knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute=attribute, claim_text=claim_text,
        method="structural" if source_type == "schema" else "semantic", source_type=source_type,
        provider="aws", valid_from=observed_at, observed_at=observed_at,
    )


def test_build_delegation_request_returns_none_when_single_claim_resolved(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    assert knowledge_delegation.build_delegation_request(conn, "aws_s3_bucket", "acl") is None


def test_build_delegation_request_returns_none_when_schema_wins_via_freshness(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "web", "acl is fine", "2026-07-01T00:00:00Z")
    _insert(conn, "schema", "acl is deprecated", "2026-07-05T00:00:00Z")
    assert knowledge_delegation.build_delegation_request(conn, "aws_s3_bucket", "acl") is None


def test_build_delegation_request_returns_well_formed_dict_when_needs_review(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    web_id = _insert(conn, "web", "acl is fine", "2026-07-05T00:00:00Z")
    req = knowledge_delegation.build_delegation_request(conn, "aws_s3_bucket", "acl")
    assert req["resource_type"] == "aws_s3_bucket"
    assert req["attribute"] == "acl"
    assert req["reason"] == "non_schema_claim_observed_more_recently_than_schema_fetch"
    ids = {c["id"] for c in req["claims"]}
    assert ids == {schema_id, web_id}
    for c in req["claims"]:
        assert set(c) == {"id", "claim_text", "source_type", "source_url", "provider",
                           "provider_version", "observed_at", "valid_from"}


def test_build_delegation_request_orders_claims_newest_first(tmp_path):
    # Insertion order deliberately scrambled against timestamp order (standing convention, Global
    # Constraints) -- the OLDER claim is inserted second so a bug that just returns resolve()'s
    # claims list in whatever order it came back in cannot pass by coincidence.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    newer_id = _insert(conn, "web", "acl is fine", "2026-07-10T00:00:00Z")
    older_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    req = knowledge_delegation.build_delegation_request(conn, "aws_s3_bucket", "acl")
    assert [c["id"] for c in req["claims"]] == [newer_id, older_id]


def test_build_delegation_request_claims_list_is_never_empty(tmp_path):
    # Asserted directly, not inferred from resolve()'s len(claims) <= 1 early return living in a
    # different file -- same "assert don't infer" discipline as Task 3's empty-set test.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    _insert(conn, "web", "acl is fine", "2026-07-05T00:00:00Z")
    req = knowledge_delegation.build_delegation_request(conn, "aws_s3_bucket", "acl")
    assert len(req["claims"]) >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_knowledge_delegation.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'knowledge_delegation'` (the file doesn't
exist yet).

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_knowledge_delegation.py tests/test_knowledge_core_boundary.py -v`
Expected: PASS (5 + 5 = 10 passed) -- the new file is picked up by the boundary guard's glob
automatically; confirms no `_ALLOWED` change was needed.

- [ ] **Step 5: Empirically verify the newest-first ordering actually matters**

Temporarily change `reverse=True` to `reverse=False` (or drop the `key=` entirely) in the
`sorted(...)` call. Run
`pytest tests/test_knowledge_delegation.py -v -k orders_claims_newest_first` alone, confirm it
FAILS (`assert [older_id, newer_id] == [newer_id, older_id]`, order reversed). Revert, confirm it
passes again. Paste both results in the report.

- [ ] **Step 6: Commit**

```bash
git add core/generation/knowledge_delegation.py tests/test_knowledge_delegation.py
git commit -m "feat: knowledge-layer spine Step 4 -- build_delegation_request() packages needs_review for the driving agent

New core/generation/knowledge_delegation.py, the agent-delegation
contract's first half. Packages a needs_review resolve() result into a
newest-first ordered hand-off (provenance, no raw DB rows) for the driving
agent to adjudicate. No local model anywhere in this path. Imports only
knowledge_store, already in the boundary guard's _ALLOWED set -- no
boundary-guard change needed, and the new file is picked up by
test_knowledge_core_boundary.py automatically via its existing glob."
```

---

### Task 2: `knowledge_store.py` (`claim_adjudications` table) + `knowledge_delegation.py` (`record_delegation_verdict()`)

**Files:**
- Modify: `core/generation/knowledge_store.py` (add `claim_adjudications` table to `_SCHEMA`)
- Modify: `core/generation/knowledge_delegation.py`
- Modify: `tests/test_knowledge_delegation.py`

**Interfaces:**
- Consumes: `knowledge_store._parse_ts`, `knowledge_store.insert_claim` (both existing).
- Produces: `record_delegation_verdict(conn, resource_type, attribute, *, claim_text, valid_from,
  provider, adjudicated_ids, observed_at=None, source_url=None, confidence=None,
  provider_version=None) -> int` (the new verdict claim's id); the `claim_adjudications` table,
  read by Task 3's `_adjudicated_ids()`.

- [ ] **Step 1: Write the failing tests**

Update the top of `tests/test_knowledge_delegation.py` to add the two new imports:

```python
import datetime

import pytest

import knowledge_delegation
import knowledge_store
```

Append:

```python
def _count_claims(conn):
    return conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]


def test_record_delegation_verdict_inserts_claim_with_correct_fields(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    web_id = _insert(conn, "web", "acl is fine", "2026-07-05T00:00:00Z")
    verdict_id = knowledge_delegation.record_delegation_verdict(
        conn, "aws_s3_bucket", "acl", claim_text="acl is deprecated, per agent review",
        valid_from="2026-07-10T00:00:00Z", observed_at="2026-07-10T00:00:00Z",
        provider="aws", adjudicated_ids=[schema_id, web_id], confidence=0.9,
    )
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (verdict_id,)).fetchone()
    assert row["claim_text"] == "acl is deprecated, per agent review"
    assert row["provider"] == "aws"
    assert row["confidence"] == 0.9
    assert row["valid_from"] == "2026-07-10T00:00:00Z"


def test_record_delegation_verdict_always_sets_agent_delegated_and_semantic(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    verdict_id = knowledge_delegation.record_delegation_verdict(
        conn, "aws_s3_bucket", "acl", claim_text="anything",
        valid_from="2026-07-10T00:00:00Z", provider="aws", adjudicated_ids=[schema_id],
    )
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (verdict_id,)).fetchone()
    assert row["source_type"] == "agent_delegated"
    assert row["method"] == "semantic"


def test_record_delegation_verdict_defaults_observed_at_to_now(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    before = datetime.datetime.now(datetime.timezone.utc)
    verdict_id = knowledge_delegation.record_delegation_verdict(
        conn, "aws_s3_bucket", "acl", claim_text="anything",
        valid_from="2026-07-01T00:00:00Z", provider="aws", adjudicated_ids=[schema_id],
    )
    after = datetime.datetime.now(datetime.timezone.utc)
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (verdict_id,)).fetchone()
    observed = knowledge_store._parse_ts(row["observed_at"])
    assert before <= observed <= after


def test_record_delegation_verdict_accepts_explicit_observed_at(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    verdict_id = knowledge_delegation.record_delegation_verdict(
        conn, "aws_s3_bucket", "acl", claim_text="anything",
        valid_from="2026-07-01T00:00:00Z", observed_at="2026-07-15T00:00:00Z",
        provider="aws", adjudicated_ids=[schema_id],
    )
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (verdict_id,)).fetchone()
    assert row["observed_at"] == "2026-07-15T00:00:00Z"


def test_record_delegation_verdict_rejects_unparseable_valid_from(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="not-a-timestamp", provider="aws", adjudicated_ids=[schema_id],
        )
    assert _count_claims(conn) == before


def test_record_delegation_verdict_rejects_unparseable_observed_at(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="2026-07-01T00:00:00Z", observed_at="not-a-timestamp",
            provider="aws", adjudicated_ids=[schema_id],
        )
    assert _count_claims(conn) == before


def test_record_delegation_verdict_rejects_valid_from_after_observed_at(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="2026-07-20T00:00:00Z", observed_at="2026-07-10T00:00:00Z",
            provider="aws", adjudicated_ids=[schema_id],
        )
    assert _count_claims(conn) == before


def test_record_delegation_verdict_rejects_empty_adjudicated_ids(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="2026-07-01T00:00:00Z", provider="aws", adjudicated_ids=[],
        )
    assert _count_claims(conn) == before


def test_record_delegation_verdict_rejects_nonexistent_adjudicated_id(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="2026-07-01T00:00:00Z", provider="aws", adjudicated_ids=[999999],
        )
    assert _count_claims(conn) == before


def test_record_delegation_verdict_rejects_inactive_adjudicated_id(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    knowledge_store.invalidate_claim(conn, schema_id, valid_until="2026-07-05T00:00:00Z")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="2026-07-10T00:00:00Z", provider="aws", adjudicated_ids=[schema_id],
        )
    assert _count_claims(conn) == before


def test_record_delegation_verdict_rejects_adjudicated_id_from_different_attribute(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    other_attr_id = _insert(conn, "schema", "bucket is optional", "2026-07-01T00:00:00Z",
                             attribute="bucket")
    before = _count_claims(conn)
    with pytest.raises(ValueError):
        knowledge_delegation.record_delegation_verdict(
            conn, "aws_s3_bucket", "acl", claim_text="anything",
            valid_from="2026-07-10T00:00:00Z", provider="aws", adjudicated_ids=[other_attr_id],
        )
    assert _count_claims(conn) == before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_knowledge_delegation.py -v -k record_delegation_verdict`
Expected: FAIL -- `AttributeError: module 'knowledge_delegation' has no attribute
'record_delegation_verdict'`.

- [ ] **Step 3: Write minimal implementation**

In `core/generation/knowledge_store.py`, extend `_SCHEMA`:

```python
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
```

In `core/generation/knowledge_delegation.py`, add `import datetime` at the top and append:

```python
def record_delegation_verdict(conn, resource_type, attribute, *, claim_text, valid_from,
                                provider, adjudicated_ids, observed_at=None, source_url=None,
                                confidence=None, provider_version=None):
    """Records the driving agent's adjudication as a new claim -- an INSERT, never an UPDATE.
    source_type and method are fixed by this function (not caller-supplied): "agent_delegated"
    and "semantic".

    This is the external boundary of the two-clock model -- an arbitrary driving agent supplies
    valid_from/observed_at. Both are validated: each must parse via knowledge_store._parse_ts
    (rejects garbage/wrong-type input), and valid_from must not be AFTER observed_at (a fact
    can't be observed before it became true). observed_at defaults to now; valid_from has no
    default (same no-default precedent as invalidate_claim's valid_until).

    adjudicated_ids is the second external-input boundary surface: required (no default), must
    be non-empty, and every id must reference a currently-active claim for this exact
    resource_type/attribute -- a stale or fabricated id would make the coverage test in
    resolve() (Task 3) meaningless, silently. All validation runs BEFORE any write: a rejection
    leaves no partial claim row behind."""
    observed_at = observed_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        parsed_valid_from = knowledge_store._parse_ts(valid_from)
        parsed_observed_at = knowledge_store._parse_ts(observed_at)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(
            f"record_delegation_verdict: valid_from/observed_at must be parseable ISO "
            f"timestamps -- got valid_from={valid_from!r}, observed_at={observed_at!r}"
        ) from exc
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
        valid_from=valid_from, observed_at=observed_at,
    )
    conn.executemany(
        "INSERT INTO claim_adjudications (verdict_claim_id, adjudicated_claim_id) VALUES (?, ?)",
        [(verdict_id, aid) for aid in adjudicated_ids],
    )
    conn.commit()
    return verdict_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_knowledge_delegation.py -v`
Expected: PASS (16 passed) -- 5 from Task 1 + 11 new above.

- [ ] **Step 5: Empirically verify every rejection actually rejects**

Temporarily comment out the entire validation block in `record_delegation_verdict` -- everything
between the docstring and `verdict_id = knowledge_store.insert_claim(...)` (the timestamp parse,
the `valid_from > observed_at` check, the empty-`adjudicated_ids` check, and the
found/missing-id check). Run `pytest tests/test_knowledge_delegation.py -v -k rejects` (7 tests),
confirm all 7 FAIL (each with `Failed: DID NOT RAISE <class 'ValueError'>`). Restore the
validation block. Re-run, confirm all 7 PASS. Paste both results in the report.

- [ ] **Step 6: Commit**

```bash
git add core/generation/knowledge_store.py core/generation/knowledge_delegation.py tests/test_knowledge_delegation.py
git commit -m "feat: knowledge-layer spine Step 4 -- record_delegation_verdict() + claim_adjudications table

record_delegation_verdict() is the second external-input boundary in this
codebase (after the clocks): valid_from/observed_at are parsed and
validated (valid_from must not be after observed_at), and adjudicated_ids
is validated non-empty with every id required to reference a currently-
active claim for the exact resource_type/attribute -- all validation runs
before any write, so a rejection leaves no partial claim row behind. Always
an INSERT, never an UPDATE; source_type='agent_delegated' and
method='semantic' are fixed by this function, not caller-supplied.

claim_adjudications is a join table (verdict_claim_id, adjudicated_claim_id),
tracking exactly what a verdict adjudicated for Task 3's scoped-authority
check in resolve()."
```

---

### Task 3: `knowledge_store.py` — `resolve()`'s scoped-authority extension

**Files:**
- Modify: `core/generation/knowledge_store.py` (`resolve()`, plus new `_adjudicated_ids()` helper)
- Test: `tests/test_knowledge_store.py`

**Interfaces:**
- Consumes: `claim_adjudications` (Task 2), `_active_claims`, `_parse_ts` (both existing,
  unchanged).
- Produces: `_adjudicated_ids(conn, verdict_claim_id) -> set[int]`; `resolve()`'s new
  `"delegated_verdict_covers_active_claims"` reason string, returned before the existing
  schema/non-schema comparison, which is otherwise completely unchanged below it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_knowledge_store.py` (reuses the file's existing `_insert` helper):

```python
def _record_verdict(conn, adjudicated_ids, claim_text="agent verdict",
                     observed_at="2026-07-20T00:00:00Z", attribute="acl"):
    verdict_id = knowledge_store.insert_claim(
        conn, resource_type="aws_s3_bucket", attribute=attribute, claim_text=claim_text,
        method="semantic", source_type="agent_delegated", provider="aws",
        valid_from=observed_at, observed_at=observed_at,
    )
    conn.executemany(
        "INSERT INTO claim_adjudications (verdict_claim_id, adjudicated_claim_id) VALUES (?, ?)",
        [(verdict_id, aid) for aid in adjudicated_ids],
    )
    conn.commit()
    return verdict_id


def test_resolve_delegated_verdict_wins_when_it_covers_all_active_claims(tmp_path):
    # Insertion order here necessarily matches timestamp order -- a verdict's adjudicated_ids
    # must reference claims that already exist, so it structurally cannot be inserted before
    # schema_id/web_id. This does NOT violate the standing "scramble insertion order" testing
    # convention in spirit: that convention exists to stop a test passing by accident under a
    # LAST/FIRST-inserted-wins bug, and the dedicated tie-breaking test below (which uses
    # monkeypatch to force BOTH row orderings explicitly, independent of insertion order)
    # supplies that guarantee for this exact selection logic instead.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    web_id = _insert(conn, "web", "acl is fine", "2026-07-05T00:00:00Z")
    verdict_id = _record_verdict(
        conn, [schema_id, web_id], claim_text="acl is deprecated, per agent review",
        observed_at="2026-07-10T00:00:00Z")
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["winner"]["id"] == verdict_id
    assert result["reason"] == "delegated_verdict_covers_active_claims"


def test_resolve_delegated_verdict_does_not_win_when_new_claim_appeared(tmp_path):
    # The staleness scenario that sank the naive "newest agent_delegated claim always wins"
    # design: a fresh schema re-fetch the verdict never adjudicated must NOT be silently
    # outranked by a stale July verdict.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    web_id = _insert(conn, "web", "acl is fine", "2026-07-05T00:00:00Z")
    _record_verdict(
        conn, [schema_id, web_id], claim_text="acl is deprecated, per agent review",
        observed_at="2026-07-10T00:00:00Z")
    # September: a fresh schema re-fetch supersedes the July schema claim -- the verdict no
    # longer covers the active set, since the new schema claim was never adjudicated.
    new_schema_id = _insert(conn, "schema", "acl is required now", "2026-09-01T00:00:00Z")
    knowledge_store.invalidate_claim(
        conn, schema_id, valid_until="2026-09-01T00:00:00Z", invalidated_by=new_schema_id)
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["reason"] != "delegated_verdict_covers_active_claims"
    assert result["status"] == "resolved"
    assert result["winner"]["id"] == new_schema_id
    assert result["reason"] == "schema_observed_same_or_more_recently_than_non_schema_claim"


def test_resolve_delegated_verdict_wins_with_proper_subset_of_adjudicated_ids(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    web_id = _insert(conn, "web", "acl is fine", "2026-07-05T00:00:00Z")
    verdict_id = _record_verdict(
        conn, [schema_id, web_id], claim_text="acl is deprecated, per agent review",
        observed_at="2026-07-10T00:00:00Z")
    # One of the originally-adjudicated claims is later invalidated by something other than the
    # verdict itself (not reachable via any real pathway today -- constructed directly to prove
    # the subset test is PROPER-subset-or-equal by design, not exact-match).
    knowledge_store.invalidate_claim(conn, web_id, valid_until="2026-07-11T00:00:00Z")
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["winner"]["id"] == verdict_id
    assert result["reason"] == "delegated_verdict_covers_active_claims"


def test_resolve_delegated_verdict_tie_breaking_does_not_grant_authority_on_ambiguous_newest(
        tmp_path, monkeypatch):
    # The tie-breaking fix: a verdict tied for newest-observed_at with ONE OF ITS OWN adjudicated
    # claims must NOT be treated as an unambiguous newest claim. This is the only construction
    # that actually discriminates a unique-max requirement from a plain max(): tying the verdict
    # against a claim it did NOT adjudicate is theater (a new, unadjudicated claim breaks the
    # subset test regardless of which side wins the tie, so naive and fixed code produce the same
    # final outcome). Tying it against one of its OWN adjudicated claims is different: if the
    # verdict wins the tie, other_active_ids becomes exactly {schema_id}, a PROPER SUBSET of
    # {schema_id, web_id} -- authority is (wrongly) granted. If web_id wins the tie instead, the
    # authority check never even fires. Only a unique-max requirement closes this off regardless
    # of which row happens to be treated as "the" tied newest.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    web_id = _insert(conn, "web", "acl is fine", "2026-07-05T00:00:00Z")
    # Verdict's observed_at is the EXACT SAME literal string as web_id's -- a deliberate tie.
    verdict_id = _record_verdict(
        conn, [schema_id, web_id], claim_text="acl is deprecated, per agent review",
        observed_at="2026-07-05T00:00:00Z")
    rows = {c["id"]: c for c in knowledge_store._active_claims(conn, "aws_s3_bucket", "acl")}
    schema_row, web_row, verdict_row = rows[schema_id], rows[web_id], rows[verdict_id]

    for ordering_name, ordering in [
        ("verdict_before_tied_claim", [schema_row, verdict_row, web_row]),
        ("tied_claim_before_verdict", [schema_row, web_row, verdict_row]),
    ]:
        monkeypatch.setattr(knowledge_store, "_active_claims",
                             lambda *a, _o=ordering, **k: list(_o))
        result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
        assert result["reason"] != "delegated_verdict_covers_active_claims", (
            f"authority wrongly granted under ordering {ordering_name}")


def test_resolve_when_verdict_is_the_only_active_claim_uses_single_or_no_claim_not_authority_check(
        tmp_path):
    # The vacuous-subset case (other_active_ids == set() <= adjudicated_ids is trivially True) is
    # claimed structurally UNREACHABLE: resolve()'s own len(claims) <= 1 early return fires first
    # whenever the verdict is the only active claim, before the authority-check code ever runs.
    # Constructed directly (not reachable via any real pathway today -- invalidating BOTH
    # originally-adjudicated claims manually) specifically to prove this, not leave it as an
    # inference: if the early-return above ever moves or changes, this test fails LOUDLY by
    # landing in the authority-check branch instead of silently opening the vacuous-subset path.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    web_id = _insert(conn, "web", "acl is fine", "2026-07-05T00:00:00Z")
    verdict_id = _record_verdict(
        conn, [schema_id, web_id], claim_text="acl is deprecated, per agent review",
        observed_at="2026-07-10T00:00:00Z")
    knowledge_store.invalidate_claim(conn, schema_id, valid_until="2026-07-11T00:00:00Z")
    knowledge_store.invalidate_claim(conn, web_id, valid_until="2026-07-11T00:00:00Z")
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["reason"] == "single_or_no_claim"
    assert result["winner"]["id"] == verdict_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_knowledge_store.py -v -k delegated_verdict`
Expected: FAIL -- `AttributeError: module 'knowledge_store' has no attribute '_adjudicated_ids'`
for tests that reach it; the others fail with `AssertionError` (falling through to ordinary
schema/non-schema comparison, e.g. `reason == "needs_review"`/`"no_ground_truth_arbiter"` instead
of `"delegated_verdict_covers_active_claims"`).

- [ ] **Step 3: Write minimal implementation**

Add `_adjudicated_ids`, right before `resolve()`:

```python
def _adjudicated_ids(conn, verdict_claim_id):
    rows = conn.execute(
        "SELECT adjudicated_claim_id FROM claim_adjudications WHERE verdict_claim_id = ?",
        (verdict_claim_id,),
    ).fetchall()
    return {row["adjudicated_claim_id"] for row in rows}
```

Replace `resolve()` in full (the new early-return sits between the existing `len(claims) <= 1`
check and the existing schema/non-schema comparison, which is unchanged below it):

```python
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
    claim_adjudications rows, read through _adjudicated_ids()). Proper-subset, not exact-match,
    deliberately: coverage means nothing NEW has appeared since the verdict, not that the active
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_knowledge_store.py -v`
Expected: PASS (27 passed) -- 22 existing + 5 new above.

- [ ] **Step 5: Empirically verify the tie-breaking fix actually matters**

Temporarily replace the unique-max guard:
```python
    if len(newest_claims) == 1 and newest_claims[0]["source_type"] == "agent_delegated":
```
with a naive single `max()`:
```python
    newest = max(claims, key=lambda c: _parse_ts(c["observed_at"]))
    if newest["source_type"] == "agent_delegated":
        verdict = newest
```
(adjusting the two lines below accordingly). Run
`pytest tests/test_knowledge_store.py -v -k tie_breaking` alone. Confirm it FAILS specifically
under the `verdict_before_tied_claim` ordering (`AssertionError: authority wrongly granted under
ordering verdict_before_tied_claim`) while the reasoning above predicts `tied_claim_before_verdict`
still passes (Python's `max()` returns the first-encountered maximal element on a tie, and
`web_row` -- not `agent_delegated` -- comes first in that ordering, so the naive code never even
enters the branch). Revert, confirm both orderings pass again. Paste both results in the report.

- [ ] **Step 6: Empirically verify the empty-set case is genuinely guarded by the early return**

Temporarily move the `len(claims) <= 1` early-return block to AFTER the new authority-check block
(so the authority check runs first even when there's only one active claim). Run
`pytest tests/test_knowledge_store.py -v -k verdict_is_the_only_active_claim` alone. Confirm it
FAILS -- with only the verdict active, `other_active_ids` is the empty set, trivially a subset of
anything, so the reordered code now returns `reason == "delegated_verdict_covers_active_claims"`
instead of the expected `"single_or_no_claim"`. This proves the test would catch a regression if
the early return ever moved. Revert, confirm it passes again. Paste both results in the report.

- [ ] **Step 7: Commit**

```bash
git add core/generation/knowledge_store.py tests/test_knowledge_store.py
git commit -m "feat: knowledge-layer spine Step 4 -- scoped-authority extension to resolve()

A delegated verdict becomes resolve()'s winner only when it is the UNIQUE
newest-observed active claim (a plain max() by observed_at is not enough --
see the tie-breaking test) AND the currently-active claims other than the
verdict itself are a subset-or-equal of what it adjudicated
(claim_adjudications, via the new _adjudicated_ids() helper). Proper-subset,
not exact-match, deliberately: coverage means nothing NEW has appeared, not
that the active set is frozen at exactly what was adjudicated.

Rejected during design: 'newest agent_delegated claim always wins' -- that
would let a stale verdict outrank fresh evidence forever, the freshness
clause's own failure mode through a new door. This scoped-authority design
was chosen instead, and is traced end-to-end against that exact staleness
scenario in a dedicated test.

The vacuous-subset case (verdict is the only active claim) is claimed
structurally unreachable -- guarded by resolve()'s own len(claims) <= 1
early return, which fires first -- and is asserted by its own dedicated
test, not left as an inference. Flagged for ray's review: verify this is
ACTUALLY unreachable, not just apparently."
```

---

### Task 4: end-to-end delegation loop + staleness + `claims_agree` safety net

**Files:**
- Modify: `tests/test_knowledge_delegation.py`

**Interfaces:**
- Consumes: everything from Tasks 1-3, plus `knowledge_degradation.check_and_refresh` (Step 3,
  unmodified) for the staleness test.
- Produces: nothing new -- integration coverage only, proving the pieces from Tasks 1-3 (and
  Step 3's already-shipped `check_and_refresh`) work together, not just in isolation.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_knowledge_delegation.py`:

```python
def test_full_delegation_loop_verdict_becomes_resolve_winner(tmp_path):
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    _insert(conn, "web", "acl is fine", "2026-07-05T00:00:00Z")
    request = knowledge_delegation.build_delegation_request(conn, "aws_s3_bucket", "acl")
    assert request is not None
    verdict_id = knowledge_delegation.record_delegation_verdict(
        conn, "aws_s3_bucket", "acl", claim_text="acl is deprecated, confirmed by agent review",
        valid_from="2026-07-10T00:00:00Z", provider="aws",
        adjudicated_ids=[c["id"] for c in request["claims"]],
    )
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["status"] == "resolved"
    assert result["winner"]["id"] == verdict_id
    assert result["reason"] == "delegated_verdict_covers_active_claims"
    assert knowledge_delegation.build_delegation_request(conn, "aws_s3_bucket", "acl") is None


def test_delegation_verdict_falls_back_to_ordinary_logic_when_new_evidence_appears(
        tmp_path, monkeypatch):
    import knowledge_degradation
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    _insert(conn, "web", "acl is fine", "2026-07-05T00:00:00Z")
    request = knowledge_delegation.build_delegation_request(conn, "aws_s3_bucket", "acl")
    knowledge_delegation.record_delegation_verdict(
        conn, "aws_s3_bucket", "acl", claim_text="acl is deprecated, confirmed by agent review",
        valid_from="2026-07-10T00:00:00Z", provider="aws",
        adjudicated_ids=[c["id"] for c in request["claims"]],
    )
    assert knowledge_store.resolve(conn, "aws_s3_bucket", "acl")["reason"] == \
        "delegated_verdict_covers_active_claims"

    fresh_claim = {
        "resource_type": "aws_s3_bucket", "attribute": "acl",
        "claim_text": "acl is required now", "method": "structural", "source_type": "schema",
        "provider": "aws", "provider_version": "6.60.0",
        "valid_from": "2026-09-01T00:00:00Z", "observed_at": "2026-09-01T00:00:00Z",
    }
    monkeypatch.setattr(
        knowledge_degradation.knowledge_diff, "schema_claims_for_type",
        lambda provider, resource_type, observed_at=None, kind="resource": [dict(fresh_claim)])
    knowledge_degradation.check_and_refresh(conn, "aws", "aws_s3_bucket")

    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["reason"] != "delegated_verdict_covers_active_claims"
    assert result["winner"]["claim_text"] == "acl is required now"


def test_redundant_delegated_verdict_not_covering_everything_still_absorbed_by_claims_agree(
        tmp_path):
    # A verdict that does NOT fully cover the active set (adjudicated_ids=[schema_id] only,
    # while web_id remains active and uncovered -- the new b' authority mechanism does not apply
    # here) but whose claim_text happens to exactly match the schema's claim_text is still
    # absorbed gracefully by the PRE-EXISTING (Step 2, completely unmodified) claims_agree
    # short-circuit. Proves "materiality is agent-side" has a real safety net independent of the
    # new scoped-authority mechanism: an agent that redundantly re-confirms an already-correct
    # schema claim doesn't create a spurious conflict just because its verdict didn't cover
    # every active claim.
    conn = knowledge_store.init_db(str(tmp_path / "claims.db"))
    schema_id = _insert(conn, "schema", "acl is deprecated", "2026-07-01T00:00:00Z")
    _insert(conn, "web", "acl is fine", "2026-07-05T00:00:00Z")
    knowledge_delegation.record_delegation_verdict(
        conn, "aws_s3_bucket", "acl", claim_text="acl is deprecated",  # matches schema's text
        valid_from="2026-07-10T00:00:00Z", provider="aws", adjudicated_ids=[schema_id],
    )
    result = knowledge_store.resolve(conn, "aws_s3_bucket", "acl")
    assert result["reason"] == "claims_agree"
    assert result["winner"]["id"] == schema_id
```

- [ ] **Step 2: Run tests to verify they pass**

By this point Tasks 1-3 are already committed, so no new production code exists for this task --
this is integration coverage only, proving the already-built pieces work together. If any of
these three fail, that is a genuine integration bug Tasks 1-3's isolated tests didn't catch --
diagnose and fix the production code (in `knowledge_delegation.py` or `knowledge_store.py`,
whichever the failure implicates) before proceeding; do not weaken these tests to make them pass.

Run: `pytest tests/test_knowledge_delegation.py tests/test_knowledge_store.py tests/test_knowledge_diff.py tests/test_knowledge_core_boundary.py tests/test_knowledge_degradation.py -v`
Expected: PASS (19 + 27 + 4 + 5 + 13 = 68 passed).

- [ ] **Step 3: Commit**

```bash
git add tests/test_knowledge_delegation.py
git commit -m "test: knowledge-layer spine Step 4 -- end-to-end delegation loop + staleness + claims_agree safety net

Three integration tests closing the loop across knowledge_delegation.py and
knowledge_store.py together: (1) needs_review -> build_delegation_request ->
record_delegation_verdict -> resolve() returns the verdict as winner via the
new authority path; (2) the same loop, then fresh evidence appears and
resolve() correctly falls back to ordinary logic instead of trusting the
now-stale verdict; (3) a verdict that does NOT fully cover the active set
but whose claim_text happens to match the schema claim's text is still
absorbed gracefully by the PRE-EXISTING (Step 2, unmodified) claims_agree
short-circuit -- proving 'materiality is agent-side' has a real safety net
independent of the new scoped-authority mechanism."
```

---

### Step 5 — breadth×depth budget knob (request-shaping contract, not a loop MinusOps runs)

- **New**: a plain parameter object (e.g. `ResearchBudget(breadth: int, depth: int)`) that MinusOps hands to the driving agent alongside a delegation request or an authoring-context request (`assemble_authoring_context()`, `core/generation/synthesizer.py:139`) -- purely descriptive, MinusOps never iterates or enforces it itself.
- **Open question for ray**: does this belong in `knowledge_store.py`/`knowledge_diff.py` at all, or is it purely a `synthesizer.py`-side concern that only *reads* the knowledge layer's `needs_review` queue depth to decide how much budget to hand out? Leaning toward the latter -- keeps the knowledge-layer core from acquiring an opinion about how the agent should spend its own research effort.

---

## Self-Review

**Spec coverage**: Step 2's four settled pieces (a: placement/reuse, b: schema, c: boundary, d: resource) each map to a task above (Tasks 1-2 = schema/store, Task 3 = boundary, Task 4 = diff engine reusing `get_type_schema`, all five tasks together = `aws_s3_bucket` end to end). Steps 3-5 are explicitly out of scope for this pass, sketched only for red-team input on shape.

**Placeholder scan**: no TBD/"add appropriate handling" in any Task 1-5 step; Steps 3-5's "open question for ray" markers are deliberate discussion points for the review this plan is headed to, not implementation gaps in tasks meant to be built now.

**Type consistency**: `insert_claim`'s keyword signature (Task 1) matches exactly what `schema_claims_for_type` (Task 4) produces per-dict and what the end-to-end test (Task 5) calls with `**c`; `resolve()`'s return shape (`status`/`winner`/`claims`/`reason`) is identical across Task 2's unit tests and Task 5's end-to-end tests. Post-red-team: the renamed `reason` string (`non_schema_claim_observed_more_recently_than_schema_fetch`, replacing `web_claim_observed_more_recently_than_schema_fetch`) was checked and updated in both places it appears -- Task 2's own test and Task 5's end-to-end test -- so the two don't drift.

### Self-Review addendum: Step 3 (2026-07-19)

**Spec coverage**: every decision locked in Step 3's "Status" block maps to a task -- always-insert (Task 3), `output_root()` default path (Task 5), `_ALLOWED` extension (Task 2), removed-attribute handling (Task 4), the single read-primitive simplification and the `_parse_ts`-not-SQL-ordering catch (both Task 1).

**Placeholder scan**: no TBD in any Task 1-5 step. Steps 4-5 (agent-delegation, budget knob) remain untouched sketches, correctly out of scope for this pass.

**Type consistency, checked and one real bug fixed before this went to review**: Task 4's original draft referenced `knowledge_degradation.toolpath.find_tool(...)` in a `skipif` decorator, but `knowledge_degradation.py` never imports `toolpath` -- would have raised `AttributeError` at test collection, not at test run, so it would have broken collection of the entire file. Fixed to match every other terraform-gated test file's own established pattern (`import toolpath` + `TERRAFORM = toolpath.find_tool("terraform")` at test-module level, never reached through the production module). Also caught: Task 5's two new CLI tests use `os.path.exists`/`os.path.join`, but Task 3's initial test-file header didn't import `os` -- noted explicitly in Task 5's Step 1 rather than left implicit. Test counts verified arithmetically consistent task-to-task: `test_knowledge_store.py` 17→22 (Task 1); `test_knowledge_degradation.py` 0→4 (Task 3)→9 (Task 4)→13 (Task 5); combined-suite total 22+4+5+13=44 in Task 5's Step 5.

### Self-Review addendum: Step 3, round 2 (ray's implementation-level review, 2026-07-19)

Sent the bite-sized plan above to ray with the two focus areas named (removed-attribute synthetic
claims, `invalidate_claim`'s two clocks). Round 2 found a fifth instance of the same "confident
wrong answer" family this whole plan is watching for, plus a flaw in the test written to guard
the fourth instance -- both accepted in full, folded in before any code was written:

1. **Type-not-found guard** (Task 4): `schema_claims_for_type()` returning `[]` was being trusted
   as "every previously-tracked attribute was removed," reachable by an ordinary typo or the wrong
   `--kind`. Fixed with a guard that skips the removed-attribute pass and reports
   `skipped_removed_attribute_check` instead of cascading -- verified to fail pre-fix. Extended
   further per direct instruction: a passive summary field isn't enough (nobody reads those), so
   the CLI (Task 5) also warns on stderr and exits non-zero.
2. **Theater test, generalized into a standing rule**: `test_check_and_refresh_targets_the_newest_
   of_multiple_pre_existing_active_claims` inserted claims in an order that let SQLite's natural
   row-return order coincide with the correct pick, so it would likely have passed under both the
   buggy and fixed code. Fixed by scrambling insertion order (mirroring Step 2's own
   `test_resolve_uses_the_truly_newest_among_multiple_schema_claims`), plus a required fail-first
   verification step. Per direct instruction, "insertion order deliberately scrambled against
   timestamp order" is now a standing testing convention in this plan's Global Constraints, not a
   fact to remember per-test -- it has now caused a real test-quality bug twice.
3. **`valid_until` axis correction**: switched from the superseding claim's `observed_at` to its
   `valid_from`, at both call sites (present-attribute loop, Task 3; removed-attribute synthesis,
   Task 4) plus `invalidate_claim`'s own docstring (Task 1). Doesn't change `resolve()`'s behavior
   today (it never reads `valid_until`'s value), but fixed now, while free, rather than left
   latent for whatever eventually reads it for real.

All three verified to fail pre-fix per this plan's own standing discipline, not just asserted --
see each task's dedicated verification step above.

**Deliberately not built in this step, and why:** SQL-level `ORDER BY observed_at` for "pick the newest active schema claim" was drafted and rejected before being written into any task -- it would have reintroduced bug #2's exact string-comparison shape on a `Z`/`+00:00`-mixed column. All recency comparison in this step goes through `knowledge_store._parse_ts()` in Python (reused, not reimplemented).

**A second, related bug caught in self-review, not by ray:** `check_and_refresh`'s first draft built `previously_active_by_attr` via a plain `{c["attribute"]: c for c in ...}` dict comprehension over `_active_schema_claims_for_resource`'s result. That silently keeps whichever row SQLite happens to return LAST for a given attribute (unspecified order) -- correct only if at most one active schema claim can ever exist per attribute, which is NOT guaranteed (a DB used since Step 2, before this step's invalidate-on-insert discipline existed, can already hold more than one). `resolve()` itself already handles this correctly via its own `max()`-by-`observed_at` (Step 2's `test_resolve_uses_the_truly_newest_among_multiple_schema_claims` proves it), so this wouldn't have made `resolve()` return a wrong answer -- but `check_and_refresh`'s OWN bookkeeping (`invalidated_by`, which row gets `valid_until` set) could have silently referenced the wrong duplicate. Fixed to reduce via `_parse_ts()`-based comparison (Task 3/4's code below), mirroring `resolve()`'s own discipline instead of trusting dict-comprehension iteration order.
