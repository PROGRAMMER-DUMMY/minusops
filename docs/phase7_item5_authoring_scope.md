# Phase 7 Item 5 scope — the authoring mechanism itself

Scope document only. No implementation until this is reviewed and agreed. This gets more scrutiny
than any other item in this plan, by explicit instruction, because it is the one piece the entire
pivot was for and the one piece never built: everything in items 1–4 made the seam able to carry
real content and made a real requirements-driven decision honestly scoped — none of it decides
what HCL to write. This document has to answer, concretely, what does.

> **CORRECTION (2026-07-16), after the checks/audit-record below were already built:** Section 1
> as originally written assumed MinusOps would embed its own LLM API client (an Anthropic SDK
> call, its own model choice, its own credentials) to do the authoring. That is wrong for this
> project — **MinusOps is operated THROUGH an agentic CLI tool** (Claude Code, Codex, agy, etc.),
> not run as a standalone LLM app. The driving agent already has full authoring capability; it
> does not need MinusOps to make a second, separate LLM call on its own. What it needs is the
> same real, live context a human author would want — the declared type's live provider schema
> and real grounding examples from this codebase's own reviewed modules — surfaced as a plain,
> callable function (`synthesizer.assemble_authoring_context()`) and a matching CLI subcommand
> (`synthesizer.py author-context <resource_type> <requirements>`). The driving agent reads that
> context, writes the HCL itself, and hands it back through the exact same `authored_content`
> interface every other caller of `synthesize()` already uses — nothing about that interface
> changes. Everything below section 1 (the INVARIANT, the fail-closed checks, the no-retry
> decision, the proof bar) is UNCHANGED and still applies — it was never actually about *how* the
> call happens, only about what a caller's authored output must satisfy. Only "an LLM call this
> project makes on its own" is struck; replace every reading of that phrase below with "whatever
> agent is driving the session, using `assemble_authoring_context()`'s output."

## 1. What actually authors

**Named concretely: an LLM call**, not a template engine and not a decision tree. Justified by
elimination, not by default: a template engine or decision tree can only reproduce patterns
someone already encoded — that is what the 16-module catalog and `compose()`'s copy-path already
do. The survey's own finding was explicit: *"nothing decides what HCL to write from a resource-
type name or a requirement — this is the core, unbuilt capability."* Only a model that can
generalize past encoded patterns can do that. This project's own docs already named this the
"research-grounded generation" endgame (`docs/g2_scope.md`, quoted in `docs/phase6_scope.md`
§1.2); this item is where that stops being deferred language and becomes a design decision with
consequences.

**Naming it an LLM call means naming what that costs, not filing it as a footnote:**

> **INVARIANT, not a design note — the boundary this whole item depends on:** the LLM authors
> *syntax* for a resource_type a human has already named and justified in `novel_resources`. **It
> never proposes a type, never decides what gets built.** `novel_resources`/`architecture_
> decision.json` stays the one human-reviewed record deciding *what* — exactly the same
> discipline that record's own docstring already states for module selection (*"keyword matching
> cannot silently become a recommendation engine"*), extended to authoring without exception. If
> this boundary erodes — if authoring is ever allowed to introduce a resource_type nobody
> declared, or to expand scope beyond the declared type — the entire human-reviewed-record
> discipline this project has held since Phase 1 collapses, not just this item's own safety
> story. Any future change that touches this boundary needs its own explicit review, not a quiet
> refactor.

- **Non-determinism.** The same declared resource_type + justification can produce different HCL
  on different calls. This scope does not try to make authoring deterministic — the invariant
  above is what stays deterministic and human-reviewed; non-determinism is confined to *how* to
  author HCL for a type already named, never *what* to build.
- **Occasional confident wrongness.** The model can hallucinate a nonexistent type, invent an
  attribute a real type doesn't have, or author content for a type other than the one it was
  asked for. Section 4 below maps every one of these to a specific check and verdict — this is
  not deferred to "the gates will catch it eventually," it's enumerated.
