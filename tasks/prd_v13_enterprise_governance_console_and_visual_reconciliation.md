# Product Requirements Document (PRD) — Enterprise Visual Governance Console, Multi-Agent Tracing, Data Lineage & Bi-Directional Architecture Reconciliation (v13.0)

| Attribute | Details |
| :--- | :--- |
| **Document ID** | PRD-ARCH-2026-013 (Revision 13.0 — Next-Gen Governance Console & Visual Reconciliation) |
| **Document Name** | `tasks/prd_v13_enterprise_governance_console_and_visual_reconciliation.md` |
| **Status** | APPROVED SPECIFICATION FOR IMPLEMENTATION |
| **Lead Architect** | Matt (Principal Cloud Architect & CLI Design Lead) |
| **Target Components** | `app/console_app.py`, `core/architecture/reconciler.py`, `core/reporting/lineage_graph.py`, `core/governance/agent_tracer.py`, `core/reporting/vault.py`, `core/cli/commands/console.py`, `tests/test_console_app.py`, `tests/test_reconciler.py` |
| **Target Audience** | Coding Agent, Platform Engineers, Enterprise Cloud Architects, SecOps Teams |
| **Date** | August 23, 2026 |

---

## 1. Executive Summary & Core Motivation

### 1.1 The Problem
The legacy dashboard (`app/dashboard_app.py`) was structured around 5 disjointed operational tabs that mixed CLI command execution with basic FinOps and report viewers. It lacked:
1. **Interactive Visual Topology:** Could not provide an in-browser editable canvas or visual inspection of network boundaries.
2. **Data Lineage Architecture:** Fails to display dataset-to-dataset flow (Medallion architecture: Ingress -> Bronze -> Silver -> Gold -> Serving) and Lake Formation PII column masking.
3. **Multi-Agent Observability:** Provided zero visibility into active subagents, agent relays, execution durations, and input/output handoffs.
4. **Governed Visual-to-Code Reconciliation:** When architects spotted misalignments in visual diagrams, there was no safe, governed mechanism to translate visual corrections back into Terraform HCL without risking out-of-band drift.
5. **Unified Deliverables Vault:** Generated PDFs, HTML reports, and signed JSON evidence were scattered across subdirectories rather than presented in an intuitive document center.

### 1.2 The Solution (PRD v13.0)
This specification deprecates the legacy dashboard and replaces it with the **MinusOps Enterprise Visual Governance Console (`app/console_app.py`)**, anchored on four pillars:
* **Interactive Architecture & Topology Canvas:** Embedded Draw.io viewer with official cloud vector stencils (AWS/Azure/GCP/Databricks), multi-zone subnet clusters, and 1-click external editing via `app.diagrams.net/#R...`.
* **5-Hop Data Lineage & Governance Graph:** Visual dataset flow tracing raw ingestion through PySpark transformations, Great Expectations data quality gates, quarantine poison isolation, Iceberg curated tables, and Lake Formation TBAC/PII masking.
* **Multi-Agent Execution Trace & Live Monitor:** Real-time active agent status telemetry and chronological relay DAG tracing the full lifecycle (User Prompt -> `grill-me` -> `architect` -> `synthesizer` -> `reflector` -> `plan_gate` -> `proving` -> `slack-agent`) with cryptographic audit hash linkages.
* **Governed Bi-Directional Visual Reconciliation (`core/architecture/reconciler.py`):** Enables architects to correct topological connections on the canvas, intercepts edits with an explicit **Architecture Change Review Modal** (Author, Plain-English Diff, Impact Warning, Unified HCL Diff), and requires explicit confirmation before updating `main.tf` and invalidating the prior plan hash.
* **Unified Deliverables & Compliance Vault (`core/reporting/vault.py`):** In-browser previewer and 1-click download center for all generated PDFs (`plan.pdf`, `cost.pdf`, `inspect.pdf`), interactive HTML reports, Draw.io XML, FinOps Excel workbooks, and signed 5-hop `proving_report.json`.

---

## 2. Core Architectural Invariants (Non-Negotiable)

