# Product Requirements Document (PRD) — Enterprise Multi-Repo Deployment, Lifecycle Proving & Semantic Run Governance

| Attribute | Details |
| :--- | :--- |
| **Document ID** | PRD-ARCH-2026-005 (Revision 5.0 — Enterprise Multi-Repo Topology & Lifecycle Governance) |
| **Document Name** | `tasks/deplyoymend_pr.md` |
| **Status** | DRAFT ARCHITECTURE SPECIFICATION (READY FOR ADVISORY REVIEW) |
| **Lead Architect** | Principal Cloud Architect & Enterprise Governance Lead |
| **Target Engine** | MinusOps Governance Control Plane (`core/`, `modules/`, `runs/`, `tasks/`) |
| **Target Audience** | Data Platform Teams, Central Platform Engineering, FinOps Directors, Coding Agents |
| **Date** | August 22, 2026 |

---

## 1. Executive Summary & Problem Statement

Enterprises deploying data platforms with MinusOps face real-world organizational realities that extend beyond single-folder scripting:

1. **Two-Repository Enterprise Topology:**
   - **Repository A (`minusops` Control Plane):** The central governance, synthesis, policy, and compliance engine owned by the Central Data Platform / Infrastructure team.
   - **Repository B (Customer Domain Repositories):** Vendor- or domain-specific repositories owned by domain teams (e.g., `acme-corp/marketing-analytics`, `acme-corp/finance-data-platform`). Each domain repository houses **multiple data pipelines** with different schedules, triggers, compute footprints, and SLA requirements.
   - *Requirement:* MinusOps must provide a frictionless, audited mechanism to synthesize, package, and export standalone pipeline code and per-pipeline CI/CD workflows into domain repositories without coupling domain teams to the `minusops` internal source code.

2. **Semantic Run Workspaces & Global Run Index:**
   - Instead of opaque or timestamp-only directories (e.g., `20260822-111530-manual`), run workspaces must be named semantically from the grilling session: `<domain>-<workload>-<orchestrator>_<timestamp>` (e.g., `marketing-clickstream-mwaa_20260822_111530`).
   - A central index (`runs/INDEX.md` and `runs/index.json`) must maintain an up-to-date registry of all generated runs, owners, domains, architectures, cost estimates, and deployment states.

3. **Multi-Tier Lifecycle Governance (`dev` $\to$ `test` $\to$ `uat` $\to$ `prod`):**
   - MinusOps is the continuous control plane across all tiers, enforcing automated **Synthetic Data Proving** in `test`/`dev` before promoting through `uat` (reconciliation and data contracts) to `prod` (plan-hash bound MFA apply).

4. **Telemetry-Correlated Cloud Drift:**
   - Correlates out-of-band AWS Console / CLI modifications with CloudWatch failure logs (e.g., OOMs, timeouts) and CloudTrail identity, providing empirical proof before preventing accidental revert outages.

---

## 2. Enterprise Two-Repository Architecture

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        CENTRAL CONTROL PLANE: `minusops` (Repo A)                      │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ • Requirements & Grilling Interrogation (`grill-me` Pillar 1..14)                      │
│ • Architecture Synthesizer & Module Registry (`core/generation/`)                     │
│ • G6 Rego Policy Engine & G9 Ephemeral Sandbox Verification                           │
│ • Live BCM Pricing Calculator & Cost Intelligence                                     │
│ • Generated Workspace: `runs/<generated_name>_<timestamp>/`                            │
│   ├── terraform/          (Modular HCL: VPC, Medallion S3, Glue, Athena, IAM)         │
│   ├── dags/               (Airflow TaskFlow Python DAGs / Step Functions ASL)         │
│   ├── scripts/            (PySpark ETL, Iceberg Compaction, Metadata Fetcher)         │
│   ├── configs/            (connections.yaml, logging_config.yaml, teams.yaml)         │
│   ├── reports/            (proving_report.json, architecture.svg, cost_estimate.json) │
│   └── .github/workflows/  (Tailored per-pipeline GitHub Actions workflow)            │
└─────────────────────────────────────────┬──────────────────────────────────────────────┘
                                          │
                    minusctl export --target-repo <path-or-git-url>
                                          │
                                          ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                 DOMAIN REPOSITORY: `acme-corp/marketing-analytics` (Repo B)            │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ pipelines/                                                                             │
