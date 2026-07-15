# Phase 7 plan — sequencing the generation engine from the ground-truth survey

Plan document only. No implementation until this is reviewed and agreed — same discipline as
every prior phase. This sequences five pieces of work the 2026-07-15 ground-truth survey
(`docs/generation_engine_ground_truth_survey.md`) surfaced as necessary before "build the
authoring mechanism" is even a well-formed task. The headline result of that survey: three of the
five items below are not authoring work at all — they are gaps in the seam a generator would plug
into, found by trying to reason concretely about what plugging in would require.

## Why this order, not authoring-first

The instinct is to treat "decide what HCL to write" as the generation engine and everything else
as plumbing around it. The survey inverts that. `synthesizer.py`'s `authored_content` mechanism —
the only seam that exists today for trusted, non-catalog HCL to enter the pipeline — cannot
express a real module (item 1), and the one public entry point that would carry it can't run
catalog-free (item 2). An authoring mechanism built today would be writing content for a socket
that drops half of what it produces on the floor. Items 1 and 2 are prerequisite in the literal
sense: they are true regardless of what authors the HCL, human or otherwise, and they are false
today. Item 3 is a decision, not code, but it has to be made before a generator's inputs are
designed, not discovered after the generator is built and found to be reading two numbers. Item 4
is small, additive, and item 5 (and arguably item 3's "real" branch) needs it. Item 5 — the actual
authoring logic — comes last because it is the one piece that depends on all four of the others
being either built or explicitly decided against.

## 1. Extend `authored_content` to a module-shaped unit — prerequisite, first

**The gap, from the survey (§1, §4):** `synthesizer.synthesize(authored_content=...)` takes
`dict[str, str]` — one flat HCL blob per resource type, composed as a bare `authored_{type}.tf`
file at the composition root. There is no `variable`/`output` boundary, no place for `locals`, no
way to express a resource's own input contract. Every real catalog module needs this: 7 variables
on average, 11 for `databricks-workspace` (§4's structural corpus table). The regression harness
built in Phase 6 Step 5 had to hand-carry `variable`/`locals` blocks as separate root-level files
*outside* the mechanism it was testing (`tests/test_teardown_regression_harness.py`) — proof by
construction that the current seam can't carry what it would need to for a generator's output to
look like any existing module.

**What "done" looks like:** `authored_content` (or its successor) can express a unit with its own
declared inputs (`variable` blocks), its own `output`s, its own `locals`, and — per item on the
survey's derived list — a way to carry a non-HCL companion asset (the `path.module`-relative
script gap `compute-glue-etl`/`compaction-glue` already expose). The existing flat per-type
composition path must keep working unchanged for any caller still using it that way; this is an
extension, not a breaking change to `synthesize()`'s current contract. Proof bar: re-run the
Phase 6 Step 5 regression harness's 5 named blockers (`docs/phase6_step5_teardown_scope.md`
Results) against the extended mechanism — the 2 `path.module`-asset blockers should close for
real, not by a test-only workaround.

## 2. Fix `synthesize()`'s zero-catalog path

**The gap, from the survey (§1):** `synthesize()` always calls `select_modules()` →
`match_modules()`, which auto-adds `governance-observability` when nothing else scores — there is
no way to call the real public API and get a purely authored composition with zero catalog
modules. The Step 5 regression harness worked around this by calling
`synthesizer._validate_novel_resources()` + `synthesizer.compose([], ...)` directly, bypassing
`select_modules()` entirely (`tests/test_teardown_regression_harness.py`). That means the authored
path, exercised through the real entry point a caller would actually use, has never once run
catalog-free. This is a design bug at the seam, not a missing feature — the underlying
`compose()` guard was already fixed in Step 1 (`if not chosen and not authored_resources: raise`);
`synthesize()` itself never got the equivalent path to reach it.

**What "done" looks like:** a caller can invoke `synthesize()` (not `compose()` directly) with an
empty or absent module selection and non-empty `authored_content`/`novel_resources`, and get a
clean authored-only composition — no silent fallback to keyword matching, no auto-added module.
Proof bar: a test that calls the real public `synthesize()` entry point exactly the way the
regression harness's workaround called the private path, and gets the same result without
bypassing `select_modules()`.

## 3. Resolve the requirements-schema symbolic-vs-real decision

**The gap, from the survey (§3) and HANDOFF §5 item 2:** of `architecture_decision.json`'s 11
fields, 2 are load-bearing (`selected_modules`, `novel_resources`); of the full
`requirements.json` schema, only `data_pipeline.data_volume` and `non_functional.budget` shape any
generation-adjacent output. Nine of ten `data_pipeline` fields and five of six NFR axes are
validated on the way in by `requirements.py`'s fail-closed gate and never read again by anything
downstream.

**This is a decision, not a build item.** Two honest branches, not a false choice — either is a
legitimate outcome, but the current state (neither decided nor disclosed) is not:

