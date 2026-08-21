# Product Requirements Document (PRD) — MinusOps Enterprise Data Platform & Control Plane (v3.0)

| Attribute | Details |
| :--- | :--- |
| **Document ID** | PRD-ARCH-2026-003 (Revision 3 — Enterprise Data Platform) |
| **Status** | APPROVED ARCHITECTURE SPECIFICATION |
| **Lead Architect** | Matt (Principal Cloud Architect & Governance Lead) |
| **Author** | MinusOps Autonomous Architecture Engine |
| **Target Engine** | `core/` Control Plane & `modules/` IaC Library |
| **Target Cloud** | AWS (Multi-Cloud extensible via Terraform Provider Registry) |
| **Date** | 2026-08-21 |

---

## 1. Executive Summary & Core Mission

**MinusOps** is a multi-cloud, workload-agnostic operational control plane and IaC synthesis engine. It eliminates cloud infrastructure sprawl, unauthorized production changes, runaway data warehouse bills, and compliance breaches by pairing:
1. A **13-Pillar Architectural Grilling Engine** that extracts full functional and non-functional requirements before any Terraform is authored.
2. A **4-Tier Zero-Trust Guardrail Engine** (`plan_gate.py`) that enforces SHA-256 plan-hash locking, cryptographic Two-Person STS rules, and AST-level destructive action blocking.
3. A **Smart Transport Subagent Fabric** (`.agents/subagents/`) providing approval-gated, pure-Python integrations with Slack, Microsoft Teams, Outlook, Confluence, and Jira.

---

## 2. The 13-Pillar Architectural Grilling Matrix

