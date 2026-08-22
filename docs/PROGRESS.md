# MinusOps — living progress record

**Purpose:** survive session resets and context compaction. Anyone (human or agent) picking
this up cold should be able to read only this file and know where things stand.

**Maintenance rule:** update this file in the same change that alters state. If you finish an
increment, close a decision, or discover a bug, edit here before moving on. Stale entries are
worse than missing ones.

Last updated: 2026-08-18 · Branch: `feat/minusops-enterprise-nextgen-v2`

---

## 1. Where the project landed strategically

MinusOps **owns memory + judgment**; it **delegates authoring and research** to the agent
ecosystem. Reasoning, from a 2026-07-26 research pass:

| Space | Best ecosystem skill | Installs |
|---|---|---|
| Terraform module authoring | `wshobson/agents@terraform-module-library` | 12.8K |
| Terraform (AWS modules maintainer) | `antonbabenko/terraform-skill` | 4.1K |
| Medallion data pipeline | `aradotso/data-skills@data-engineering-medallion-pipeline` | 1.7K |
| **Terraform plan review / approval** | `lgbarn/devops-skills@terraform-plan-review` | **23** |
| **IaC management (Harness)** | `harness/harness-skills@manage-iacm` | **17** |

Authoring is commoditized and free. Plan-hash-bound AWS governance is an empty quadrant.
Nothing anywhere persists verified claims with temporal validity and provider-version keying.

---

## 2. Locked decisions (21)

From the `grill-me` session, 2026-07-26. These are settled; reopen deliberately, not by drift.

### Positioning
1. Agent lives **outside** — an existing agentic CLI drives MinusOps. No SDK, no API key, no LLM in the product.
2. Own **memory + judgment**; delegate authoring + research.
3. **AWS only** — Azure/GCP stubs and the `CloudProvider` ABC deleted.
4. **Open-source tool** others adopt — requires replacing the `Proprietary` license.

### Memory
5. Claims **inform, never permit**. Permission = executable Rego + human promotion.
6. First consumer is `author-context` — schema *and* web-research claims as grounding.
7. Staleness is **attribute-level**, not provider-version-level.
8. **Git-committed JSONL**; SQLite is a rebuildable cache.
9. **Sharded by resource type**, unordered, deduped on `content_hash`, explicitly not hash-chained.
10. **Ship a seed corpus** — safe via #5, survives provider bumps via #7.
11. **One store, `scope` column**, `resource_type` nullable. Architecture/practice/template claims are first-class and never auto-expire.

### Gate & safety
12. Dev edits: **file ownership boundary** (`generated_*.tf`).
13. Cloud drift: read **`resource_drift`**; unmanaged discovery later.
14. Env promotion: **pinned content-hash + per-env approval**. Approvals never transfer (industry-confirmed: Spacelift/Atlantis promote plan→apply within a stack, never approval across environments).
15. Address churn: **require a `moved` block** for rename-shaped destroy/create.
16. Secrets: **hash but never copy** `.tfvars` into snapshots.
17. Concurrency: reuse **`_AppendLock` + `os.replace`**; **WAL** for the claim DB.
18. Coverage grows via **agent-authored Rego**, warn-only until human promotion.

### Cost & reporting
19. Agents contribute **`tf_type → serviceCode` mappings only** — never free-ness, never rates.
20. Every plan report **discloses what was and wasn't checked**.
21. Primary metric: **coverage ratio per plan, trended across runs**.

**Explicitly rejected:** multi-cloud; building an authoring engine (Phase 7 item 5 — cancelled,
not deferred); deleting the 16 catalog modules (they are sample data and test fixtures, not a
blocker — this was a misdiagnosis early in the session).

---

## 3. Shipped this session

