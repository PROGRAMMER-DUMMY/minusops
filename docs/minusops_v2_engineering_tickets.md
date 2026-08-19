# MinusOps Enterprise v2.0 -- Engineering Ticket Ledger

> **Author:** Matt (Staff Auditor & Principal Architect)  
> **Target Release:** MinusOps Enterprise v2.0  
> **Ticket Range:** `MINUS-140` - `MINUS-160` (21 Sequenced Engineering Tickets)  
> **Status:** Ratified & Ready for Coding Agent Execution  

---

## Master Ticket Index

```
+------------+----------------------------------------------------------+----------+-----------+
| Ticket ID  | Summary                                                  | Priority | Epic      |
+------------+----------------------------------------------------------+----------+-----------+
| MINUS-140  | Pytest Knowledge Store Clock Freezing (datetime.now())   | P1 High  | Hardening |
| MINUS-141  | Remote S3 State & Multi-Team Path Generator              | P0 Block | Multi-Team|
| MINUS-142  | Team-Scoped IAM Deploy Roles & STS Policy Binding        | P1 High  | Multi-Team|
| MINUS-143  | Turnkey GitHub Action PR Reviewer (action-review@v1)     | P0 Block | GitOps    |
| MINUS-144  | PR Bot Sticky Comment (Architecture SVG + BCM Diff Table)| P1 High  | GitOps    |
| MINUS-145  | OIDC Production Merge Gate & Hash Re-Verification        | P0 Block | GitOps    |
| MINUS-146  | Heuristic BCM Usage-Line Auto-Populator from Requirements| P1 High  | FinOps    |
| MINUS-147  | Immutable S3 WORM & CloudWatch Dual-Audit Log Shipper    | P1 High  | Security  |
| MINUS-148  | Snowflake on AWS Integration Module (External Stages)    | P1 High  | Catalog   |
| MINUS-149  | Databricks Unity Catalog External Locations & Delta Sink | P1 High  | Catalog   |
| MINUS-150  | Amazon Managed Workflows for Apache Airflow (MWAA Module)| P2 Medium| Catalog   |
| MINUS-151  | AWS Managed Streaming for Apache Kafka (MSK Module)      | P2 Medium| Catalog   |
| MINUS-152  | Apache Iceberg Table Compaction & Maintenance Lambda     | P1 High  | Catalog   |
| MINUS-153  | Central Team Directory (configs/teams.yaml) Resolver     | P1 High  | Core      |
| MINUS-154  | minusctl doctor --fix Container Auto-Recovery Engine     | P2 Medium| DX/Tools  |
| MINUS-155  | G6 Rego Gate Promotion from Shadow to Primary Blocker    | P1 High  | Security  |
| MINUS-156  | Dedicated Day-0 Setup & Doctor Agent Skill               | P1 High  | DX/Agent  |
| MINUS-157  | minusctl Fuzzy Run Matching & Typo Auto-Recovery         | P1 High  | Agent-DX  |
| MINUS-158  | Pre-Requisite Stage Interception & Step-by-Step Guidance | P0 Block | Agent-DX  |
| MINUS-159  | Rich Subcommand --help, CLI Introspection & Examples     | P2 Medium| Agent-DX  |
| MINUS-160  | Actionable Error Formatter & Next-Command Generator      | P1 High  | Agent-DX  |
+------------+----------------------------------------------------------+----------+-----------+
```

---

## Deep Engineering Specifications for Every Ticket

---

### [MINUS-140] Pytest Knowledge Store Corpus Diversion
* **Priority:** P1 (High) | **Component:** `tests/conftest.py`, `core/generation/knowledge_store.py`
* **Problem:** Running pytest rewrites timestamps in `knowledge/claims/*.jsonl`, creating dirty working tree diffs.
* **Fix:** Divert test corpus to `tmp_path_factory` so test executions produce zero git churn.

---

### [MINUS-141] Remote S3 State & Multi-Team Path Generator
* **Priority:** P0 (Blocker) | **Component:** `core/generation/synthesizer.py`, `core/governance/plan_gate.py`
* **Fix:** Implement remote state generation targeting `s3://<bucket>/teams/<team_id>/<workload_id>/terraform.tfstate` with `use_lockfile = true`.

---

### [MINUS-142] Team-Scoped IAM Deploy Roles & STS Policy Binding
* **Priority:** P1 (High) | **Component:** `core/governance/plan_gate.py`
* **Fix:** Enhance `_identity()` and `stage_approve` in `plan_gate.py` to assert that caller's STS role matches the authorized deploy role for the target team.

---

### [MINUS-143] Turnkey GitHub Action PR Reviewer (`action-review@v1`)
* **Priority:** P0 (Blocker) | **Component:** `.github/actions/pr-reviewer/action.yml`
* **Fix:** Package `.github/actions/pr-reviewer` into a standalone, versioned GitHub Action supporting composite execution with AWS OIDC federation.

---

### [MINUS-144] PR Bot Sticky Comment (Architecture SVG + BCM Diff Table)
* **Priority:** P1 (High) | **Component:** `.github/actions/pr-reviewer/comment.py`
* **Fix:** Implement sticky PR comment bot that posts click-to-code `architecture.svg`, BCM monthly cost diff table, Reflector 5-gate badge, and SHA-256 plan hash.

---

### [MINUS-145] OIDC Production Merge Gate & Hash Re-Verification
* **Priority:** P0 (Blocker) | **Component:** `.github/workflows/deploy.yml`
* **Fix:** On push to `main`, re-verify the plan hash against the PR-reviewed hash before invoking `plan_gate.py apply`.

---

