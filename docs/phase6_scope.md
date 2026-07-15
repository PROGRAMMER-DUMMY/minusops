# Phase 6 scope — the generation pipeline, and catalog teardown (last, conditionally)

Scope document only. No implementation until this is reviewed and agreed — this phase gets more
scrutiny than any prior one, per explicit instruction, because it is the thing every ordering
invariant this session has stated ("catalog teardown stays last, regardless of gate progress")
was protecting. Posture: compliance-carrying product.

## 0. What Phase 6 actually is — two things, not one, and why teardown is a condition, not a step

The G1–G9 taxonomy has always listed Phase 6 as "Generation pipeline / catalog teardown — last."
Read literally, that's one phase with two clauses. It is really two pieces of work in a strict
dependency order:

1. **Build the generation pipeline** — genuine generation-time, research-grounded HCL authoring,
   the thing `docs/g2_scope.md` already named as "the generation-time-authoring pivot's eventual
   endgame... that engine doesn't exist yet." This is new capability, not a refactor of existing
   capability.
2. **Tear down the 16-module catalog** — only once (1) is proven to be at least as safe and at
   least as capable as what it replaces, using the *current* catalog as the regression baseline.

Teardown is not a milestone that happens "after Phase 6 starts" — it is a **condition on Phase 6
ending**, gated on a real proof bar (section 5). Building the pipeline and never being able to
prove parity against the current catalog is a legitimate, disclosed outcome of this phase; it is
not required to end in teardown, and this scope does not assume it will.

## 1. What generation actually does today — verified against the real code, not the old plan

This section exists because the instruction was explicit: scope against the current synthesizer/
module code, not the plan as originally imagined. Read directly, not from memory:

### 1.1 The real call chain

`core/generation/synthesizer.py:synthesize()` is the entry point:

1. `reqgate.require(spec)` / `archdec.require(decision)` — **fail-closed gates**, not advisory.
   Without a complete `requirements.json` (`core/architecture/requirements.py`, gathered by the
   `grill-me` interview) and a complete `architecture_decision.json` (`core/architecture/
   architecture_decision.py`), `synthesize()` raises and refuses to run, unless the caller passes
   the explicit, audited `allow_incomplete=True` override (logged to the same audit chain
   `plan_gate.py` writes to — `_audit_allow_incomplete_bypass`).
2. `architecture_decision.json` is a **human-authored research record** — read directly:
   `"Requirements say what must be built. This record says why a particular architecture and
   module set was selected after research. Production synthesis is bound to this file so keyword
   matching cannot silently become a recommendation engine."` `selected_modules` in that record
   must reference real, existing module IDs from the fixed registry (`synthesize()` raises
   `ValueError` on any unknown id). **There is no automated research step today** — a human (or
   an agent standing in for one) writes this file; `synthesize()` only enforces that it exists
   and is complete, never that its content is *correct*.
