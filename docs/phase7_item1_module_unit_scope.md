# Phase 7 Item 1 scope — extending `authored_content` to a module-shaped unit

Scope document only. No implementation until this is reviewed and agreed —
`docs/phase7_generation_engine_plan.md` names this as the first, prerequisite piece of work; this
is that item scoped concretely, against the real current code, before any of it is built.

## 0. The problem, stated against the actual mechanism

`synthesizer.compose(authored_resources=...)` (`core/generation/synthesizer.py:297-411`) writes
each entry as a single flat file, `authored_{resource_type}.tf`, directly at the composition root.
Whatever `var.x`/`local.x` references that content contains resolve against the ROOT's own
declarations — `compose()`'s hardcoded `_VARIABLES`/the `locals {}` block `_render_main()` always
writes. This works precisely because today's only real callers (Step 1's design target, the Step 5
regression harness) author single, standalone, zero-extra-input resources. It breaks the moment
authored content needs its own declared inputs, its own outputs for something else to reference,
or a companion non-HCL asset resolved via `path.module` — because there is no module boundary for
`path.module` to resolve against, and no variable/output namespace of its own. The Step 5 harness
proved this concretely, twice:

- It had to hand-carry the module's own `variable`/`locals` blocks as *separate root-level files*
  outside the mechanism under test (`tests/test_teardown_regression_harness.py:267-274`,
  `_extract_locals_blocks`), filtering out only the names `compose()` already declares
  (`_COMPOSE_STANDARD_VARIABLES`) to avoid duplicate-declaration errors.
- Two modules (`compute-glue-etl`, `compaction-glue`) could not be reproduced through the
  authored path AT ALL: `aws_s3_object.script`'s `filemd5("${path.module}/scripts/etl.py")`
  resolves `path.module` to the composition root in the flat-file shape, where the script was
  never copied — named as a real, structural blocker
  (`tests/test_teardown_regression_harness.py:290-309`), not a test bug.

Every real catalog module keeps its own `variable`/`output` declarations in the same `main.tf` as
its resources (confirmed: `modules/compute-glue-etl/main.tf` declares 7 variables and 3 outputs
alongside its resource blocks, no separate `variables.tf`/`outputs.tf` file). The gap is not that
authored content needs multiple files — it's that it needs its own **directory**, so Terraform's
own module-scoping rules (a fresh variable/output/local namespace, `path.module` resolving to that
directory) apply to it, the same way they already apply to every catalog module.

## 1. What must stay true — backward compatibility, not a rewrite

`authored_content`'s existing contract (`dict[str, str]`, one resource/data blob per type,
composed as a flat root file) has real callers today: `test_synthesizer.py`'s five authored-
content tests, the Step 5 regression harness, and `_validate_novel_resources()`'s existing
fail-closed checks (missing content → block, zero HCL blocks → block, G2 blocking → block). None
of this should change for a caller with a simple, standalone, no-extra-input resource — that case
needs no module boundary and shouldn't be forced to declare one. **This is an extension, not a
replacement**: the flat, single-string shape stays valid and stays the default reading of a plain
`str` value.

## 2. Proposed shape — two forms, distinguished by value type

`authored_content`'s values gain a second legal shape. Per entry, keyed the same way as today
(by resource type, or a caller-chosen unit key for the module form):

- **A plain `str`** (unchanged): today's contract exactly. One resource/data blob, composed as
  `authored_{key}.tf` at the composition root, sharing the root's `variable`/`locals`
  declarations directly. No behavior change for any existing caller.