1. **Git / HCL as Single Source of Truth:** The canvas is an *interface for intent*, never the state database. All approved changes must be reconciled into standard Terraform HCL in Git.
2. **No Direct Cloud Mutations from UI:** The console is strictly a review and governance interface. It never invokes un-gated cloud mutation APIs.
3. **Plan-Bound Deploy Gate Integrity:** Visual reconciliation automatically invalidates prior approvals (`status = STALE_PLAN`) and forces a fresh `minusctl gate plan` cycle.
4. **Standard-Library Core Engine:** All backend reconciliation, lineage graph compilation, agent tracing, and vault packaging must rely strictly on the Python standard library.
5. **Zero-Emoji Doctrine:** Strictly zero emojis in terminal output, log lines, generated XML, markdown documentation, or UI elements.

---

## 3. Functional Requirements (FR)

### FR-01: Redesigned Visual Governance Console (`app/console_app.py`)
* **FR-01.1 (Run-Scoped 4-View Architecture):** Provide four clean, dedicated views per run workspace:
  1. `[ 1. Architecture Topology ]`: Interactive Draw.io canvas with official AWS vector stencils, subnet clusters, and 1-click editor link.
  2. `[ 2. Data Lineage & Governance ]`: 5-Hop Medallion dataset flow, schema formats, quarantine branch, and Lake Formation PII masking.
  3. `[ 3. Multi-Agent Execution Trace ]`: Live active subagents, chronological relay timeline, input/output inspection, and audit links.
  4. `[ 4. Deliverables & Compliance Vault ]`: In-browser preview and 1-click downloaders for PDFs, HTML, XML, Excel, and JSON evidence.
* **FR-01.2 (Run Header & Safety Card):** Persistent header displaying Semantic Run ID, Domain, Target Tier, Plan Hash (`plan_hash: <sha256>`), BCM Evidenced Cost ($/mo) vs Budget Cap, and Reflector Gate Status.
* **FR-01.3 (Requirements & Decision Drawer):** Collapsible drawer presenting business goals, quantified NFRs (scale, latency SLA, retention, budget), missing/deferred requirement alerts, module justifications, and rollback procedures.

### FR-02: Interactive Architecture Canvas & 1-Click Live Editor
* **FR-02.1 (Embedded Draw.io Viewer):** Interactive canvas with pan, zoom, and click-to-inspect resource cards displaying sizing (workers/memory) and encryption badges (SSE-KMS, PAB).
* **FR-02.2 (1-Click Editor Launcher):** Prominent button launching the deflated URL (`https://app.diagrams.net/#R...`) in a new browser tab for live multi-stakeholder design sessions.
* **FR-02.3 (Step Flow Execution Ledger):** In-canvas and tabular ledger detailing each sequential hop (`[1]` -> `[5]`), communication protocol (HTTPS, S3 Read, JDBC), latency budget, and security controls.

### FR-03: End-to-End Data Lineage & PII Masking Architecture (`core/reporting/lineage_graph.py`)
* **FR-03.1 (Dataset-to-Dataset Flow):** Visual Directed Acyclic Graph (DAG) tracing:
  * `Ingress Sources` (API Gateway, Kinesis, AppFlow, SFTP)
  * `S3 Bronze Landing` (Raw JSON/CSV, SSE-KMS CMK)
  * `PySpark Transformation` (AWS Glue 4.0 / EMR Serverless)
  * `S3 Silver Stage` (Cleaned, partitioned Parquet)
  * `Data Quality Gate` (Great Expectations contracts)
  * `Quarantine Isolation` (Schema anomaly / poison records S3 bucket)
  * `S3 Gold Curated Lakehouse` (ACID Apache Iceberg v2 tables)
  * `Serving Consumption Endpoints` (Athena Workgroup, Redshift Serverless, dbt MetricFlow).
* **FR-03.2 (Lake Formation & PII Masking Layer):** Visual representation of column-level access controls (highlighting unmasked access for billing roles vs. masked views `***-**-1234` for analytics roles).
* **FR-03.3 (Click-to-Inspect Node):** Sidebar displaying table format, partitioning scheme (`year/month/day`), retention lifecycle (Glacier after 90d), and DPU worker allocation.

