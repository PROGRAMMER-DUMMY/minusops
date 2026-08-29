# Generation Layer Context (`core/generation`)

The `core/generation` directory contains the requirements-driven composition engine, catalog module registry, blueprint fixtures, schema linting and watching tools, bi-temporal knowledge store, intent resolution logic, and workflow drivers for MinusOps.

Instead of deploying static monolithic blueprints, MinusOps composes vetted modules from `modules/<id>/` based on recorded requirements (`requirements.json`) and architecture decisions (`architecture_decision.json`). Synthesized Terraform workspaces then flow through the plan-bound deploy gate.

---

## Directory Overview & File Map

| File | Purpose | Key Responsibilities |
| :--- | :--- | :--- |
| [`__init__.py`](./__init__.py) | Package initialization | Module docstring defining generation & composition responsibilities |
| [`synthesizer.py`](./synthesizer.py) | Composition engine | Assembles modules + novel resources into root Terraform workspace; gating, context assembly, audit logging |
| [`modules.py`](./modules.py) | Module registry | Catalog metadata, keyword matching, preplan module derivation, grounding retrieval |
| [`module_provenance.py`](./module_provenance.py) | Module version pinning | Content hashing (`PROVENANCE.json`), versioning, upgrade reports, drift verification |
| [`blueprints.py`](./blueprints.py) | Blueprint registry | Schema validation and matching for demo/cached blueprint definitions |
| [`accelerators.py`](./accelerators.py) | Architecture accelerators | Pre-packaged reviewable requirement + decision templates (e.g. AWS Lakehouse) |
| [`intent_resolver.py`](./intent_resolver.py) | Intent classification | Maps natural language user requests to `REQUIREMENTS` or `OPERATION` intents |
| [`patterns.py`](./patterns.py) | Approved pattern store | Captures and matches previously approved module compositions (`.minus/patterns.json`) |
| [`workflow.py`](./workflow.py) | Request-to-run driver | Safe creation entry point: request -> run workspace -> `requirements.json` skeleton |
| [`terraform_generator.py`](./terraform_generator.py) | Demo HCL generator | Generates Terraform source for the `aws-data-pipeline-standard` demo fixture |
| [`demo.py`](./demo.py) | No-cloud demo runner | Standalone demo orchestrator creating run workspace, synthetic plan, and reports without AWS |
| [`schema_lint.py`](./schema_lint.py) | G2 pre-write schema linter | Validates HCL attribute references, deprecations, types, and required fields against live provider schema |
| [`schema_watch.py`](./schema_watch.py) | CI schema drift detector | Fetches live `terraform providers schema -json` and diffs against snapshot for deprecation/version changes |
| [`knowledge_store.py`](./knowledge_store.py) | Bi-temporal knowledge DB | SQLite database and JSONL corpus for bi-temporal facts, freshness clauses, and agent claims |
| [`knowledge_degradation.py`](./knowledge_degradation.py) | Schema degradation check | Re-fetches live schemas to update/invalidate active schema claims in the knowledge store |
| [`knowledge_delegation.py`](./knowledge_delegation.py) | Agent delegation hand-off | Formats `needs_review` claims for driving agent adjudication and records verdict claims |
> **Ranking is not selection.** [`modules.match_modules`](./modules.py) scores a whole-phrase
> hit at 3 and a single shared token at 1. The weak signal is right for ranking -- it is why a
> near-miss still appears for a human to see -- and wrong anywhere a set of modules is
> *chosen*. [`patterns._reuse_target`](./patterns.py) therefore filters to
> `min_score=3`. Without that filter every module added to the catalog grew the Jaccard
> denominator and pushed every stored pattern's `reuse_score` down, so a growing catalog would
> silently stop reusing approved compositions with nothing reporting it (caught 2026-08-22 when
> `governance-lakeformation`'s "lake formation" started matching every "data lake" request).

| [`cicd.py`](./cicd.py) | CI/CD synthesis | 4-lane pre-merge validation, reusable feed factory, Jenkins parity, and the exported per-pipeline deploy workflow (`render_pipeline_workflow`, FR-04) whose `paths:` filter keeps one pipeline's commit from planning every sibling in a shared domain repo; OIDC only, never a static key. Renders text and writes without overwriting |
| [`git_agent.py`](./git_agent.py) | Pattern promotion & PR agent | Automated Git PR agent (`minusctl pattern promote`) verifying UAT proofs, branch creation, commit creation, and pull request generation |
| [`knowledge_diff.py`](./knowledge_diff.py) | Structural schema claim builder | Extracts deterministic `schema` claims from live provider schemas for `knowledge_store` |

---

## Exhaustive File Specifications

### 1. [`__init__.py`](./__init__.py)

* **Exact Purpose:** Package marker for the `core.generation` module.
* **Key Functions / Classes:** None (package docstring only).
* **Inputs / Outputs:** None.
* **Failure Modes:** None.
* **Architectural Role:** Package namespace definition.

---

### 2. [`synthesizer.py`](./synthesizer.py)

* **Exact Purpose:** Core composition engine. Assembles catalog modules and generation-time authored novel resources into a governed Terraform workspace.
* **Key Functions & Classes:**
  * [`synthesize(requirements_text, spec=None, decision=None, ...)`](./synthesizer.py): Main entrypoint. Validates gates (`requirements.py`, `architecture_decision.py`), resolves novel resources, copies modules, renders root HCL (`main.tf`, `variables.tf`, `providers.tf`, `versions.tf`), writes manifests, and updates workflow records.
  * [`compose(module_ids, name_prefix, out_dir, ..., state_backend=None)`](./synthesizer.py): Copies module assets, auto-wires module inputs, renders root HCL, writes authored resources/modules, formats output with `terraform fmt`, and generates `COMPOSITION.md`.
  * `_render_backend(state_backend, name_prefix, run_id)` / `_BACKEND_TEMPLATE`: Emits the S3 remote-state block (MINUS-104), or `""` when no state bucket was supplied. **Opt-in on purpose:** a `backend "s3"` block makes `terraform init` fail until the bucket exists, so emitting one by default would break every local and test run. Locking uses S3-native `use_lockfile`, **not** a DynamoDB table -- HashiCorp deprecated DynamoDB locking ("will be removed in a future minor version"), so generating a table would ship a removal deadline to every operator. The key is directory-bound, `<name_prefix>/<run_id>/terraform.tfstate` (MINUS-134), so pipelines sharing one state bucket cannot collide on a key or block each other's lock (TerraShark FM-03). Reached via `synthesizer.py --state-bucket <b> [--state-region <r>]`; `--state-region` without `--state-bucket` is refused.
  * `_render_outputs(present_ids)` / `_OUTPUT_BLOCKS`: Writes `outputs.tf`, emitting only the outputs whose module is actually present (an output referencing an absent module is a hard `terraform validate` failure). Exists because the values a caller cannot compute at synthesis time -- anything containing the AWS account id or the run hash -- have to be readable after apply; `src/dbt/README-dbt.md` and `minusctl seed` both consume them instead of re-deriving bucket names by string surgery.
  * **dbt integration (MINUS-119/120):** `dbt_schema(name_prefix)`, `_dbt_profiles()`, `_dbt_project()`, `write_dbt_project(project_dir, name_prefix)`, `transform_engine(decision)`, `DBT_ENGINE`.
    - `write_dbt_project()` scaffolds `src/dbt/` (`profiles.yml`, `dbt_project.yml`, `README-dbt.md`, empty `models/`) at the **run root**, sibling to `terraform/`, whenever `query-athena` is selected -- not only in dbt-only mode, since dbt on top of a Glue pipeline is a normal shape.
    - `profiles.yml` targets the dbt-athena adapter. `s3_staging_dir`/`s3_data_dir`/`region_name` go through `env_var` because the Athena results bucket name contains the account id and run hash and does not exist at synthesis time; `terraform output` fills them (README-dbt.md carries the exact commands). `database: awsdatacatalog` is Athena's catalog, and dbt's `schema:` is the Glue database -- `dbt_schema()` must equal `query-athena`'s `aws_glue_catalog_database.gold.name`, which [`tests/test_dbt_scaffold.py`](../../tests/test_dbt_scaffold.py) asserts against the module's HCL.
    - `models/` is scaffolded empty on purpose: a generated model would have to invent a column schema, and one that does not match the data fails on first run.
    - `transform_engine(decision)` reads `transform_engine` off `architecture_decision.json`. **Absent means Glue, never dbt** -- omitting the compute module is a real architecture change and must be stated. When it is `"dbt"`, `synthesize()` drops `compute-glue-etl` **even if explicitly selected** (keeping both is the contradiction the field resolves), prints the reason, and **refuses** the composition if `query-athena` is absent, because dbt-athena has no engine to run against without a workgroup.
  * **Compute tier matrix (MINUS-128):** `modules.compute_tier(daily_gb, latency_text)` returns `{module_id, reason, execution_class, daily_gb}`. Crossovers are the points where the cheaper option stops being cheaper: **< 1 TB/day** Glue (per-DPU-second, no cluster to idle), **1-5 TB/day** EMR Serverless on Graviton, **>= 5 TB/day** EMR on EC2 with Spot task fleets. An **undeclared volume gets the smallest tier**, not a guess -- recommending a cluster off no evidence is how a $40/month pipeline acquires a $4,000/month bill. `execution_class` is FLEX only when the stated SLA tolerates an unpredictable start, and an intolerant phrase **wins a tie** ("hourly batch feeding a real-time dashboard" gets STANDARD). Threaded through `synthesize()` -> `compose()` -> `_render_main()` -> `_module_args()`, and recorded on the result as `compute_tier` so readiness can show why, not just what.
  * **`--based-on` inheritance (MINUS-135):** `inherit_from_run(run_root)` / `format_inheritance()`. Reads `region`, `owner`, `cost_center`, `data_classification` from the base run's tfvars (prod overrides root, and `REVIEW_REQUIRED` placeholders are skipped so a stack is never tagged to a cost centre literally named REVIEW_REQUIRED), plus `selected_architecture` / `transform_engine` / candidate modules from its decision record. **Volume, latency, and functional requirements are never inherited** -- those are what make two pipelines different, and copying them would size the new one for the old one's data. Every value carries its source file so an operator can reject any of it; candidate modules are printed as a suggestion, never applied.
  * **Project scaffold (MINUS-118):** `write_project_scaffold(project_dir)` creates `src/{compute,sql,quality,orchestration}` and `tests/fixtures` at the run root. Each directory gets a README saying what belongs there rather than being left empty -- an empty folder communicates nothing and git does not track it. **Never overwrites:** re-synthesising into an existing run must not discard the operator's PySpark, so an existing file is skipped and the return value lists only what was actually written. `sample.json` deliberately contains one `amount = 0.0` row, because a fixture where every row passes proves nothing about the quality gate.
  * **Weak-match stopwords** (in [`modules.py`](./modules.py)): `_WEAK_STOPWORDS` excludes ubiquitous tokens (`data`, `aws`, `amazon`, `managed`, ...) from the single-token scoring path. `"data"` sits inside "change data capture", "data quality", "data lake", and "data contracts", so a weak hit on it made every module look partly relevant to every data-pipeline request -- adding the ingestion modules pulled `ingestion-dms` into an Airflow-lakehouse match and broke pattern reuse. Whole-phrase matches are untouched and still score 3.
  * **Promotion matrix (MINUS-114 / MINUS-130):** `_ENV_MATRIX`, `_render_env_tfvars(env, ...)`, `write_env_tfvars(out_dir, ...)`. Writes `envs/{dev,staging,prod}.tfvars` into the Terraform root; promotion is `terraform plan -var-file=envs/prod.tfvars` against the **same** root, never a forked `main.tf`. Only size, retention, and destroyability differ, so a prod plan is the same resource graph as the dev plan already reviewed.
    - **`force_destroy` is deliberately absent from the matrix.** `main.tf` derives it from `var.environment == "dev"`, so no var-file can enable it for prod. [`tests/test_promotion_matrix.py`](../../tests/test_promotion_matrix.py) fails if it is ever added as a convenience.
    - A declared budget is **scaled** per tier (dev 0.25x, staging 0.5x, prod 1.0x) rather than copied flat -- one identical ceiling means the prod alarm is tuned for dev traffic, or the dev alarm never fires. An undeclared budget stays a commented REVIEW line rather than being invented.
    - New root variables backing it: `glue_worker_type`, `glue_number_of_workers`, `retention_days`, `monthly_budget_usd`, `cost_center`, `data_classification`. Wiring these also cleared the `worker_type` / `number_of_workers` / `retention_days` REVIEW items every composed stack used to carry.
  * **Mandatory tags (MINUS-132):** the provider's `default_tags` now stamps `managed_by`, `owner`, `environment`, `run_id`, merging in `cost_center` / `data_classification` **only when set** -- an empty tag value looks allocated in Cost Explorer while carrying no owner. `variables.tf` carries a `check "mandatory_tags_present"` block requiring both for `staging`/`prod`. **A `check` block warns; it does not fail the plan.** Cross-variable `validation` (which would hard-fail) needs Terraform >= 1.9 and `required_version` here is `">= 1.5"`; raising the floor would break operators on 1.5-1.8 to gain an error over a warning. Hard enforcement today is the deploy gate, not Terraform.
  * `_module_args(...)` auto-wiring added in Step 2/3: `storage-medallion-s3.force_destroy = var.environment == "dev"` (MINUS-101); `compute-glue-etl` gains `data_buckets = values(module.storage_medallion_s3.bucket_names)`, `kms_key_arn`, `source_bucket = ...["bronze"]`, `target_bucket = ...["silver"]` (MINUS-108/109). All four leave the module's REVIEW list, so a synthesized run is runnable without hand-editing.
  * [`assemble_authoring_context(resource_type, justification, requirements_text, provider="aws")`](./synthesizer.py): Assembles live schema, grounding examples, and knowledge claims for an authoring agent.
  * [`remember_claim(...)`](./synthesizer.py): Validates and persists researched claims into `knowledge_store` and JSONL corpus. Protects against prompt injections and fake price markers.
  * [`_validate_novel_resources(decision, authored_content, ...)`](./synthesizer.py): Fail-closed validator for authored resources using `schema_lint.gate_content()`.
  * [`AuthoredContentRejected(ValueError)`](./synthesizer.py): Exception carrying structured rejection reasons and G2 lint findings.
* **Inputs / Outputs:**
  * *Inputs:* Requirements text, spec dictionary, decision dictionary, output directory, authored content maps.
  * *Outputs:* Composed Terraform workspace files, `COMPOSITION.md`, `minus-generated.json`, source guard baseline.
* **Failure Modes:**
  * Raises `RequirementsIncomplete` or `ArchitectureDecisionIncomplete` if gates fail and `allow_incomplete=False`.
  * Raises `AuthoredContentRejected` if authored resources fail G2 schema linting, missing asset references, or un-wired required variables.
  * Raises `ValueError` if unexplained files exist in target directory and `overwrite=False`.
* **Architectural Role:** Primary infrastructure generator replacing hardcoded blueprints with modular, audited composition.

---

### 3. [`modules.py`](./modules.py)

* **Exact Purpose:** Catalog registry of small, composable Terraform modules stored under `modules/<id>/`.
* **Key Functions & Classes:**
  * [`MODULES`](./modules.py): Immutable list of catalog module definitions (ID, category, title, satisfies keywords, services, inputs, provides).
  * [`match_modules(requirements, min_score=1)`](./modules.py): Scores modules against free-text requirements based on keyword overlap with `satisfies` phrases.
  * [`derive_module_ids(requirements_data)`](./modules.py): Deterministically extracts module recommendations from explicit `data_pipeline` and `latency` fields in `requirements.json`.
  * [`retrieve_grounding_examples(requirements, top_n=3)`](./modules.py): Retrieves top-N module `main.tf` source code snippets as RAG grounding context for authoring agents.
  * [`validate_modules()`](./modules.py): Validates schema completeness and verifies on-disk existence of `modules/<id>/main.tf`.
* **Inputs / Outputs:**
  * *Inputs:* Free-text requirements strings, `requirements.json` dictionaries, module IDs.
  * *Outputs:* Lists of matched/scored module dicts, grounding example dicts, or validation error lists.
* **Failure Modes:**
  * `validate_modules()` returns errors if required fields are missing or if `modules/<id>/main.tf` is missing on disk.
* **Architectural Role:** Source of truth for pre-vetted infrastructure building blocks.

---

### 4. [`module_provenance.py`](./module_provenance.py)

* **Exact Purpose:** Manages module catalog versioning, content hashing, and provenance tracking (`PROVENANCE.json`).
* **Key Functions & Classes:**
  * [`content_hash(module_dir)`](./module_provenance.py): Calculates deterministic SHA-256 hash over module directory contents (excluding `PROVENANCE.json`).
  * [`pin(module_id, source, ...)`](./module_provenance.py): Bumps version counter, records content hash, source, G2 findings, and writes upgrade reports into `upgrades/`.
  * [`verify(module_id)`](./module_provenance.py): Compares current content hash against recorded `PROVENANCE.json` to detect unpinned drift.
  * [`show(module_id)`](./module_provenance.py): Loads recorded `PROVENANCE.json` for a module.
* **Inputs / Outputs:**
  * *Inputs:* Module ID, source description, provider version, G2 lint findings.
  * *Outputs:* `PROVENANCE.json` record, upgrade report JSON, verification status boolean.
* **Failure Modes:**
  * Raises `FileNotFoundError` if module directory does not exist.
  * `verify()` returns `(False, recorded, current)` if drift is detected or if module was never pinned.
* **Architectural Role:** Catalog auditability and tamper-evidence tracking for module changes.

---

### 5. [`blueprints.py`](./blueprints.py)

* **Exact Purpose:** Registry for product blueprints (specifically `aws-data-pipeline-standard` demo fixture).
* **Key Functions & Classes:**
  * [`BLUEPRINTS`](./blueprints.py): Registered blueprint metadata list.
  * [`validate_blueprint(blueprint)`](./blueprints.py) / [`validate_blueprints()`](./blueprints.py): Checks required blueprint fields, inputs, choices, and non-empty strings.
  * [`match_blueprints(query, cloud=None)`](./blueprints.py): Scores blueprints against natural language queries using alias and service matches.
* **Inputs / Outputs:**
  * *Inputs:* Query string, cloud filter, blueprint ID.
  * *Outputs:* Matched blueprint dicts, deep copies of blueprint definitions, validation error maps.
* **Failure Modes:**
  * Returns validation error dict if required fields or input shapes are violated.
* **Architectural Role:** Demo/fixture blueprint contracts for testing and no-cloud demos.

---

### 6. [`accelerators.py`](./accelerators.py)

* **Exact Purpose:** Reviewable architecture accelerators generating complete `requirements.json` and `architecture_decision.json` starting points.
* **Key Functions & Classes:**
  * [`lakehouse_requirements(owner=..., daily_data_gb=..., latency=...)`](./accelerators.py): Generates pre-populated requirements record for an AWS Lakehouse.
  * [`lakehouse_decision(requirements_file=..., streaming=..., daily_data_gb=...)`](./accelerators.py): Generates architecture decision record, selecting scale-appropriate modules (compaction for TB, Iceberg for PB). Satisfies the TerraShark 4-part output contract (MINUS-136): it populates `validation` (validate + SEC scan + conformance + BCM evidence), `rollback` (hash-bound gate, `--destroy`, source-snapshot revert), and the `failure_modes` it designs against (`FM-01`, `FM-03`, `FM-05`) alongside `assumptions` and `alternatives`. Without these the accelerator's own output would no longer pass `architecture_decision.validate()`.
  * [`write_lakehouse(run, ...)`](./accelerators.py): Writes both records into a run workspace.
* **Inputs / Outputs:**
  * *Inputs:* Run dictionary, owner string, daily data GB, streaming flag.
  * *Outputs:* Written `requirements.json`, `architecture_decision.json`, and next command hint.
* **Failure Modes:**
  * Raises `FileExistsError` if target files already exist and `force=False`.
* **Architectural Role:** Jump-start accelerator for common architecture patterns without bypassing governance gates.

---

### 7. [`intent_resolver.py`](./intent_resolver.py)

* **Exact Purpose:** Enterprise intent classifier that turns user requests into safe product decisions (`REQUIREMENTS` vs `OPERATION`).
* **Key Functions & Classes:**
  * [`is_creation_request(query)`](./intent_resolver.py): Classifies request by matching infrastructure terms and checking against interrogative ("what", "show") or operational ("deploy", "destroy") veto terms.
  * [`resolve(query, cloud=None)`](./intent_resolver.py): Maps creation queries to `REQUIREMENTS` intent and operational queries to `OPERATION`.
  * [`format_resolution(result)`](./intent_resolver.py): Formats resolution output for CLI display.
* **Inputs / Outputs:**
  * *Inputs:* Query string, optional cloud name.
  * *Outputs:* Resolution dictionary with intent, confidence, recommendation, and safe next actions.
* **Failure Modes:**
  * Returns `OPERATION` intent with no-op recommendation when query is classified as non-creation or ambiguous.
* **Architectural Role:** Front door for intent routing in `minusctl` and `workflow.py`.

---

### 8. [`patterns.py`](./patterns.py)

* **Exact Purpose:** Persistence registry for approved, deployed architecture compositions (`.minus/patterns.json`).
* **Key Functions & Classes:**
  * [`capture_pattern(requirements, module_ids, name=..., plan_hash=..., approver=...)`](./patterns.py): Saves approved composition pattern.
  * [`match_patterns(requirements, min_overlap=0.5)`](./patterns.py): Calculates Jaccard similarity between candidate module sets and historical patterns to recommend proven compositions.
  * [`load_patterns()`](./patterns.py): Reads patterns from `.minus/patterns.json`.
* **Inputs / Outputs:**
  * *Inputs:* Requirements string, list of module IDs, plan hash, approver name.
  * *Outputs:* Stored pattern dictionary, list of matched patterns with `reuse_score`.
* **Failure Modes:**
  * Returns empty list on missing or corrupt `.minus/patterns.json`.
* **Architectural Role:** Shared composition memory allowing teams to reuse proven recipes across runs.

---

### 9. [`workflow.py`](./workflow.py)

* **Exact Purpose:** Orchestrates the request-to-run workflow, creating run workspaces and seeding `requirements.json`.
* **Key Functions & Classes:**
  * [`resolve_to_run(query, cloud=..., inputs=..., generate=False)`](./workflow.py): Calls `intent_resolver.resolve()`, creates a run directory via `runs.py`, seeds `requirements.json` with the goal, and blocks direct generation.
  * [`format_result(record)`](./workflow.py): Prepares human-readable summary of the run creation status.
* **Inputs / Outputs:**
  * *Inputs:* User query string, cloud filter, inputs dictionary, generate flag.
  * *Outputs:* Run record dictionary (`workflow.json`), populated run workspace.
* **Failure Modes:**
  * Returns `ok: False` if intent resolver classifies request as `OPERATION`.
  * Blocks generation when `generate=True` with explicit requirements-first reason.
* **Architectural Role:** Safe entry point for CLI and agent workflows initiating new runs.

---

### 10. [`terraform_generator.py`](./terraform_generator.py)

* **Exact Purpose:** Generates static Terraform HCL files for the demo fixture blueprint (`aws-data-pipeline-standard`).
* **Key Functions & Classes:**
  * [`generate(blueprint, inputs, terraform_dir)`](./terraform_generator.py): Dispatcher validating blueprint ID.
  * [`generate_aws_data_pipeline(inputs, terraform_dir)`](./terraform_generator.py): Writes `main.tf`, `provider.tf`, `variables.tf`, `kms.tf`, `s3.tf`, `iam.tf`, `glue.tf`, `scripts.tf`, `step_functions.tf`, `athena.tf`, `monitoring.tf`, and `outputs.tf`. Also writes source guard baseline.
* **Inputs / Outputs:**
  * *Inputs:* Blueprint dict, inputs dict (`owner`, `daily_data_gb`, `environment`, `region`), output terraform directory.
  * *Outputs:* Written `.tf` files and `minus-generated.json` manifest.
* **Failure Modes:**
  * Raises `ValueError` for unsupported blueprint IDs.
* **Architectural Role:** Pure HCL writer for offline demo fixture generation (`minusctl demo`).

---

### 11. [`demo.py`](./demo.py)

* **Exact Purpose:** Standalone orchestrator for no-cloud demos. Generates Terraform, builds synthetic plan JSON, and triggers reports without contacting AWS or running Terraform CLI.
* **Key Functions & Classes:**
  * [`governed_data_pipeline(owner, daily_data_gb)`](./demo.py): Runs `runs.new_run()`, calls `terraform_generator.generate()`, generates `synthetic_plan()`, and invokes `reporter.generate_from_plan_json()`.
  * [`synthetic_plan(tf_dir, inputs)`](./demo.py): Generates synthetic `terraform show -json` structure matching standard resource types.
* **Inputs / Outputs:**
  * *Inputs:* Owner string, daily data GB number.
  * *Outputs:* Complete run directory with synthetic plan JSON, reports, and workflow record.
* **Failure Modes:**
  * Returns non-zero exit code if command line arguments fail parsing.
* **Architectural Role:** Demonstrates end-to-end control-plane governance without live cloud credentials.

---

### 12. [`schema_lint.py`](./schema_lint.py)

* **Exact Purpose:** Pre-write G2 schema linter. Validates HCL attribute references, types, deprecations, and required fields against live `terraform providers schema -json`.
* **Key Functions & Classes:**
  * [`gate_content(content, source_label)`](./schema_lint.py): Core G2 linter. Parses top-level blocks, scans assigned attributes (`_scan_body`), extracts references (`extract_references`), and checks against live provider schema.
  * [`gate_module(module_id)`](./schema_lint.py): Reads `modules/<module_id>/main.tf` and passes content to `gate_content()`.
  * [`iter_hcl_blocks(content)`](./schema_lint.py): Yields top-level resource and data blocks using depth-aware brace matching (`_matching_brace`).
  * [`_scan_body(body, prefix="")`](./schema_lint.py): Recursively scans block bodies for assigned attributes and flags unparseable `dynamic` blocks.
  * [`_extract_assigned_values(body, prefix="")`](./schema_lint.py): Extracts assigned RHS text for literal shape inference (`_infer_literal_shape`).
* **Inputs / Outputs:**
  * *Inputs:* Raw HCL content string or module ID, source label string.
  * *Outputs:* Dict `{"blocking": bool, "findings": [...], "warnings": [...], "schema_hash": str}`.
* **Failure Modes:**
  * Yields `schema_fetch_failed` or `schema_malformed` blocking findings if live provider schema cannot be retrieved.
  * Yields `unknown_type`, `unknown_attribute`, `deprecated_attribute_in_use`, `type_mismatch`, or `required_attribute_absent` findings for invalid HCL.
* **Architectural Role:** Pre-write gate for module pinning (`module_provenance.py`) and novel resource authoring (`synthesizer.py`).

---

### 13. [`schema_watch.py`](./schema_watch.py)

* **Exact Purpose:** CI provider schema-diff watcher. Detects provider schema drift (attribute deprecations, version bumps, type removals) for catalog modules.
* **Key Functions & Classes:**
  * [`run_provider(provider, ...)`](./schema_watch.py): Fetches live schema, extracts types used by catalog modules (`used_types`), diffs against `schema-snapshot.json`, writes timestamped diff reports, and logs to `audit.jsonl`.
  * [`get_type_schema(provider, type_name, kind="resource")`](./schema_watch.py): Returns raw schema block for a single resource/data source type.
  * [`used_types(modules_dir, provider)`](./schema_watch.py): Scans `modules/*/main.tf` to identify active resource types.
  * [`_fetch_schema(provider, workdir)`](./schema_watch.py): Runs `terraform init` and `terraform providers schema -json` in a temporary directory.
  * [`_diff(old_snapshot, reduced, used_keys)`](./schema_watch.py): Compares current reduced schema against previous snapshot.
* **Inputs / Outputs:**
  * *Inputs:* Provider name ("aws", "databricks"), optional output paths.
  * *Outputs:* `schema-snapshot.json`, diff report JSON, audit event entry, tuple `(findings, new_of_interest)`.
* **Failure Modes:**
  * Raises `RuntimeError` if `terraform` is not on PATH, or `terraform init` / `schema -json` fails or times out (120s limit).
* **Architectural Role:** Out-of-band CI detector keeping the catalog aware of upstream cloud provider schema changes.

---

### 14. [`knowledge_store.py`](./knowledge_store.py)

* **Exact Purpose:** Bi-temporal SQLite store and JSONL corpus for facts, claims, and agent adjudications (`claims.db` & `claims/*.jsonl`).
* **Key Functions & Classes:**
  * [`init_db(path)`](./knowledge_store.py): Creates SQLite tables (`claims`, `claim_adjudications`), indexes, and enables WAL mode.
  * [`insert_claim(conn, ...)`](./knowledge_store.py): Inserts a bi-temporal claim (`valid_from`, `observed_at`, `ingested_at`, `scope`, `content_hash`).
  * [`invalidate_claim(conn, claim_id, *, valid_until, ...)`](./knowledge_store.py): Marks a claim as invalidated by a newer claim.
  * [`resolve(conn, resource_type, attribute=None)`](./knowledge_store.py): Determines winning claim or `needs_review` status based on bi-temporal freshness clauses, string agreement, and delegated agent verdicts.
  * [`export_jsonl(conn, root)`](./knowledge_store.py) / [`import_jsonl(conn, root)`](./knowledge_store.py): Serializes SQLite state to git-committable sharded JSONL files (`<type>.jsonl`, `_adjudications.jsonl`) and rebuilds cache.
* **Inputs / Outputs:**
  * *Inputs:* SQLite connection, claim attributes, corpus directory path.
  * *Outputs:* Claim ID int, resolution dict, sharded JSONL files.
* **Failure Modes:**
  * Raises `ValueError` for unknown scopes, missing `resource_type` on resource scopes, or offset-naive timestamps.
* **Architectural Role:** Knowledge layer backbone maintaining bi-temporal ground truth for architecture decisions and authoring grounding.

---

### 15. [`knowledge_degradation.py`](./knowledge_degradation.py)

* **Exact Purpose:** Re-fetches live schemas to check for degradation, invalidating stale claims and inserting fresh ones in `knowledge_store`.
* **Key Functions & Classes:**
  * [`check_and_refresh(conn, provider, resource_type, kind="resource")`](./knowledge_degradation.py): Fetches fresh claims via `knowledge_diff.schema_claims_for_type()`, inserts new claims, invalidates old claims (`valid_until=valid_from`), and handles removed attributes.
* **Inputs / Outputs:**
  * *Inputs:* SQLite connection, provider, resource type, resource kind.
  * *Outputs:* Summary dictionary `{"resource_type": ..., "inserted": [...], "invalidated": [...], "removed_attributes": [...], "skipped_removed_attribute_check": bool}`.
* **Failure Modes:**
  * Warns and sets `skipped_removed_attribute_check=True` if live schema fetch returns empty for an already-tracked resource type (guards against typos).
* **Architectural Role:** Automated schema claim freshness maintenance for the knowledge store.

---

### 16. [`knowledge_delegation.py`](./knowledge_delegation.py)

* **Exact Purpose:** Manages agent-delegation hand-offs when `resolve()` encounters conflicting or `needs_review` claims.
* **Key Functions & Classes:**
  * [`build_delegation_request(conn, resource_type, attribute)`](./knowledge_delegation.py): Packages conflicting claims into a structured hand-off payload for the driving agent.
  * [`record_delegation_verdict(conn, resource_type, attribute, ...)`](./knowledge_delegation.py): Inserts agent verdict as a new `agent_delegated` claim and writes `claim_adjudications` linkage in a single transaction.
* **Inputs / Outputs:**
  * *Inputs:* SQLite connection, resource type, attribute, adjudication IDs list, ISO timestamps.
  * *Outputs:* Delegation request dictionary or newly created verdict claim ID int.
* **Failure Modes:**
  * Raises `ValueError` if timestamps are invalid/naive, if `valid_from > observed_at`, or if `adjudicated_ids` are empty, duplicated, or non-active.
* **Architectural Role:** Bridge connecting driving agent semantic decisions back into the formal knowledge store.

---

### 17. [`knowledge_diff.py`](./knowledge_diff.py)

* **Exact Purpose:** Converts live provider schema blocks into deterministic structural `schema` claim dictionaries.
* **Key Functions & Classes:**
  * [`schema_claims_for_type(provider, resource_type, observed_at=None, kind="resource")`](./knowledge_diff.py): Uses `schema_watch._fetch_schema()` to extract required/deprecated/optional flags per attribute and format claim dictionaries.
* **Inputs / Outputs:**
  * *Inputs:* Provider, resource_type, optional `observed_at` timestamp string, kind string.
  * *Outputs:* List of claim dictionaries ready for `knowledge_store.insert_claim()`.
* **Failure Modes:**
  * Returns empty list if resource type is not found in the live schema.
* **Architectural Role:** Bridge transforming raw Terraform provider schema JSON into normalized knowledge claims.
