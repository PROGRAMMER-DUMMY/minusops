# Product Requirements Document (PRD) — Intelligent Incident Diagnostics, Remediation Trade-Offs & Serving Layer Topology (v9.0)

| Attribute | Details |
| :--- | :--- |
| **Document ID** | PRD-ARCH-2026-009 (Revision 9.0 — Intelligent Diagnostics, Remediation Options & Serving Layer Topology) |
| **Document Name** | `tasks/prd_v9_incident_diagnostics_and_serving_topology.md` |
| **Status** | PENDING ARCHITECTURAL ADVISORY & IMPLEMENTATION |
| **Lead Architect** | Matt (Principal Cloud Architect & CLI Design Lead) |
| **Author** | MinusOps Autonomous Architecture Engine |
| **Target Components** | `core/reporting/incident_diagnostics.py`, `core/cli/commands/gate.py`, `core/governance/plan_gate.py`, `modules/query-athena/`, `modules/consumption-redshift-serverless/`, `modules/dbt-semantic-layer/` |
| **Target Runtime** | Local CLI (`minusctl`), AWS CloudWatch, AWS Glue, Amazon Athena, Redshift Serverless |
| **Date** | August 22, 2026 |

---

## 1. Executive Summary & Problem Statement

When infrastructure deployments or analytical pipelines encounter errors in enterprise environments, traditional tools emit raw, opaque stack traces that force engineers into tedious trial-and-error debugging.

This specification establishes two core pillars for MinusOps:
1. **Intelligent Incident Diagnostics & Remediation Engine:** Converts raw Terraform apply errors, Glue JobRun failures, and Athena query exceptions into a structured **4-part resolution report** containing exact log evidence, root-cause analysis, evaluated remediation alternatives with cost trade-offs, and the exact next command (`minusctl next`).
2. **Serving Layer Topology & Endpoints:** Formalizes where and how transformed analytical data is served across four core enterprise consumption archetypes (Ad-hoc Serverless SQL, High-Concurrency Data Warehouse, Semantic Metrics for BI/AI, and Reverse ETL).

---

## 2. Functional Requirements (FR)

### FR-01: Structured Incident Resolution Report
Whenever a command fails (e.g. `minusctl gate apply`, `minusctl prove --execute`, or a live pipeline run), the CLI formats the diagnostic output into a standardized, emoji-free ASCII report:

```text
====================================================================================================
DIAGNOSTIC & INCIDENT RESOLUTION REPORT
====================================================================================================

1. EXACT LOG & TELEMETRY EVIDENCE
   • Resource Address: aws_glue_job.customer_events_etl
   • Run/Job ID:       jr_8a71ef09c21b
   • Failure Event:    2026-08-22 02:45:12 UTC
   • Raw Error Log:    "/aws-glue/jobs/error: Container killed by YARN for exceeding memory limits.
                       5.5 GB of 5.5 GB physical memory used. Container marked as failed."

2. ROOT-CAUSE ANALYSIS
   • Category:         Compute Memory Exhaustion (OutOfMemoryError)
   • Detailed Cause:   Executor memory exhausted during wide shuffle join operation.
   • Vulnerability:    WorkerType G.1X (16 GB RAM) is insufficient for current 50 GB partition batch.

3. EVALUATION OF ALTERNATIVES & TRADE-OFFS

   Option A (Vertical Scaling — Zero Code Change):
   • Change:           Upgrade worker_type from "G.1X" to "G.2X" (32 GB RAM per worker).
   • Cost Impact:      +$0.44/hour per worker (from $0.44 -> $0.88/hour).
   • Implementation:   Update `worker_type = "G.2X"` in `modules/compute-glue-etl/main.tf`.

   Option B (Partition Optimization — FinOps Optimized):
   • Change:           Keep G.1X workers; adjust Spark partition size in `scripts/etl.py` (`repartition(200)`).
   • Cost Impact:      $0.00 additional cost.
   • Implementation:   Add `.repartition(200)` before wide join in transformation script.

   Option C (Architecture Pivot — Serverless Scaling):
   • Change:           Replace AWS Glue with EMR Serverless (dynamic memory allocation).
   • Cost Impact:      Pay per vCPU-second utilized; scales down to zero when idle.

4. ACTIONABLE INSTRUCTION & NEXT COMMAND
   To proceed with Option A (Vertical Scale):
   1. Update `modules/compute-glue-etl/main.tf`
   2. Execute:
      $ minusctl gate plan
====================================================================================================
```

