# MinusOps Control Plane — Project Plan

This project plan maps out the completed milestones and outlines the remaining deliverables required to test, run, and maintain the agentic DevOps workflow.

> **Note (pivot):** the repo is now the **generic governance engine only**. The original AWS
> medallion-pipeline example (its `*.tf`, ETL scripts, and the `bootstrap/aws` governance IaC)
> was removed so the engine stays workload-agnostic — you bring your own Terraform and pass an
> explicit `--dir`. The history below is kept for context; the medallion milestones validated the
> engine against a real workload before it was generalised.

---

## Project Milestones & Status

```mermaid
gantt
    title Ingestion Pipeline & Control Plane Roadmap
    dateFormat  YYYY-MM-DD
    section Phase 1: Infrastructure
    S3 & Glue Deployment         :done, p1, 2026-06-25, 1d
    Step Functions Orchestrator   :done, p2, 2026-06-25, 1d
    Observability & SNS Alarms    :done, p3, 2026-06-25, 1d
    section Phase 2: DevOps Control
    Auditing & HITL Gatekeeping   :done, p4, 2026-06-26, 1d
    Optimization Scanner Engine  :done, p5, 2026-06-26, 1d
    Operator Control Dashboard   :done, p6, 2026-06-26, 1d
    section Phase 3: Testing
    Natural Language Dispatcher  :done, p8, 2026-06-27, 1d
    Scheduled Health Daemon      :p10, 2026-06-27, 1d
```

---

## Natural Language Intent Dispatcher
We deployed a central query parsing coordinator: [dispatcher.py](/core/reporting/dispatcher.py).

Rather than executing scripts individually, operators can type vague queries. Operational queries still route to the target script:
* **Query**: `"check if the pipeline is online"` &rarr; triggers `health_checker.py`.
* **Query**: `"audit the code security"` &rarr; triggers `optimize_analyzer.py`.
* **Query**: `"forecast the monthly cost"` &rarr; triggers `budget_calculator.py`.
* **Query**: `"why did spend spike / find anomalies"` &rarr; triggers `finops_agent.py` (live AWS).
* **Query**: `"apply the changes"` &rarr; triggers `plan_gate.py run` (the deploy gate).

Creation requests now take a safer enterprise path through [intent_resolver.py](/core/generation/intent_resolver.py) and the requirements/architecture decision record:
* **Query**: `"create a data pipeline"` &rarr; creates a requirements-first run; no production Terraform is generated until requirements and an architecture decision are recorded.
* The resolver creates a requirements-first path and lists the next safe actions.
* It does not generate Terraform, plan, or apply infrastructure by itself.
* The blueprint registry can be checked with `python core/generation/intent_resolver.py --validate-blueprints`.

---

## What Has Been Built (Completed)

1. **Reference workload (removed after validation)**:
   * The medallion pipeline (S3 bronze/silver/gold with KMS + lifecycle, PySpark ETL jobs, a
     Step Functions orchestrator with SQS DLQ + EventBridge triggers, and CloudWatch→SNS alarms)
     was built first to exercise the engine end-to-end, then deleted so the repo is engine-only.
   * It remains recoverable from git history if a worked example is needed for reference.
2. **`agy` Customizations & Diagnostics**:
   * Workspace Rules ([AGENTS.md](/.agents/AGENTS.md)) to enforce safety boundaries.
   * [audit_logger.py](/core/governance/audit_logger.py) and [plan_gate.py](/core/governance/plan_gate.py) to audit actions and gate mutating deployments (plan-bound, MFA via the cloud CLI).
   * [approval.py](/core/governance/approval.py) approval gate with selectable `gatekeeper` / `auto-approve` modes for side effects.
   * [finops_agent.py](/core/reporting/finops_agent.py) live cost intelligence over the real account (Cost Explorer, anomalies, CloudTrail correlation).
   * [optimize_analyzer.py](/core/reporting/optimize_analyzer.py) configuration scanner.
   * [intent_resolver.py](/core/generation/intent_resolver.py) to map short enterprise creation requests to requirements-first runs and architecture decisions.
   * Live FinOps operator console ([app/dashboard_app.py](/app/dashboard_app.py)) — a Plotly Dash app rendering real spend, monthly burn, and the anomaly ledger via the active cloud provider.

