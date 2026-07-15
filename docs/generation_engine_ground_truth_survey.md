# Generation-engine ground-truth survey (2026-07-15)

Read-only survey. No code changed as a result of this document. Purpose: map what exists in the
repo today that a future HCL-generation engine — the one capability the Phase 6 pivot set out to
build and, per `HANDOFF.md`'s own final-status ledger, never built — would actually plug into.
Every claim below is verified against the current code or real command output, not against
HANDOFF prose, comments, or memory of prior sessions. Where a fact couldn't be confirmed, that is
stated explicitly rather than filled in.

## 1. The authoring seam — what would a generator actually plug into?

### `synthesizer.synthesize(authored_content=...)`'s exact contract

Signature (`core/generation/synthesizer.py:530-532`):

```python
def synthesize(requirements_text, spec=None, decision=None, allow_incomplete=False,
               name_prefix=None, explicit_ids=None, owner="data-platform", cloud="aws",
               target_run=None, overwrite=False, validate=False, authored_content=None):
```

`authored_content` is a flat `dict[str, str]`: key = a resource type string (e.g.
`"aws_dynamodb_table"`, or `"data.aws_iam_policy_document"` for a data source — the type
optionally prefixed with `data.` to disambiguate resource vs. data blocks of the same name),
value = the complete, literal HCL text for that one resource/data block. This is **one entry per
resource TYPE**, not per file, not per module — there is no concept of a multi-resource,
parameterized "authored module" anywhere in this mechanism.

The actual validation path is `_validate_novel_resources(decision, authored_content)`
(`synthesizer.py:467-527`), called unconditionally inside `synthesize()` (line ~563) regardless
of `allow_incomplete`. For each entry in `decision["novel_resources"]`:

1. `resource_type = entry.get("resource_type", "")`; `content = authored_content.get(resource_type)`.
   **If `content is None` → hard `ValueError`** (line 502-507): a declared novel-resource intent
   with no matching authored HCL blocks synthesis outright, before a run workspace is even
   created. This is what keeps `novel_resources` a human-reviewed record, not a trigger that
   silently invents content.
2. **If `schema_lint.iter_hcl_blocks(content)` finds zero resource/data blocks → hard `ValueError`**
   (line 509-513): garbage/empty authored output blocks.
3. **`schema_lint.gate_content(content, source_label)` (G2) runs; if `blocking` → hard `ValueError`**
   (line 514-519) quoting the exact findings. A hallucinated/nonexistent type surfaces here as
   `unknown_type`.
4. Only content that survives all three becomes an `authored_resources` entry: `{resource_type,
   content, justification, decision_source, content_hash}` (lines 520-526).

`compose()` (`synthesizer.py:297-359`) then writes each surviving entry as its own flat file at
the **composition root** — `authored_{resource_type}.tf` (line 359) — never inside a `modules/`
subdirectory, never wrapped in a synthetic child module. `compose()` itself never lints; the
docstring is explicit that content arriving here is "already lint-checked by the caller."

### The minimal real example, walked end-to-end

To compose HCL with zero catalog modules selected, a caller must:

1. Build a `decision` dict satisfying `architecture_decision.validate()`'s completeness bar
   (`selected_architecture`, `decision_summary`, `alternatives`, `assumptions`, `risks`,
   `sources` each non-empty) with `selected_modules: []` and a `novel_resources` list, each entry
   `{resource_type, justification, alternatives_considered}` (`architecture_decision.py:94-103`
   is the completeness check).
2. Supply `authored_content = {resource_type: "<real HCL text>"}` for every declared
   `novel_resources` entry.
3. Call `synthesizer.compose([], name_prefix, out_dir, authored_resources=<validated list>)`
   directly, or `synthesize(..., authored_content=...)` — the latter still calls
   `select_modules()` internally, which (since `explicit_ids`/`decision_module_ids` would be
   `[]`, falsy) falls through to `module_registry.match_modules(requirements_text)` and
   auto-adds `governance-observability` (`synthesizer.py:67-80`) — meaning **a pure zero-catalog
   composition is only cleanly achievved by calling `compose()` directly**, bypassing
   `select_modules()`, exactly the workaround `tests/test_teardown_regression_harness.py` had to
   use (confirmed by re-reading that file's own `_new_path_plan()`, which calls
   `synthesizer._validate_novel_resources()` + `synthesizer.compose([], ...)` directly rather
   than `synthesize()`, specifically to avoid this auto-selection side effect).