The mandatory front door for any data platform request is the **13-Pillar Requirements Interrogation** (`.agents/skills/grill-me/SKILL.md`):

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ MINUSOPS 13-PILLAR DATA PLATFORM & INFRASTRUCTURE MATRIX                    │
├────┬─────────────────────────┬──────────────────────────────────────────────┤
│ #  │ Pillar                  │ Architectural Decisions & Generated HCL      │
├────┼─────────────────────────┼──────────────────────────────────────────────┤
│ 1  │ **Ingestion Source**    │ Database (CDC DMS), SaaS (AppFlow), Files    │
│    │                         │ (S3), Webhooks (API GW), Streams (Kinesis).  │
├────┼─────────────────────────┼──────────────────────────────────────────────┤
│ 2  │ **Storage & Format**    │ Medallion S3 (Bronze/Silver/Gold), Apache    │
│    │                         │ Iceberg, S3 Object Lock (Governance Mode).   │
├────┼─────────────────────────┼──────────────────────────────────────────────┤
│ 3  │ **Compute Engine**      │ Glue Spark (`G.1X` vs `G.2X`), EMR Serverless│
│    │                         │ dbt-on-Athena (SQL-only serverless).         │
├────┼─────────────────────────┼──────────────────────────────────────────────┤
│ 4  │ **Orchestration**       │ AWS Step Functions (DAGs) or MWAA Airflow;   │
│    │                         │ explicit cron schedule or EventBridge rule.  │
├────┼─────────────────────────┼──────────────────────────────────────────────┤
│ 5  │ **Data Quality**        │ Great Expectations contracts; dead-letter    │
│    │                         │ routing to Quarantine S3 bucket.             │
├────┼─────────────────────────┼──────────────────────────────────────────────┤
│ 6  │ **Serving Layer**       │ Athena Workgroups (10 GiB cutoff) vs.        │
│    │                         │ Amazon Redshift Serverless consumption mart. │
├────┼─────────────────────────┼──────────────────────────────────────────────┤
│ 7  │ **Alert Routing**       │ 3-tier routing: P1 Crash, DQ Failure, Spend. │
├────┼─────────────────────────┼──────────────────────────────────────────────┤
│ 8  │ **Logging & FinOps**    │ CloudWatch retention (30d Dev / 90d Prod);   │
│    │                         │ automated PII masking; S3 Server Access logs.│
├────┼─────────────────────────┼──────────────────────────────────────────────┤
│ 9  │ **Secrets & KMS**       │ AWS Secrets Manager ARNs (dynamic) vs SSM    │
│    │                         │ (static); dedicated Customer Managed CMK.    │
├────┼─────────────────────────┼──────────────────────────────────────────────┤
│ 10 │ **VPC Endpoints**       │ S3 Gateway Endpoints (bypasses $0.045/GB NAT)│
│    │                         │ Private subnets with zero public ingress.    │
├────┼─────────────────────────┼──────────────────────────────────────────────┤
│ 11 │ **Data Modeling & SCD** │ Star Schema (Fact/Dimension) vs. OBT;        │
│    │                         │ SCD Type 1 vs Type 2 history; worker sizing. │
├────┼─────────────────────────┼──────────────────────────────────────────────┤
│ 12 │ **Warehouse FinOps**    │ Redshift Serverless Base RPUs (8 RPU) + Max  │
│    │                         │ RPU cap + Auto-Suspend (60s) on Snowflake.   │
├────┼─────────────────────────┼──────────────────────────────────────────────┤
│ 13 │ **Data Governance**     │ Lake Formation Tag-Based Access Control      │
│    │                         │ (TBAC), Column-level PII masking, S3 Tiering.│
└────┴─────────────────────────┴──────────────────────────────────────────────┘
```

---

## 3. Data Modeling & Infrastructure Sizing Matrix

The data model directly dictates the compute worker class, partition keys, and storage tiering:

| Data Modeling Requirement | Glue Worker Class | Partition Strategy (`aws_glue_catalog_table`) | S3 Storage Lifecycle |
| :--- | :--- | :--- | :--- |
| **Append-Only Event Stream** | `G.1X` (4 vCPU, 16GB) | `year/month/day/hour` | Bronze ➔ Glacier IR at 30d ➔ Deep Archive at 90d |
| **SCD Type 1 Overwrite** | `G.1X` (4 vCPU, 16GB) | `domain/entity_id` | Standard storage |
| **SCD Type 2 Historical Mart** | `G.2X` (8 vCPU, 32GB) | `valid_from_year/valid_from_month` | Fact: Glacier at 30d; Dim: Standard storage |
| **High-Volume CDC Merge** | `G.2X` + DynamoDB Lock | `table_name/date` | Daily compaction; Iceberg snapshot expiry at 7d |

---

## 4. Smart Transport Subagent Fabric (`.agents/subagents/`)

MinusOps delegates all outbound messaging to single-purpose, stdlib-only transport subagents:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 5 OUTBOUND TRANSPORT SUBAGENTS                                              │
├──────────────────────┬──────────────────────────────────────────────────────┤
│ Manifest             │ Transport & Core Capability                          │
├──────────────────────┼──────────────────────────────────────────────────────┤
│ **`slack-agent`**    │ Slack Block Kit JSON; interactive Plan-Approval      │
│                      │ cards with SHA-256 hash-bound Approve/Reject buttons.│
├──────────────────────┼──────────────────────────────────────────────────────┤
│ **`teams-agent`**    │ MS Teams Adaptive Cards v1.4; structured FactSets for│
│                      │ Great Expectations data-quality & quarantine alerts. │
├──────────────────────┼──────────────────────────────────────────────────────┤
│ **`outlook-agent`**  │ Multi-part MIME HTML email over SMTP (Port 587       │
│                      │ STARTTLS) with `.xlsx` dual-workbooks attached.      │
├──────────────────────┼──────────────────────────────────────────────────────┤
│ **`confluence-agent`**│ Confluence Cloud REST API; idempotent XHTML living  │
│                      │ architecture doc upsert (`version = current + 1`).   │
├──────────────────────┼──────────────────────────────────────────────────────┤
│ **`jira-agent`**     │ Atlassian Document Format (ADF); automated creation  │
│                      │ of Change-Management and incident tickets.           │
└──────────────────────┴──────────────────────────────────────────────────────┘
```

### Safety & Deduplication Invariants:
1. **No Bearer Credentials in Tool Calls:** Webhook URLs and API tokens are resolved internally via `base_hook.resolve_secret()`.
2. **Alert Storm Shield:** 5-minute sliding window memory-efficient deduplication cache suppresses duplicate alert bursts (bypassed on intentional docs/tickets).
3. **Approval Gated:** Every send passes through `approval.request_approval()` and appends to `.agents/logs/audit.jsonl`.
4. **`ok` is NOT `sent`:** Unconfigured destinations return `{"ok": True, "sent": False, "reason": "not_configured"}`.

---

## 5. Security Architecture & 2-Tier Permissions Boundary