### FR-02: Failure Signature & Log Extraction Engine
* Scans and parses failure events across 4 operational domains:
  1. **Terraform Apply Errors:** IAM eventual consistency (`InvalidParameterException`), KMS service principal omission, S3 naming collisions, VPC quota limits (`VcpuLimitExceeded`).
  2. **Glue / Spark JobRun Failures:** OOM kills (`Container killed by YARN`), skew timeouts, schema mismatches, partition limit breaches.
  3. **Athena / Trino Query Failures:** Query timeout (30 min limit), HIVE_CANNOT_OPEN_SPLIT, Iceberg metadata corruption, partition projection parsing errors.
  4. **Proving Harness Data Quality Failures:** Great Expectations assertion failures, quarantine spillover thresholds exceeded.

### FR-03: Evaluation of Alternatives & Trade-Off Matrix
* Every diagnosed issue must evaluate at least **two viable paths forward**:
  * **Option A (Infrastructure / Scaling):** Immediate fix via capacity or configuration adjustment, including the calculated cost delta.
  * **Option B (Code / Optimization):** Zero-cost optimization (partitioning, indexing, query rewrite) that preserves current spend.

### FR-04: Next Command Integration (`minusctl next`)
* Updates the `minusctl next` CLI state machine to surface the recommended diagnostic action and command line based on the latest failure event in `workflow.json`.

---

## 3. Serving Layer Topology & Consumption Archetypes

MinusOps pipelines store clean data in the **Gold Medallion Layer** (S3 Parquet / Iceberg / Delta). The serving layer delivers this data to downstream enterprise consumers across four defined archetypes:

```text
                               [ S3 Gold Medallion Layer ]
                               (Clean, Partitioned Data)
                                           │
         ┌──────────────────┬──────────────┴───────────────┬──────────────────┐
         ▼                  ▼                              ▼                  ▼
  [ Archetype 1 ]    [ Archetype 2 ]                [ Archetype 3 ]    [ Archetype 4 ]
  Ad-Hoc Serverless  High-Concurrency Enterprise    Semantic Metrics   Reverse ETL &
  SQL & BI Reports   Data Warehouse                 for BI / AI Apps   Operational Apps
  ─────────────────  ───────────────────────────    ────────────────   ────────────────
  • Amazon Athena    • Amazon Redshift Serverless   • dbt MetricFlow   • S3 Direct Stage
  • Glue Iceberg     • Star / Snowflake Schemas     • Cube.js SQL API  • Snowflake Stage
  • Tableau / PowerBI• Sub-Second Executive Dash    • LangChain/Agents • Salesforce/Hubspot
```

### 3.1 Serving Archetype Matrix

| Archetype | Primary Technology | Latency SLA | Target Consumers | Module Responsible |
| :--- | :--- | :--- | :--- | :--- |
| **1. Ad-Hoc SQL & Exploration** | Amazon Athena v3 (Presto/Trino) | Seconds ($\sim 2\text{s} - 15\text{s}$) | Data Analysts, Ad-hoc SQL, Tableau, Superset | `modules/query-athena` |
| **2. Enterprise Data Warehouse** | Amazon Redshift Serverless / Snowflake | Sub-second ($\sim 200\text{ms} - 1\text{s}$) | Executive Dashboards, High-Concurrency BI | `modules/consumption-redshift-serverless` |
| **3. Semantic Layer & AI Agents**| dbt MetricFlow / Cube.js SQL API | Milliseconds to Sub-second | AI Agents, Text-to-SQL LLMs, Metric Consumers | `modules/dbt-semantic-layer`, `modules/cube-semantic-layer` |
| **4. Operational Reverse ETL** | AWS AppFlow / S3 Sync / Snowflake Stage| Scheduled / Event-Driven | Salesforce, HubSpot, Zendesk, Internal DBs | `modules/storage-medallion-s3` (Export) |

---

## 4. Non-Functional Requirements (NFR)

* **NFR-01 (Zero Emojis):** Strict ASCII formatting across all terminal reports, error logs, and generated documentation.
* **NFR-02 (Sub-Second Offline Diagnostics):** Failure analysis against local error logs must execute sub-50ms with zero network calls in offline mode.
* **NFR-03 (Cost Delta Accuracy):** Scaling options must reference verified AWS pricing rates (e.g. Glue DPU rate `$0.44$/DPU-hour`).

---

## 5. Technical Advisory Questions for Principal Architect (Matt) & Coding Agent

1. **Failure Signature Parsing Architecture:**
   * Should failure signatures be implemented as a declarative lookup table of regex patterns (`FAILURE_SIGNATURES = [...]`) or a modular rule engine in `core/reporting/incident_diagnostics.py`?
2. **Telemetry Extraction in Offline / Credential-Less Environments:**
   * When `--with-telemetry` is not specified, how should the diagnostics engine extract error logs (e.g. reading from local `tfplan.json`, `terraform.log`, or `proving_report.json`) while failing open gracefully?
3. **Serving Endpoints Display:**
   * How should the 4 serving archetypes and their connection strings (Athena JDBC URL, Redshift Endpoint, dbt MetricFlow path) be integrated into `minusctl runs describe` and `minusctl export`?