---

## Current Status

What runs end to end, offline, with no cloud credentials:

* Governed production runs start via `core/reporting/minusctl.py create ...`; Terraform generation waits for completed requirements and an architecture decision. The no-cloud
  `minusctl demo`, producing the full report bundle (`architecture.svg`, `plan.json`,
  `cost.json`, `plan.html`/`.pdf`, `cost.html`/`.pdf`, `report.html`, and BCM review payloads).
* PDF rendering has a built-in text fallback when headless Edge/Chrome is unavailable.
* The full test suite passes (`python -m pytest`).
* `minusctl prove` confirms the offline governance chain (run → report artifacts → audit-chain
  integrity → readiness) and reports the remaining AWS-gated steps.
* Cost totals publish only from the AWS BCM Pricing Calculator; generated usage carries
  `REVIEW_REQUIRED` placeholders until a reviewed profile is supplied, so unsupported totals are
  never fabricated.

The AWS-side steps require live credentials and Terraform, and are run by the operator against
their own account:

```powershell
python core/governance/plan_gate.py verify --dir runs/<run-id>/terraform --policy-mode production
python core/governance/plan_gate.py plan   --dir runs/<run-id>/terraform
python core/reporting/minusctl.py readiness --run <run-id>
python core/reporting/minusctl.py package   --run <run-id>
python app/dashboard_app.py
```

---

## Roadmap (2026-07) — from validated engine to scale-aware data platform

Set after the first end-to-end agent-driven run (`build us a data pipeline for sales data`
→ 100/100 readiness, AWS-priced $116.59/mo). Ordered by dependency, not ambition.

### Phase A — Hardening (close what the live run exposed)
1. Port the four run-local module fixes upstream (`compute-glue-etl` computed-count alarm,
   Step Functions null-field state machine, results-bucket lifecycles in
   `dq-great-expectations` + `query-athena`).
2. Readiness "core files present" must check content, not existence (an agent gamed it
   with one-line comment stubs in the first run).
3. `guard refresh` needs a second pair of eyes: require a different operator (or an
   explicit `--i-edited-generated-code` ack that lands in the audit log).
4. Commit the branch (everything to date is uncommitted).

### Phase B — Cost completeness (make the forecast whole)
1. Synthesizer maps the requirements volume answer into a `daily_data_gb` variable so S3
   prices and cost/GB unit economics fire on every run.
2. Budget-vs-forecast: compare the BCM total to the plan's own `monthly_budget_usd` in the
   cost report + an overview tile (both numbers already exist).
3. Showback tags: stamp `run_id`/`owner` into `default_tags` so Cost Explorer can attribute
   actuals per pipeline (per-team showback, the FinOps allocation capability).
4. Post-deploy actuals cadence: scheduled `bcm actuals` pull + variance alert when actuals
   drift ≥N% from forecast.

### Phase C — Scale-awareness (GB → TB → PB are different products)
The six-layer model stays; the module choices, conformance checks, and cost mechanics
change per tier. Encode the tier as a first-class field of the data profile
(`daily volume: GB / TB / PB`, growth, latency class, query concurrency) and drive:

| Concern | GB/day (current sweet spot) | TB/day | PB total / 100s TB/day |
| :-- | :-- | :-- | :-- |
| Compute | Glue (2 workers, Flex, bookmarks) | Glue autoscaling / EMR Serverless; compaction jobs mandatory | EMR on EC2/EKS with SP/RI commitments |
| Storage | S3 standard + lifecycle | Partitioning + columnar enforced; Intelligent-Tiering | Iceberg/Delta table format mandatory; partition indexes |
| Ingestion | S3 batch drops | Firehose/Kinesis; DMS for CDC | MSK / DMS fleets; multi-account landing |
| Consumption | Athena per-query | Athena + scan cutoffs scaled; result reuse | Redshift RA3/Serverless for BI concurrency |
| Governance | KMS + IAM roles | Lake Formation permissions | Data mesh, cross-account, mandatory chargeback |
| Cost strategy | on-demand list | scenario-check commitments | commitments dominate; EDP; unit-economics SLOs |

