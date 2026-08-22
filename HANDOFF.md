# HANDOFF.md — Project Status & Handoff Ledger

> **Connected Context Map:** [`CONTEXT-MAP.md`](./CONTEXT-MAP.md)  
> **Primary Operating Rules:** [`.agents/AGENTS.md`](.agents/AGENTS.md)  
> **Active Implementation Branch:** `feat/minusops-enterprise-nextgen-v2`  
> **Master Architecture Specification:** [`2026-08-17_minusterraformrunaudit.md`](./2026-08-17_minusterraformrunaudit.md)

---

## Executive Summary

**MinusOps** is a workload-agnostic, multi-cloud infrastructure control plane and governance engine for Terraform. It wraps all infrastructure mutations in a plan-bound, MFA-gated, cryptographic audit trail.

This handoff ledger records the current state of the workspace, recent architectural completions, the verified directory context tree, and active operational procedures.

---

## 📍 Current State & Recent Milestones

### 0. Enterprise Next-Gen Upgrade — Branch `feat/minusops-enterprise-nextgen-v2` (2026-08-19)

All 21 engineering tickets from the **MinusOps Enterprise v2.0 Roadmap (`MINUS-140` – `MINUS-160`)** are **implemented, tested, and verified**.

* **Authoritative Progress Ledger:** [`docs/PROGRESS.md`](./docs/PROGRESS.md) Section 8.
* **Fast Test Suite:** **770 passed**, 85 skipped across **76 test files** (100% pass rate).
* **Module Catalog:** **24 production-grade Terraform modules** (added Snowflake, MSK, Databricks Delta, MWAA, Iceberg maintenance).
* **Working Tree:** 100% clean after test runs.

#### Enterprise v2.0 Sprints 1–4 Delivery Ledger:

| Sprint | Tickets | Delivered Scope & Capabilities | Primary Files |
| :--- | :--- | :--- | :--- |
| **Sprint 1** | `MINUS-140`, `156`, `143`, `144`, `145` | Pytest corpus diversion (clean tree); Day-0 Doctor skill; composite GitHub Action PR Reviewer with sticky SVG/BCM comments; OIDC merge-gate plan hash re-verification. | [`tests/conftest.py`](./tests/conftest.py), [`.agents/skills/doctor/SKILL.md`](.agents/skills/doctor/SKILL.md), [`.github/actions/pr-reviewer/`](.github/actions/pr-reviewer/) |
| **Sprint 2** | `MINUS-141`, `142`, `147`, `153` | Multi-team S3 remote state (`teams/<team_id>/<workload_id>/`); team derived from backend key (not user flags); discrete WORM S3 audit logger; sanitized team directory (`[a-z0-9-]{1,63}`). | [`core/architecture/team_resolver.py`](./core/architecture/team_resolver.py), [`core/governance/plan_gate.py`](./core/governance/plan_gate.py), [`core/governance/audit_logger.py`](./core/governance/audit_logger.py) |
| **Sprint 3** | `MINUS-148`, `149`, `150`, `151`, `152` | Snowflake on AWS (2-sided handshake defense); Databricks Unity Catalog external locations & Delta Sharing; private MWAA Airflow; AWS MSK Kafka (IAM SASL); Iceberg table maintenance Lambda. | [`modules/warehouse-snowflake-aws/`](./modules/warehouse-snowflake-aws/), [`modules/compute-databricks-delta/`](./modules/compute-databricks-delta/), [`modules/streaming-msk-kafka/`](./modules/streaming-msk-kafka/) |
| **Sprint 4** | `MINUS-146`, `154`, `155`, `157`, `158`, `159`, `160` | Dynamic BCM quantity derivation from requirements (15-min micro-batch cost impact); `minusctl doctor --fix` container auto-recovery; fail-closed production OPA gate; fuzzy run typo recovery with attached description tips; pre-requisite stage interception; 3-part actionable error formatting. | [`core/cost/bcm_pricing_calculator.py`](./core/cost/bcm_pricing_calculator.py), [`core/reporting/cli_diagnostics.py`](./core/reporting/cli_diagnostics.py), [`core/reporting/minusctl.py`](./core/reporting/minusctl.py) |

#### Governance State Note:
* **Plan `5cad83d9` Approval Revocation:** Approval record for `5cad83d9` was revoked and purged from disk following MINUS-146 discovery that 15-minute micro-batching requires ~$1,478/mo. The deploy gate now fails closed and requires a fresh plan/BCM/approval cycle before application.

#### Deliberately Scoped Decisions:
* **MINUS-126 & MINUS-127:** Scoped out third-party cloud provider bloat per `core/providers/base.py` AWS-only architecture. Cross-cloud ingestion uses **AWS IAM OIDC Workload Identity** (STS role assumption by GCP/Azure identities writing directly to S3).