│ ├── clickstream_lakehouse/            <── Exported from MinusOps Run                   │
│ │   ├── terraform/                    (Clean, unencumbered HCL owned by domain team)   │
│ │   ├── dags/data_pipeline_dag.py     (Airflow DAG deployed to domain DAG bucket)      │
│ │   ├── scripts/etl.py                (PySpark Glue job script)                        │
│ │   └── configs/connections.yaml      (Domain-specific connection endpoints)           │
│ ├── customer_360_pipeline/            <── Another domain pipeline in same repo         │
│ └── ad_spend_attribution/             <── Another domain pipeline in same repo         │
│                                                                                        │
│ .github/workflows/                                                                     │
│ ├── clickstream-deploy.yml            <── Tailored workflow (Triggers on clickstream/) │
│ ├── customer360-deploy.yml            <── Tailored workflow (Triggers on cust360/)     │
│ └── adspend-deploy.yml                <── Tailored workflow (Triggers on adspend/)     │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Functional Requirements (FR)

### FR-01: Semantic Run Workspace Naming
* **Pattern:** `<domain>-<workload>-<orchestrator>_<YYYYMMDD_HHMMSS>`
* **Examples:**
  * `marketing-clickstream-mwaa_20260822_111530`
  * `finance-ledger-stepfunctions_20260822_113045`
  * `adtech-bidding-databricks_20260822_120500`
* **Derivation:** Automatically extracted from `requirements.json` (captured via `grill-me` session). If unspecified, falls back to `<sanitized-request>_<timestamp>`.

### FR-02: Central Run Registry (`runs/INDEX.md` & `runs/index.json`)
* Every run creation (`minusctl create` / `workflow.resolve`) automatically inserts/updates the registry index.
* **Schema of `runs/index.json`:**
  ```json
  [
    {
      "run_name": "marketing-clickstream-mwaa_20260822_111530",
      "pipeline_name": "marketing-clickstream",
      "owner": "marketing-data-eng@acme.com",
      "domain": "marketing",
      "orchestrator": "orchestrator-mwaa",
      "compute_engine": "compute-glue-etl",
      "storage_zones": ["bronze", "silver", "gold", "quarantine"],
      "created_at": "2026-08-22T11:15:30Z",
      "governance_status": "PROVEN_TEST",
      "estimated_monthly_cost": 248.50,
      "target_repo": "acme-corp/marketing-analytics",
      "path": "runs/marketing-clickstream-mwaa_20260822_111530"
    }
  ]
  ```
* **Markdown Representation (`runs/INDEX.md`):** Formatted summary table rendering pipeline names, owners, compute types, estimated costs, and clickable links to each run's HCL, DAGs, and verification reports.

### FR-03: Frictionless Export & Packaging (`minusctl export`)
* **Command:**
  ```bash
  minusctl export \
    --run marketing-clickstream-mwaa_20260822_111530 \
    --target-repo /path/to/marketing-analytics \
    --dest-dir pipelines/clickstream \
    --generate-workflow
  ```
* **Export Artifacts:**
  1. `pipelines/<pipeline_name>/terraform/` (Standard Terraform files, variables, outputs).
  2. `pipelines/<pipeline_name>/dags/` (Python Airflow DAGs / Step Functions ASL).
  3. `pipelines/<pipeline_name>/scripts/` (Glue PySpark scripts, compaction routines).
  4. `pipelines/<pipeline_name>/configs/` (`connections.yaml`, `logging_config.yaml`).
  5. `.github/workflows/<pipeline_name>-deploy.yml` (Scaffolded CI/CD workflow).

### FR-04: Tailored Per-Pipeline GitHub Actions Workflows
* In a multi-pipeline repository, a change to Pipeline A must **not** trigger deployments for Pipeline B.
* The exported workflow must configure path-based filters and environment-specific triggers:
  ```yaml
  name: Deploy Marketing Clickstream Pipeline
  on:
    push:
      branches: [main, dev, uat]
      paths:
        - 'pipelines/clickstream/**'
        - '.github/workflows/clickstream-deploy.yml'
    pull_request:
      branches: [main]
      paths:
        - 'pipelines/clickstream/**'
  ```
* Supports multi-environment promotion matrix (`dev` $\to$ `test` $\to$ `uat` $\to$ `prod`) using GitHub Environments and AWS OIDC STS AssumeRole.

### FR-05: Synthetic Data Proving Harness (`minusctl seed --execute`)
* Generates synthetic mock datasets (using rule-based/Faker generators) tailored to the schema declared in `requirements.json`.
* Executes step-by-step validation:
  1. **Hop 1 (Ingest):** Uploads mock data to Bronze S3 bucket $\to$ checks `PutObject` response.
  2. **Hop 2 (Transform):** Triggers Glue PySpark ETL $\to$ polls execution until `SUCCEEDED`.
  3. **Hop 3 (Data Quality):** Evaluates Great Expectations assertions on Silver/Gold $\to$ verifies quarantine routing.
  4. **Hop 4 (Serving):** Runs `MSCK REPAIR TABLE` and `SELECT COUNT(*)` on Athena Gold table.
  5. **Hop 5 (Evidence Report):** Writes signed `proving_report.json` with step-by-step latency, row counts, and error budget metrics.