- **A `dict` describing a module-shaped unit** (new): a single HCL text body (resources, its own
  `variable`/`output`/`locals` blocks, exactly like a real catalog module's `main.tf` — no need to
  split into separate files internally, matching the corpus's own actual convention) plus an
  optional map of companion asset files (relative path → text/bytes content). Composed into its
  own subdirectory (`authored_modules/<unit_key>/main.tf`, plus asset files at their declared
  relative paths under that same directory), wrapped in a real `module "authored_<unit_key>" {
  source = "./authored_modules/<unit_key>" ... }` block — giving it exactly the same `path.module`
  resolution and variable/output isolation every catalog module already has.

## 3. The open design question this scope surfaces, not resolves: call-site wiring

A module-shaped unit that declares its own `variable`s needs those variables given real values at
the call site — the same job `_module_args()` (`synthesizer.py:87-151`) already does for catalog
modules, via a hardcoded if/elif ladder keyed on specific, named module IDs. That ladder cannot
extend to an arbitrary, caller-defined unit key; there is no fixed catalog to key off of. Two
honest options, not resolved here:

- **(a) The caller supplies input values explicitly**, alongside the unit's own HCL — e.g. a
  `module_args: {var_name: hcl_expression}` entry in the same dict, the caller (today: the Step 5
  harness's own logic; eventually: whatever authors the unit) stating exactly what each declared
  variable should resolve to, the same way `_module_args()`'s ladder does today but supplied
  per-call instead of hardcoded per-module-ID.
- **(b) A convention-based auto-wire for the handful of common root values** (`name_prefix`,
  `tags`, `owner`, `run_id`) — any variable in the unit matching one of these well-known names gets
  the corresponding root value automatically, exactly the shape `_module_args()` already applies
  to catalog picks (`args = {"name_prefix": ..., "tags": ...}` before any module-specific logic
  runs) — with anything else left as an explicit `# REVIEW:` placeholder, matching
  `_render_main()`'s existing review-comment convention for unfilled catalog module inputs.

(b) is more consistent with the composition path's existing behavior (every catalog module already
gets these auto-wired, unfilled inputs already surface as `# REVIEW:` comments rather than blocking
composition) and requires no new field in the authored-content contract. (a) is more explicit and
gives a future authoring step full control without inferring intent from variable names. Recommend
(b) as the default with (a) available as an override for anything not name-matched — but this is a
real judgment call for review, not decided by this document.

## 4. G2 / fail-closed handling — what extends, what doesn't need to

`schema_lint.iter_hcl_blocks()` (`schema_lint.py:69-74`) already matches only top-level
`resource`/`data` blocks by regex — a `variable`/`output`/`locals` block in the same text is
already invisible to it and passes through unaffected. **No change needed to `gate_content()`
itself**: the module-shaped unit's resource/data content goes through the exact same G2 check
today's flat form already gets; its variable/output/locals blocks carry no schema-content risk G2
exists to check.

One real, new fail-closed check this extension requires, directly targeting the harness's two
named blockers: **any `path.module`-relative reference in the unit's HCL must have a matching
entry in its companion-assets map, or composition must block** — not silently write a broken
reference the way a hand-authored workaround might paper over. This is new logic, not an extension
of an existing gate; it belongs in `_validate_novel_resources()` alongside its existing checks
(missing content, zero blocks, G2 blocking), same fail-closed posture, same place.

## 5. Proof bar

The Step 5 regression harness (`tests/test_teardown_regression_harness.py`) already measured the
exact bar: re-run `compute-glue-etl` and `compaction-glue` through the extended mechanism using
its **module form**, with the real `scripts/etl.py`/`scripts/compact.py` content supplied as
companion assets — not the harness's own hand-carried-file workaround. Success means both modules
move from `_NEW_PATH_KNOWN_BLOCKERS` to the passing parametrize list, with the harness's own
plan-equivalence assertion (`_plan_signature` match) holding for real, unmodified. `table-format-
iceberg` (the third named blocker) is explicitly NOT expected to close by this work — its blocker
is G2's own dynamic-block limitation (item 5 in the survey's derived list, a separate, larger
question), unrelated to the module-boundary gap this item addresses.

The two currently-passing paths (`networking-vpc`, `orchestrator-stepfunctions` excluded — they
can't plan standalone at all, unrelated to this) must keep passing unchanged — this is an addition
to `authored_content`'s contract, not a rewrite of its existing flat-string behavior.

## Ordering invariant

No implementation starts until this scope is reviewed and the wiring-question (section 3) is
decided. Once agreed: extend `_validate_novel_resources()` and `compose()` to recognize the dict
form, add the path.module/asset fail-closed check, then re-run the Step 5 harness's two named
blockers as the acceptance test — in that order, so the proof bar is checked against the real
mechanism, not a re-derived one.