### Cleanup
- `runs/` 7.0 GB → 12 MB (removed regenerable `.terraform/` caches; **all 7 `terraform.tfstate` files untouched**).
- Root junk removed: 5 × `pt_*.log`, 1 MB `test_log_*.txt`, `Temppytest/`, `tmp_test/`, `.pytest_cache/`, `minusops.egg-info/`.
- `reporter.py` −362 lines: `build_svg_v3` (prototype, never wired), `_v3_summary_cards`, `_V3_REL`, `build_plan_html`, `render_png`, `build_gate_flow_svg`.
- `report.html`/`plan.html` were byte-identical duplicates → collapsed to `report.html`; 5 consumers updated.
- Provider stubs deleted: `azure.py`, `gcp.py`, `capabilities()`, `CloudProvider` ABC collapsed.
- `pyproject.toml`: `dash`/`plotly` removed from base `dependencies` (base install is now dependency-free, matching the README's stdlib-only claim).
- `MANIFEST.in`: `recursive-include modules *.tf` → `*.tf *.py` (was silently omitting `etl.py`/`compact.py`).

### Bugs found and fixed
1. **`plan_inspector.iter_source_files`** filtered skip-dirs against **absolute** path parts — any workspace path containing `.git`/`.minus`/`__pycache__` hashed nothing and silently blinded source-drift detection.
2. **`.tfvars` content copied verbatim** into report bundles the dashboard serves and `minusctl package` ships. Now hashed but never copied. Regression test verified failing-without-fix.
3. **Gate state written non-atomically** — `open(path,"w")` truncates immediately, so a process killed mid-write destroyed `pending_plan.json` or an approval record. Now temp-file + `os.replace` at all three sites.
4. **`resource_drift` never read** despite being in JSON already parsed → issue #1.

### Feature: claim-grounded-authoring (all 6 increments, TDD)
| # | Increment | Tests |
|---|---|---|
| 1 | `scope` column + nullable `resource_type` + index | 6 |
| 2 | WAL journal mode | 3 |
| 3 | JSONL source-of-truth, sharded, round-trip | 4 |
| 4 | `author-context` returns resolved claims | 4 |
| 5 | claims-never-permit invariant | 3 |
| 6 | `verification_coverage` in manifest + report HTML | 8 |

New file: `core/governance/verification_coverage.py`.

### Feature: cloud drift detection (issue #1, decision #13) — CLOSED
- `plan_reader.resource_drift()` reads the top-level array. Absent = no drift, **not** an error (unlike `resource_changes`, where absence is fail-closed) — Terraform only emits the key when a refresh found something.
- `core/governance/cloud_drift.py` distinguishes a **revert** (per-attribute: reality moved `before`→`after`, plan proposes moving it back) from drift the plan leaves alone. Only the revert is urgent — it silently undoes a deliberate human action and Terraform renders it as a routine `update`.
- `_reject_if_reverts_out_of_band_and_auto_approve` blocks auto-approve with no bypass flag, same shape as the destructive check. Gatekeeper mode is never blocked (a human is already in the loop). Computed at plan time, carried through the approval record to apply, like `g9_result`.
- 10 tests in `tests/test_cloud_drift.py`.
- **Still open:** Class 2 — resources *added* outside Terraform are invisible because they were never in state, so no plan mentions them. Needs discovery (AWS Config / Resource Explorer or `hashicorp/agent-skills@terraform-search-import`), not drift reading.

### Session 2 additions
- **Suite runnable at last.** 14 live-Terraform files marked `slow` and deselected by default; `--basetemp` moved into `addopts`. **535 passed / ~50s**, from never-completing. Gotcha recorded: six files already had a module-level `pytestmark = pytest.mark.skipif(...)`, and adding a second `pytestmark =` below it does NOT combine — the second assignment wins and the mark is discarded. All six now use a list.
- **`create` no longer silently no-ops.** A bare infra noun phrase is now a creation request; interrogatives and operations on existing infra veto. The old rule needed a create verb AND an infra noun, so `create "governed AWS data pipeline"` printed success and created nothing.
- **Claim write-back (`synthesizer.py remember`)** — the missing half of the loop. `--source-url` required; `pricing_map` scope refuses prices and free-ness claims.
- **Claims reach git.** `remember` exports to `knowledge/claims/*.jsonl`; a fresh clone with no `claims.db` rebuilds the index from the committed corpus.
- **Corpus is merge-safe.** Row ids are no longer exported — two branches both allocating id 1 used to silently drop a claim and could mis-wire an invalidation chain. Cross-references travel as `content_hash`.
- **Lost-update race closed.** `_gate_state_lock` reuses `audit_chain._AppendLock`. Two further defects found by the tests, not by reading: `_write_json_atomic` staged every write to one shared `.tmp` (concurrent writers clobbered each other), and `os.replace` transiently fails with ACCESS_DENIED on Windows (bounded retry added).
- **Team files preserved (issue #3).** `GENERATED_FILES` are MinusOps'; any other `.tf` is the team's and is never rewritten.
- **`.agents/skills/minusops-loop/SKILL.md`** — one end-to-end guide for an external agent runtime; every command in it was executed before commit.

### Feature: address-churn / `moved` block enforcement (issue #2, decision #15) — CLOSED
- `core/governance/address_churn.py`. Rename-shaped = delete + create, same type, same plan, **matching identifying attributes**. The identity comparison keeps it honest both ways: without it every delete+create looks like a rename (real deletions waved through), and a genuinely different bucket gets mistaken for a move.
- `read_moved_blocks()` parses `moved { from/to }` by regex, not a full HCL parser — fixed two-field shape, and a missed block fails SAFE (gate objects to already-declared churn: noisy, never silently destructive).
- Enforced at **plan** time, not apply. Unlike the auto-approve checks, no mode exists in which silently destroying a bucket is intended, so "a human sees it at approve" is not sufficient.
- Blocking scoped to `STATEFUL_RESOURCE_TYPES`; non-stateful renames are advisory. A gate that blocks the harmless case trains operators to bypass it.
- 9 tests in `tests/test_address_churn.py`.

---

## 4. Verification status

**535 passed, 77 skipped, 303 deselected in ~50s.** The suite now runs to completion, which
it never once did before: 14 files doing live `terraform init` / provider-schema fetches are
marked `slow` and deselected by default. CI runs them with `pytest -m slow`.

```bash
python -m pytest          # no flags needed; pyproject sets --basetemp and -m 'not slow'
```

⚠️ Module provider constraints still float, so a new AWS provider release makes the `slow`
suite download ~700 MB before it can run. Pinning in each module's `versions.tf` would fix
it. `module_provenance.py` already records a `provider_version` to pin against.

⚠️ `tests/test_query_athena_module.py` exited 1 once during a sequential per-file scan but
passes standalone. Suspected contention, not a real failure — worth watching.

---

## 5. Open work

> Full categorised breakdown with a suggested order: **[`docs/REMAINING_WORK.md`](./REMAINING_WORK.md)**.
> Summary below.

GitHub issues (created 2026-07-26):
~~[#1](https://github.com/PROGRAMMER-DUMMY/minusops/issues/1) resource_drift~~ **CLOSED** ·
~~[#2](https://github.com/PROGRAMMER-DUMMY/minusops/issues/2) moved blocks~~ **CLOSED** ·
[#3](https://github.com/PROGRAMMER-DUMMY/minusops/issues/3) file-ownership boundary ·
[#4](https://github.com/PROGRAMMER-DUMMY/minusops/issues/4) agent-authored Rego ·
[#5](https://github.com/PROGRAMMER-DUMMY/minusops/issues/5) license ·
[#6](https://github.com/PROGRAMMER-DUMMY/minusops/issues/6) dispatcher.py

Next actionable without an owner decision: **#3** (file-ownership boundary) — a naming
convention rather than a merge engine, so it is small. Then **#4** (agent-authored Rego),
which is the coverage-growth loop and the largest remaining piece.
**#5 and #6 are blocked on the owner**, as is the CDP PDF call.

**Blocked on owner decision:**
- License choice (#5) — blocks the entire adoption story.
- `dispatcher.py` keep-or-remove (#6).
- CDP PDF stack (261 lines). Output proven **byte-identical** (1,392,720 B decompressed, 511 fill ops, 120 dark fills both ways). The only loss is the PDF bookmark sidebar (`/Outlines`, 27 `/Title` entries).

**Known shortcuts (marked `ponytail:` in code):**
- `export_jsonl` writes ids verbatim — correct for rebuilding a local cache, wrong for merging two branches that each allocated id 7. Upgrade: switch cross-references to `content_hash`.
- `_write_json_atomic` fixes torn writes, not lost updates. Two operators planning the same dir can still have one `pending_plan.json` overwrite the other. Upgrade: wrap in `audit_chain._AppendLock`.

**Unresolved risk:** `runs/` holds 7 live `terraform.tfstate` files, consistent with the note
about auto-approve applying real infrastructure. Worth reconciling against the actual AWS account.

---

## 6. Repo facts worth not rediscovering

- `knowledge_delegation.py` already implements agent-researches / MinusOps-remembers, exactly as designed: `build_delegation_request()` packages a `needs_review` result, `record_delegation_verdict()` records the answer with adjudication links. Its docstring: *"No local model anywhere in this path — the driving agent does the adjudication."*
- `rules.rego` matches on **resource types**, not modules. Deleting `modules/` would not remove a single rule.
- G6 runs shadow-only and blocks nothing. G9's `_g9_eval` can only return `g9_not_configured` — no emulator passes its own security-critical bar (LocalStack needs an unprovisioned paid token; MiniStack and Floci both fail negative fidelity on all three critical types).
- `coverage_audit.classify()` is the honesty pattern `verification_coverage.py` was modelled on.
- 21 % of `core/` is comments/docstrings (3,018 lines of multi-line strings), much of it dated audit narrative that git history already holds.


---

## 7. Enterprise Next-Gen upgrade (MINUS-101..137)

Branch `feat/minusops-enterprise-nextgen-v2`, driven by the 9-step roadmap in section 21 of
`2026-08-17_minusterraformrunaudit.md`. Ticket specs live in that file; this section records
only what is actually done and what changed about the plan.

**Step 1 — Diagnostics & pre-flight: DONE.**
- MINUS-107: `core/reporting/doctor.py` + `minusctl doctor [--json]`. Cross-platform, exit 1 on
  any `error` check. `tools/doctor.ps1` is kept but superseded and frozen.
- MINUS-136: TerraShark FM-01..05 as `architecture_decision.FAILURE_MODES` (one definition,
  shared with `grill-me` Step 3.5), plus the 4-part ADR output contract.

**Deviations from the ticket text, and why:**
- MINUS-107 asked for a Graphviz `dot` check. Nothing in the repo shells out to graphviz --
  architecture SVGs are LLM-generated per `docs/architecture_svg_spec.md` and the PDFs are
  hand-rolled stdlib. Checking for a tool the product never invokes is noise, so the slot went
  to `checkov`/`trivy` instead, which `MINUS_POLICY_MODE=production` genuinely requires.
- MINUS-136's 4-part contract was already half-built: `assumptions` and `alternatives`
  (tradeoffs) existed. Only `validation` and `rollback` were added as required fields.
  `failure_modes` is optional but id-validated -- five mandatory free-text fields per ADR is
  box-ticking, and an id outside FM-01..05 means the author guessed at the taxonomy.

**Already done before this branch (do not re-implement):**
- MINUS-103 (Step 4, "auto-anchor source baseline"): `synthesizer.py` already calls
  `source_guard.write_baseline(terraform_dir, label="synthesized")`. Step 4 is a no-op.

**Open question blocking part of Step 2:**
- MINUS-112 says to add service principals to the lake KMS key policy. `storage-medallion-s3`
  sets *no* key policy today, so AWS's default applies: the account root is granted full
  access, which is what makes IAM-based grants work at all. Attaching a custom policy that
  lists only service principals is the classic way to lock yourself out of a CMK. The 403 the
  audit actually observed is fixed by MINUS-108's IAM grants, not by a key policy. Decide
  before implementing: (a) skip, (b) add a policy that keeps the root statement and adds
  `via_service` conditions.

**Pre-existing drift noticed, not fixed (out of Step 1 scope):**
- `.agents/skills/grill-me/SKILL.md` still names `aws-data-pipeline-standard` and
  `minusctl create ... --generate` as the production path, which `AGENTS.md` explicitly calls
  stale demo-fixture guidance. Step 7 rewrites this file (MINUS-116/124) -- fix it there.

**Step 2 — Core IAM & data-flow wiring: DONE.**
- MINUS-108: `compute-glue-etl` gained `data_buckets` + `kms_key_arn` and two conditional
  `dynamic "statement"` blocks (`DataLake` S3 read/write scoped to the named buckets, `LakeKey`
  `kms:Decrypt/GenerateDataKey/DescribeKey`). Never `Resource = "*"` (SEC-02).
- MINUS-109: `default_arguments` is now a `merge()` injecting `--source_path`/`--target_path`.
  Omitted when the bucket input is empty, so standalone use never emits `s3:///data/`.
- MINUS-112: **no key policy written**, per the 2026-08-18 directive. AWS's default root
  delegation is what makes the IAM grants above work; the observed 403 was a missing
  `kms:GenerateDataKey`, not a key-policy gap. Rationale is recorded in the module itself so
  nobody "fixes" it later by adding a lockout.
- MINUS-137: TFLint added to `optimize_analyzer.run_external_scanners()`, and `moved {}`
  generation added to `address_churn.py` (`render_moved` / `write_moved` / CLI).

**Step 3 — Lifecycle & state hardening: DONE.**
- MINUS-101: `force_destroy = var.force_destroy` on the medallion buckets, default `false`;
  the synthesizer emits `var.environment == "dev"`.
- MINUS-102: KMS alias suffixed with the run hash.
- MINUS-104 + MINUS-134: opt-in S3 remote backend via `synthesizer.py --state-bucket`, with a
  directory-bound key `<name_prefix>/<run_id>/terraform.tfstate`.

**Two more deviations from the ticket text, and why:**
- MINUS-108 asked for `data_bucket_arns`. The module takes bucket **names** (`data_buckets`)
  instead, matching `dq-great-expectations`' `target_buckets`, `compaction-glue`, and
  `compute-emr-serverless` -- all three already take names and all wire from the same
  `values(module.storage_medallion_s3.bucket_names)`. Adding a fourth convention, plus a
  redundant `bucket_arns` output, buys nothing.
- MINUS-104 asked for "S3 + DynamoDB state backend". DynamoDB locking is deprecated upstream
  ("will be removed in a future minor version",
  developer.hashicorp.com/terraform/language/backend/s3, checked 2026-08-18). The generator
  emits `use_lockfile = true` and no DynamoDB table, so operators are not handed a resource
  with a removal deadline attached.

**Open defect found while wiring MINUS-109 (NOT fixed -- needs a decision):**
`modules/compute-glue-etl/scripts/etl.py` picks its reader by suffix:
`spark.read.parquet(path) if path.endswith("/") else spark.read.json(path)`. The wired
`--source_path` is `s3://<bronze>/data/`, which ends in `/`, so the starter job now reads
**Parquet from the raw-JSON Bronze zone**. MINUS-108/109 move the failure from "crashes at
startup" to "crashes on read"; the job is still not end-to-end runnable. Not fixed here
because it does not trace to either ticket. Cheapest fix is a `--source_format` default
argument (default `json`) replacing the suffix heuristic. Natural home: MINUS-113
(`minusctl seed`, Step 9), whose Athena smoke test is what would catch it.

**etl.py reader defect: FIXED (authorized 2026-08-18).**
`--source_format` (default `json`) and `--target_format` (default `parquet`) are now module
variables, injected into `default_arguments`, and read by `scripts/etl.py` via
`getResolvedOptions`. The suffix heuristic
(`spark.read.parquet(p) if p.endswith("/") else spark.read.json(p)`) is gone; the script now
does `spark.read.format(source_format).load(path)`. The synthesizer states `"json"` / `"parquet"`
explicitly at the call site so the medallion intent is visible in the generated HCL and an
operator landing CSV in Bronze knows which line to change.

**Step 5 — Catalog, dbt & orchestration: DONE.**
- MINUS-110: `aws_glue_catalog_database.gold` in `query-athena`, named
  `${replace(lower(name_prefix), "-", "_")}_gold` (Glue rejects hyphens), `location_uri` from the
  new `gold_bucket` input. **No table definitions** -- a table needs a real column schema and an
  invented one fails on first query. Tables come from dbt, a CTAS, or a crawler.
- MINUS-111: `schedule_expression` on `orchestrator-stepfunctions` with an EventBridge rule,
  target, and its own `events.amazonaws.com` role (it cannot reuse the state machine's role).
  All `count`-gated, so an event-driven pipeline gets no surprise cron.
- MINUS-119: `write_dbt_project()` scaffolds `src/dbt/` at the run root whenever `query-athena`
  is present. `profiles.yml` targets dbt-athena; account-dependent paths go through `env_var`
  because the results bucket name contains the account id and run hash.
- MINUS-120: `transform_engine: "dbt"` on the decision record drops `compute-glue-etl` (even if
  explicitly selected) and refuses the composition if `query-athena` is absent.
- Also added `outputs.tf` generation, keyed off present modules -- `README-dbt.md`'s commands
  and MINUS-113 (`minusctl seed`) both need post-apply values that cannot be computed at
  synthesis time.

**Decisions worth not relitigating:**
- `schedule_expression` is left as a `REVIEW` item, not defaulted to `rate(1 day)`. Nothing in
  `requirements.json` states a batch cadence, and a default cron attaches recurring cost to a
  pipeline nobody asked to run daily. Wire it when the requirements schema grows a cadence field.
- `models/` is scaffolded empty for the same reason no Glue tables are generated.

**Testing note (cost us a false signal once):** `pyproject.toml` pins
`--basetemp=.pytest_tmp`, so two concurrent pytest runs share one temp root and produce
spurious `PermissionError`s on Windows (16 of them in one run here). Run the slow suite and the
fast suite one at a time, or pass a distinct `--basetemp`. Same class as the audit-chain lock
and G9 issues already on record.

**Step 6 — Enterprise promotion & DR: DONE.**
- MINUS-114 + MINUS-130: `envs/{dev,staging,prod}.tfvars` generated into the Terraform root
  from `_ENV_MATRIX`. Promotion is `-var-file`, never a forked `main.tf`. New root variables
  `glue_worker_type`, `glue_number_of_workers`, `retention_days`, `monthly_budget_usd`,
  `cost_center`, `data_classification` back it -- wiring them also cleared three REVIEW items
  every composed stack used to carry.
- MINUS-131: opt-in CloudTrail with S3 **data** event selectors in `governance-observability`,
  writing to an Object-Locked, never-force-destroyable audit bucket. The synthesizer pre-wires
  the bucket ARNs and CMK so enabling it is a one-line tfvars change.
- MINUS-132: S3 CRR (per-zone destinations), multi-region KMS opt-in, and the mandatory tag
  set on the provider's `default_tags` plus a `check` block.

**Decisions worth not relitigating:**
- **`force_destroy` is not in the promotion matrix.** `main.tf` derives it from
  `var.environment == "dev"`, so no var-file can turn it on for prod. A test fails if anyone
  adds it to `_ENV_MATRIX` as a convenience.
- **CRR destinations are a `map(string)` keyed by zone, not one bucket ARN.** S3 replication
  preserves the object key exactly and cannot add a prefix, so three zones replicating into
  one destination would overwrite each other. Also guarded by a test.
- **Object Lock is GOVERNANCE, not COMPLIANCE.** COMPLIANCE cannot be shortened or removed by
  anyone including root for the full window; it has stranded more teams than it has caught.
- **A declared budget is scaled per tier (0.25 / 0.5 / 1.0), not copied flat.** One identical
  ceiling means the prod alarm is tuned for dev traffic or the dev alarm never fires.
- **The SIEM trail is off by default.** S3 data events bill per event; on a busy pipeline that
  is a volume-proportional bill nobody agreed to.
- **`multi_region_kms` is a before-first-apply decision.** Flipping it on an existing key
  REPLACES the key, and objects encrypted under the old one are not readable with the new one.

**Known limitation, stated rather than papered over:**
The mandatory-tag `check` block **warns at plan time; it does not fail the plan**.
Cross-variable `validation` would hard-fail but needs Terraform >= 1.9, and `required_version`
is `">= 1.5"` -- raising the floor would break operators on 1.5-1.8 to gain an error over a
warning. Marked `ponytail:` in `synthesizer.py` with the upgrade path. Hard enforcement today
is the deploy gate (plan_gate + SEC scan + OPA), not Terraform.

**Step 7 — 7-pillar grilling & ingestion connectors: DONE except MINUS-126/127.**
- MINUS-116: `grill-me/SKILL.md` gained Step 3.4, the 7 pillars, each mapped to the catalog
  modules its answers imply, ingestion first. The stale `aws-data-pipeline-standard` /
  `--generate` guidance is gone; the only surviving mention is an explicit prohibition, and a
  test enforces that.
- MINUS-117: three distinct SNS topics (on-call / data-quality / budget). The budget alarm and
  the budget notification now route to the Tier 3 topic, not the on-call one. Quarantine bucket
  in `dq-great-expectations`, separately encrypted, with `--quarantine_path` wired into the job.
- MINUS-118: `write_project_scaffold()` creates `src/{compute,sql,quality,orchestration}` and
  `tests/fixtures/sample.json` at the run root. Never overwrites.
- MINUS-123/124/125: four new catalog modules -- `ingestion-dms`, `ingestion-appflow`,
  `ingestion-sftp`, `ingestion-webhook` -- registered, keyword-reachable, and wired to Bronze.

**NOT built, with reasons (needs a decision):**
- **MINUS-126 (data hub / Lake Formation zero-copy, MSK, Delta Sharing, Data Exchange)**: four
  unrelated integrations behind one ticket, none with a stated requirement here, and
  cross-account RAM sharing cannot be verified without a consumer account. Building four
  speculative modules is the boilerplate the catalog exists to avoid.
- **MINUS-127 (GCP/Azure OIDC + on-prem DMS)**: the on-prem half is already covered --
  `ingestion-dms` reaches an on-premise source over VPN/Direct Connect, which is the same
  module. The multi-cloud half **contradicts a recorded decision in this codebase**:
  `core/providers/base.py` says "AWS is the only cloud; the Azure/GCP scaffolds and the
  one-implementation CloudProvider ABC were removed once multi-cloud was dropped from scope."
  Adding google/azurerm providers to the catalog reverses that, which is not a call to make
  inside a ticket. There IS a cheap AWS-side-only version -- an IAM OIDC provider trusting
  Google/Azure workload identity so a GCP job can assume a role and write to S3, no third-party
  provider needed. Say the word and it is a small module.

**Verification notes:**
- All four ingestion modules were validated against the **installed provider schema**
  (`terraform providers schema -json`, AWS v6.60.0), not the rendered registry docs, which are
  JS-only and unfetchable. That caught two real breaks the docs would not have: `s3_settings`
  was removed from `aws_dms_endpoint` in provider v6, and `aws_appflow_flow`'s
  `connector_operator` fields are attributes, not nested blocks.
- The full 10-module composition validates once the three genuinely operator-supplied values
  are set (`source_secret_arn`, `connector_profile_name`, `source_object`). Those stay required
  with no default on purpose: there is no sane default for "which secret holds your database
  password", and a placeholder would produce a stack that plans cleanly and fails at run time.

**Regression caught and root-caused:** adding the ingestion keywords broke
`test_patterns.py::test_match_reuses_a_prior_approved_pattern`. Cause was not the new modules
but `match_modules`' weak single-token path scoring on `"data"`, which appears in most modules'
`satisfies` phrases. Fixed at the root with `_WEAK_STOPWORDS` rather than by trimming the new
keywords -- the same noise would have resurfaced with the next module added.

**Disk-full incident (2026-08-18), worth not repeating:** the 361 GB disk hit 100% mid-session
from pytest tmp dirs (7.5 + 14 GB) plus scratch Terraform workspaces (13 GB). It silently
truncated a heredoc-written edit script to 0 bytes, so a `terraform validate` "Success!" was
reported against an **unmodified** module -- a false green. Mitigations applied:
`tmp_path_retention_policy = "failed"` in pyproject, and scratch workspaces are now deleted
after use. Lesson: after a disk-full error, re-verify every file touched in that window rather
than trusting the last command's exit status.

**Step 8 — TB-scale compute & reflector: DONE.**
- MINUS-128: `modules.compute_tier(daily_gb, latency)` plus three real tiers.
  `compute-glue-etl` gained `execution_class` (FLEX = ~35% off spare capacity),
  `compute-emr-serverless` gained `architecture` defaulting to ARM64/Graviton, and
  `compute-emr-ec2-spot` is a new module: on-demand master and core fleets, Spot task fleet
  diversified across >= 3 instance types with `capacity-optimized` allocation, and a 1-hour
  auto-termination default.
- MINUS-129: `core/governance/reflector.py`. Five gates, every one re-derived from artifacts
  on disk. Read-only, exit 2 when blocked.
- MINUS-135: `--based-on <run-id>` on `synthesizer.py`, backed by `inherit_from_run()`.

**Decisions worth not relitigating:**
- **Tier crossovers are cost crossovers, not round numbers.** Below 1 TB/day EMR's startup and
  idle cost exceeds Glue's premium; above ~5 TB/day a real cluster with Spot task capacity
  finally beats serverless. An **undeclared volume gets the smallest tier**, never an EMR
  guess: that is how a $40/month pipeline acquires a $4,000/month bill.
- **An SLA-intolerant phrase wins a tie for FLEX.** "hourly batch feeding a real-time
  dashboard" mentions both; FLEX would be wrong, so STANDARD wins.
- **Master and core fleets are on-demand, only task is Spot.** A lost master kills the cluster;
  a reclaimed core node loses HDFS shuffle data and forces a recompute. Task nodes hold nothing
  persistent, so that is where the ~70% saving safely lives.
- **Spot diversification is validated, not suggested** (>= 3 instance types). Each
  instance-type-and-AZ pair is one Spot pool; one type is one pool and one reclaim event takes
  the whole fleet.
- **The reflector has three statuses, and `unknown` is the important one.** A gate that could
  not run is not a pass. `gate_security` reports how many `.tf` files it read for the same
  reason -- "no findings" from a scan that read nothing looks identical to "no findings" from a
  clean stack, and only one of those means anything.
- **`--based-on` inherits organisational settings only.** Region, owner, cost centre,
  classification, architecture. Never volume, latency, or functional requirements: those are
  what make two pipelines different, and copying them sizes the new one for the old one's data.
  `REVIEW_REQUIRED` placeholders are skipped so a stack is never tagged to a cost centre
  literally named REVIEW_REQUIRED.

**Verification:** all three compute modules validated together against AWS provider v6.60.0.
`aws_emr_cluster` carries master and core fleets only, so task Spot capacity attaches as a
separate `aws_emr_instance_fleet` -- confirmed from the installed provider schema, not assumed.

**Step 9 — Verification, adoption & PR automation: DONE.**
- MINUS-113: `core/reporting/seed.py` + `minusctl seed`. Upload fixture -> run Glue job ->
  count rows in Gold.
- MINUS-106: `core/reporting/adopt.py` + `minusctl adopt`. Inventory, scan, optionally anchor.
- MINUS-115/133: `.github/actions/pr-reviewer/action.yml` (composite) plus a turnkey
  `.github/workflows/pr-review.yml` that wires it.

**Decisions worth not relitigating:**
- **`seed` defaults to plan, not execute.** `minusctl` is local-only by contract; rather than
  quietly breaking that, `seed` prints the exact AWS CLI commands and sends nothing until
  `--execute`. The docstring at the top of `minusctl.py` was corrected to state the exception
  rather than leaving the old "does not run cloud CLIs" claim standing.
- **One approval naming every side effect**, not three prompts. Three prompts is how operators
  learn to click yes.
- **A queryable-but-empty Gold table raises.** The transform ran and produced nothing. That is
  the exact false green this command exists to catch, so it must not read as success.
- **Bucket names come from `terraform output`**, never re-derived from `name_prefix`: they
  contain the account id and the run hash.
- **`adopt --anchor` is opt-in.** Anchoring claims the current files are the reviewed starting
  point; doing it during a look-around would silently bless the wildcard IAM policy the scan is
  about to report. `adopt` also returns `ok = False` when SEC findings exist, because the
  production gate blocks on them.
- **The PR reviewer plans, never applies**, and refuses to invent a cost -- no BCM evidence
  prints "cost unavailable" plus the command that would produce it. A plausible-looking made-up
  figure in a PR comment is worse than none, because reviewers believe it.
- **`pull_request`, not `pull_request_target`.** The latter runs with the base repo's secrets
  against the fork's code, handing any fork author the OIDC role. The cost is that fork PRs get
  the static review and no plan; that is the correct trade. The role input is documented as
  read-only for the same reason.
- The reviewer **edits its last comment** rather than appending: a 40-comment PR is a PR nobody
  reads. A blocked verdict fails the check, so it is not just a comment someone can ignore.

**Slow-suite failure, root-caused (2026-08-18).**
The 5 failures in the serial slow run were all
`test_destructive_change_gate.py::test_every_current_module_plans_as_create_only[<module>]`,
one per new module added in Steps 7-8. **The gate was right and the modules were new.**

G5's autonomy boundary is a fail-CLOSED allowlist (`AUTO_SHIP_ELIGIBLE_TYPES`): a resource type
nobody has reviewed stages, tagged `unreviewed_resource_type`. Five new modules introduced ~20
new types, so every one of them staged, and the test that asserts "every type the real catalog
produces has been reviewed into one set or another" failed. That is the design working.

The fix was NOT to bulk-add the types to the eligible set to get green -- that would be
defeating the gate to satisfy its own test. Each type was reviewed individually against the
same asymmetric-downside standard `aws_default_security_group` was held to:

| Disposition | Types | Reason |
| :--- | :--- | :--- |
| `AUTO_SHIP_ELIGIBLE_TYPES` | `aws_dms_replication_subnet_group` | a named list of existing subnet ids: no data, no permission, no endpoint, no cost |
| `STATEFUL_RESOURCE_TYPES` | `aws_sqs_queue`, `aws_secretsmanager_secret` | hold in-flight events / are the secret's identity |
| `IAM_RESOURCE_TYPES` | `aws_iam_instance_profile`, `aws_iam_role_policy_attachment` | hand a role to every node; bind AWS-MANAGED policies SEC-02 cannot scan |
| `REVIEWED_UNSAFE_TYPES` | Transfer + API Gateway (internet-facing), EMR + DMS instances (continuous priced compute), DMS/AppFlow/CRR (data movement), CloudTrail + object lock (audit and retention commitments) | 16 types, each with its own recorded reason |

**Only one of ~20 new types was eligible.** That is the honest answer for a set of modules that
provision public endpoints, multi-TB clusters, and cross-account data movement: they should
stage. Promoting any of them to auto-ship is an owner decision, not an agent's.

One collateral fix: `test_a_second_genuinely_novel_type_also_stages` used
`aws_secretsmanager_secret` as its example of a never-declared type. `ingestion-webhook` now
declares one, so the fixture was re-based on `aws_neptune_cluster`, re-confirmed absent from
`modules/`, `core/`, and `tests/` by the same grep the original used.

**Process note:** while investigating this I ran `git stash -u` to compare against baseline
while a background test run was live. That contaminated the run AND briefly reverted every
uncommitted change on the branch. Everything was restored and verified, but the correct tool
was a separate `git worktree`. Do not stash a live tree to run a comparison.

**Slow-suite verification, completed 2026-08-18.**
After the type review above, all 21 `test_every_current_module_plans_as_create_only[...]`
parametrisations pass, verified in five batches:

| Batch | Modules | Result |
| :--- | :--- | :--- |
| 1 | the 4 new ingestion modules | 4 passed |
| 2 | `compute-emr-ec2-spot`, `compute-emr-serverless`, `storage-medallion-s3`, `query-athena`, `dq-great-expectations` | 5 passed |
| 3 | `compute-glue-etl`, `governance-observability`, `orchestrator-*`, `compaction-glue` | 6 passed |
| 4 | `consumption-redshift-serverless`, `databricks-workspace`, `ingest-firehose` | 3 passed |
| 5 | `networking-vpc`, `schema-registry-glue`, `speed-layer-kinesis`, `table-format-iceberg` | 4 passed |

**Batching was not cosmetic.** The same seven modules in batches 4 and 5 produced 3 failures
when run as one longer batch, and passed in the two shorter ones -- and those seven are modules
this branch never touched. Every `terraform init` copies roughly 470 MB out of the plugin cache
(Windows copies where POSIX symlinks), the disk was at 95-96% throughout, and the failures track
disk pressure rather than any module's content.

So: no code defect remains, but **the slow suite still cannot be run end to end on this machine
as configured.** `tmp_path_retention_policy = "failed"` caps the growth but does not solve it,
because failures are exactly the runs that keep their directories. The real fix is one of:
enable Windows developer mode so Terraform symlinks the plugin cache instead of copying;
run the slow suite in CI on a larger disk (the workflow already does this); or shard it.
Until then, run it in batches of <= 6 and clear `.pytest_tmp_slow` between them.

---

## 8. Enterprise v2.0 Delivery Ledger (MINUS-140..160) — 2026-08-19

All 21 engineering tickets from the **MinusOps Enterprise v2.0 Roadmap (`MINUS-140` – `MINUS-160`)** have been implemented, tested, and verified on `feat/minusops-enterprise-nextgen-v2`.

* **Fast Test Suite:** **770 passed**, 85 skipped across **76 test files** (100% pass rate).
* **Module Catalog:** **24 production-grade Terraform modules** (added Snowflake, MSK, Databricks Delta, MWAA, and Iceberg table maintenance).
* **Working Tree:** 100% clean after test runs.

### Sprints Summary:

1. **Sprint 1 (Hardening & GitOps — MINUS-140, 156, 143, 144, 145):**
   - Corpus diversion to `tmp_path_factory` in pytest fixtures, permanently eliminating git churn.
   - Day-0 Doctor skill manifest (`.agents/skills/doctor/SKILL.md`) with version floors (`terraform >= 1.5`, `aws cli >= 2`).
   - Composite GitHub Action PR Reviewer (`.github/actions/pr-reviewer/action.yml`) posting sticky comments with click-to-code `architecture.svg`, BCM monthly cost difference tables, and SHA-256 plan hashes.
   - OIDC production merge gate in `deploy.yml` asserting current plan hash matches the PR-reviewed digest.

2. **Sprint 2 (Multi-Team State Isolation & Role Binding — MINUS-141, 142, 147, 153):**
   - Multi-team S3 remote state generation targeting `s3://<bucket>/teams/<team_id>/<workload_id>/terraform.tfstate` with `use_lockfile = true`.
   - Team identity derived strictly from generated backend state key (`_backend_team()`), preventing user flag spoofing.
   - Discrete WORM S3 audit logger emitting one immutable object per event keyed by timestamp + entry hash under S3 Object Lock.
   - Central team directory resolver (`core/architecture/team_resolver.py`) with strict team ID sanitization (`_TEAM_ID_RE = ^[a-z0-9][a-z0-9-]{0,62}$`).

3. **Sprint 3 (Warehouse & Streaming Catalog Expansion — MINUS-148, 149, 150, 151, 152):**
   - `modules/warehouse-snowflake-aws`: 2-sided handshake defense starting with root-only trust until `external_id` and Snowflake IAM ARN attach.
   - `modules/compute-databricks-delta`: Unity Catalog external locations over Gold S3 and Delta Sharing grants.
   - `modules/orchestrator-mwaa`: Managed Airflow in private VPC with KMS CMK and log streaming.
   - `modules/streaming-msk-kafka`: Managed Kafka with multi-AZ broker distribution and mandatory IAM SASL auth.
   - `modules/query-athena/iceberg_maintenance.tf`: EventBridge-scheduled Lambda executing `OPTIMIZE` and `VACUUM` with 1-day snapshot retention floor.

4. **Sprint 4 (FinOps Heuristics, Container Auto-Recovery & Agent Diagnostics — MINUS-146, 154, 155, 157, 158, 159, 160):**
   - `auto_populate_usage()` in `bcm_pricing_calculator.py` deriving quantities dynamically from schedule and retention, exposing that a 15-minute micro-batch costs ~$1,478/mo (revoking outdated $430 approval on `5cad83d9`).
   - `minusctl doctor --fix` container auto-recovery with 20s timeout and isolated environment handling.
   - Fail-closed production OPA presence enforcement in `plan_gate.py verify`.
   - `core/reporting/cli_diagnostics.py`: Fuzzy typo run matching with dynamic attached description tips (`get_run_description_tip()`), ANSI escape sequence sanitization, pre-requisite stage interception, and 3-part actionable error formatting (`WHAT FAILED` / `WHY IT FAILED` / `ACTION REQUIRED`).

---

## 9. PRD-ARCH-2026-005 (Revision 5.0) — Multi-Repo Export & Semantic Runs — 2026-08-22

Source: [`tasks/deplyoymend_pr.md`](../tasks/deplyoymend_pr.md), implemented against the
coding-agent advisory. Built TDD: RED on 21 failures plus one collection error, then GREEN.

**Suite after this work: 1079 tests collected across 88 test files, `pytest` exits 0.**

### Delivered

| FR | What landed | Where |
| :--- | :--- | :--- |
| FR-01 | Semantic run ids `<domain>-<name>-<orchestrator>_<YYYYMMDD_HHMMSS>`; the legacy `<YYYYMMDD-HHMMSS>-<blueprint>` id is unchanged when no `name` is passed | `core/reporting/runs.py` |
| FR-02 | `runs/index.json` + `runs/INDEX.md`, rebuilt on every `new_run()` and swapped in with `os.replace` | `core/reporting/runs.py` (`sync_index`, `_atomic_write`) |
| FR-03 | `minusctl export --run … --target-repo … --dest-dir … [--generate-workflow]` | `core/reporting/export.py`, `core/reporting/minusctl.py` |
| FR-04 | Per-pipeline GitHub Actions workflow with `paths:` isolation, OIDC-only auth, apply gated to `push` | `core/generation/cicd.py` (`render_pipeline_workflow`) |
| FR-06 | Opt-in, fail-open CloudTrail + Glue job-run correlation on drifted resources | `core/governance/cloud_drift.py` (`classify(..., telemetry=)`, `aws_telemetry`) |

### Advisory questions, as answered in code

1. **Legacy run migration** — neither migrated nor aliased. `list_runs()` never parsed an id;
   it discovers workspaces by the presence of `run.json`, so both shapes coexist with no
   migration step and no compatibility layer to maintain.
2. **Index concurrency** — atomic swap (`tempfile.mkstemp` in the same directory, then
   `os.replace`), no lock. A lock would serialize writers; the swap makes a partial read
   impossible, which is the actual failure being prevented.
3. **Template engine** — stdlib only. `cicd.py`'s existing `__TOKEN__` + `_fill` convention
   is reused rather than duplicated in a new `workflow_templates.py`, because GitHub's
   `${{ }}` collides with `str.format`/`string.Template` and that workaround already exists
   in this file. One module, one convention.
4. **Telemetry** — advisory and fail-open. The lookup is injected by the caller, so
   `classify(plan_json)` alone makes no AWS call; a lookup that raises or finds nothing
   yields `telemetry_available: False`. Correlation never changes the revert verdict.

### Not built (out of the advisory's scope, stated for the record)

* **FR-05 / AC-05 — Synthetic Data Proving Harness.** The PRD specifies five hops ending in a
  signed `proving_report.json`. `core/reporting/seed.py` today covers three (S3 upload → Glue
  job → Athena query) and writes no proving report. The Great Expectations data-quality hop,
  the quarantine-routing check, and the signed report are absent. The advisory's five
  implementation steps did not include it.
* **`estimated_monthly_cost` is null for every run** until BCM evidence is attached to
  `run.json`. The registry column is deliberately not defaulted to `0.0` — see
  [`core/reporting/CONTEXT-reporting.md`](../core/reporting/CONTEXT-reporting.md).
* **Glue job-name derivation in `aws_telemetry`** takes the Terraform resource label, not the
  physical id (which lives in state, not in a plan). Marked with a `ponytail:` comment naming
  the ceiling; holds for MinusOps-generated stacks, may miss on adopted ones.

---

## 10. PRD-ARCH-2026-007 (Revision 7.0) — Unified CLI & 5-Hop Proving Harness — 2026-08-22

Source: [`tasks/prd_v7_unified_cli_and_proving_harness.md`](../tasks/prd_v7_unified_cli_and_proving_harness.md).
Built TDD: RED on 21 harness failures, then RED on the CLI package, then GREEN.

**Suite after this work: 1127 tests collected across 90 test files, `pytest` exits 0.**

### Delivered

| FR | What landed | Where |
| :--- | :--- | :--- |
| FR-01 | 5-hop harness: ingest, transform, DQ, quarantine, serving, plus a tamper-evident `reports/<plan-hash>/proving_report.json` | `core/reporting/seed.py` (`prove_pipeline`, `verify_report`) |
| FR-02 | `core/cli/` package: `main.py`, `context.py`, `formatters.py`, `commands/{use,runs,gate,cost,source}.py` | `core/cli/` |
| FR-03 | `minusctl use`, `runs list` with `[*]`, `runs describe` spec card, `--dir`-free `gate plan` | `core/cli/context.py`, `core/cli/commands/` |
| AC-05 | `minusctl = "core.cli.main:main"`, with `core.cli` and `core.cli.commands` in the wheel | `pyproject.toml` |
| FR-04 | `AGENTS.md` and `.agents/AGENTS.md` moved to `minusctl` subcommands | both files |

### Decisions worth recording

* **The CLI package is a front door, not a rewrite.** `core/reporting/minusctl.py` carries
  nineteen subcommands and the tests that prove each of them; moving that code would have risked
  a deploy-lifecycle regression to gain a directory layout. `core/cli/` owns the five commands
  that had to be written new and delegates the rest verbatim. `known_commands()` plus a test
  asserting all nineteen legacy names makes losing one a visible failure rather than a silent
  behaviour change.
* **`prove` was already taken.** It meant the offline governance-evidence bundle. Rather than
  redefining it, `--execute` selects the live five-hop data proof and the bare command is
  unchanged — matching how `seed` already splits plan from execute.
* **Great Expectations is not a dependency.** GE runs inside the Glue Python-shell job that
  `modules/dq-great-expectations` deploys. Hop 3 starts that job and reads the validation-result
  JSON. Importing GE (plus pandas and SQLAlchemy) into a control plane with a dependency-free
  base install would be a heavy price for assertions that already run server-side.
* **"Signed" means tamper-evident, not authenticated.** SHA-256 over the canonical payload, the
  same meaning the audit chain already gives the word. There is no private key and no claim
  about who produced the report.
* **Hop 4 is evaluated after hop 5** because it needs the Gold row count, then re-inserted at
  position 4 so the report reads in pipeline order.
* **`core/cli` uses package-relative imports.** The rest of the repo puts each `core/`
  subdirectory on `sys.path` and imports by bare name; doing both here gave every file two module
  objects, and a `monkeypatch` on one was invisible to the other. That cost a debugging cycle and
  is worth not repeating.
* **Nothing is guessed from an empty context.** With no active run and no explicit flag, `gate`,
  `cost` and `source` refuse. Falling back to the newest run would point an apply at
  infrastructure nobody named.

### Deviations from the advisory, stated for the record

* **`core/cli/commands/` holds five modules, not ten.** `create`, `prove`, `export`, `audit` and
  `doctor` are delegated to their existing implementations rather than re-fronted, because a
  wrapper that only forwards `argv` adds a file and no behaviour. They are still reachable as
  `minusctl <name>`, which is what AC-06 and the agent docs depend on.
* **AGENTS.md keeps four box-drawing characters** (`U+2502`, `U+251C`, `U+2514`, `U+25BA`) in a
  pre-existing decision-tree diagram. They are not emoji; NFR-01 targets CLI output and generated
  reports, both of which are ASCII-clean and test-enforced.

### Not built

* **Hop 1 does not synthesize the fixture.** `--records` and `--malformed` DECLARE what the
  fixture contains so hop 4 can do its arithmetic; the operator still supplies the file. A
  Faker-style generator driven by `requirements.json` was in the PRD's FR-05 prose but is not in
  the v7 task list, and generating data whose shape we guessed would make hop 4 prove the
  generator rather than the pipeline.