### [MINUS-146] Heuristic BCM Usage-Line Auto-Populator from Requirements
* **Priority:** P1 (High) | **Component:** `core/cost/bcm_pricing_calculator.py`
* **Fix:** Implement `auto_populate_usage(requirements)` that dynamically derives quantities from schedule (e.g. 15-min micro-batch = 96 runs/day) and retention.

---

### [MINUS-147] Immutable S3 WORM & CloudWatch Dual-Audit Log Shipper
* **Priority:** P1 (High) | **Component:** `core/governance/audit_logger.py`, `core/governance/audit_chain.py`
* **Fix:** Add synchronous dual-emission to ship signed audit records to an S3 bucket configured with Object Lock (GOVERNANCE mode) or CloudWatch LogStream.

---

### [MINUS-148] Snowflake on AWS Integration Module (External Stages)
* **Priority:** P1 (High) | **Component:** `modules/warehouse-snowflake-aws/main.tf`
* **Fix:** Create `modules/warehouse-snowflake-aws` providing AWS IAM cross-account storage integration role with SEC-05 external ID verification and Snowpipe SQS auto-ingest queues.

---

### [MINUS-149] Databricks Unity Catalog External Locations & Delta Sink
* **Priority:** P1 (High) | **Component:** `modules/compute-databricks-delta/main.tf`
* **Fix:** Provision `databricks_external_location`, `databricks_storage_credential`, and Delta Sharing share grants pointing to Medallion Gold S3 storage.

---

### [MINUS-150] Amazon Managed Workflows for Apache Airflow (MWAA Module)
* **Priority:** P2 (Medium) | **Component:** `modules/orchestrator-mwaa/main.tf`
* **Fix:** Provision `aws_mwaa_environment` in private VPC subnets with dedicated DAG bucket storage, KMS encryption, and CloudWatch log streaming.

---

### [MINUS-151] AWS Managed Streaming for Apache Kafka (MSK Module)
* **Priority:** P2 (Medium) | **Component:** `modules/streaming-msk-kafka/main.tf`
* **Fix:** Provision `aws_msk_cluster` with multi-AZ broker distribution, IAM SASL authentication, and S3 Sink connector integration.

---

### [MINUS-152] Apache Iceberg Table Compaction & Maintenance Lambda
* **Priority:** P1 (High) | **Component:** `modules/query-athena/iceberg_maintenance.tf`
* **Fix:** Synthesize an EventBridge-scheduled Lambda function that runs Iceberg compaction (`OPTIMIZE`) and `VACUUM` snapshot expiration against Gold tables.

---

### [MINUS-153] Central Team Directory (`configs/teams.yaml`) Resolver
* **Priority:** P1 (High) | **Component:** `core/architecture/team_resolver.py`
* **Fix:** Implement `team_resolver.py` to parse `configs/teams.yaml`, auto-populating team DLs, Slack/Teams webhook Secrets Manager references, and chargeback tags.

---

### [MINUS-154] `minusctl doctor --fix` Container Auto-Recovery Engine
* **Priority:** P2 (Medium) | **Component:** `core/reporting/doctor.py`
* **Fix:** Implement `minusctl doctor --fix` which checks Docker daemon health and automatically starts a LocalStack container bound to port 4566.

---

### [MINUS-155] G6 Rego Gate Production Policy Enforcement
* **Priority:** P1 (High) | **Component:** `core/governance/rego_gate.py`, `core/governance/plan_gate.py`
* **Fix:** Enforce mandatory OPA presence in `production` policy mode, while following the per-rule `rule_stages.json` registry.

---

### [MINUS-156] Dedicated Day-0 Setup & Doctor Agent Skill
* **Priority:** P1 (High) | **Component:** `.agents/skills/doctor/SKILL.md`
* **Fix:** Specialized skill manifest that auto-invokes `minusctl doctor --json` and gives the engineer/agent a green light before workspace creation.

---

### [MINUS-157] `minusctl` Fuzzy Run Matching & Typo Auto-Recovery
* **Priority:** P1 (High) | **Component:** `core/reporting/minusctl.py`, `core/reporting/runs.py`
* **Problem:** If an agent makes a typo in a run ID (e.g. `minusctl plan --run 20260819-10142`), it gets a vague error.
* **Fix:** Implement `difflib.get_close_matches()` fuzzy lookup with dynamic attached description tips (`get_run_description_tip()`) and terminal control character stripping.

---

### [MINUS-158] Pre-Requisite Stage Interception & Step-by-Step Guidance
* **Priority:** P0 (Blocker) | **Component:** `core/reporting/minusctl.py`, `core/reporting/runs.py`
* **Problem:** If an agent jumps ahead, error messages are cryptic.
* **Fix:** Intercept subcommands to verify prior lifecycle artifacts (Requirements -> ADR -> Synthesis -> Plan -> Approval).

---

### [MINUS-159] Rich Subcommand `--help`, CLI Introspection & Examples
* **Priority:** P2 (Medium) | **Component:** `core/reporting/minusctl.py`
* **Problem:** Subcommand `--help` displays bare argparse text with no usage examples or stage context.
* **Fix:** Upgrade all `--help` epilogs with concrete copy-pasteable examples, required inputs, output artifacts, and next steps per command, following the `cli-architect` dbt-gold standard.

---

### [MINUS-160] Actionable Error Formatter & Next-Command Generator
* **Priority:** P1 (High) | **Component:** `core/reporting/minusctl.py`
* **Problem:** General failures leave the agent guessing what to do next.
* **Fix:** Standardize all CLI error outputs to the **3-Part Actionable Diagnostics Structure** (WHAT FAILED, WHY IT FAILED, ACTION REQUIRED).
