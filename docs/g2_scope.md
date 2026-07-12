# G2 scope — pre-write schema linter

Scope document only. No implementation in this pass; brought back for review per standing
instruction. Posture: compliance-carrying product (2026-07-11) — this scope reflects that,
particularly in the HARD-FAIL default.

## Where "pre-write" actually hooks in

The generation-time-authoring pivot's eventual endgame is live per-request HCL synthesis
beyond the frozen 16-module catalog — that engine doesn't exist yet. Today, the only point
where genuinely new or modified HCL enters the system is `core/generation/module_provenance.
py`'s `pin(module_id, ...)`: a maintainer hand-authors or edits a module's `main.tf`, then
pins it, and from that moment the content is trusted (copied verbatim into every future
`synthesizer.compose()` run). `compose()` itself never writes new HCL — it only copies
already-pinned module content and renders root-level glue (`versions.tf`, `providers.tf`,
`variables.tf`, `main.tf` composition) from templates that don't reference resource
attributes directly.

**G2 gates `pin()`, not `compose()`.** When live generation-time authoring exists, the same
check extends to whatever produces HCL at that point too — but that's future scope, not
this one.

## 1. HARD-FAIL vs WARN

Default is HARD-FAIL (block the pin). One WARN category, justified below — everything else
blocks.

| Condition | Verdict | Why |
|---|---|---|
| Used resource/data type no longer exists in the live schema | **HARD-FAIL** | Guaranteed `terraform init`/`plan` failure. No valid degraded mode. |
| HCL references an attribute name the live schema doesn't recognize for that type (the `.name`→`.region` class) | **HARD-FAIL** | Same — guaranteed breakage. This is the exact case that motivated the pivot. |
| An attribute the module HCL sets or reads has a schema type-shape change (string→list, block→attribute, etc.) | **HARD-FAIL** | Same reasoning — no degraded mode exists between "type matches" and "type doesn't." |
| An attribute the module HCL actually references is now marked `deprecated` in the live schema | **HARD-FAIL** | Deprecated today is frequently removed next major version; a compliance product should not let new infrastructure get pinned against a field already flagged for removal. Verified live: `data.aws_region.name` is `deprecated: true` on the currently-resolving AWS provider (6.54.0) right now — this is not a hypothetical condition, it's live. |
| Resource's schema `version` integer bumped, with **no accompanying attribute-level signal** (no removed/deprecated/mismatched attribute detected) | **WARN** | The only justified exception. A version bump alone usually signals a *state upgrade* path (how Terraform reads old state on refresh), not necessarily a config-breaking change — and G2 runs pre-write, before any state exists for the thing being pinned. Without a concrete attribute-level finding to point at, there's nothing to block on, only something worth a human's attention. If the bump *does* correlate with a detectable attribute change, that change is caught by one of the HARD-FAIL rows above instead — this WARN row only fires on the residual "something changed, we can't tell what" case. |

## 2. REUSE vs BUILD

**Reused verbatim from `schema_watch.py`** (no reason to duplicate):
- `_fetch_schema()` — the `terraform init` + `providers schema -json` mechanic, including
  reading the version constraint from `synthesizer.py`'s single source of truth.
- `used_types()` — parsing `resource "type" "name"` / `data "type" "name"` declarations out
  of a module's `main.tf`.
- `_PROVIDER_PREFIX` / `_PROVIDER_SOURCE` / `_version_constraint()` — provider identity and
  constraint plumbing.
- `_deprecated_attrs()` — the recursive deprecated-attribute walk (reused as-is for the
  deprecation check).

**Genuinely net-new** (schema_watch.py has no coverage of any of this):
- **HCL attribute-reference extraction.** schema_watch.py's `_reduce()` records a type's
  schema `version` and its deprecated-attribute *names* — it never looks at which specific
  attributes a module's own HCL actually dereferences (e.g. the `.region` in
  `data.aws_region.current.region`) or sets. G2 needs a new extractor that walks a module's
  HCL body (not just its `resource "type" "name"` declaration lines) for `<type>.<name>.
  <attr>` reference chains and `<attr> = ...` assignment lines inside each block.
- **Unknown/invalid-attribute check.** Cross-referencing extracted attribute names against
  the *full* attribute set in the live schema (`block.attributes`), not just the deprecated
  subset schema_watch.py already tracks. New comparison logic.