### FR-04: Multi-Agent Execution Trace & Live Telemetry (`core/governance/agent_tracer.py`)
* **FR-04.1 (Live Active Agent Monitor):** Real-time monitoring card displaying running subagents, assigned persona (`grill-me`, `architect`, `diagrammer`, `reflector`, `slack-agent`), model tier (`pro`, `flash`), execution duration, and live tool call snippet.
* **FR-04.2 (Chronological Agent Relay Timeline):** Interactive timeline detailing the complete workflow execution path:
  * `grill-me-agent`: Captured requirements -> `requirements.json`
  * `architect-agent`: Researched services & selected modules -> `architecture_decision.json`
  * `synthesizer-engine`: Generated Terraform HCL -> `terraform/main.tf`
  * `diagrammer-agent`: Compiled Draw.io XML & URL -> `architecture.drawio`
  * `reflector-agent`: Evaluated 5 independent gates -> `reflector_verdict.json`
  * `orchestrator`: Generated plan & computed hash -> `plan.json` (`plan_hash`)
  * `proving-agent`: Executed 5-hop synthetic data test -> `proving_report.json`
  * `slack-agent` / `teams-agent`: Dispatched approval card -> Webhook confirmation.
* **FR-04.3 (Hop Inspection & Audit Binding):** Clicking any timeline node reveals input parameters, output artifacts, execution duration, and cryptographic SHA-256 link to `.agents/logs/audit.jsonl`.

### FR-05: Governed Bi-Directional Visual Reconciliation (`core/architecture/reconciler.py`)
* **FR-05.1 (Canvas Change Interception):** When an architect modifies a connection or module parameter on the visual canvas, the console intercepts the change before code modification.
* **FR-05.2 (Architecture Change Review Modal):** Presents an unbypassable confirmation modal containing:
  1. *Author & Timestamp:* Authenticated operator identity and session timestamp.
  2. *Plain-English Change Summary:* Exact declaration of what changed (e.g. `Re-routed Glue ETL source: module.storage.gold_bucket_arn -> module.storage.bronze_bucket_arn`).
  3. *Safety & Lineage Warning:* Clear notice regarding data flow alterations and plan invalidation.
  4. *Proposed HCL Code Diff:* Side-by-side git diff of proposed updates in `terraform/main.tf` and `architecture_decision.json`.
* **FR-05.3 (Atomic HCL Update & Plan Invalidation):**
  * Upon human confirmation, updates `terraform/main.tf` and `architecture_decision.json`.
  * Automatically revokes prior plan approval, setting run status to `STALE_PLAN (NEEDS_REPLAN)`.
  * Logs the change to `.agents/logs/audit.jsonl` under action `ARCH_VISUAL_RECONCILIATION`.
  * Prompts operator in UI and terminal to execute `minusctl gate plan`.

### FR-06: Unified Deliverables & Compliance Vault (`core/reporting/vault.py`)
* **FR-06.1 (Document Catalog & Viewer):** In-browser modal viewer and direct downloaders for:
  * Executive PDFs: `plan.pdf`, `cost.pdf`, `inspect.pdf`.
  * Interactive HTML Reports: `report.html`, `cost.html`.
  * Diagrams & Visual Assets: `architecture.drawio`, `architecture_url.txt`, `architecture.svg`, `dataflow.svg`.
  * FinOps Excel Workbooks: `executive_project_summary.xlsx`, `pipeline_detailed_ledger.xlsx`.
  * Signed Governance Evidence: `proving_report.json`, `enterprise-package.md`, `manifest.json`.
* **FR-06.2 (1-Click Compliance Bundle Export):** Generates a cryptographically signed `.zip` compliance archive containing all artifacts for auditor handoff.

---

## 4. Technical Architecture & Component Specifications

