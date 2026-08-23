# MinusOps Live AWS Run Audit & Staff Architecture Master Blueprint
**Date:** 2026-08-17 / 2026-08-18  
**Author:** Matt (Staff Principal Infrastructure Architect) & Platform Engineering  
**Workspace:** `C:\Users\shubh\PycharmProjects\MinusTeraformCli`  
**Target AWS Account:** `450374452930` (`TerraForm-admin`)  
**Run ID Audited:** `runs/20260817-144027-requirements-first`

---

## Master Table of Contents
1. [Executive Summary & Proof of Execution](#1-executive-summary--proof-of-execution)
2. [Visual Architecture Evolution: Before vs. After](#2-visual-architecture-evolution-before-vs-after)
3. [The Complete 23-Ticket Engineering Ledger (MINUS-101 to MINUS-137)](#3-the-complete-23-ticket-engineering-ledger)
4. [Tooling Surface: MinusOps CLI (`minusctl`) vs. Standard Terraform](#4-tooling-surface-minusops-cli-minusctl-vs-standard-terraform)
5. [Enterprise Adoption, GitOps & Brownfield Strategy](#5-enterprise-adoption-gitops--brownfield-strategy)
6. [Workload Agnostic & Multi-Cloud Versatility](#6-workload-agnostic--multi-cloud-versatility)
7. [The Multi-Agent Generation Swarm Architecture](#7-the-multi-agent-generation-swarm-architecture)
8. [The 7-Pillar Data Engineering Grilling Framework](#8-the-7-pillar-data-engineering-grilling-framework)
9. [3-Tier Alert Routing & Quarantine Incident Architecture](#9-3-tier-alert-routing--quarantine-incident-architecture)
10. [Layer-Agnostic Project Code Layout (Compute, SQL, Quality, Orchestration)](#10-layer-agnostic-project-code-layout)
11. [Native dbt Integration (SQL-Only Transformation Lakehouse)](#11-native-dbt-integration-sql-only-transformation-lakehouse)
12. [External, Multi-Cloud & On-Premise Ingestion Gateways](#12-external-multi-cloud--on-premise-ingestion-gateways)
13. [TB-Scale Compute Cluster Selection Matrix (EMR / Glue Flex / Graviton)](#13-tb-scale-compute-cluster-selection-matrix)
14. [The Independent "Stage Reflector" Agent Circuit Breaker](#14-the-independent-stage-reflector-agent-circuit-breaker)
15. [Multi-Environment Promotion (Dev / Staging / Prod Isolation)](#15-multi-environment-promotion-dev--staging--prod-isolation)
16. [Enterprise SIEM Logging & DevSecOps Audit Fabric](#16-enterprise-siem-logging--devsecops-audit-fabric)
17. [GitHub-Native DevOps Platform Engineering (PR Bot & Environment Gates)](#17-github-native-devops-platform-engineering)
18. [Multi-Pipeline Isolation & Enterprise Pattern Catalog](#18-multi-pipeline-isolation--enterprise-pattern-catalog)
19. [Context-Aware Pipeline Inheritance (`--based-on` Delta Engine)](#19-context-aware-pipeline-inheritance---based-on-delta-engine)
20. [TerraShark (FM-01..05) & Awesome-TF Tooling Integration](#20-terrashark-fm-0105--awesome-tf-tooling-integration)
21. [Sequenced 9-Step Implementation Roadmap for Coding Agents](#21-sequenced-9-step-implementation-roadmap-for-coding-agents)

---

## 1. Executive Summary & Proof of Execution

On August 17, 2026, an end-to-end live test was executed against real AWS infrastructure using **MinusOps**. Starting from a natural language requirement:

> *"We need a governed data pipeline on AWS for our customer analytics team."*

The platform executed the full operational lifecycle:
1. **Requirements Gathering (`grill-me`):** Interrogated volume (< 50 GB/day), PII (KMS CMK), SLA (< 2 hrs), and budget ($500/mo).
2. **Modular HCL Synthesis (`synthesizer.py`):** Composed 4 building blocks (`storage-medallion-s3`, `compute-glue-etl`, `query-athena`, `governance-observability`) in `runs/20260817-144027-requirements-first/terraform/`.
3. **Live AWS BCM Pricing:** Queried the AWS BCM Pricing Calculator API to generate a verified monthly estimate of **$221.19 / month** against the $500 ceiling.
4. **Enterprise Readiness Score:** Achieved **100 / 100 (READY)** with 0 security findings.
5. **Plan-Bound Deploy Gate:** Bound SHA-256 plan hash `9b17727ee008...` to STS caller identity `TerraForm-admin`.
6. **Live AWS Deployment:** Provisioned 30 resources cleanly (KMS CMK, 3 S3 buckets, Glue ETL job, Athena workgroup, Budgets, Alarms).
7. **Governed Teardown:** Cleanly destroyed all 30 resources via `plan_gate.py run --destroy` ($0 ongoing cost).

---

## 2. Visual Architecture Evolution: Before vs. After

### [FAIL] BEFORE: The Naive Initial Run (What Ran in Test 1)
```
┌─────────────────────────┐
│ [?] Upstream Source     │  (UNKNOWN / EMPTY - No Ingestion Bridge)
└────────────┬────────────┘
             │ [FAIL] Nothing lands in Bronze
             ▼
┌─────────────────────────┐
│ S3 Bronze Bucket        │  (Empty Bucket)
└────────────┬────────────┘
             │ [FAIL] Missing --source_path / --target_path
             │ [FAIL] Glue IAM Role missing s3:PutObject on Silver
             ▼
┌─────────────────────────┐
│ AWS Glue PySpark        │  RUNTIME CRASH:
│    (etl.py script)      │     • SystemExit (missing arguments)
└────────────┬────────────┘     • 403 AccessDenied (IAM write permissions)
             │
             ▼
┌─────────────────────────┐
│ S3 Silver & Gold        │  (Empty Buckets)
└────────────┬────────────┘
             │ [FAIL] No Glue Catalog Database or Tables
             ▼
┌─────────────────────────┐
│ Athena Workgroup        │  (No Tables to Query)
└─────────────────────────┘
```

### [OK] AFTER: The Complete Enterprise Data Platform
```
========================================================================================
                      1. UPSTREAM INGESTION GATEWAYS
========================================================================================
   [PostgreSQL / MySQL]     [Salesforce / Stripe]     [External SFTP Partner]
              │                            │                            │
   (AWS DMS CDC Task)             (AWS AppFlow Flow)           (AWS Transfer Family)
              │                            │                            │
              └────────────────────────────┼────────────────────────────┘
                                           │
                                           ▼ (Automated S3 Drop)
========================================================================================
                      2. S3 MEDALLION LAKE & QUARANTINE (KMS CMK)
========================================================================================
                      ┌─────────────────────────────────────────┐
                      │ BRONZE S3 (s3://...-bronze/raw/)        │
                      │    Raw JSON / CSV / Event Payloads      │
                      └────────────────────┬────────────────────┘
                                           │
                                           ▼ (Triggers EventBridge)
========================================================================================
                 3. SERVERLESS WORKFLOW ORCHESTRATION (AWS Step Functions)
========================================================================================
                      ┌─────────────────────────────────────────┐
                      │ Step Functions State Machine            │
                      │    (src/orchestration/workflow.json)    │
                      │    • Automated Retries & Catchers       │
                      │    • Coordinates Quality & Transforms   │
                      └────────────────────┬────────────────────┘
                                           │
                                           ▼
========================================================================================
                 4. TRANSFORMATION & QUALITY GATES (src/compute/ & src/dbt/)
========================================================================================
                      ┌─────────────────────────────────────────┐
                      │ Great Expectations / Glue DQ Gate       │
                      │    • Asserts not_null, valid schema     │
                      └────────────┬───────────────────────┬────┘
                                   │                       │
           (Passed Clean Rows)     │                       │ (Failed / Malformed Rows)
                                   ▼                       ▼
            ┌──────────────────────────────┐    ┌──────────────────────────────┐
            │ dbt-Athena / Glue PySpark    │    │ QUARANTINE S3 ZONE           │
            │    (src/compute/etl.py)      │    │    (s3://...-quarantine/)    │
            │    • Auto-wired S3 paths     │    │    • Bad rows isolated       │
            │    • KMS Encrypted Writes    │    │    • Pipeline never crashes! │
            └──────────────┬───────────────┘    └──────────────┬───────────────┘
                           │                                   │
                           ▼                                   │
            ┌──────────────────────────────┐                   │
            │ SILVER S3 (Clean Parquet)    │                   │
            └──────────────┬───────────────┘                   │
                           │                                   │
                           ▼ (dbt Business Mart Aggregations)  │
            ┌──────────────────────────────┐                   │
            │ GOLD S3 (Iceberg Tables)     │                   │
            └──────────────┬───────────────┘                   │
                           │                                   │
                           ▼                                   ▼
========================================================================================
                  5. SERVING LAYER & 6. 3-TIER INCIDENT ROUTING
========================================================================================
  [Athena SQL & BI Dashboards]             [3-Tier Alert Routing Hub]
  • AWS Glue Catalog: `customer_gold`         • Tier 1 (Crash): Slack #data-ops / PagerDuty
  • Partitioned Iceberg Table Schemas         • Tier 2 (DQ Fail): Slack #data-quality-log
  • 10 GB per-query cost limit cutoff         • Tier 3 (Budget): Email to Budget Owner
========================================================================================
```

---

## 3. The Complete 23-Ticket Engineering Ledger

```
┌──────────────┬──────────────────────────────────────────────────────────┬──────────┐
│ Ticket ID    │ Summary                                                  │ Priority │
├──────────────┼──────────────────────────────────────────────────────────┼──────────┤
│ MINUS-101    │ Dynamic `force_destroy` on Ephemeral Dev S3 Buckets      │ P0 Blocker│
│ MINUS-102    │ Unique Hash Suffix for AWS KMS Aliases in Run Workspaces │ P0 Blocker│
│ MINUS-103    │ Auto-Anchor Source Baseline upon Synthesis Completion   │ P1 High  │
│ MINUS-104    │ Remote S3 + DynamoDB State Backend Generator             │ P1 High  │
│ MINUS-105    │ Non-Empty Notification Subscriptions in Governance Module│ P2 Medium│
│ MINUS-106    │ Brownfield IaC Adoption Engine (`minusctl adopt`)        │ P1 High  │
│ MINUS-107    │ Native Cross-Platform `minusctl doctor` CLI Diagnostics  │ P1 High  │
│ MINUS-108    │ Glue IAM Role S3 Multi-Bucket Write & KMS Grants         │ P0 Blocker│
│ MINUS-109    │ Auto-wire S3 Source/Target Paths in Glue Job Arguments   │ P0 Blocker│
│ MINUS-110    │ Glue Data Catalog Database & Table Schema Generation     │ P1 High  │
│ MINUS-111    │ Step Functions / EventBridge Scheduled Pipeline Trigger  │ P2 Medium│
│ MINUS-112    │ KMS Key Policy Service Principals & Multi-Service Grants │ P0 Blocker│
│ MINUS-113    │ Automated Data Seeding & Verification (`minusctl seed`)  │ P1 High  │
│ MINUS-114    │ Multi-Environment Promotion Matrix & `tfvars` Overlays   │ P1 High  │
│ MINUS-115    │ Turnkey GitHub Actions PR Review Bot (`action.yml`)      │ P2 Medium│
│ MINUS-116    │ 7-Pillar Data Engineering Grilling Questionnaire Engine  │ P1 High  │
│ MINUS-117    │ 3-Tier Enterprise Notification & Quarantine Engine       │ P1 High  │
│ MINUS-118    │ Layer-Agnostic Workspace Layout (`src/compute`, `src/sql`)│ P1 High  │
│ MINUS-119    │ Native dbt Integration & Starter Project Generator       │ P1 High  │
│ MINUS-120    │ 'dbt-Only' Serverless Lakehouse Architecture Profile     │ P1 High  │
│ MINUS-121    │ Evidence-Based Market Research & Trade-Off Advisor       │ P1 High  │
│ MINUS-122    │ Ambiguity Resolver & ADR Assumption Ledger               │ P1 High  │
│ MINUS-123    │ Enterprise Ingestion Connectors & Lifecycle Hook Fabric  │ P1 High  │
│ MINUS-124    │ Upstream Ingestion Source Archetype Questionnaire        │ P0 Blocker│
│ MINUS-125    │ External Source Ingestion Modules (SFTP, Webhooks, APIs) │ P1 High  │
│ MINUS-126    │ Enterprise Data Hub & Zero-Copy Lake Formation Connector │ P1 High  │
│ MINUS-127    │ Non-AWS Multi-Cloud (GCP/Azure) & On-Premise DMS Sync    │ P1 High  │
│ MINUS-128    │ TB-Scale Compute Selection Matrix (EMR / Glue Flex / Spot│ P1 High  │
│ MINUS-129    │ Independent Stage Reflector & 2-Way Flow Auditor Agent   │ P0 Blocker│
│ MINUS-130    │ Multi-Account Promotion Engine (dev/staging/prod.tfvars) │ P1 High  │
│ MINUS-131    │ Comprehensive SIEM Logging & CloudTrail S3 Events Module │ P0 Blocker│
│ MINUS-132    │ Disaster Recovery (S3 CRR + KMS Replica) & Tag Policy    │ P1 High  │
│ MINUS-133    │ GitHub-Native DevOps Suite (PR Bot, Environments & Rules)│ P1 High  │
│ MINUS-134    │ Multi-Pipeline Isolation & Enterprise Pattern Catalog    │ P1 High  │
│ MINUS-135    │ Context-Aware Pipeline Inheritance (`--based-on` Engine) │ P1 High  │
│ MINUS-136    │ TerraShark Failure-Mode Pre-Flight & Output Contract     │ P1 High  │
│ MINUS-137    │ Awesome-TF Ingestion (TFLint, Pike, Checkov & `moved {}`) │ P1 High  │
└──────────────┴──────────────────────────────────────────────────────────┴──────────┘
```

---

### Deep Specifications for Every Ticket:

#### [MINUS-101] Dynamic `force_destroy` on Ephemeral Dev S3 Buckets
* **Priority:** P0 (Blocker) | **Component:** `modules/storage-medallion-s3/main.tf`
* **Problem:** In non-empty S3 buckets, `terraform destroy` fails with `BucketNotEmpty`.
* **Fix:** Parameterize `force_destroy = var.force_destroy` where `var.force_destroy = var.environment == "dev"`.

#### [MINUS-102] Unique Hash Suffix for AWS KMS Aliases
* **Priority:** P0 (Blocker) | **Component:** `modules/storage-medallion-s3/main.tf`
* **Problem:** KMS keys enter a 7-30 day `PendingDeletion` state. Recreating an alias collisions immediately.
* **Fix:** Suffix alias with run hash: `name = "alias/${var.name_prefix}-${substr(md5(var.run_id), 0, 8)}-lake"`.

#### [MINUS-103] Auto-Anchor Source Baseline upon Synthesis Completion
* **Priority:** P1 (High) | **Component:** `core/generation/synthesizer.py`
* **Problem:** Synthesized runs flag `source is current (STALE)` in `minusctl readiness`.
* **Fix:** Call `source_guard.write_baseline(out_dir, label="synthesized")` inside `synthesizer.py` before returning.

#### [MINUS-104] Remote S3 + DynamoDB State Backend Generator
* **Priority:** P1 (High) | **Component:** `core/generation/synthesizer.py`
* **Problem:** Local `terraform.tfstate` prevents multi-developer concurrency and CI/CD automation.
* **Fix:** Auto-emit `backend "s3"` in `providers.tf` with S3 state bucket and DynamoDB locking table.

#### [MINUS-105] Non-Empty Notification Subscriptions in Governance Module
* **Priority:** P2 (Medium) | **Component:** `modules/governance-observability/main.tf`
* **Problem:** Billing alarms trigger SNS topics with 0 subscribers ("silent alarms").
* **Fix:** Populate `notification_emails` with at least one verified address or Slack webhook during synthesis.

#### [MINUS-106] Brownfield IaC Adoption Engine (`minusctl adopt`)
* **Priority:** P1 (High) | **Component:** `core/reporting/adopt.py`
* **Problem:** Enterprises have existing unmanaged Terraform directories.
* **Fix:** Provide `minusctl adopt --dir <dir>` to baseline, scan (`SEC-*`), generate SVGs, and place existing code under deploy gate governance.

#### [MINUS-107] Native Cross-Platform `minusctl doctor` CLI Diagnostics
* **Priority:** P1 (High) | **Component:** `core/reporting/doctor.py`
* **Problem:** PowerShell `doctor.ps1` fails on Linux, macOS, and CI Docker containers.
* **Fix:** Implement `minusctl doctor [--json]` checking Terraform, AWS CLI STS identity, Python packages, Graphviz `dot`, and OPA binary.

#### [MINUS-108] Glue IAM Role S3 Multi-Bucket Write & KMS Grants
* **Priority:** P0 (Blocker) | **Component:** `modules/compute-glue-etl/main.tf`
* **Problem:** Glue IAM role lacks `s3:PutObject` on Silver/Gold buckets and `kms:GenerateDataKey` on the lake key.
* **Fix:** Pass `data_bucket_arns` and `kms_key_arn` to `compute-glue-etl` and add explicit IAM allow statements.

#### [MINUS-109] Auto-wire S3 Source/Target Paths in Glue Job Arguments
* **Priority:** P0 (Blocker) | **Component:** `modules/compute-glue-etl/main.tf`
* **Problem:** PySpark `etl.py` script exits with `SystemExit` because `--source_path` and `--target_path` are missing.
* **Fix:** Auto-inject `--source_path = "s3://${var.source_bucket}/data/"` and `--target_path = "s3://${var.target_bucket}/data/"` into `default_arguments`.

#### [MINUS-110] Glue Data Catalog Database & Table Schema Generation
* **Priority:** P1 (High) | **Component:** `modules/query-athena/main.tf`
* **Problem:** Athena workgroup created without a Glue Data Catalog database or schemas to query.
* **Fix:** Provision `aws_glue_catalog_database` and partitioned table definitions pointing to Gold S3 storage.

#### [MINUS-111] Step Functions / EventBridge Scheduled Pipeline Trigger
* **Priority:** P2 (Medium) | **Component:** `modules/orchestrator-stepfunctions/main.tf`
* **Problem:** Pipeline has no automated cron schedule or S3 upload trigger.
* **Fix:** Synthesize EventBridge rule (`schedule_expression = "rate(1 day)"`) or Step Functions state machine.

#### [MINUS-112] KMS Key Policy Service Principals & Multi-Service Grants
* **Priority:** P0 (Blocker) | **Component:** `modules/storage-medallion-s3/main.tf`
* **Problem:** Athena and Glue hit `AccessDenied` on SSE-KMS encrypted buckets.
* **Fix:** Add service principals (`athena.amazonaws.com`, `glue.amazonaws.com`, `logs.amazonaws.com`) to KMS key policy.

#### [MINUS-113] Automated Data Seeding & Verification (`minusctl seed`)
* **Priority:** P1 (High) | **Component:** `core/reporting/seed.py`
* **Problem:** Deployed buckets are empty; developers cannot verify end-to-end data flow.
* **Fix:** Implement `minusctl seed --run <run-id>`: uploads 10 sample JSON rows to Bronze, starts Glue job, and runs an Athena smoke test against Gold.

#### [MINUS-114] Multi-Environment Promotion Matrix & `tfvars` Overlays
* **Priority:** P1 (High) | **Component:** `core/generation/synthesizer.py`
* **Problem:** Single environment generated; no clean path to promote to Staging and Prod.
* **Fix:** Generate `envs/dev.tfvars`, `envs/staging.tfvars`, and `envs/prod.tfvars` with scaled DPU allocations and budget limits.

#### [MINUS-115] Turnkey GitHub Actions PR Review Bot (`action.yml`)
* **Priority:** P2 (Medium) | **Component:** `.github/actions/pr-reviewer/action.yml`
* **Problem:** Manual review of CLI output slows PR reviews.
* **Fix:** Automate PR comments containing `architecture.svg`, BCM cost estimate, and plan diff.

#### [MINUS-116] 7-Pillar Data Engineering Grilling Questionnaire Engine
* **Priority:** P1 (High) | **Component:** `.agents/skills/grill-me/SKILL.md`
* **Problem:** `grill-me` stops after 4 basic questions, missing orchestration, data quality, and alerting.
* **Fix:** Interrogate all 7 Pillars (Ingestion, Storage, Compute, Orchestration, DQ, Serving, Alerting).

#### [MINUS-117] 3-Tier Enterprise Notification & Quarantine Engine
* **Priority:** P1 (High) | **Component:** `modules/governance-observability/main.tf`
* **Problem:** Alert fatigue from single email inboxes; bad records crash jobs.
* **Fix:** Implement 3-tier alerting (P0/P1 Slack/PagerDuty, P2 S3 Quarantine + `#data-quality`, P3 Budget Email).

#### [MINUS-118] Layer-Agnostic Workspace Layout (`src/compute`, `src/sql`, `src/quality`)
* **Priority:** P1 (High) | **Component:** `core/generation/synthesizer.py`
* **Problem:** Non-HCL application code lacks standardized multi-cloud structure.
* **Fix:** Scaffold `src/compute/`, `src/sql/`, `src/quality/`, `src/orchestration/`, and `tests/fixtures/`.

#### [MINUS-119] Native dbt Integration & Starter Project Generator
* **Priority:** P1 (High) | **Component:** `core/generation/synthesizer.py`, `src/dbt/`
* **Problem:** Analytics engineers writing SQL in dbt must manually configure profiles and credentials.
* **Fix:** Scaffold `src/dbt/` with auto-rendered `profiles.yml` connected to the provisioned Athena workgroup and Gold lake.

#### [MINUS-120] 'dbt-Only' Serverless Lakehouse Architecture Profile
* **Priority:** P1 (High) | **Component:** `core/generation/synthesizer.py`
* **Problem:** Glue PySpark compute is expensive and overkill for tabular SQL transformations.
* **Fix:** When `transform_engine == "dbt"`, omit Glue compute, configure Athena serverless SQL, and scaffold `dbt-athena` with Iceberg table support.

#### [MINUS-121] Evidence-Based Market Research & Trade-Off Advisor
* **Priority:** P1 (High) | **Component:** `core/architecture/requirements.py`
* **Problem:** Agents provide generic scaffolds to please users without empirical backing.
* **Fix:** Embed audited adoption statistics, cost formulas, and Well-Architected benchmarks into recommendation dialogues.

#### [MINUS-122] Ambiguity Resolver & ADR Assumption Ledger
* **Priority:** P1 (High) | **Component:** `core/architecture/architecture_decision.py`
* **Problem:** Ambiguities during development lead to hidden assumptions that break in production.
* **Fix:** Inspect local cloud state first, ask 1 targeted question with a default, and record all assumptions in `architecture_decision.json`.

#### [MINUS-123] Enterprise Ingestion Connectors & Lifecycle Hook Fabric
* **Priority:** P1 (High) | **Component:** `modules/ingestion-*`
* **Problem:** Data pipelines are isolated from existing enterprise IT systems.
* **Fix:** Provide catalog modules for AppFlow SaaS ingestion, JDBC PrivateLink tunnels, and Jira/ServiceNow incident webhooks.

#### [MINUS-124] Upstream Ingestion Source Archetype Questionnaire
* **Priority:** P0 (Blocker) | **Component:** `.agents/skills/grill-me/SKILL.md`
* **Problem:** Initial run created empty landing buckets without knowing where data originates.
* **Fix:** Make Ingestion Source Question #1 in `grill-me` (RDS/CDC, SaaS AppFlow, Streaming API/Kinesis, Managed SFTP, Direct S3).

#### [MINUS-125] External Source Ingestion Modules (SFTP, Webhooks, APIs)
* **Priority:** P1 (High) | **Component:** `modules/ingestion-sftp`, `modules/ingestion-webhook`
* **Problem:** External partners outside AWS cannot connect via internal VPC peering.
* **Fix:** Provision AWS Transfer Family SFTP endpoints, API Gateway Webhook listeners with Secrets Manager HMAC verification, and scheduled API pullers.

#### [MINUS-126] Enterprise Data Hub & Zero-Copy Lake Formation Connector
* **Priority:** P1 (High) | **Component:** `modules/ingestion-data-hub`
* **Problem:** Copying petabytes from central enterprise hubs wastes money and creates stale replicas.
* **Fix:** Provision Zero-Copy Lake Formation RAM sharing, Confluent Kafka MSK connectors, Databricks Delta Sharing, and AWS Data Exchange sync.

#### [MINUS-127] Non-AWS Multi-Cloud (GCP/Azure) & On-Premise DMS Sync
* **Priority:** P1 (High) | **Component:** `modules/ingestion-multicloud`, `modules/ingestion-onprem`
* **Problem:** Enterprise data lives in Google Cloud Storage, Azure ADLS Gen2, and on-premise Oracle/SAP data centers.
* **Fix:** Implement GCP/Azure OIDC Workload Identity Federation (zero static secrets) and AWS Site-to-Site VPN with DMS CDC replication.

#### [MINUS-128] TB-Scale Compute Selection Matrix (EMR / Glue Flex / Graviton)
* **Priority:** P1 (High) | **Component:** `core/generation/synthesizer.py`
* **Problem:** Fixed 2-worker Glue clusters fail on Terabyte-scale workloads.
* **Fix:** Present 3 curated compute options: Glue Flex (35% off for nightly batch), EMR Serverless (auto-scaling Spark), and EMR on EC2 Graviton Spot Fleets (70% savings).

#### [MINUS-129] Independent Stage Reflector & 2-Way Flow Auditor Agent
* **Priority:** P0 (Blocker) | **Component:** `core/governance/reflector.py`
* **Problem:** Primary agent gets jumbled, misses cross-module wiring, or hallucinates parameters.
* **Fix:** Decoupled Reflector Agent pauses at each stage boundary (G1 Scope, G2 Wiring, G3 Security, G4 Cost, G5 Plan Hash) to intercept anomalies and force self-correction.

#### [MINUS-130] Multi-Account Promotion Engine (dev/staging/prod.tfvars)
* **Priority:** P1 (High) | **Component:** `core/generation/synthesizer.py`
* **Problem:** No standard multi-account parameterization for AWS Control Tower environments.
* **Fix:** Auto-generate isolated state keys and `.tfvars` profiles with account-specific safety invariants (`force_destroy = false` and Object Lock in Prod).

#### [MINUS-131] Comprehensive SIEM Logging & CloudTrail S3 Events Module
* **Priority:** P0 (Blocker) | **Component:** `modules/governance-observability/main.tf`
* **Problem:** SecOps teams cannot audit who accessed PII data or executed SQL queries.
* **Fix:** Provision CloudTrail S3 Data Events to an immutable security bucket, GuardDuty threat detection, VPC Flow Logs, and Athena SQL query audit logging.

#### [MINUS-132] Disaster Recovery (S3 CRR + KMS Replica) & Mandatory Tag Policy
* **Priority:** P1 (High) | **Component:** `modules/storage-medallion-s3/main.tf`
* **Problem:** Regional AWS outages cause total data loss; missing FinOps tags prevent cost allocation.
* **Fix:** Provision S3 Cross-Region Replication (`us-east-1` -> `us-west-2`), Multi-Region KMS keys, and enforce 5 mandatory tags (`CostCenter`, `Environment`, `Owner`, `Workload`, `DataClassification`).

#### [MINUS-133] GitHub-Native DevOps Suite (PR Bot, Environments & Secret Protection)
* **Priority:** P1 (High) | **Component:** `.github/workflows/deploy.yml`
* **Problem:** Manual CLI deployments lack collaborative code review and PR approval gates.
* **Fix:** Provide GitHub Actions CI with OIDC role assumption, visual PR Bot commenting (SVGs + BCM cost), and GitHub Environment Two-Person Production Rules.

#### [MINUS-134] Multi-Pipeline Isolation & Enterprise Pattern Catalog
* **Priority:** P1 (High) | **Component:** `core/generation/patterns.py`
* **Problem:** Running 50 pipelines causes resource naming collisions and state locking conflicts.
* **Fix:** Enforce `<name_prefix>-<account_id>-<hash>` namespacing, directory-bound state keys, and provide `patterns.py capture`/`match` for organization-wide pattern reuse.

#### [MINUS-135] Context-Aware Pipeline Inheritance (`--based-on` Engine)
* **Priority:** P1 (High) | **Component:** `core/architecture/requirements.py`, `minusctl.py`
* **Problem:** Creating a new pipeline asks redundant questions already solved by existing company pipelines.
* **Fix:** Add `minusctl create --based-on <run-id|stack>`; auto-inherit company Region, KMS, and Tags, pruning the interview to only pipeline-specific deltas.

#### [MINUS-136] TerraShark Failure-Mode Pre-Flight & Output Contract Engine
* **Priority:** P1 (High) | **Component:** `.agents/skills/grill-me/SKILL.md`, `core/architecture/architecture_decision.py`
* **Problem:** AI models hallucinate infrastructure and trigger Identity Churn (`FM-01`) or Secret Leaks (`FM-02`).
* **Fix:** Ingest TerraShark's 5 Failure Modes and enforce the 4-part ADR Output Contract (Assumptions, Tradeoffs, Validation, Rollback).

#### [MINUS-137] Awesome-TF Tooling Ingestion (TFLint, Pike, Checkov & `moved {}` Blocks)
* **Priority:** P1 (High) | **Component:** `core/reporting/optimize_analyzer.py`, `core/generation/synthesizer.py`
* **Problem:** Hardcoded IAM policies are overly permissive; refactoring modules destroys live S3 buckets.
* **Fix:** Integrate Pike for mathematical least-privilege IAM, TFLint for provider validation, and auto-synthesize `moved {}` blocks during refactoring.

---

## 21. Sequenced 9-Step Implementation Roadmap for Coding Agents

When executing these tickets, follow this exact numbered sequence:

1. **Step 1: Diagnostics & Pre-Flight (`MINUS-107`, `MINUS-136`)**
   * Implement `core/reporting/doctor.py` and bind `minusctl doctor`.
   * Embed TerraShark's Failure-Mode profiler (`FM-01..05`) and ADR Output Contract.
2. **Step 2: Core IAM & Data Flow Wiring (`MINUS-108`, `MINUS-109`, `MINUS-112`, `MINUS-137`)**
   * Update `modules/compute-glue-etl/main.tf` with multi-bucket S3 and KMS permissions.
   * Auto-wire `--source_path` and `--target_path` in Glue `default_arguments`.
   * Add service principals to KMS key policies; integrate Pike least-privilege calculation.
3. **Step 3: Lifecycle & State Hardening (`MINUS-101`, `MINUS-102`, `MINUS-104`, `MINUS-134`)**
   * Add `force_destroy = var.force_destroy` and unique hash suffixes for KMS aliases.
   * Auto-emit remote S3 state backend and enforce `<name_prefix>-<account_id>-<hash>` isolation.
4. **Step 4: DX & Baseline Synchronization (`MINUS-103`)**
   * Call `source_guard.write_baseline(out_dir, label="synthesized")` inside `synthesizer.py`.
5. **Step 5: Catalog, dbt & Orchestration (`MINUS-110`, `MINUS-111`, `MINUS-119`, `MINUS-120`)**
   * Synthesize `aws_glue_catalog_database` and EventBridge / Step Functions triggers.
   * Scaffold `src/dbt/` with auto-rendered `profiles.yml` and support 'dbt-only' serverless mode.
6. **Step 6: Enterprise Promotion & DR (`MINUS-114`, `MINUS-130`, `MINUS-131`, `MINUS-132`)**
   * Generate `envs/dev.tfvars`, `envs/staging.tfvars`, `envs/prod.tfvars`.
   * Provision SIEM CloudTrail S3 Data Events, S3 Cross-Region Replication, and mandatory tag policies.
7. **Step 7: 7-Pillar Grilling & Ingestion Connectors (`MINUS-116`, `MINUS-117`, `MINUS-118`, `MINUS-123` to `MINUS-127`)**
   * Interrogate the 7 Pillars in `grill-me`; scaffold `src/compute`, `src/sql`, `src/quality`, `src/orchestration`.
   * Add ingestion modules for RDS/CDC (DMS), SaaS (AppFlow), SFTP, Webhooks, Data Hubs (Lake Formation), and Non-AWS GCP/Azure OIDC sync.
8. **Step 8: TB-Scale Compute & Reflector Agent (`MINUS-128`, `MINUS-129`, `MINUS-135`)**
   * Implement TB-scale cluster selection (Glue Flex / EMR Serverless / Graviton Spot).
   * Implement decoupled Stage Reflector Agent (`core/governance/reflector.py`) circuit breaker.
   * Implement `--based-on` context-aware pipeline inheritance and delta engine.
9. **Step 9: Verification, Adoption & PR Automation (`MINUS-106`, `MINUS-113`, `MINUS-115`, `MINUS-133`)**
   * Implement `minusctl seed` (mock data upload + Glue + Athena smoke test).
   * Implement `minusctl adopt` (brownfield repository onboarding).
   * Package turnkey GitHub Action PR Bot (`.github/actions/pr-reviewer/action.yml`).
   * Run full test suite: `pytest tests/` to guarantee 100% pass rate.