Deliverables:
1. **Tiered decision matrix** in the architect phase (deterministic, cited to AWS guidance)
   — same requirements schema, different composed modules per tier.
2. **Tier-conditional conformance**: e.g. TB-tier plan without partitioned tables /
   columnar / compaction → HIGH finding; PB-tier without a table format or commitment plan
   → HIGH. (Today's DATA-01..03 are the GB-tier versions of these checks.)
3. **Cost-at-scale section** in the cost report: BCM bill scenarios at 1×/5×/10× declared
   volume (AWS prices each point — no local extrapolation), rendering the scaling curve and
   cost/GB at each point so diseconomies show up before they are deployed.
4. New modules as tiers demand them: `ingest-firehose`, `table-format-iceberg`,
   `compute-emr-serverless`, `consumption-redshift-serverless`, `compaction-glue`.

### Phase D — Product surface
1. Optimization tab: surface DATA-* advisories + scenario shortcuts (thinnest tab today).
2. Decision versioning/diff in the Control tab.
3. Multi-run trends in Readiness (score and cost/GB across runs).

### Researched trigger thresholds (evidence for the tier matrix — 2026-07-02)
These are the published numbers that turn "scale tier" from opinion into checkable rules;
each becomes a tier-conditional conformance check or advisory finding:

| Signal | Threshold (source) | Consequence in MinusOps |
| :-- | :-- | :-- |
| File size | target ~128 MB splits; many small files = 62–88% slower, S3 rate-limit errors (AWS Athena tuning) | TB tier: compaction module required, finding if absent |
| Partitioning | partition filter = 99% cheaper / 85% faster; >100k partitions need partition indexes (16x speedup) (AWS) | TB tier: unpartitioned tables = HIGH finding; PB tier: partition indexes required |
| Columnar | CSV→Parquet ≈ 91–99.9% cheaper scans (AWS); 70% storage / 80% time saved (telecom case) | TB tier: non-columnar storage = HIGH finding |
| Query concurrency | Athena default 20 concurrent; hundreds of users → warehouse-class engine (Firebolt/Hevo) | consumers>~50 in requirements → recommend Redshift/warehouse module |
| Warehouse economics | 115 TB + 2–3%/mo growth broke BigQuery/Postgres economics; Iceberg beat Hudi 3x; engine concurrency behavior diverges (TRM Labs) | ≥100 TB total: table-format module mandatory, engine benchmark advisory |
| Compute engine | Glue $0.44/DPU-h wins for short/infrequent; EMR for long-running; EMR Serverless for unpredictable (comparisons) | job-hours/day from assumptions drive compute module choice + scenario check |
| Streaming | micro-batch (1–2s) over pure streaming at high velocity; CDC for change-heavy sources | latency-class requirement selects ingest module (batch / Firehose / MSK+CDC) |
| Org scale | central-team bottleneck → data mesh; per-job I/O chargeback (Uber) | multi-team requirement → multi-account/showback pattern (v2) |
| Quality | silent data-quality failures compound at scale ($5M mispricing case) | DQ module stays mandatory at every tier ≥ TB |

### Phase C addendum — the platform dimension (native AWS vs Databricks vs Snowflake)
Scale is one axis; **platform** is the other. The Terraform registry has mature official
providers (`databricks/databricks`, `snowflakedb/snowflake`), so the module-composition
engine extends naturally: platform-tagged module packs behind the same six-layer model.

Recommendation logic (asked in the grill phase when signals warrant, recorded in
`architecture_decision.json` with alternatives/rejections — the schema already supports it):

| Signal in requirements | Recommended platform | Why |
| :-- | :-- | :-- |
| GB-tier batch, SQL consumers, min ops | **Native AWS serverless** (current) | best economics at small scale; tightest BCM/Cost Explorer integration |
| Heavy Spark/ML, notebooks, streaming+batch, TB+ | **Databricks on AWS** (ask first if already on AWS) | Delta/Photon at scale, collaboration; data stays in the customer's S3; Marketplace billing lands in Cost Explorer |
| SQL-warehouse-centric, high BI concurrency, cross-org data sharing | **Snowflake** | elasticity + concurrency without cluster ops; data sharing |

Honesty constraint: our forecast gate prices via AWS BCM only. Databricks DBUs and
Snowflake credits are NOT BCM-priceable — for those platforms the cost report must say
"platform compute: not estimated (vendor pricing)" and lean on post-deploy actuals
(Databricks-on-AWS Marketplace charges DO appear in Cost Explorer). Never hardcode
DBU/credit rates.

### Phase E addendum — Databricks-on-AWS: build authorized (2026-07-08)

The row above was recommendation-only — no `databricks_workspace` module pack existed
(tracked as a remaining item in `HANDOFF.md`). That changes: Databricks-on-AWS is now an
active build target, sequenced behind the VPC/networking module (Databricks requires a
customer-managed VPC; this also finally closes the deferred MWAA-scratch-VPC gap — see
`runs/manual-mwaa-network-scratch/RECORD.md`). "AWS" in this project's scope now explicitly
includes AWS-managed/Marketplace Databricks, not AWS-native services only.

Deployment posture decisions made alongside this (see the MCP integration plan for full
tradeoffs):
- **Single AWS account, no landing-zone**, until a second real tenant exists. Per-team
  isolation for now is Terraform-state/workspace-level, not account-level.
- **Classic (customer-managed VPC) workspace is the Phase 2 target, not serverless.** The
  provider supports `compute_mode = "SERVERLESS"`, which needs none of `databricks_mws_credentials`
  / `databricks_mws_storage_configurations` / `databricks_mws_networks` — but serverless hands
  networking to Databricks and removes the VPC, subnets, and security groups from this project's
  governance entirely, which contradicts the whole point of a *governed* control plane. It also
  removes Phase 1's only consumer: the VPC module is only justified if classic is the target.
  **Serverless is a deliberate deferral, not a rejection** — record it as a valid Phase 2b path
  for teams that explicitly don't want to manage networking, at the cost of MinusOps not
  governing it, same deferred-with-a-reason pattern as SageMaker/EKS/ECS.
  **Another point for classic, found while scoping Phase 2's live-test teardown story
  (2026-07-08):** because the VPC is customer-managed, AWS-side teardown (VPC/NAT/IAM/S3) is
  entirely ours via `networking-vpc`'s own `destroy` — deterministic, no dependency on
  Databricks' control plane. Only Databricks' own account-level state (workspace object,
  metastore) goes through Databricks' async/failure-mode-prone deletion path, and that state
  leaves **no residue in our AWS account** either way. A serverless workspace would hand that
  AWS-side teardown to Databricks too, losing this contained blast radius.
- **Minimal VPC-endpoint set** (S3 gateway, STS, Kinesis if needed), not the full
  PrivateLink/SCC-relay no-egress set — built extensibly so the full set can be added later
  behind a flag if a real no-egress requirement appears. **This is one decision, not two:**
  the minimal endpoint set is a direct consequence of assuming no no-egress requirement exists
  today. They move together — if that assumption changes, the Phase 1 VPC-endpoint set changes
  with it, not independently of it.
- **DBU cost policy (decided now, not discovered mid-build):** Databricks is DBU-metered and
  not in the AWS Price List API, so `coverage_audit.py`'s production-mode unresolved-type block
  will fire on this module the moment Phase 2 starts — by design, not a bug to work around. DBU-
  metered resources are reported as **"not priced / metered externally"** in the cost report,
  never estimated or guessed. Consistent with the existing honesty constraint above (Databricks
  DBUs/Snowflake credits are not BCM-priceable) — restated here explicitly because this is the
  decision that makes coverage_audit.py's Phase-2 block expected behavior, not a surprise.
- **Module content is fetched live only at module-update time**, via a maintainer command
  (`minus update-module <name>`), never at synthesis/plan time. HashiCorp's Terraform MCP
  server (provider docs/schema) and AWS's MCP Server (current AWS API/docs) are the fetch
  sources; the result is committed as a pinned module version and reused byte-for-byte
  after that, same trust model as every other module in the catalog. This preserves
  plan-hash reproducibility (identical requests always synthesize identical HCL between
  updates) and audit-trail integrity (the report reflects what was actually reviewed, not
  what live docs said on deploy day).
- **§4.2's cross-account role uses `data "databricks_aws_assume_role_policy"` (`external_id =
  var.databricks_account_id`), never hand-rolled trust-policy JSON.** Confirmed via the Phase 0
  smoke test (2026-07-08, `databricks` provider v1.121.0): this data source is Databricks' own
  canonical generator for that trust policy, external-ID condition included. This removes the
  highest-risk authoring step at the source instead of catching mistakes after the fact.
- **`SEC-05` (cross-account trust-policy shape) is a hard prerequisite, and stays even though
  the data source above removes most of its original reason to exist — with one precision
  correction.** `databricks_aws_assume_role_policy`'s emitted JSON is computed inside the
  provider at plan/apply time, invisible to `optimize_analyzer.py`'s static-text scanner — a
  static rule cannot literally "verify the emitted policy has the external-ID condition." What
  it *can* and does check: the module actually supplies `external_id` to that data source (the
  one input the module author controls), plus the original hand-rolled-`aws_iam_policy_document`
  check as an unconditional backstop against a future maintainer bypassing the data source and
  hand-rolling JSON again. `terraform validate`/`plan` and the four original `SEC-*` rules still
  cannot detect a malformed trust policy either way — this rule is still the only thing that can.
- The **Databricks managed MCP server** (Unity Catalog/SQL/Genie/AI Search endpoints for
  deployed agents) is a separate, **runtime-only** concern — Public Preview as of Jan 2026,
  not GA. It has no role in provisioning and is out of scope until a workspace already
  exists (tracked as its own later phase).
- **Phase 3 (multi-tenant platform layer) deferred to fast-follow, not day one (2026-07-08).**
  Rationale: no current second tenant; isolation requirements (workspace- vs account-scoped
  IAM, shared vs per-tenant metastore, per-team state) are unknown until a real second tenant
  exists; building now repeats the speculative-infrastructure anti-pattern already rejected
  this session for landing-zone multi-account, full PrivateLink/no-egress, and AWS MCP Server.
  Phase 3's design is to be informed by Autoresearch's real usage as the first team onboarded
  onto the current single-account stack, not designed from guesswork in advance.

**Phase 0 tooling status (2026-07-08):** Terraform MCP server is wired (`docker
hashicorp/terraform-mcp-server:1.0.0`, `--toolsets=registry`, no credentials, no operations —
covers provider-schema/module-doc lookups). **AWS MCP Server is deliberately deferred to
Phase 2**, not Phase 0: AWS's setup docs confirmed it requires valid AWS credentials just to
initialize the connection (no zero-auth docs-only mode, contrary to the original plan's
assumption) — the ambient credential chain on this machine is the full-admin `TerraForm-admin`
identity, and scoping it to genuinely read-only means creating a new IAM identity first, which
is real AWS infrastructure that belongs behind Terraform + the governed plan/approve/apply
path, not a raw CLI call made in passing while wiring dev tooling. Phase 0's actual need (live
provider-schema lookup) is already covered by Terraform MCP alone. Revisit AWS MCP Server when
Phase 2 needs real account inspection — build its IAM identity as a module, the same way
everything else in this catalog gets created.