- **No retry on a failed check — decided here, not left for whoever writes the loop.** A naive
  implementation reaches for "check fails → call the model again until one passes." That
  selects for output that *slips past* the checks, not output that's *correct* — the same
  "re-run the flaky test until green" pattern this project has already refused twice (elsewhere:
  fail-closed-on-unparseable-input, never fail-open-and-retry). **Decision: no retries in the
  first pass.** A failed check (any row in section 4 marked "hard block") is a hard stop: reported
  to a human, with the failure reason and the raw authored output preserved in the audit record
  (below) so a human can see exactly what the model produced and why it was rejected — not
  silently discarded, and not retried into something that happens to pass.
- **Reproducibility of the DECISION, not the output.** Since the exact HCL isn't reproducible
  call-to-call, what must be reproducible is the record of what happened: the resource_type
  requested, the justification, the grounding examples and live schema supplied, and the raw
  authored output (whether it passed or was hard-stopped) — written to the same audit chain
  `plan_gate.py`/`_audit_allow_incomplete_bypass()` already write to, so a specific authoring
  decision is always reconstructable after the fact even though repeating the call might not
  produce the same bytes. **Payload size, checked, not assumed**: a live `get_type_schema()` block
  for `aws_dynamodb_table` measured 8,996 bytes; `retrieve_grounding_examples()` for a typical
  request measured ~4.5 KB across 2 real module bodies (both measured against the real running
  code, not estimated). `audit_chain.append()` has no hard size limit — it's a plain
  `json.dumps()` + sha256 per line — so inlining this would not technically break. It is still the
  wrong call: this project's own established pattern for bulky artifacts (`source_guard.py`'s
  baseline manifests, `requirements.json`/`architecture_decision.json` themselves) is a small,
  hash-verified audit record pointing at real files written into the run's own workspace, not
  bulk content inlined into the hash-chained log itself. The authoring record follows that same
  pattern: the chain entry carries `resource_type`, `justification`, `verdict`, and a
  `content_hash`/`schema_hash`/`grounding_hash` plus the relative path each full artifact was
  written to (e.g. `authoring/<resource_type>-{request,schema,grounding,output}.json` under the
  run root) — tamper-evidence via hash comparison, without bloating the chain itself.

**Concrete inputs to the call** (not designed further here — the prompt/model/provider choice is
implementation, not scope, and correctly out of this document):

1. `novel_resources[i]`'s own `resource_type` + `justification` + `alternatives_considered` — the
   human-reviewed decision of *what* and *why*, unchanged.
2. `get_type_schema(provider, resource_type)` (Item 4) — the real, live attribute/block shape for
   the declared type, grounding the model in what attributes actually exist rather than letting it
   invent plausible-sounding ones.
3. `retrieve_grounding_examples(requirements)` (Step 5, additive, **zero production callers today
   — this is its first real one**) — real, human-reviewed HCL from the closest existing catalog
   modules, grounding style and wiring convention (naming, tagging, how outputs are typically
   shaped), not correctness (correctness is items 2's job and section 4's checks).