3. `select_modules()` → `module_registry.match_modules()` (`core/generation/modules.py`): a
   **keyword-overlap scorer** over a hardcoded, 16-entry `MODULES` list (`satisfies` phrases,
   title, services tokens vs. the free-text requirements string) — deterministic, explainable
   (every match records which phrase matched and why), but a fixed catalog lookup, not a
   generative or retrieval step in any AI sense. `explicit_ids` (from `architecture_decision.
   json`'s `selected_modules`) overrides this scoring entirely in production use — matching is
   really only exercised for the demo/`--allow-incomplete` path.
4. `compose()` — for every selected module: `shutil.copytree(modules/<id>/, out_dir/modules/<id>/)`,
   a **byte-for-byte copy of already-pinned, human-authored Terraform**. No HCL is generated,
   templated from resource attributes, or synthesized by anything resembling an LLM anywhere in
   this path. Root-level glue (`versions.tf`, `providers.tf`, `variables.tf`, `main.tf`) is
   rendered from fixed string templates in `synthesizer.py`, with cross-module wiring
   (`_module_args()`) as an explicit if/elif ladder keyed on **specific, named module-ID pairs**
   (e.g. `has_storage and module_id == "compute-glue-etl"` → wire `script_s3_bucket`). Every input
   this ladder doesn't recognize becomes a literal `# REVIEW: set <input>` comment — a human
   completes it before the deploy gate will meaningfully validate anything beyond syntax.
5. `module_provenance.py:pin(module_id, ...)` is the **one and only** point today where new or
   edited HCL enters the trusted set — a maintainer hand-writes/edits `modules/<id>/main.tf`,
   pins it (records a content hash + provenance), and G2 (`core/generation/schema_lint.py`)
   gates *that* action against the live provider schema. `compose()` never calls `pin()` and
   never writes new module-level HCL; it only copies what's already been pinned.
6. `plan_gate.py`'s deploy gate (G1 `terraform validate`/`fmt`, the regex SEC-*/COST-* scan, G5
   destructive-change classification, G6 Rego shadow evaluation, Phase 4 intent assertions, BCM
   cost estimation, plan-hash-bound approval) runs against whatever `compose()` produced, exactly
   as it would against hand-written Terraform — this part is already generic, not tied to the
   16-module catalog's mechanism, and is unaffected by anything this phase changes.

### 1.2 What this means, stated plainly

**Today's "generation" is selection and assembly of pre-vetted, human-authored content — not
authoring.** Every module's actual resource configuration was written and reviewed by a human
before it was ever pinned; `compose()`'s job is picking which pre-approved pieces to include and
wiring their known-shape inputs/outputs together. This is why G2 (schema lint) only needs to gate
`pin()` — that is the only point where genuinely new HCL content is trusted. It is also why the
autonomy boundary (G5) and the security rules (G6) have been provably safe to test and reason
about all session: the space of possible generated output is exactly the 41 resource types this
repo's 16 modules already declare, enumerated, grepped, and checked directly, over and over, all
session. **Generation-time authoring changes that premise entirely** — see section 3.

## 2. What the generation pipeline must become

Concretely, replacing step 3–5 above (fixed-catalog selection + verbatim copy) with genuine
authoring, while keeping steps 1, 2, and 6 (which are not catalog-specific) intact:

### 2.1 Research (replaces `match_modules()`'s role, keeps `architecture_decision.json`'s discipline)

`match_modules()`'s keyword-overlap scoring is real, working, explainable code — it does not need
to be discarded, it needs to be **repurposed from a final-selection mechanism into a
retrieval-for-grounding mechanism**: given a requirements record, retrieve the most relevant
*existing* modules/resources as reference examples (real, human-reviewed patterns for wiring a
given AWS service, real known-good attribute shapes, real cross-module wiring precedent) for
whatever authors the new HCL to ground against — not to determine 1:1 what gets emitted. This is
architecturally identical to a RAG retrieval step; `match_modules()`'s scoring logic is a
legitimate, reusable retrieval ranking function for this purpose without needing to be rewritten
from scratch.

`architecture_decision.json`'s own discipline — "keyword matching cannot silently become a
recommendation engine," a human-reviewable record of what was decided and why, before anything
is written — **must survive unchanged in spirit**, whatever authors the HCL. If an LLM or agent
does the research, its output is still a *decision record for a human to review*, not a
silent, un-auditable jump straight to generated Terraform. `selected_modules` (currently
required to reference the fixed catalog) needs to generalize to describe *novel* resource
choices too — this is real design work this scope does not resolve here, only names as required:
the decision record's shape must be able to express "a new resource type/pattern, here's why,
here's what was considered instead" with the same rigor it already requires for catalog picks.

### 2.2 Author (replaces `compose()`'s `shutil.copytree`)

Whatever authors HCL at this step is producing genuinely new content — the load-bearing
difference from today. This scope does not prescribe the authoring mechanism (a specific LLM,
prompt design, or templating engine is implementation, not scope) but does require, as
non-negotiable properties carried over from every other phase's own discipline:

- **Fail-closed on its own output being unparseable or structurally unrecognizable** — the same
  posture G2/G5/G6/G9 all already take on malformed *input*; freshly-authored HCL that doesn't
  parse, or that plan_reader.py's shared reader can't classify, must never be silently treated as
  "nothing to check."
- **Every new resource type or pattern must be checked against the live provider schema before
  being trusted**, the same job G2 already does at `pin()` time — this is the point where G2's
  own scope doc named its future extension ("when live generation-time authoring exists, the same
  check extends to whatever produces HCL at that point too") as coming due. This is real,
  required Phase 6 work, not optional.
- **Provenance stays real, not decorative.** `source_guard.py`'s hash-baseline mechanism is
  already generic (works on any file tree) and should wrap freshly-authored output the same way
  it wraps composed output today — a record of exactly what was generated, at what point, so
  later drift/hand-editing is still detectable.

### 2.3 Validate + the gate gauntlet (mostly unchanged, one real new requirement)

G1 (`terraform validate`/`fmt`), G5 (destructive-change classification), G6 (Rego shadow
evaluation), Phase 4 (intent assertions), and BCM cost estimation are **not catalog-specific
today** — they operate on `terraform show -json` plan output regardless of where the HCL came
from, and require no changes to run against generation-time-authored output. **G9 is the one
gate this pipeline needs to newly wire in, not just newly build**: `ephemeral_apply.py` today
runs only as a standalone CI job against fixed, hand-written test fixtures (confirmed directly —
neither `plan_gate.py` nor `synthesizer.py` imports or calls it anywhere). For G9 to mean
anything for *generated* infrastructure specifically, `stage_plan()`'s pipeline (or an equivalent
new stage) needs an actual call path to it, per-request, not just per-CI-run against this repo's
own modules. This is real, scoped, un-built work — named here, not assumed already covered by
G9's existing CI proof.

### 2.4 Verdict (unchanged mechanism, newly load-bearing)

The autonomy boundary already exists and already produces a verdict — `destructive_change_gate.
classify()`'s `autonomous_eligible` bit, `plan_gate.py stage_apply --mode auto-approve`'s
enforcement of it. This mechanism does not need to be rebuilt. **Section 4 is the reason it
cannot be trusted as-is once its input stops being limited to this repo's known 41 resource
types** — read that section before assuming "the verdict mechanism already works."

## 3. The catalog-teardown dependency map — verified against current code, not the original plan