To eliminate privilege escalation, MinusOps enforces a **2-Tier IAM Boundary**:
1. **Agent Runner Boundary (`MinusOpsAgentConstructionBoundary`):** Attached to the CI/CD runner and AI agent. Strictly denies `s3:DeleteBucket*`, `kms:Delete*`, `kms:PutKeyPolicy`, `iam:DeleteRolePermissionsBoundary`, and `dynamodb:DeleteTable`.
2. **Workload Execution Boundary (`MinusOpsWorkloadExecutionBoundary`):** Attached to generated Glue/EMR roles. Permits `s3:DeleteObject` and `glue:DeleteTableVersion` exclusively within scoped project prefixes to enable Iceberg compaction and vacuuming.

---

## 6. Failure Modes & Mitigations (TerraShark Taxonomy)

| Code | Canonical Failure Mode | MinusOps Pre-Flight Mitigation |
| :--- | :--- | :--- |
| **FM-01** | **Identity Churn** (count indexing, missing `moved {}` blocks) | Enforces `for_each` on stable keys; synthesizes `moved` blocks during refactors. |
| **FM-02** | **Secret Exposure** (hardcoded defaults, log leaks) | Resolves credentials via AWS Secrets Manager ARNs; suppresses state output. |
| **FM-03** | **Blast Radius** (shared state across envs, missing locks) | Scoped remote state paths (`teams/<domain>/<project>/<workload>/`) + S3 locks. |
| **FM-04** | **CI Drift** (floating provider versions, re-planning in CI) | Pinned `.terraform.lock.hcl` + SHA-256 plan-hash bound deploy gate. |
| **FM-05** | **Compliance Gate Gaps** (static docs instead of CI policies) | 4-Tier Zero-Trust Guardrails (AST verify + checkov/tfsec + Two-Person STS Rule). |

---

## 7. Functional Requirements (FR)

* **FR-01 (Multi-Project State Isolation):** Deterministic remote state layout scoped per Domain, Project, and Workload.
* **FR-02 (Deterministic Guardrails):** AST-level plan verification and fail-closed block on unapproved destructive actions.
* **FR-03 (Two-Person STS Rule):** Cryptographic enforcement that $\text{Planner STS Identity} \neq \text{Approver STS Identity}$.
* **FR-04 (Attributed MoM Variance):** Compute dollar and percentage variance with exact cost driver attribution.
* **FR-05 (Dual-Workbook FinOps):** Pure-Python OpenXML generator producing `executive_project_summary.xlsx` and `pipeline_detailed_ledger.xlsx`.
* **FR-06 (13-Pillar Grilling Engine):** Interrogate the complete 13 data platform dimensions prior to HCL synthesis.
* **FR-07 (Crypto-Shredding Compliance):** S3 Object Lock in Governance mode + per-batch KMS CMK crypto-shredding for GDPR Art. 17.
* **FR-08 (FinOps Circuit Breakers):** Explicit 120-min Glue timeout (`timeout = var.timeout_minutes`), 10 GiB Athena scan cutoff, and S3 lifecycle tiering.
* **FR-09 (Feed-Factory CI/CD):** Reusable `workflow_call` template with dynamic matrix discovery (`feeds/*.yaml`) and Jenkins parity.
* **FR-10 (Subagent Transport Fabric):** 5 transport subagents with 5-minute sliding window alert deduplication.
* **FR-11 (Data Modeling & Warehouse Sizing):** Sizing Glue workers (`G.1X`/`G.2X`) and Redshift Serverless RPUs based on dimensional modeling.
* **FR-12 (Lake Formation & Column Masking):** Tag-based access control (TBAC) and PII masking policies in generated HCL.

---

## 8. Verification & Acceptance Test Suite

* [x] **AST Destructive Change Gate:** `tests/test_destructive_governance.py` validates fail-closed blocks on destroy actions.
* [x] **Plan-Gate Invariant Tests:** `tests/test_plan_gate.py` verifies plan-hash immutability and approval validation.
* [x] **FinOps Circuit Breaker Tests:** `tests/test_finops_circuit_breakers.py` asserts Glue timeouts, Athena cutoffs, and Glacier lifecycle.
* [x] **Subagent Manifest & Alert Dedup Tests:** `tests/test_subagent_manifests.py` and `tests/test_alert_dedup.py` pass across all 5 transports.
* [x] **Logging & KMS Governance Tests:** `tests/test_logging_governance.py` asserts explicit retention and CMK encryption.
* [x] **CI/CD Feed Factory Tests:** `tests/test_cicd.py` validates 4-lane pre-merge gates, matrix discovery, and Jenkins parity.
* [ ] **Live AWS Acceptance (Phase 2):** S3 RTC replication benchmark ($< 15\text{m}$ RPO) and Disaster Recovery Replay ($< 120\text{m}$ RTO).