### FR-06: Telemetry-Correlated Cloud Drift Intelligence
* Integrates `cloud_drift.py` with CloudTrail event lookups and CloudWatch error logs.
* When cloud drift is detected on a resource (e.g. Glue worker scaling `G.1X` $\to$ `G.2X`):
  * Queries CloudTrail for principal identity (`john.doe@acme.com`).
  * Queries CloudWatch/Glue logs for preceding error signatures (e.g. `OutOfMemoryError`).
  * If verified, surfaces advisory recommendation: *"Manual scaling was caused by verified OOM logs. Update `main.tf` to keep the scaling and re-anchor with `minusctl adopt --anchor`."*

---

## 4. Non-Functional Requirements (NFR)

* **NFR-01 (Zero Vendor Lock-in):** Exported pipelines must run cleanly using vanilla `terraform init && terraform apply` without requiring any MinusOps runtime or proprietary modules.
* **NFR-02 (Security & Least-Privilege):** Exported workflows must never contain static AWS access keys (`AKIA...`); all AWS authentication must utilize GitHub OIDC federated STS roles.
* **NFR-03 (Audit Integrity):** Every export, proving run, and environment promotion must append a signed entry to `.agents/logs/audit.jsonl`.
* **NFR-04 (Performance):** Central index generation and export packaging must complete in $< 500\text{ms}$ locally.

---

## 5. Coding Agent Advisory & Technical Questions

To ensure seamless implementation by coding agents, the following architectural advisory points and decisions are submitted for review:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        CODING AGENT ADVISORY & REVIEW QUESTIONS                        │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 1. [Workspace Renaming & Backward Compatibility]                                       │
│    • Proposal: Refactor `core/reporting/runs.py` so `new_run()` accepts an explicit    │
│      `name` argument, generating `<name>_<timestamp>`. If omitted, fallback to         │
│      `<blueprint>_<timestamp>`.                                                       │
│    • Question: Should legacy runs formatted as `YYYYMMDD-HHMMSS-<blueprint>` be        │
│      automatically migrated/aliased in `list_runs()`, or indexed as-is?                │
│                                                                                        │
│ 2. [Index Registry Concurrency]                                                        │
│    • Proposal: Store `runs/index.json` and generate `runs/INDEX.md` synchronously on    │
│      `new_run()` and `workflow.resolve()`.                                             │
│    • Question: Should we implement a lightweight file lock or atomic rename to         │
│      prevent race conditions during parallel test runs?                                │
│                                                                                        │
│ 3. [Export Workflow Template Structure]                                                │
│    • Proposal: Create modular Jinja2/string templates under `core/generation/templates/`│
│      for GitHub Actions, GitLab CI, and Jenkinsfile pipelines.                         │
│    • Question: Do we prefer pure Python stdlib template formatters (e.g. `string.Template│
│      / str.format`) over external dependencies like Jinja2 to maintain zero dependencies?│
│                                                                                        │
│ 4. [Telemetry Log Correlation Hook]                                                    │
│    • Proposal: Connect `core/governance/cloud_drift.py` to `core/providers/aws.py` to   │
│      query `glue:GetJobRuns` and `logs:FilterLogEvents` when a drift delta is detected │
│      on `aws_glue_job` or `aws_emr_cluster`.                                           │
│    • Question: Should telemetry log lookup be advisory (opt-in via `--with-telemetry`  │
│      or ambient AWS creds) to prevent blocking offline/dry-run environments?           │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Acceptance Criteria

1. **AC-01 (Semantic Run Creation):** Running `minusctl create "Clickstream pipeline for marketing team" --name marketing-clickstream` creates directory `runs/marketing-clickstream-mwaa_YYYYMMDD_HHMMSS/`.
2. **AC-02 (Central Index):** `runs/INDEX.md` and `runs/index.json` reflect the newly created run with owner, domain, estimated cost, and status.
3. **AC-03 (Export Command):** Running `minusctl export --run <run_name> --target-repo <path> --dest-dir pipelines/<name>` copies clean Terraform, DAGs, scripts, configs, and tailored GitHub Actions workflows.
4. **AC-04 (Per-Pipeline CI Isolation):** The exported GitHub Actions workflow contains `paths: ['pipelines/<name>/**']` so changes to unrelated folders do not trigger redundant deploys.
5. **AC-05 (Proving Report):** `minusctl seed --run <run_name>` generates `proving_report.json` detailing hop-by-hop latency and row counts.
6. **AC-06 (Drift Correlation):** `cloud_drift.py` surfaces preceding failure logs alongside drift warnings when AWS resources were scaled out-of-band.