| Component | Disposition | Why |
|---|---|---|
| `synthesizer.compose()`'s `shutil.copytree` copy-path | **Dies** | The entire reason it exists — copying pre-pinned, human-authored files verbatim — has no role once HCL is authored per-request. |
| `synthesizer._module_args()`/`_render_main()`'s hardcoded module-ID wiring ladder | **Dies** | Keyed on specific, named module-ID pairs from the fixed 16-module catalog (`if has_storage and module_id == "compute-glue-etl"`); does not generalize to novel resource combinations. Cross-module wiring in the new world has to be resolved by whatever authors the HCL, informed by retrieved examples (2.1), not a static if/elif ladder. |
| `modules.py`'s `MODULES` list + `match_modules()` | **Repurposed** | The 16-entry catalog and its keyword-overlap scorer become the retrieval/grounding corpus and ranking function (section 2.1) — not deleted, not the final-selection authority anymore. |
| `module_provenance.py`'s `pin()`/`verify()` | **Repurposed or retired, genuinely unresolved here** | `pin()`'s job — gate genuinely-new HCL content against the live schema before trusting it — is exactly what section 2.2 requires for freshly-authored output too. Whether that becomes "every generation-time output goes through the same `pin()`-shaped check" or a new, differently-shaped check is real design work not resolved by this scope; named as open, not silently assumed either way. |
| `accelerators.py` (`lakehouse_requirements`/`lakehouse_decision`/`write_lakehouse`) | **Repurposed, narrower than it sounds** | This is a single hardcoded demo/quick-start preset (one scenario, "aws-lakehouse"), not a general blueprint engine — it pre-fills a `requirements.json`/`architecture_decision.json` pair and calls `synthesize()` the same as any other path. Its *concept* (a fast, canned starting point for a common scenario) can survive as a convenience layer in front of the new pipeline; its *mechanism* (one hardcoded scenario function) does not generalize and was never meant to. |
| `requirements.py` (`grill-me` interview, FR/NFR schema, the data-pipeline `DATA_FR`/`DATA_NFR` profile) | **Survives unchanged** | Requirements-gathering is upstream of and independent from how HCL gets authored. |
| `architecture_decision.py` (the human-research record + its fail-closed gate) | **Survives, generalizes** | See 2.1 — the discipline survives; the schema needs to grow to describe novel choices, not just catalog picks. |
| `source_guard.py` (hash-baseline drift detection) | **Survives unchanged** | Already file-tree-generic, not catalog-specific. |
| `runs.py` (workspace/run management), the audit chain, RBAC/`authz.py` | **Survive unchanged** | None of these are catalog-specific. |
| G1 (`tf_validate.py`), G5 (`destructive_change_gate.py`), G6 (`rego_gate.py`/`rules.rego`), Phase 4 (`intent_assertions.py`), BCM cost estimation | **Survive, but see section 4 for real, load-bearing caveats on G5/G6 specifically** | Operate on plan JSON, not on where the HCL came from — but their *coverage* was proven against, and in G5's case explicitly scoped to, the current 41-type catalog. |
| G2 (`schema_lint.py`) | **Repurposed** | Currently gates only `pin()`. Needs a real, designed extension to gate generation-time output too (2.2) — not a mechanical port, since there's no `pin()`-equivalent checkpoint in a per-request authoring flow yet. |
| G9 (`ephemeral_apply.py`) | **Survives, needs real wiring** | The mechanism (allowlist, fail-closed on unverified types, the proven Firecracker isolation boundary) is sound and stays — it has simply never been connected to anything but its own CI self-test. Section 2.3. |
| `modules/*/main.tf` themselves (the 16 real Terraform modules) | **Dies, conditionally** | Only once the new pipeline is proven capable of reproducing (or improving on) what each of these 16 modules provides — see section 5. Not deleted as a matter of course; deleted as the *last* step once that proof exists. |

## 4. THE KEY QUESTION — are the built gates sufficient for the autonomy boundary, once generation moves past the fixed catalog?

**No, not as they stand today** — this is a real, code-grounded finding, not a hedge. Verified
directly against each gate's actual implementation, not its intent:

### 4.1 G5's autonomy boundary is fail-OPEN on any resource type it hasn't been told about — the single most severe gap

> **SUPERSEDED — closed in Step 0 (2026-07-14).** This section describes the PRE-fix
> architecture, kept verbatim below for history. `AUTO_SHIP_ELIGIBLE_TYPES` was added as the real
> gate; `STATEFUL_RESOURCE_TYPES`/`IAM_RESOURCE_TYPES` were demoted to annotation-only. A type in
> neither danger set AND not in `AUTO_SHIP_ELIGIBLE_TYPES` now correctly routes to
> `unreviewed_resource_type` → `autonomous_eligible=False`, proven with a real
> `dynamodb_table` fail-before/pass-after test, CI green. **Do not read this section as live
> state** — see `core/governance/destructive_change_gate.py`'s own docstring (2026-07-14 entry)
> and `docs/phase7_generation_engine_plan.md`'s Banked section for the current, verified
> behavior.

