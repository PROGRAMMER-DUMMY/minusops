# Phase 4 scope — intent-spec format + auto-generated test grounding (G3/G4)

Scope document only. No implementation until this is reviewed and agreed, per the same
discipline G2/G6's scopes went through. Posture: compliance-carrying product — the proof bar
below reflects that.

## What currently exists (verified against real code, not assumed)

- `core/architecture/requirements.py`: `requirements.json` — a gated record (`goal`,
  `system_class`, `functional[]`, `non_functional{latency,scale,availability,retention,security,
  budget}`, plus an additive `data_pipeline` profile for data workloads). Generation is blocked
  (`RequirementsIncomplete`) until every required field has a value or a real, non-lazy
  `deferred: <reason>`. Two fields already have canonical numeric parsers consumed elsewhere:
  `parse_daily_gb()` (upper-bound GB/day, conservative-high) and `parse_budget_usd()`
  (lower-bound $, conservative-low guardrail) — both return `(0, "")` rather than guess when
  nothing is parseable.
- `core/architecture/architecture_decision.py`: `architecture_decision.json` — a gated record
  binding synthesis to a specific, justified choice (`selected_architecture`,
  `selected_modules[]`, `alternatives[]` with reasons, `assumptions[]`, `risks[]`, `sources[]`).
  Synthesis is blocked (`ArchitectureDecisionIncomplete`) until complete.
- `core/generation/blueprints.py`: each blueprint declares a `controls[]` list — free-text
  claims made **to the user, before generation** (e.g. "SSE-KMS for storage and logs", "S3
  public access blocks", "Per-service IAM roles with scoped resource permissions"). Printed by
  `intent_resolver.py`'s `format_resolution()`. **Nothing currently verifies these claims are
  true of the generated Terraform or the real plan** — confirmed by grep, zero hits for any
  control-to-plan verification path.
- `core/architecture/architecture_model.py`'s `conformance()`: scores a plan against the
  six-layer reference architecture + Well-Architected Analytics Lens — **generic** shape/
  best-practice checks (layer coverage, KMS presence, monitoring presence, tier-based checks at
  TB/PB scale). It does not read `requirements.json`/`architecture_decision.json` at all and
  has no concept of *this run's specific* intent — it would score two totally different
  pipelines with the same generic findings if they had the same resource shape.
- `core/generation/terraform_generator.py`: **confirmed by grep, does not consume most
  `requirements.json` fields at all.** Generation is blueprint/module-driven; a field like
  `data_pipeline.storage_zones` is a gated, audited answer but does not itself parametrize what
  gets built — `storage-medallion-s3`'s zones, for instance, come from the module's own default
  (`["bronze","silver","gold"]`), not from what the user typed into `storage_zones`. **This is
  a real, load-bearing scoping constraint, not a detail**: any auto-generated assertion that
  checks the plan against a *free-text* requirements answer risks checking against something
  generation never actually used to shape the build, producing an assertion that just happens
  to pass or fail by coincidence rather than by real traceability. Anything Phase 4 asserts
  against must be something generation actually consumes today, or this becomes a second,
  disconnected source of truth.
- G6's precedent for plan-JSON access already exists (`configuration.root_module.resources`,
  `resource_changes`, `prior_state`) and is independently re-implemented in
  `destructive_change_gate.py`, `rego_gate.py`/`policy/g6/rules.rego`, and
  `architecture_model.py`'s `extract_resources()` — three separate parsers of the same document
  shape, not one shared module.

## 1. What "intent-spec format" means for Phase 4

**Not a new file.** `requirements.json` + `architecture_decision.json` already are the intent —
gated, validated, audited. Inventing a fourth record would fork the source of truth the same way
`optimize_analyzer.py`'s regex path and G6's Rego path currently do (deliberately, in shadow
mode, under active reconciliation) — Phase 4 should not create a *fifth* place intent can live.
"Intent-spec format" here means: a single, documented, **read-only projection** — a function
that takes a run's `requirements.json` + `architecture_decision.json` + the blueprint's
`controls[]` and derives a normalized set of **checkable claims**, each one either:
- **directly traceable to something generation actually consumes** (a selected module id, a
  parsed numeric ceiling with its own existing parser), or
- **a blueprint-declared control** (already structured, already enumerable, currently unverified).

Free-text `functional[]`/`non_functional.*` answers that generation doesn't consume are
**explicitly out of scope for auto-generated assertions** — narrower than "verify everything
the user said," disclosed rather than quietly attempted with fragile NLP-style keyword matching
that would silently miss real intent (the same fail-open shape as every other case flagged this
session). They stay valuable as an audit record and as input to a human reviewing
`architecture_decision.json`; they are not made into an automated gate by this phase.

## 2. What G3 ("auto-generated assertions") covers

Three claim classes, each mechanically derivable, no NLP:

| Claim source | Claim | Checked against |
|---|---|---|
| `architecture_decision.json.selected_modules` | Every selected module id actually appears in the real plan (as a `module.<id>.*` address family) | `resource_changes` addresses |
| Blueprint `controls[]` | Each control string maps to a concrete resource-type/attribute check (a fixed, hand-authored mapping table, not derived from the English text at runtime) — e.g. "SSE-KMS for storage and logs" → a KMS key exists and storage resources reference it | `resource_changes` / `configuration` |
| `requirements.json` parsed numerics | `parse_budget_usd()`'s ceiling matches the real `aws_budgets_budget.limit_amount` in the plan; `parse_daily_gb()`'s tier matches `conformance()`'s own `volume_tier()` conditional checks actually firing/not firing as expected | `resource_changes` `after` values |