```
                                  ┌────────────────────────────────────────────────────────┐
                                  │      MinusOps Control Plane Console (app/console_app)  │
                                  └───────────────────────────┬────────────────────────────┘
                                                              │
               ┌──────────────────────────────┬───────────────┴──────────────┬──────────────────────────────┐
               ▼                              ▼                              ▼                              ▼
┌──────────────────────────────┐┌──────────────────────────────┐┌──────────────────────────────┐┌──────────────────────────────┐
│ 1. Topology & Draw.io        ││ 2. Data Lineage Engine       ││ 3. Multi-Agent Tracer        ││ 4. Deliverables Vault        │
│    `drawio_generator.py`     ││    `lineage_graph.py`        ││    `agent_tracer.py`         ││    `vault.py`                │
│    - Official AWS stencils   ││    - 5-Hop Medallion DAG     ││    - Live active subagents   ││    - In-browser PDF viewer   │
│    - Subnet clusters         ││    - Quarantine fork         ││    - Relay timeline DAG      ││    - 1-Click .zip bundle     │
│    - 1-Click deflated URL    ││    - Lake Formation PII TBAC ││    - Cryptographic audit link││    - FinOps Excel workbooks  │
└──────────────┬───────────────┘└──────────────────────────────┘└──────────────────────────────┘└──────────────────────────────┘
               │
               ▼ (On Visual Connection Edit)
┌────────────────────────────────────────────────────────┐
│ 5. Bi-Directional Reconciler (`reconciler.py`)         │
│    - Architecture Change Review Modal                  │
│    - Side-by-side HCL git diff                         │
│    - Plan hash invalidation (`STALE_PLAN`)             │
│    - Audit logging -> `.agents/logs/audit.jsonl`       │
└────────────────────────────────────────────────────────┘
```

---

## 5. Delivery Work Packages & Implementation Plan

| Work Package | Target Files | Delivered Scope |
| :--- | :--- | :--- |
| **WP-01** | `core/architecture/reconciler.py` | Bi-directional Draw.io-to-HCL reconciliation engine, diff generator, and plan-hash invalidator. |
| **WP-02** | `core/reporting/lineage_graph.py` | 5-Hop Medallion dataset lineage generator with quarantine branching and Lake Formation PII masking representation. |
| **WP-03** | `core/governance/agent_tracer.py` | Multi-agent execution telemetry engine parsing `audit.jsonl`, `run.json`, and live subagent states. |
| **WP-04** | `core/reporting/vault.py` | Centralized deliverables document vault, previewer endpoints, and 1-click ZIP packager. |
| **WP-05** | `app/console_app.py`, `core/cli/commands/console.py` | Next-generation Plotly Dash / modern web console replacing legacy `app/dashboard_app.py`, exposed via `minusctl console`. |
| **WP-06** | `tests/test_reconciler.py`, `tests/test_console_app.py`, `tests/test_lineage_graph.py` | Comprehensive test suite covering reconciliation safety, lineage validity, agent tracing, and stdlib invariants. |

---

## 6. Acceptance Criteria (Sign-Off Invariants)

1. **Strict Reconciliation Safety:** Modifying a connection on the canvas must never update `main.tf` without rendering the Architecture Change Review Modal and receiving explicit user confirmation.
2. **Automatic Plan Invalidation:** Confirming a visual reconciliation must immediately revoke any prior `plan_hash` approval and set the run status to `STALE_PLAN (NEEDS_REPLAN)`.
3. **Complete Lineage & Traceability:** Lineage graph must correctly display the 5 Medallion hops, the quarantine fork, and Lake Formation PII masking; agent trace must link each execution step to its entry in `.agents/logs/audit.jsonl`.
4. **All Generated Artifacts Reachable:** Every document in the 6 categories (PDFs, HTML, Draw.io XML, Excel, JSON evidence) must be previewable and downloadable from the Vault.
5. **Zero External Binary Dependencies:** Core engine modules (`reconciler.py`, `lineage_graph.py`, `agent_tracer.py`, `vault.py`) must import strictly from the Python standard library.
6. **100% Test Pass Rate:** `pytest` must pass with 100% exit code 0 across the entire repository test suite.
7. **Clean Zero-Emoji Compliance:** All terminal outputs, code comments, markdown documentation, and UI components must strictly adhere to the zero-emoji invariant.