4. Output: exactly one `authored_content` entry (Item 1's flat-`str` or module-`dict` form),
   nothing else — the interface items 1/2 already built and proved, unchanged by this item.

## 2. The smallest honest first target — `aws_dynamodb_table`, and why

Not "author any resource type." One specific, simple, well-understood type, authored end-to-end
through the real seam, proven to pass every gate. The same discipline G9 used to prove itself —
one real caught failure, not a claim of general coverage.

**Picked: `aws_dynamodb_table`.** Verified against the real code, not chosen for convenience:

- **Already this project's own canonical fixture for exactly this scenario.** `test_synthesizer.
  py`'s `_NOVEL_DECISION`/`_VALID_DYNAMODB_HCL` has used a hand-typed `aws_dynamodb_table` as the
  "no catalog module provides this" example since Step 1, and `destructive_change_gate.py`'s own
  module docstring cites it as the live-confirmed proof that a genuinely novel type stages under
  G5's Step 0 fix. Item 5 closes the loop on the SAME type: from "hand-typed in a test fixture, in
  the abstract" to "authored for real by the real mechanism, through the real pipeline."
- **Confirmed absent from every gate's own reviewed-type set** (grepped, not assumed):
  `destructive_change_gate.py` has zero matches outside its own docstring's comment — not in
  `STATEFUL_RESOURCE_TYPES`, `IAM_RESOURCE_TYPES`, or `AUTO_SHIP_ELIGIBLE_TYPES`. Zero matches in
  `policy/g6/rules.rego`. Zero matches in `ephemeral_apply.py`'s `RESOURCE_TYPE_ALLOWLIST`. This
  means it correctly routes to `unreviewed_resource_type` under G5 (never `autonomous_eligible`)
  AND correctly reports as unverified under G9's `entry is None` branch — staged for two
  independent reasons, neither a bug, giving a clean demonstration with no ambiguity about why it
  didn't ship.
- **Minimal attribute surface**: `name`, `billing_mode`, `hash_key`, one `attribute { name, type }`
  block. No dynamic blocks, no companion assets, no cross-resource wiring — none of items 1's
  named structural gaps (path.module assets, required-variable wiring) are even in play for this
  target, so a first pass isn't simultaneously debugging the seam and the mechanism.
- **A real, well-known AWS type** — `get_type_schema("aws", "aws_dynamodb_table")` resolves a real,
  non-trivial schema (partition/sort keys, billing mode, TTL, point-in-time recovery, encryption),
  giving the grounding step real material to work with, not a toy.

## 3. The coverage expectation, stated so staging reads as correct, not as failure

`aws_dynamodb_table` sits outside the 41-type reviewed universe by construction (that's *why* it's
the right first target — section 2). This means, unavoidably and correctly: **G5 stages it, always
— it is not `autonomous_eligible` regardless of how clean the authored content is. G6 fires zero
rules — not because the content is safe, but because nothing has been written to check DynamoDB
configuration at all (the same 33/41-zero-coverage gap already escalated in HANDOFF §5, unrelated
to and not fixed by this item). G9 reports it as unverified on every emulator.**

This is stated here explicitly so it is never misread later: **the first real generated output
correctly landing in the staged, human-reviewed path is the pipeline working exactly as designed
— not the generator being broken, not a disappointing result, not evidence Item 5 "didn't really
work."** Proof bar item 6 (below) makes this an asserted, checked fact, not a hoped-for one. A
generated `aws_dynamodb_table` auto-shipping on its first appearance would be the actual failure —
it would mean G5's fail-closed boundary broke, not that generation succeeded.

## 4. Fail-closed on the generator's own output — every mode named, mapped to a verdict

Items 1/2 already established the standard: a hallucinated, nonexistent type is a hard block
(authoring malfunctioned), distinct from a legitimate novel type that exists and flows to G2 for
its real content check. This carries that forward and closes a gap this item's own analysis
found — the existing checks were built for a human/test caller who naturally authors content
matching what they declared; an LLM is the first caller where "declared X, authored Y" becomes a
real, non-hypothetical failure mode.

| Failure mode | Check | Verdict | Status |
|---|---|---|---|
| Declared `resource_type` doesn't exist in the live provider schema at all | **NEW**: `get_type_schema(provider, resource_type)` called BEFORE authoring; `None` → refuse | Hard block, before any authoring call is even made | New work, this item |
| Authored content's own resource/data block type doesn't match the declared `resource_type` (flat form) | **NEW**: cross-check the block type(s) found by `iter_hcl_blocks()` against the declared type | Hard block — content that doesn't address what was asked is authoring malfunction, not novel output | New work, this item |
| Hallucinated attribute, or a mismatched type that IS real but wrong | `schema_lint.gate_content()` (G2) — `unknown_type`/`unknown_attribute` | Hard block | Already built (Step 1) |
| Zero resource/data blocks declared at all | `_validate_novel_resources()`'s existing check | Hard block | Already built (Step 1) |
| `path.module` reference with no matching companion asset | Item 1's asset check | Hard block | Already built |
| Required variable (module form) with no default, no `module_args`, no auto-wire match | Item 1's required-variable check | Hard block | Already built |
| Structurally malformed HCL a regex-based scan can't reliably catch (unbalanced braces etc.) | **G1** (`terraform validate`), downstream in the real `stage_plan` flow — the actual parser, not a best-effort scan | Refuse to plan | Already built, generic, unaffected by this item |
| Well-formed, real, novel type with an unreviewed configuration risk (e.g., no encryption) | G5 (`unreviewed_resource_type`) stages it; G6 has no rule for an out-of-universe type | **Stages** for human review; G6 provides no content-level assurance (disclosed, matches the already-escalated 33/41 gap, not newly introduced or newly fixed here) | Existing, unaffected |
| Non-reproducible output across repeated calls | **NEW**: the full authoring record (declared type, justification, schema + grounding examples supplied, raw output) written to the audit chain | Not blocked — made reconstructable after the fact | New work, this item |

The two genuinely new checks (pre-authoring schema existence, and declared-vs-authored type match)
both belong in the same place the existing ones live: `_validate_novel_resources()`'s fail-closed
chain, run in that order — schema-exists check first (cheapest, and the only one that can save an
LLM call entirely), type-match check right after content exists, before the already-built G2/asset/
variable checks.

**Named, not solved here**: the type-match check is scoped to the flat (`str`) form, matching
this item's own first target. The module (`dict`) form's unit can legitimately bundle several
resource/data blocks under one caller-chosen key that isn't itself a literal type string (Item 1's
own real modules, decomposed, are the existing proof of this shape) — what "the content actually
addresses the declared need" means for a multi-resource unit is a real, harder question this scope
does not resolve, because it isn't required for the target in section 2.

## 5. The proof bar — what "Item 5 works" means, concretely

Not "the generator produced plausible HCL." Every item below is a checkable fact:

1. `aws_dynamodb_table` is authored **fresh, and this is made checkable, not asserted**:
   `test_synthesizer.py`'s existing fixture (`_VALID_DYNAMODB_HCL`) is a single-hash-key table
   named `"novel-table"`, `billing_mode = "PAY_PER_REQUEST"`, one `attribute { name = "id", type =
   "S" }`. "Looks different" is not a check, so the proof request's own parameters must make
   reproducing the fixture impossible, not just unlikely: a **different table name**, a
   **composite key** (`hash_key` + `range_key`, the fixture has neither a range key nor a second
   attribute block), and **`billing_mode = "PROVISIONED"`** with explicit `read_capacity`/
   `write_capacity` (the fixture only ever uses `PAY_PER_REQUEST`, which take no capacity
   arguments at all). Output containing a `range_key` and `read_capacity`/`write_capacity` cannot
   be the fixture reproduced — it can only be a real response to the actual request, which is
   what makes "fresh" a checkable assertion instead of a claim.
2. The pre-authoring `get_type_schema()` check passes (the type is confirmed real before
   authoring is attempted).
3. The authored content passes every existing `_validate_novel_resources()` check: non-zero
   blocks, the new type-match check, G2's `gate_content()`.
4. Composed through the real public `synthesize()` entry point, via Item 2's zero-catalog path
   (`selected_modules: []`, `novel_resources` covering the DynamoDB table) — not `compose()`
   called directly.
5. **G1** (`terraform validate`) passes for real.
6. **G5 classifies it `unreviewed_resource_type`, `autonomous_eligible: False`** — asserted and
   checked, not assumed from section 3's reasoning alone.
7. **G6** shadow-evaluates it; zero rules fire — asserted as the expected, correct result, not
   silently skipped or treated as "nothing to check."
8. **G9** reports it unverified (absent from `RESOURCE_TYPE_ALLOWLIST`) — a second, independent
   confirmation that this output correctly cannot auto-ship.
9. The full path — decision record, the new authoring record (section 1's audit requirement),
   every gate verdict — is present in the audit chain and reconstructable end-to-end from it
   alone, without needing to re-run anything.
10. The result is reported as what it is: **the pipeline correctly refusing to trust unproven
    output** — matching section 3, so this is never later misread as a shortfall.

## What this scope does not decide

- The specific LLM/provider and prompt design — implementation, correctly deferred.
- The module-form type-match question (section 4's named gap) — not required for this item's
  target, named so it isn't silently forgotten when a module-shaped novel unit is authored later.
- Whether/when to build out G6 coverage for `aws_dynamodb_table` or any other newly-authored
  type — a separate, already-escalated, larger question (HANDOFF §5 item 1), not this item's job.

## Ordering invariant

No implementation starts until this scope is reviewed and agreed, same as every item before it.
Once agreed, build order: (1) the two new fail-closed checks in `_validate_novel_resources()`
(schema-exists, type-match), each independently testable against hand-authored fixtures exactly
like Item 1's own tests, before any real LLM call is wired in; (2) the audit-record write for the
authoring step; (3) the actual authoring call, last, since it's the one piece every other check
in this document exists to constrain. Proof bar (section 5) is the acceptance test for the whole
item, not for any one piece of it in isolation.
