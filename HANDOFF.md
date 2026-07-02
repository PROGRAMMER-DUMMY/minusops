# Handoff — Data-Pipeline Specialization

**Date:** 2026-07-02 · **Branch:** `restructure/multi-cloud-foundation` · **Status:** all work **uncommitted**, `177 tests passing`, full composed lakehouse passes `terraform validate`.

---

## 1. Direction (decided this session)

MinusOps is being positioned as a **requirements-first, governed IaC tool for *data pipelines*** — not a generic IaC tool. Principle: **keep the engine generic/robust** (classification fallbacks, multi-cloud prefixes so it never breaks on non-data / Azure / GCP resources), but aim all **value-add** (blueprints, conformance, diagrams, requirements schema, optimization) at data pipelines.

Grounding reference: the AWS Serverless Data Analytics Pipeline six-layer model (ingestion → storage[raw/cleaned/curated] → cataloging → processing → consumption → cross-cutting security/governance) + the Well-Architected **Data Analytics Lens**. See `docs/architecture_svg_spec.md` and the memory notes `aws-reference-architectures-for-design`, `data-pipeline-specialization`.

---

## 2. What shipped

### Phase 1 — six-layer model (the shared brain)
- **`core/architecture_model.py`** (new): generic, cloud-agnostic `classify_role()` (ingest/stage/store_other/catalog/transform/orchestrate/consume/security/observability + `other` fallback), `layer_of()`, `module_dependencies()` (real refs from `configuration.module_calls`), and `conformance()` scoring a plan vs the reference architecture + WA Lens (each finding cites its BP). Multi-cloud keyword rules (AWS/Azure/GCP) with graceful fallback.
- Tests: `tests/test_architecture_model.py`.

### Phase 2 — conformance surfaced everywhere
- `minusctl conformance --run <id> [--json] [--strict]`.
- Folded into `minusctl._readiness` (a check + full `conformance` object), the **enterprise package** (new section), and the **dashboard** "Reference conformance" panel (`app/dashboard_app.py`).

### Phase 3 — data-aware requirements
- **`core/requirements.py`**: additive data-pipeline FR/NFR profile (`DATA_FR`/`DATA_NFR` mapped to the six layers + WA pillars), `is_data_pipeline()`, `validate_data_pipeline()`, `requirements.py data-check <file>` CLI. Generic `validate()` untouched (backward-compatible). Surfaced as a non-blocking readiness warning.
- **`core/accelerators.py`**: `aws-lakehouse` now populates the data-pipeline profile (`sources` explicitly deferred).

### Phase 4 — data optimization analyzer
- **`core/optimize_analyzer.py`**: `DATA-01` (Glue job without job bookmarks / not incremental), `DATA-02` (Glue table not partitioned), `DATA-03` (Athena workgroup without scan cutoff). Advisory (non-blocking). Grounded in WA BP10.

### Phase 6 — observability generation (design-time slice)
- **`modules/compute-glue-etl/main.tf`**: per-job Glue-failure EventBridge rule → SNS (BP 6.2/6.3), wired via synthesizer to the governance alerts topic.
- **Deferred (honestly):** a *live* 5-pillar data-observability dashboard (freshness/volume/schema/distribution/lineage) — those are runtime metrics that need real data flowing, which a pre-apply governance tool doesn't have. Faking them would violate the no-fabrication principle.

### Phase 8 — multi-cloud
- `architecture_model` classifier hardened with Azure/GCP data services (Data Factory, Pub/Sub, Dataproc, Synapse, BigQuery, Cosmos/Spanner/Bigtable, Key Vault, …). Still fallback-safe.

### B′ — loop-close (make generated pipelines runnable/conformant)
- **`modules/orchestrator-stepfunctions/main.tf`**: `definition_json` optional; builds a **real** state machine from wired Glue job names (`glue:startJobRun.sync`).
- **`core/synthesizer.py`** (`_module_args`): wires `glue_job_names`/`task_role_arns = module.compute_glue_etl.*` (creates the orchestration→processing edge) + a default `bronze_to_silver` job + `alarm_sns_topic_arn = module.governance_observability.alerts_topic_arn`.
- **`modules/compute-glue-etl/main.tf`**: default job + uploads bundled starter `scripts/etl.py` (`aws_s3_object`) + `glue_job_arns` output.
- **`modules/governance-observability/main.tf`**: creates an **SNS alerts topic**, wired to the alarm + budget → resolves `WA-REL-NOTIFY`.
- `core/modules.py`: registry `inputs`/`provides` updated to match.
- Net: a fresh accelerator run is conformant-by-construction (only INFO "no ingestion" remains → ~100/READY), diagram shows a solid `orchestrates` edge. Verified with `terraform validate`.