`destructive_change_gate.py`'s own docstring is explicit about this scope: *"Scoped deliberately
to what MinusOps' own 16 modules can actually produce today... not a general-purpose
cloud-resource classifier. Extend this list when a new module introduces a new data-bearing or
catastrophic-blast-radius resource type."* Read the actual classification logic
(`classify()`, lines ~125–153): a resource type is flagged **only** if it is a member of
`STATEFUL_RESOURCE_TYPES` (11 named types) or `IAM_RESOURCE_TYPES` (2 named types) — a resource
type not in either frozenset produces **no finding at all**, and if its action is `["create"]`,
it is `autonomous_eligible`. This is an **allowlist-of-danger**, which is fail-**open** by
construction: anything not recognized is implicitly safe. This is the exact opposite of every
other gate's own fail-closed posture this session established as non-negotiable (G2 blocks on
unknown attribute/type, G6 blocks on unresolved fields, G9 blocks on an unreviewed resource type)
— and it is the **one gate standing directly between "generated" and "auto-shipped to real
AWS."** The moment generation-time authoring can produce a resource type this repo's fixed
catalog never used — `aws_dynamodb_table`, `aws_rds_cluster`, `aws_secretsmanager_secret`,
`aws_elasticache_replication_group`, `aws_lambda_function` with broad `iam:PassRole` implications,
`aws_ecs_service`, any of hundreds of real AWS resource types — a genuinely stateful, costly, or
sensitive net-new resource, correctly plan-shaped as `["create"]`, sails through as
`autonomous_eligible` with zero findings, purely because the classifier has never heard of it.
**This is not a hypothetical edge case; it is the default outcome for every resource type outside
today's 41, and generation-time authoring's entire purpose is to produce resource types outside
today's 41.**

*What this requires, named as real Phase 6 work, not solved here*: G5's classifier needs to
invert its default — an unrecognized resource type must route to the staged path, not
auto-ship, the same fail-closed shape G9's `RESOURCE_TYPE_ALLOWLIST` already uses successfully.
`STATEFUL_RESOURCE_TYPES`/`IAM_RESOURCE_TYPES` would need to become either (a) a maintained,
reviewed allowlist of resource types confirmed *safe* to auto-ship (inverting the whole list's
direction), or (b) supplemented by a live schema-driven heuristic (e.g., any resource type whose
provider schema shows an attribute suggesting persistent state, or any type in a
security-sensitive AWS service namespace) reviewed and rejected/accepted case by case — a real
design decision this scope surfaces but does not make.

### 4.2 G6's coverage is enumerated per-resource-type, AND it never blocks anything regardless

Two separate, compounding gaps, both real:

- **Coverage is exactly as wide as the rules that have been written.** SEC-01/COST-01 (S3),
  SEC-03 (Redshift), SEC-04 (MSK), COST-02 (Databricks cluster), COST-03 (EMR), and this
  session's own SEC-06 (KMS)/SEC-07 (S3 bucket policy) additions are each scoped to a **named
  resource type**. A freshly-generated `aws_dynamodb_table` with no encryption or point-in-time
  recovery configured, or an `aws_rds_instance` with `publicly_accessible = true`, has **no rule
  checking it at all** — not a false negative from a buggy rule, an absent rule. (The IAM-content
  rules — SEC-02, SEC-05, and this session's extensions — are the one meaningful exception: they
  apply to *any* `aws_iam_role`/`aws_iam_policy`/`aws_iam_role_policy`/`aws_kms_key`/
  `aws_s3_bucket_policy` resource regardless of what other AWS resource it's attached to, so they
  generalize better than the type-specific rules — worth noting as a real, disclosed asymmetry
  within G6 itself, not assuming uniform coverage.)
- **Every G6 rule, old and new, is shadow-only.** Even a rule that *does* exist and correctly
  fires produces a finding that is logged and printed — never blocking. Sections 4.1's fail-open
  autonomy gap and this shadow-only posture compound: for the narrow auto-ship path, G6 provides
  **zero actual protection today**, regardless of rule coverage, because it cannot block that
  path at all.

### 4.3 G9 is fail-closed and well-designed, but disconnected from the real flow, and AWS-only