---

### 1. Comprehensive Context Tree & Documentation Overhaul
The workspace is maintained with **14 dedicated, non-monolithic `CONTEXT-[folder].md` files** covering every subpackage, module, policy, test suite, and tool.
* **Master Map**: Connected to [`CONTEXT-MAP.md`](./CONTEXT-MAP.md) at the repository root.
* **Zero Specification Drift**: All context files are audited against disk code with 100% full-file coverage and `file://` markdown links.

---

## 🗂️ Workspace Context Directory Map

| Directory | Context File | Description |
| :--- | :--- | :--- |
| **Root Tree** | [`CONTEXT-MAP.md`](./CONTEXT-MAP.md) | Master context tree & maintenance operating guide |
| `core/` | [`core/CONTEXT-core.md`](./core/CONTEXT-core.md) | Governance & synthesis engine index |
| `core/governance/` | [`core/governance/CONTEXT-governance.md`](./core/governance/CONTEXT-governance.md) | Deploy gates, approvals, audit chains, drift & source guards |
| `core/generation/` | [`core/generation/CONTEXT-generation.md`](./core/generation/CONTEXT-generation.md) | IaC synthesizer, module registry & provenance |
| `core/architecture/` | [`core/architecture/CONTEXT-architecture.md`](./core/architecture/CONTEXT-architecture.md) | Requirements gates & 6-layer architecture model |
| `core/cost/` | [`core/cost/CONTEXT-cost.md`](./core/cost/CONTEXT-cost.md) | BCM Pricing Calculator & pricing catalog |
| `core/reporting/` | [`core/reporting/CONTEXT-reporting.md`](./core/reporting/CONTEXT-reporting.md) | `minusctl` CLI, FinOps agent & HCL scanner |
| `core/providers/` | [`core/providers/CONTEXT-providers.md`](./core/providers/CONTEXT-providers.md) | Multi-cloud provider abstraction (`aws`) |
| `app/` | [`app/CONTEXT-app.md`](./app/CONTEXT-app.md) | Plotly Dash control plane web console |
| `modules/` | [`modules/CONTEXT-modules.md`](./modules/CONTEXT-modules.md) | 21 building block Terraform modules |
| `.agents/` | [`.agents/CONTEXT-agents.md`](.agents/CONTEXT-agents.md) | Agent operating rules & 6 decision skills (grill-me, architect, etc.) |
| `docs/` | [`docs/CONTEXT-docs.md`](./docs/CONTEXT-docs.md) | IAM manifest, security model & docs library |
| `policy/` | [`policy/CONTEXT-policy.md`](./policy/CONTEXT-policy.md) | Rego policy rules & stage definitions |
| `examples/` | [`examples/CONTEXT-examples.md`](./examples/CONTEXT-examples.md) | IAM trust policies & BCM usage profiles |
| `tests/` | [`tests/CONTEXT-tests.md`](./tests/CONTEXT-tests.md) | 72 automated pytest test suites |
| `tools/` | [`tools/CONTEXT-tools.md`](./tools/CONTEXT-tools.md) | Environment diagnostic tools (`doctor.ps1`, `doctor.py`) |

---

## ⚡ Quick Start & Common Workflows

### 1. Diagnostics & Pre-Flight Check
```bash
python core/reporting/minusctl.py doctor
```

### 2. Requirements-First Creation Workflow
```bash
# 1. Gather requirements and generate run workspace
python core/reporting/minusctl.py create "create a data pipeline for ingestion and analytics"

# 2. Synthesize Terraform from requirements and decision records
python core/generation/synthesizer.py "ingest and analytics pipeline" \
  --run <run-id> \
  --requirements-file runs/<run-id>/requirements.json \
  --decision-file runs/<run-id>/architecture_decision.json
```

### 3. Stage Reflector Circuit Breaker
```bash
python core/governance/reflector.py --run-root runs/<run-id>
```

### 4. Secure Deployment Loop
```bash
# Verify HCL syntax and static rules
python core/governance/plan_gate.py verify --dir runs/<run-id>/terraform --policy-mode production

# Generate plan artifact and SHA-256 hash
python core/governance/plan_gate.py plan --dir runs/<run-id>/terraform

# Approve plan hash (HITL MFA gate)
python core/governance/plan_gate.py approve --dir runs/<run-id>/terraform

# Apply approved plan
python core/governance/plan_gate.py apply --dir runs/<run-id>/terraform
```

### 5. Launch Control Plane Dashboard
```bash
python app/dashboard_app.py
# Open http://127.0.0.1:8050
```

### 6. Run Test Suite
```bash
# Fast test suite (633 tests)
pytest tests/ -m "not slow"

# Complete test suite
pytest tests/
```