### Diagram (v3, additive)
- **`core/reporter.build_dataflow_svg`**: emits **`dataflow.svg`** alongside the v2 `architecture.svg` (which remains the binding contract for the dashboard pan-zoom viewer + tests — untouched). Shares the six-layer classifier, so the picture and the conformance report agree. Honest orchestration edge (solid only when the plan wires it, else `not wired — placeholder`). Icons are **opt-in** via `MINUS_ARCH_ICONS_DIR` / `assets/architecture-icons/<slug>.svg` with generic-glyph fallback — **no vendor icons committed**. Spec: `docs/architecture_svg_spec.md` v3.

### terraform-validate self-check (non-mutating, credential-free)
- **`core/tf_validate.py`** (new): `terraform init -backend=false` + `validate -json`, offline, never raises. `validate_and_record()` writes `validation.json`.
- Wired: `synthesize(..., validate=True)` (CLI default on; `--no-validate` to skip), `minusctl validate --run <id>`, and a readiness check reading the recorded result.

### Earlier this session (governance hardening — also uncommitted)
- **`core/plan_gate.py`**: `--policy-mode production` now enforces an approver allowlist, two-person rule (approver ≠ planner), and rejects `MINUS_ALLOW_STATIC_CREDS` (Phase 1 warn → Phase 2 enforce). `stage_plan` records the planner. Dev mode unchanged. See memory `deploy-gate-bypass`.

---

## 3. New/changed files (quick map)

**New:** `core/architecture_model.py`, `core/tf_validate.py`, `modules/compute-glue-etl/scripts/etl.py`, `tests/test_architecture_model.py`, `tests/test_tf_validate.py`, this file.
**Core changed:** `minusctl.py`, `requirements.py`, `accelerators.py`, `reporter.py`, `optimize_analyzer.py`, `synthesizer.py`, `modules.py`, `plan_gate.py`.
**Modules changed:** `orchestrator-stepfunctions`, `compute-glue-etl`, `governance-observability`.
**Other:** `app/dashboard_app.py`, `docs/architecture_svg_spec.md`, several `tests/test_*.py`.

## 4. Key commands
```
python core/minusctl.py conformance --run <id>        # six-layer + WA gap analysis
python core/minusctl.py validate    --run <id>        # offline terraform validate (no creds)
python core/requirements.py data-check <requirements.json>
python core/synthesizer.py "<summary>" --run <id> --requirements-file ... --decision-file ...   # validates by default
MINUS_ARCH_ICONS_DIR=<dir> ...                        # opt-in real AWS icons for dataflow.svg
```

## 5. Live-infra status
The demo lakehouse run `20260701-040620-requirements-first` was applied to the sandbox AWS account earlier, then **fully destroyed** (`terraform destroy`, 33/33). Its state is empty. (The run workspace was later purged with all generated artifacts for a fresh end-to-end test.)

---

## 6. Known loopholes / open items (from the audit)

**High**
1. **Gate controls are opt-in.** Production controls only fire in `--policy-mode production`; default is `dev` and nothing ties policy mode to the real target account. The `MINUS_ALLOW_STATIC_CREDS + --mode auto-approve` self-apply is still open in dev. *Fix:* infer/require production when creds point at a non-sandbox account.
2. **Source guard can be re-baselined.** An operator can hand-edit generated TF then `guard refresh` to bless it (the prior run did this). Protects drift, not tampering.
3. ~~**Icon SVG embedding is unsanitized (introduced here).**~~ **FIXED (2026-07-02).** `reporter._sanitize_svg_fragment` now strips script/foreignObject/embedding/animation elements, `on*` attributes, and non-fragment `href`s on embed, and fails closed to the generic glyph if anything active survives. Regression tests: `test_dataflow_icon_embedding_is_sanitized`, `test_dataflow_benign_icon_still_embeds`.

**Medium**
4. Conformance / data-profile / `tf_validate` / DATA-* findings are **advisory** (only `SEC-*` block apply). Broken/non-conformant pipelines can still be approved.
5. **"core Terraform files present"** readiness check tests presence, not content — empty stubs pass it.
6. Conformance **"wired" detection is heuristic** (module-input refs only) — literal-name wiring → false "unwired"; unrelated module ref → false "wired". *(2026-07-02: the dataflow diagram now uses the exact same test as `conformance()`, so at least the picture and the report can no longer disagree; the heuristic itself is unchanged.)*

**Lower**
7. `tf_validate` (init -backend=false + validate) ≠ full correctness (misses provider-side + unknown-value checks).
8. `terraform apply tfplan` uses ambient creds — not cryptographically bound to the approver's account.

---