The one genuinely reassuring finding in this section: `RESOURCE_TYPE_ALLOWLIST`'s design (block
on any unreviewed resource type, mandatory negative-fidelity for security-critical types) is
already the correct shape — the fail-closed-on-unknown posture G5 needs and doesn't have. But
confirmed directly (grep across `plan_gate.py` and `synthesizer.py`): **nothing calls
`ephemeral_apply.py` outside its own CI workflow.** It has never verified a single piece of this
repo's own *generated* output, only its own hand-written test fixtures. Wiring it in (section
2.3) is real, scoped, necessary work, not a formality. Structurally AWS-only regardless
(LocalStack has no Databricks emulation — `reduced_assurance` already names this asymmetry), and
its own fidelity matrix remains genuinely unverified for IAM/KMS/S3 on both free emulators and
entirely unverified on LocalStack (paid, unprovisioned) — a real, pre-existing, still-open
limitation this phase inherits, not one it introduces.

### 4.4 Phase 4 and G1 are not safety gates and were never meant to be

Named explicitly so neither is mistaken for covering this gap: Phase 4's intent assertions check
"did the generated output honor what was declared in requirements/architecture-decision" (module
presence, numeric declarations) — a **fidelity-to-intent** check, not a safety check; a
perfectly-intended, perfectly-matching, catastrophically-configured resource passes it cleanly.
G1 (`terraform validate`) checks **type-system syntactic validity** — a perfectly-typed
wide-open S3 bucket policy validates without complaint. Neither gate's job is the one this
section is asking about, and neither should be read as filling G5/G6's gap.

### 4.5 The honest summary

**Today's gates are sufficient for the catalog they were built against and provably insufficient
for the thing generation-time authoring exists to produce.** This is not a reason to abandon
Phase 6 — it is the reason G5's fail-open default (4.1) is this scope's single highest-priority
required fix, ahead of the authoring mechanism itself: shipping a generation engine that can
author genuinely new resource types *before* fixing the classifier that decides what auto-ships
would be building the exact vulnerability this whole gate stack exists to prevent. **G5's
fail-open-on-unknown-type gap must close before generation-time authoring produces its first
real, novel resource type outside this scope's own review** — a prerequisite for the *authoring*
work in section 2.2, not a parallel or follow-on task.

## 5. Teardown's real proof bar — the regression baseline, made concrete

Teardown is not "the new pipeline seems to work, delete the old modules." A specific, checkable
condition:

1. **Every one of the 16 real modules' actual capability is reproducible** by the new pipeline —
   concretely, for each module: given the same requirements record that currently selects it, the
   new pipeline authors a configuration that (a) plans clean (`terraform validate` + a real
   `plan`), (b) passes the exact same real proof each module already has on record this session
   (G5's 16/16 create-only baseline, G6's real per-module plan parity, G2's schema-lint pass),
   and (c) is not a regression in capability — every real output/input each module currently
   `provides`/requires is still satisfiable.
2. **G5's fail-open gap (4.1) is closed** before this proof bar is attempted, not after — running
   the regression proof against a classifier that would silently wave through a newly-generated
   stateful resource type defeats the point of calling it a proof.
3. **G9 is wired into the real flow (2.3)**, and at minimum re-run against every reproduced
   module's output, not just this repo's own static fixtures.
4. Only after (1)–(3): the 16 `modules/*/main.tf` directories, `modules.py`'s `MODULES` list (in
   its old final-selection form), and `synthesizer.compose()`'s copy-path are removed. `pin()`/
   `module_provenance.py` retire or transform per section 3's still-open design question, not
   before it's actually answered.

## Ordering invariant

Phase 6 is last, and stays two-part internally: build and prove the generation pipeline first,
including closing section 4.1's fail-open gap as a prerequisite to authoring anything novel;
tear down the catalog only once section 5's regression-baseline proof bar is met. No
implementation starts on either part until this scope is reviewed and agreed. G6 stays shadow
(both its original rules and this session's extension) pending its own separate, still-open
enforcement-flip decision, unaffected by this phase. G9's LocalStack fidelity column stays
unverified pending a provisioned account, a permanent disclosed limitation independent of this
phase's own work. Nothing in this document authorizes writing code.