4. `compose()`'s own "at least one thing composed" guard (line 308) requires `chosen or
   authored_resources` — an authored-only composition with no catalog picks is supported at this
   level.

### What the authored path does NOT support (structural gaps, not opinions)

Enumerated from real, reproduced failures (the Step 5 regression harness), not speculation:

- **No companion non-HCL asset files.** `compute-glue-etl`'s and `compaction-glue`'s
  `aws_s3_object` resources reference `filemd5("${path.module}/scripts/etl.py")` /
  `.../compact.py` (both confirmed at `modules/compute-glue-etl/main.tf:88-89` and
  `modules/compaction-glue/main.tf:88-89`). In the flat-root authored shape, `path.module`
  resolves to the composition root — there is no module subdirectory, and the script file is
  never copied. Reproduced live: `terraform plan` fails with `Call to function "filemd5" failed:
  open scripts\etl.py: The system cannot find the path specified.`
- **No genuinely dynamic blocks survive G2.** `schema_lint.py`'s `_scan_body()` treats every
  `dynamic "..." { ... }` block as `unparseable_reference` by design (the block's real emitted
  attributes depend on evaluating its `for_each`, not statically resolvable) — this is not
  specific to authored content, it is G2's general contract, but it means any authored resource
  needing a dynamic block (e.g. `table-format-iceberg`'s `dynamic "columns"`,
  `modules/table-format-iceberg/main.tf:52-58`) is unconditionally rejected at step 3 above.
- **No multi-resource, parameterized "module" concept.** `authored_content` is keyed one entry
  per resource type; there is no `variable`/`output` boundary, no reusable input contract. A
  module needing its own `variable` declarations (every real catalog module does) must have
  those variables' names/values resolved some other way — the mechanism itself carries no
  provision for declaring or supplying them. (Confirmed operationally: building the regression
  harness required manually carrying a module's own `variable`/`locals` blocks over as
  *separate*, hand-written root-level files outside the `authored_content` mechanism entirely —
  not something `synthesize()`/`compose()` does on the caller's behalf.)
- **No live-account-dependent resource types can be authored and planned standalone either.**
  This isn't specific to authoring — `networking-vpc`'s `data.aws_availability_zones` and
  `orchestrator-stepfunctions`'s `aws_sfn_state_machine` (which triggers a real
  `ValidateStateMachineDefinition` AWS API call as a provider-side plan-time side effect of that
  specific resource type — confirmed via real `terraform plan` output during this session's own
  work, not visible as an HCL-level dependency) both fail to plan under dummy credentials
  regardless of which path produced the HCL.
- **No cross-referencing convention between multiple authored resources is enforced or
  generated.** Multiple `novel_resources` entries land as separate root-level files that CAN
  reference each other via ordinary Terraform addresses (confirmed structurally: they share one
  root module, so `aws_iam_role.x.arn` is addressable from any sibling authored file) — but
  nothing in the mechanism verifies, wires, or even encourages such references; an authoring
  process would have to get this right entirely on its own.

## 2. What grounding material already exists

### `match_modules()` / `retrieve_grounding_examples()` (`core/generation/modules.py`)

`match_modules(requirements, min_score=1)` (lines 247-273): tokenizes free-text `requirements`
and scores each of the 16 `MODULES` entries — `+3` per `satisfies` phrase found as a literal
substring, `+1` per `satisfies`/`services` phrase with only token overlap. Returns the full
module dict plus `score` (int) and `matched` (sorted list of matched phrases), sorted best-first.

`retrieve_grounding_examples(requirements, top_n=3, min_score=1)` (lines added this session,
confirmed present): calls `match_modules()`, takes the top-N, and for each reads
`modules/<id>/main.tf` fresh off disk (no caching). Returns:

```python
{"id": str, "title": str, "services": list[str], "score": int, "matched": list[str], "content": str | None}
```

Ranking is proven byte-identical to `match_modules()`'s own output
(`tests/test_modules.py::test_retrieve_grounding_examples_ranks_the_same_as_match_modules`).

**Call sites, exhaustive**: `match_modules()` is called from `synthesizer.py:75` (real module
selection inside `select_modules()`) and `patterns.py:82` (pattern-reuse Jaccard overlap), plus
its own CLI and unit tests. `retrieve_grounding_examples()` is called **only from its own unit
tests** (`tests/test_modules.py`) — grep confirms zero production call sites. It exists,
functions, and is tested, but nothing in `core/` consumes it yet.

### `MODULES` metadata richness

All 16 entries carry `id`, `category`, `title`, `satisfies` (6-9 short descriptive phrases —
genuinely intent-oriented, e.g. `"bronze silver gold"`, `"unity catalog"`, not just bare
keywords), `services` (AWS/Databricks product names), `inputs`/`provides` (bare variable-name
lists, zero types, zero descriptions). This metadata is a real, usable **index** — enough to
decide *which* module is relevant and *what* its interface's identifier names are — but carries
no attribute types, no HCL shapes, no default values. It is not, by itself, grounding content;
the actual grounding (real HCL) only exists in each module's `main.tf`, reachable via
`retrieve_grounding_examples()`'s file read or direct `module_dir()` access.

### Live-schema query capability

`schema_watch._fetch_schema(provider, workdir)` (`core/generation/schema_watch.py:90-132`) runs
a real `terraform init` + `terraform providers schema -json` and returns the **entire** provider's
schema (`resource_schemas`/`data_source_schemas` for every type that provider has, not one type).
Tested against real AWS in `tests/test_schema_watch.py:403-409`, but only asserting type
*membership*, not attribute contents there.

`schema_lint._reduce_full(schema, used_keys)` (`core/generation/schema_lint.py:329-346`) is the
function that actually produces a full attribute table (dotted paths, type, deprecated flag) for
a given `(kind, type_name)` — via `_walk_attributes()`'s arbitrary-depth recursion. It is real,
callable, and unit-tested directly (`tests/test_schema_lint.py:203-312`) — **but every one of
those tests passes a hand-built synthetic schema dict**, never `_fetch_schema()`'s real output.

**Confirmed: no single function today answers "what attributes does `aws_dynamodb_table` have,
right now, on the live/pinned provider" as a standalone query.** `aws_dynamodb_table` appears
nowhere in `modules/*/main.tf`, so nothing has ever exercised this specific combination. To get
that answer today, a caller would have to compose `_fetch_schema("aws", workdir)` (real
network/terraform call, whole-provider schema) with `_reduce_full(schema, {("resource",
"aws_dynamodb_table")})` themselves — both real, exported functions, never wired together for
this purpose. Inside `gate_content()` (`schema_lint.py:386-525`) the attribute table is computed
at line 436 and used only to classify findings (lines 439-501); it is discarded, never returned
to the caller — `gate_content()`'s return shape is only `{blocking, findings, warnings,
schema_hash}`.

## 3. The intent record

### `architecture_decision.json`'s full current schema

Every field `template()`/`validate()` know about (`core/architecture/architecture_decision.py`),
and what actually happens to each downstream:

| Field | Validated? | Read back downstream | Verdict |
|---|---|---|---|
| `requirements_file` | Yes (non-empty) | No occurrence anywhere else in the repo | Validated, never read back |
| `selected_architecture` | Yes | `synthesizer.py:430` — copied into `minus-generated.json` manifest text only; `app/dashboard_app.py:752` (display) | Read, but only for manifest text/display, never shapes generation |
| `decision_summary` | Yes | `app/dashboard_app.py:758` (display) only | Validated, display-only |
| `selected_modules` | Yes (≥1) | `synthesizer.py:552` drives `select_modules()`; `intent_assertions.py:95` checks it against real plan addresses for drift | **Load-bearing** — the field that actually selects what gets composed |
| `novel_resources` | Optional; each entry checked if present | `synthesizer.py:496-524` — the entire `_validate_novel_resources()` fail-closed path described in section 1 | **Load-bearing** |
| `alternatives` | Yes (≥1 valid) | `app/dashboard_app.py:729` (display) only | Validated, decorative |
| `assumptions`/`risks`/`sources` | Yes (non-empty) | Dashboard display only (`dashboard_app.py:724-726`) | Validated, decorative |
| `decided_by`/`decided_at` | Not validated | No downstream reader found | Unchecked, unread audit metadata |

Of 11 fields, only `selected_modules` and `novel_resources` change what gets built or flag
drift; `selected_architecture` is read once but only for a manifest string; the remaining six
validated fields exist purely as an audited paper trail, never consumed by generation logic.

### `requirements.json` vs. what actually drives generation

`requirements.py`'s template defines `goal`, `system_class`, `stakeholders`, `functional`,
`non_functional` (6 axes: latency/scale/availability/retention/security/budget),
`data_pipeline` (10 fields), `constraints`, `gathered_by`, `gathered_at` — all checked for
completeness by `validate()` (`functional`, `goal`, `system_class`, the 6 NFR axes).

Grepping the three files that could plausibly turn a requirement into infrastructure
(`synthesizer.py`, `terraform_generator.py`, `demo.py`) for any raw field lookup returns **zero
raw dict-key reads in all three**. Confirmed by direct reading:

- **`synthesizer.py`** reads the parsed `spec` dict exactly two ways, both through named helpers,
  never a raw key: `parse_daily_gb(spec)` (line 573, reads `data_pipeline.data_volume` via
  regex extraction inside `requirements.py:169`) and `parse_budget_usd(spec)` (line 574, reads
  `non_functional.budget`, `requirements.py:194`). These two derived numbers thread into
  `compose(daily_data_gb=..., monthly_budget_usd=...)` and genuinely shape output (a module
  variable; conditional governance-module wiring). The free-text `requirements_text` argument
  (not the structured spec) separately drives keyword-based module selection.
  **Not consumed at all, anywhere in synthesizer.py**: `goal`, `system_class`, `stakeholders`,
  `functional`, `non_functional.latency/scale/availability/retention/security`, `constraints`,
  `gathered_by`, `gathered_at`, and 9 of 10 `data_pipeline` fields (`sources`, `storage_zones`,
  `transforms`, `catalog`, `consumption`, `data_quality`, `freshness_sla`, `governance`,
  `orchestration`).
- **`terraform_generator.py`**: grep for `reqgate|requirements|spec` returns **zero matches**.
  This file (its own docstring: "Demo Terraform generator for cached fixture blueprints") has no
  coupling to `requirements.json` whatsoever — confirmed by reading it in full (396 lines). It is
  a single hardcoded template for exactly one blueprint id (`"aws-data-pipeline-standard"`,
  line 34-35 raises `ValueError` for anything else), reading only `inputs["owner"]`,
  `inputs.get("environment"/"region"/"ingestion_mode")`, and `inputs["daily_data_gb"]` — a
  different, much smaller, unrelated `inputs` dict, not a requirements record. It is invoked only
  from `core/generation/demo.py:105` (`terraform_generator.generate(blueprint, inputs, ...)`),
  itself the no-cloud demo path CI's own "No-cloud demo end to end" step exercises — a
  self-contained fixture generator, structurally unrelated to `synthesizer.py`'s real
  requirements/authored-content composition path.
- **`demo.py`**: no requirements-dict read anywhere; one comment string only.

**Net**: of the entire requirements schema, only `data_pipeline.data_volume` and
`non_functional.budget` are load-bearing for what Terraform gets written — both only through
`synthesizer.py`, both only via canonical parsing helpers. Every functional requirement, every
other NFR axis, and nine of ten `data_pipeline` fields are recorded, validated, and otherwise
inert.

## 4. The 16 modules as a corpus

| module_id | resources | resource_types | vars | outputs | locals | dynamic block | for_each/count | path.module asset | other files |
|---|---|---|---|---|---|---|---|---|---|
| databricks-workspace | 15 | 19 distinct (incl. 6 `databricks_*`) | 11 | 6 | no | no | yes (3 optional resources) | no | PROVENANCE.json |
| networking-vpc | 14 | 9 (incl. 2 data) | 7 | 5 | no | no | yes (9 count usages, cidrsubnet math) | no | PROVENANCE.json |
| storage-medallion-s3 | 7 | 8 | 5 | 2 | no | no | yes (for_each on all zone resources) | no | — |
| compute-glue-etl | 6 | 7 | 8 | 3 | no | no | yes | **yes** — `scripts/etl.py` | scripts/etl.py |
| dq-great-expectations | 6 | 8 | 7 | 2 | no | no | no | no | — |
| compaction-glue | 5 | 6 | 7 | 1 | no | no | no | **yes** — `scripts/compact.py` | scripts/compact.py |
| query-athena | 4 | 5 | 5 | 2 | no | no | no | no | — |
| speed-layer-kinesis | 4 | 5 | 7 | 2 | no | no | yes (conditional count) | no | — |
| governance-observability | 4 | 4 | 5 | 2 | yes | no | yes | no | — |
| orchestrator-mwaa | 3 | 4 | 7 | 2 | no | no | no | no | — |
| orchestrator-stepfunctions | 3 | 4 | 5 | 2 | yes (ASL JSON gen) | no | no | no | — |
| ingest-firehose | 3 | 4 | 5 | 2 | no | no | no | no | — |
| compute-emr-serverless | 3 | 4 | 6 | 2 | no | no | no | no | — |
| schema-registry-glue | 2 | 2 | 4 | 2 | no | no | yes | no | — |
| table-format-iceberg | 2 | 2 | 5 | 3 | no | **yes** — `dynamic "columns"` | yes (via dynamic only) | no | — |
| consumption-redshift-serverless | 2 | 2 | 4 | 2 | no | no | no | no | — |

Resource count alone is a poor complexity proxy: `table-format-iceberg` and
`orchestrator-stepfunctions` have only 2-3 resources yet carry the corpus's only dynamic block
and its only nontrivial locals-driven JSON generation, respectively.

**Cross-check against the 5 known regression-harness blockers** — all confirmed independently:
`compute-glue-etl`/`compaction-glue` each reference exactly one `path.module`-relative script
(both at line 88-89 of their respective `main.tf`, both files confirmed present on disk);
`table-format-iceberg` has exactly one dynamic block in the entire 16-module corpus
(`dynamic "columns"`, lines 52-58). For `networking-vpc` and `orchestrator-stepfunctions`
(the two that can't plan standalone at all), the independent survey correctly found no HCL-level
signal explaining why — `networking-vpc`'s cause is visible (`data.aws_availability_zones`, a
live AZ lookup), but `orchestrator-stepfunctions`'s main.tf has no data source resolving live
state analogous to that. **Clarifying this from this session's own real command output** (not
inferable from HCL alone): `aws_sfn_state_machine` triggers a genuine `ValidateStateMachineDefinition`
AWS API call as a provider-side plan-time side effect specific to that resource type — an
AWS-provider behavior, not something expressed as a visible Terraform data-source dependency, and
therefore invisible to a static read of the module's own `main.tf`.

**Simple enough that a generator could plausibly attempt reproduction from a spec** (by the
measured columns: low resource count, no dynamic block, no path.module asset):
`consumption-redshift-serverless`, `schema-registry-glue` (2 resources each), `ingest-firehose`,
`compute-emr-serverless`, `orchestrator-mwaa` (3 each), `query-athena`, `speed-layer-kinesis`,
`governance-observability` (4 each, though the latter two carry locals/conditional-count logic).
**Carrying real, measured complexity**: `databricks-workspace` (15 resources, 19 types, deepest
input surface of the corpus), `networking-vpc` (14 resources, heaviest count/index arithmetic),
plus the 5 already-named harness blockers.

## 5. Gate readiness for novel types

### G5 (`core/governance/destructive_change_gate.py`)

Literal set counts: `STATEFUL_RESOURCE_TYPES` = **11** (7 AWS + 4 `databricks_*`),
`IAM_RESOURCE_TYPES` = **2**, `REVIEWED_UNSAFE_TYPES` = **2**, `AUTO_SHIP_ELIGIBLE_TYPES` = **32**
(30 AWS + 2 non-cloud test fixtures `random_id`/`terraform_data`).

`classify()`'s gating chain (lines 282-309), confirmed by reading the exact branches: a type in
none of the four sets and not `databricks_`-prefixed falls to the final `elif rtype not in
AUTO_SHIP_ELIGIBLE_TYPES` branch → `reason = "unreviewed_resource_type"` → a finding is appended
→ `autonomous_eligible = not findings and not databricks_resources` evaluates **`False`**.
**Confirmed: a genuinely novel resource type always stages, never ships autonomously.**

### G6 (`policy/g6/rules.rego`)

13 rule families confirmed in the aggregate block (SEC-01 through SEC-10, COST-01/02/03). The
41-type reviewed universe (union of G5's AWS-only sets) was independently recounted and matches
G9's own allowlist count exactly — no discrepancy.

**8 of 41 reviewed types have at least one firing G6 rule**: `aws_s3_bucket` (SEC-01, COST-01),
`aws_iam_role` (SEC-05), `aws_iam_role_policy` (SEC-02), `aws_kms_key` (SEC-06),
`aws_s3_bucket_policy` (SEC-07), `aws_redshiftserverless_workgroup` (SEC-08), `aws_subnet`
(SEC-09), `aws_s3_object` (SEC-10).

**33 of 41 have zero G6 coverage**: `aws_athena_workgroup`, `aws_budgets_budget`,
`aws_cloudwatch_event_rule`, `aws_cloudwatch_event_target`, `aws_cloudwatch_metric_alarm`,
`aws_default_security_group`, `aws_eip`, `aws_emrserverless_application`,
`aws_glue_catalog_database`, `aws_glue_catalog_table`, `aws_glue_job`, `aws_glue_registry`,
`aws_glue_schema`, `aws_glue_trigger`, `aws_internet_gateway`,
`aws_kinesis_firehose_delivery_stream`, `aws_kinesis_stream`,
`aws_kinesisanalyticsv2_application`, `aws_kms_alias`, `aws_mwaa_environment`, `aws_nat_gateway`,
`aws_redshiftserverless_namespace`, `aws_route_table`, `aws_route_table_association`,
`aws_s3_bucket_lifecycle_configuration`, `aws_s3_bucket_public_access_block`,
`aws_s3_bucket_server_side_encryption_configuration`, `aws_s3_bucket_versioning`,
`aws_sfn_state_machine`, `aws_sns_topic`, `aws_sns_topic_subscription`, `aws_vpc`,
`aws_vpc_endpoint`. (Three more G6 rules — SEC-03/`aws_redshift_cluster`,
SEC-04/`aws_msk_cluster`, COST-03/`aws_emr_cluster` — and COST-02/`databricks_cluster` target
types entirely outside the 41-type reviewed universe.) **A generated instance of any of these 33
types, misconfigured, has no G6 rule that would ever flag it.**

### G9 (`core/governance/ephemeral_apply.py`)

`RESOURCE_TYPE_ALLOWLIST`: **41** entries total. **4** have `verified=True` on at least one
emulator (`aws_iam_role`, `aws_kms_key`, `aws_s3_bucket_policy`, `aws_sns_topic`); **37** are
fully unverified on all three emulators (LocalStack is `verified=False` for every single entry —
no token provisioned). **4** entries are `security_critical=True` (`aws_iam_role`,
`aws_iam_role_policy`, `aws_kms_key`, `aws_s3_bucket_policy`); of those, **0** have
`negative_fidelity_verified=True` on any emulator — all 4 currently block under G9's own
fail-closed rule regardless.

## What a generation engine would need that doesn't exist yet

Derived directly from the five sections above, not speculative:

1. **An actual authoring mechanism.** Nothing decides what HCL to write from a resource-type name
   or a requirement — confirmed absent in sections 1 and 3. This is the core, unbuilt capability.
2. **A per-type live schema-query function.** `_fetch_schema()` + `_reduce_full()` both exist and
   are individually real, tested, callable — but nothing wires them into a single "attributes for
   type X" query today (section 2). A generator would need exactly this, likely as a thin new
   function composing the two, not a rewrite of either.
3. **A way to express module-level reusability.** `authored_content` is per-resource-type, flat,
   one-shot. No `variable`/`output` boundary, no way to declare or resolve a novel resource's own
   input contract (section 1). Real catalog modules almost universally need this (section 4: 7 of
   16 average variables, `databricks-workspace` needs 11).
4. **A way to carry non-HCL companion assets.** Two real catalog modules need a script file
   alongside their HCL (`path.module`-relative); the authored-content mechanism has no analogous
   concept (sections 1, 4).
5. **A resolution for genuinely dynamic (`for_each`-driven) blocks under G2.** Currently
   unconditionally rejected; one real catalog module needs this today (`table-format-iceberg`),
   and any generated resource needing runtime-variable-cardinality attributes would hit the same
   wall (sections 1, 4).
6. **G6 rule coverage for the 33 currently-uncovered reviewed types**, if a generator is ever
   expected to produce novel *configurations* of already-known types safely — today, misusing any
   of those 33 produces zero policy findings (section 5). This is separate from and larger than
   the "3 config-dependent types got new rules" work already done in Phase 6 Step 1.
7. **A requirements schema that's actually consumed**, or an explicit decision to keep it mostly
   symbolic. Nine of ten `data_pipeline` fields and five of six NFR axes are validated but never
   read by any generation-adjacent code (section 3) — a generator driven by "the requirements
   record" would, today, only ever legitimately be driven by two numbers (data volume, budget).
8. **G9 fidelity for whatever a generator actually produces.** 37 of 41 types are fully
   unverified on every emulator today (section 5); a generator's own output would inherit this
   same unverified status by default, correctly staging under G9's fail-closed design — but the
   underlying fidelity gap itself doesn't shrink just because generation exists.