## 7. Recommended next steps
1. ~~**Patch loophole #3** (sanitize icon SVG embed)~~ — **done 2026-07-02** (see §6 #3). Also fixed the same session: dataflow diagram no longer silently drops transforms that don't fit between stages (appended to spine) or extra consumption/catalog/orchestrator nodes (`+n more` markers); its wired/unwired verdict now uses the identical test as `conformance()`; `dataflow.svg` is now actually served + linked by the dashboard (was manifest-listed but 404 behind the route allowlist); spec doc internal contradictions corrected (group list incl. `edges`, node-card height 44).
   *Also 2026-07-02 (round 4 — FinOps-grade cost report):* per-service table now shows real **usage quantities + units and effective $/unit** (BCM cost ÷ BCM quantity — `load_bcm_estimate` was dropping the `quantity` object), **unpriced plan services are listed as "not estimated"** rows (absence of a price ≠ $0), a **What-if scenarios** section points at the existing `scenario` command (scale up/down, SP/RI commitments), **unit economics** (cost/GB processed) renders when the run states a data volume, and the overview Cost-evidence KPI shows the actual `$X/mo`. Grounded in FinOps framework guidance (unit economics, scenario planning, showback).
   *Also 2026-07-02 (round 3 — estimates are frictionless now):* **BCM estimates no longer require human approval** — an estimate is a free, deletable pricing object, so `bcm_pricing_calculator.run/scenario` default to auto-approve (still audited + RBAC-checked); human-in-the-loop stays on APPLY. New `auto_estimate()` runs during every report generation (`MINUS_BCM_AUTO=0` to disable; tests force it off in conftest): amounts derived from run inputs + recorded assumptions, catalog fields from the example profile (amounts stripped — never submitted), only complete lines submitted, skipped services recorded as `not_estimated_services`. The example profile's catalog triples were **verified against the AWS Price List API** (Glue = `USE1-ETL-DPU-Hour/Jobrun`, S3 us-east-1 = `TimedStorage-ByteHrs` with an EMPTY operation — `validate_usage` now allows empty operation). Verified live on the agy sales-pipeline run: AWS returned **$116.59/mo** (Glue $105.60 = 240 DPU-h × $0.44, Athena $10.99 = 2.1973 TB × $5), readiness went to 100/100 READY. Known gap: S3 goes not-estimated when the plan lacks a `daily_data_gb` variable — the synthesizer should map the requirements' volume answer into that variable. Report title fix: reports of run workspaces now title themselves from run.json blueprint instead of the directory basename "terraform".
   *Also 2026-07-02 (round 2):* dataflow spine now places each transform between the stages its `<from>_to_<to>` name bridges (positional interleave only as fallback), a stage boundary with **no transform in the plan renders a faint dashed gap labelled `no transform in plan`** instead of a fabricated solid arrow, and consumption anchors to the last storage stage. Overview no longer embeds the architecture (moved to the top of the **Reports** tab). Spend charts follow Cost Explorer conventions: monthly **bars** (no spline over near-zero months), emphasis coloring on spend-by-service, adaptive money ticks, micro-spend (<1¢) hides the axis and direct-labels bars, zero slices dropped from the plan donut, `.col-side` gap fixed. **Estimate path verified end-to-end** with a fixture run: BCM totals rendered verbatim ($123.45), annual ×12, variance math exact (+15.0% Glue, −58.6% S3, −10.9% total). Fixture deleted after verification.
   *Also 2026-07-02:* **dashboard overview rebuilt** around the pipeline instead of the wallet — KPIs are now Readiness / Conformance / Plan changes / Cost evidence; the dataflow diagram is embedded on the overview; the three account-level $0 charts collapsed into one compact "Account spend" evidence panel; brand renamed to "MinusOps — governed data-pipeline console". The interactive viewer gained a **Data flow ⇄ Topology toggle**. **Official AWS service icons** installed locally at `assets/architecture-icons/` (17 slugs from the aws-svg-icons npm package; the dir is gitignored — never commit vendor assets); `_df_embed_icon` now carries the source viewBox through so 80×80 icon sets aren't cropped. **All generated artifacts purged** (`runs/`, `artifacts/`, `.pytest_tmp*`) for a fresh end-to-end pipeline test; the demo run record from §5 is gone with them (its infra was already destroyed).
2. **Address #1** — refuse/loudly-audit `dev` policy when the target account isn't a known sandbox.
3. Decide whether conformance/data-profile should **block** (not just warn) in production mode.
4. Harden #5 (check core files are non-empty / contain expected resources).
5. Optionally: `dq-great-expectations` failure notification (same pattern as compute); live observability dashboard once a pipeline actually runs.
6. **Commit**: stage the whole specialization on this branch (co-author trailer, GitHub noreply email per project convention). Nothing is committed yet.

## 8. Verification
- `python -m pytest -q` → 177 passing.
- `python core/synthesizer.py ... ` (or `compose`) then `terraform validate` → "Success! The configuration is valid."
- `python core/minusctl.py conformance --run <id>` → layer coverage + WA gaps.