- **Type-shape comparison.** For attributes the module HCL sets, checking the schema's
  expected type against what's being assigned. schema_watch.py has zero coverage here — it
  only ever compares a `version` int and a deprecated-name set between two snapshots.
- **The classification/blocking result itself.** schema_watch.py has no concept of blocking —
  everything it finds becomes a report + an audit-chain entry, but `run_provider()`'s return
  value never stops anything downstream (explicit in its own docstring: "never touches
  plan_gate.py's apply path, never blocks a deploy"). G2 needs an actual gate function
  returning a fail-closed-style verdict (mirroring `destructive_change_gate.classify()`'s
  shape: a dict with a boolean pass/fail and a findings list) that `pin()` checks and refuses
  to proceed past on HARD-FAIL.
- **The `pin()` hook.** Wiring the check as a precondition `pin()` (or its CLI wrapper) runs
  before writing `PROVENANCE.json`. `pin()` already accepts an optional `schema_hash` — that
  field exists today as informational data capture, not a gate; G2 turns the *validation*
  behind it into a blocking check.

**One structural difference worth flagging up front:** schema_watch.py's whole design is a
*diff* — old snapshot vs. new snapshot, and it explicitly no-ops when there's no old snapshot
to diff against (`_diff()` returns `[]` if `old_snapshot is None`). G2 is **not** a diff. It's
a single-point validity check: does this module's HCL, right now, match the live schema,
right now. It has no "no baseline, nothing to report" escape hatch — every pin gets checked
against the live schema fresh, every time.

## 3. FAIL-CLOSED discipline

Same standard as G5's `_fail_closed()` sweep — every degradation path returns a structured
non-passing result, never a crash, never a silent pass.

| Degradation | Behavior | Reasoning |
|---|---|---|
| Can't fetch the schema (`terraform init` fails, registry unreachable, network down) | **BLOCK** | A gate that can't establish the schema is real isn't a gate. `pin()` refuses with an explicit `schema_fetch_failed` finding — never silently treated as "no findings = OK." |
| Fetched schema JSON is malformed / missing expected keys (`provider_schemas`, the specific provider key) | **BLOCK** | Same reasoning, same shape as G5's `_fail_closed("plan_json_not_a_dict")` precedent. |
| Module HCL references a resource/data type with literally no entry in the live schema table (a typo'd type, not a *removed* one — no diff history needed to know this) | **BLOCK** | Functionally the same as the unknown-attribute case, one level up: the type itself doesn't resolve. |
| Module HCL contains an attribute reference G2's extractor can't confidently resolve to a static `.attr` chain (built dynamically via `for_each`/interpolation) | **BLOCK**, with its own `unparseable_reference` finding | Silently skipping what can't be parsed is exactly the fail-open shape Probe A found and closed in G5 — "can't confirm safe" must not collapse into "assumed safe." |
| Module references zero types under a given provider (nothing to check) | **Not a failure — no findings.** | Distinguished deliberately from the row above: "nothing used" is a legitimate empty result, not a degraded one. Matches schema_watch.py's own "unused = not a finding" framing. |

## 4. Proof bar

- **Unit-level**, synthetic fixtures, same style as `test_schema_watch.py`: HARD-FAIL/WARN
  classification per condition in §1, each fail-closed degradation in §3 with its own
  regression test.
- **Fail-closed sweep**, Probe-A style: a systematic pass over every field G2's classifier
  reads, once initial implementation lands, before calling it done — the same sweep that
  found G5's six gaps, applied here before those gaps get a chance to exist rather than after.
- **Integration-level, real schema, real break** — the bar this scope doc exists to hit:
  build a throwaway test module (or reuse an existing one's fixture path) whose HCL
  references `data.aws_region.current.name` — the pre-break form — and run G2 against the
  **live, real** AWS provider schema (not a hand-written mock). Verified today or once G2
  compares, this must produce a HARD-FAIL `deprecated` finding on `name`, matching the exact
  live fact already confirmed while scoping this doc: `data.aws_region.name` carries
  `deprecated: true` on the currently-resolving AWS provider (6.54.0), while `.region` does
  not. This repo's own modules (`databricks-workspace`, `networking-vpc`) already reference
  `.region`, not `.name` — meaning the fixture only needs to intentionally regress to the old
  form, not invent a break that doesn't exist. Real diff, real provider, no synthetic schema.

## Ordering invariant

G2 is a pre-write gate. Still nowhere near catalog teardown. No implementation starts until
this scope is agreed.