The control-to-check mapping table is hand-authored and reviewed, the same way G6's rule map
was — not generated from the control string at runtime. A control with no mapping entry is a
named gap (logged, not silently skipped), same as G6's "zero real coverage" disclosure for
SEC-03/04/COST-02/03.

**Output shape**: matches G6's `finding()` shape (`id`, `category`, `severity`, `resource`) so
it can reuse the same audit-chain logging and divergence-reporting pattern already built for
G6, not a new report format.

## 3. What G4 ("plan-JSON parsing") covers

Not a new parser — **consolidation of the three existing independent plan-JSON readers**
(`destructive_change_gate.py`, `rego_gate.py`, `architecture_model.py.extract_resources()`)
behind one shared, tested module Phase 4's assertions are built on, so a fourth consumer
doesn't become a fourth reimplementation of "how do I read `resource_changes`/`configuration`/
`prior_state` safely." Whether the three existing call sites get refactored onto it is a
separate decision (touches G5/G6, already closed) — Phase 4 only requires that *new* code use
the shared module, not that old code be migrated as part of this phase.

## 4. Shadow-mode-then-advise plan

Unlike G6 (which produces SEC-*/COST-* findings of the same kind the regex path already
produced), Phase 4's assertions are **net new** — there is no existing enforcement to be
consistent with, and a false positive here blocks generation/apply on a claim MinusOps itself
made up incorrectly, which is a worse failure mode than G6's shadow-vs-shadow comparison. Plan:

1. Assertions run and log findings (same audit-chain pattern as G6) — **advisory only**,
   surfaced in the deploy report alongside `conformance()`'s existing findings, never blocking.
2. A real run across the 16-module catalog + the demo blueprint, every module-selection and
   control-mapping claim checked against a real plan, every divergence explained (a module
   claimed but genuinely absent = a real bug; a control mapped incorrectly = fix the mapping
   table, not the claim).
3. Only after that proof: decide, as its own reviewed step, whether any subset of these
   (module-presence checks are the least ambiguous candidate) becomes a real gate. Control-
   mapping and numeric-ceiling checks likely stay advisory longer, since their mapping tables
   are new and unproven — this mirrors G6's SEC-05b/c "resolved-JSON improvement, verify before
   trusting" caution, not treated as risk-free from day one.

## 5. Fail-closed handling

| Case | Behavior |
|---|---|
| `requirements.json`/`architecture_decision.json` missing or incomplete | Already blocked upstream by `RequirementsIncomplete`/`ArchitectureDecisionIncomplete` — Phase 4 never runs against an incomplete record, nothing new to add here. |
| A `selected_modules` entry has no matching plan address at all | **Finding, not silent** — this is the single least-ambiguous, most valuable check in this whole phase (did we actually build what the decision record says we built) — never treated as "nothing to check." |
| A blueprint control has no entry in the mapping table | **Named gap, logged distinctly** (e.g. `control_unmapped`) — never silently skipped, never counted as "passed." |
| Plan JSON malformed/unreadable | **BLOCK the assertion pass itself** (same `evaluation_failed`-style verdict shape as `rego_gate.py`), distinct from "checked and clean." |
| A numeric parser (`parse_budget_usd`/`parse_daily_gb`) returns `(0, "")` (nothing parseable) | **Not a failure** — matches the parsers' own existing "never guess" contract; the corresponding assertion is skipped and logged as `not_applicable`, not as a pass or a block. |

## 6. Proof bar

1. **Module-presence check, real proof**: run against real plans (or the existing 16-module
   `terraform test`/mock harness where sufficient — same shape caveat G6 hit: confirm live
   whether that harness's reduced JSON shape (no `configuration`/`prior_state`) is sufficient
   for a `resource_changes`-only presence check before assuming it, the same "verify against
   real plan, not memory" rule this whole session has enforced) for every module in the catalog,
   confirming `selected_modules` entries resolve to real addresses.
2. **Control-mapping table, verified per control**: for every blueprint control currently
   declared, either a real plan proving the mapped check fires correctly (both a clean case and
   a deliberately-broken fixture proving it catches the violation), or an explicit
   `control_unmapped` entry — no control silently unchecked without being named as such.
3. **Fail-closed sweep**, before declaring anything done, covering every row in section 5's
   table, each with its own regression test — same timing discipline as G6's condition 2.
4. **G4 consolidation, if pursued this phase**: the shared plan-JSON reader module tested
   against the same real-shape facts G6 already verified (sparse `after_unknown`, data sources
   in `prior_state` not `resource_changes`, `resource_changes` omitted entirely for zero-change
   plans) — not re-derived from scratch, reused from G6's own findings.
5. **Prove it runs where it ships** (standing item as of the G6 close, applies to every gate
   from here on): if any part of this becomes a real CI/report step, a real CI run showing it
   executes and returns a genuine verdict, not just local proof.

## Ordering invariant

Phase 4 is next. G6 (Phase 3) is closed, shadow-only. Phases 5 (G9, ephemeral apply) and 6
(generation pipeline / catalog teardown) remain after this, in that order, regardless of gate
numbering. No implementation starts until this scope is agreed.