- **(a) Make the schema real input.** Design what a generator would actually read from each
  currently-inert field — e.g., does a stated latency NFR change which resource types get
  proposed, does a named compliance requirement change which G6 rules apply — and wire it in
  deliberately, field by field, not all at once.
- **(b) Declare the schema symbolic/audit-only, explicitly.** The `grill-me` interview and
  fail-closed completeness gate still serve a real purpose (forcing a human to have actually
  answered the NFR questions, on the record, before anything ships) even if a generator never
  reads most of the answers back. If this is the intended shape, it should say so in the schema's
  own docstring, not be discovered again by the next person who greps for a field and finds
  nothing reads it.

**What "done" looks like:** one of the two branches above, chosen and written down — in
`core/architecture/requirements.py`'s module docstring if (b), or as its own follow-on scope doc
with a field-by-field consumption design if (a). No generator design proceeds past this point
without knowing which branch it's building against.

## 4. The per-type live schema query function

**The gap, from the survey (§2):** `schema_watch.py`'s `_fetch_schema()` and `_reduce_full()`
both exist, are individually real and tested, and are never wired together into a single "give me
the attributes for resource type X" query. `retrieve_grounding_examples()`
(`core/generation/modules.py`) is additive and real but has zero production call sites today —
it returns existing catalog examples, not live schema.

**What "done" looks like:** a thin function — composing the two existing pieces, not rewriting
either — that takes a resource type string and returns its current provider schema shape. This is
small and additive by design: it is infrastructure item 5 will need (to check a proposed novel
resource's shape against the real provider before authoring content for it), not a generator
capability in itself.

## 5. The authoring mechanism itself — deferred until 1–4 are done or explicitly decided

Not designed here. This scope only names the dependency: whatever authors HCL plugs into item 1's
extended seam, through item 2's fixed entry point, informed by item 3's resolved (not inert)
requirements input and item 4's live schema check. Building this before 1–4 land would repeat the
exact pattern the survey was commissioned to catch — a capability aimed at a seam that can't carry
its output. Its only real prerequisite is items 1–4 above — not a G5 fix, which is already done
(see Banked, below): `docs/phase6_scope.md` §4.1 described a fail-open gap that Step 0
(2026-07-14) closed. That section is now marked superseded; it should not be read as live.

## Banked: G5's fail-closed posture, confirmed against current code

The survey re-verified `classify()`'s actual branches directly (not from memory, not from
`phase6_scope.md`'s now-stale §4.1): a type in none of `STATEFUL_RESOURCE_TYPES`/
`IAM_RESOURCE_TYPES` AND not in `AUTO_SHIP_ELIGIBLE_TYPES` (the reviewed-safe allowlist added in
Step 0) falls to `reason = "unreviewed_resource_type"` → `autonomous_eligible` evaluates `False`.
**Confirmed: a genuinely novel resource type always stages, never ships autonomously, today.**
This has been true since Step 0, proven then with the `dynamodb_table` fail-before/pass-after
test, CI green — it is closed, not pending, and item 5 has no outstanding gate prerequisite
beyond items 1–4. Post-Step-0, "doesn't recognize" (not in `AUTO_SHIP_ELIGIBLE_TYPES`) and "in one
of the two named danger sets" are correctly two different, separately-reasoned cases — that
distinction was the entire point of Step 0's fix, not a residual gap.

## The coverage reality, and what it means for the autonomy story

Restated plainly, from the survey (§5) and now also HANDOFF §5 item 1: 33 of 41 reviewed types
have zero firing G6 rule; 37 of 41 are G9-unverified on every emulator; all 4 security-critical
types block under G9's fail-closed rule regardless of what fired. The net effect is a pipeline
that is **safe as a mechanism** — everything unrecognized or unverified stages rather than ships —
but whose actual policy/fidelity coverage is a small fraction of the type space it would need to
reason about once generation moves past the current 16 modules. Concretely: today, a generator's
output would essentially always land in the staged path, not because the gates are weak, but
because so little of the reviewable universe has been reviewed yet. That is the correct and safe
outcome under the current fail-closed design — but it means the "autonomous generation" story is
still theoretical, not just unbuilt, until G6/G9 coverage actually grows to match whatever a
generator starts producing. Coverage growth is not on this plan's critical path (nothing here
requires it to proceed), but no future status update should describe generated output as
auto-shippable without checking whether coverage grew to cover what was actually generated.

## Ordering invariant

1 → 2 → 3 → 4 → 5, as sequenced above. Item 5's only real prerequisite is items 1–4; G5's fail-
open gap that an earlier scope doc (`docs/phase6_scope.md` §4.1) once named as a prerequisite was
already closed in Step 0 and is not a gate on this plan (see Banked, above). No implementation
starts on any item until this plan is reviewed and agreed. Nothing in this document authorizes
writing code.
