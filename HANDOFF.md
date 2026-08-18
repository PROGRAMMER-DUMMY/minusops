# HANDOFF.md — Project Status & Handoff Ledger

> **Connected Context Map:** [`CONTEXT-MAP.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/CONTEXT-MAP.md)  
> **Primary Operating Rules:** [`.agents/AGENTS.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/AGENTS.md)

---

## Executive Summary

**MinusOps** is a workload-agnostic, multi-cloud infrastructure control plane and governance engine for Terraform. It wraps all infrastructure mutations in a plan-bound, MFA-gated, cryptographic audit trail.

This handoff ledger records the current state of the workspace, recent architectural completions, the verified directory context tree, and active operational procedures.

---

## 📍 Current State & Recent Milestones

### 0. Enterprise Next-Gen upgrade — branch `feat/minusops-enterprise-nextgen-v2` (2026-08-18)

Steps 1-9 of the roadmap in section 21 of `2026-08-17_minusterraformrunaudit.md` are
implemented. `docs/PROGRESS.md` section 7 is the authoritative per-step record, including
every deviation from the ticket text and why. Headlines:

| Step | Delivered |
| :--- | :--- |
| 1 | `minusctl doctor` (cross-platform); TerraShark FM-01..05 + the 4-part ADR output contract |
| 2 | Glue IAM S3/KMS grants, auto-wired job paths, TFLint, `moved {}` generation |
| 3 | `force_destroy` on dev buckets, hashed KMS alias, opt-in S3 remote state with a directory-bound key |
| 4 | already done before this branch — `synthesizer.py` anchors the baseline at line 836 |
| 5 | Glue catalog database, EventBridge schedule, `src/dbt/` scaffold, dbt-only mode |
| 6 | `envs/{dev,staging,prod}.tfvars`, SIEM CloudTrail data events, S3 CRR, mandatory tags |
| 7 | 7-pillar `grill-me`, 3-tier alerts + quarantine, `src/` scaffold, 4 ingestion modules |
| 8 | compute tier matrix + EMR Spot module, the Stage Reflector, `--based-on` |
| 9 | `minusctl seed`, `minusctl adopt`, the PR reviewer action |

**Two tickets were deliberately NOT built**, with reasoning in `docs/PROGRESS.md`:
MINUS-126 (data hub / Lake Formation / MSK / Delta Sharing — four unrelated integrations, no
stated requirement, unverifiable without a consumer account) and the multi-cloud half of
MINUS-127, which contradicts the recorded decision in `core/providers/base.py` that this build
is AWS-only. The on-premise half of MINUS-127 is already covered by `ingestion-dms`.

**Safety notes for whoever picks this up:**
* `minusctl seed --execute` is the **only** command in `minusctl` that mutates AWS. It is
  opt-in, routes through `approval.py`, and names every side effect in the prompt.
* The mandatory-tag `check` block **warns** at plan time; it does not fail the plan.
  Cross-variable `validation` needs Terraform >= 1.9 and `required_version` is `">= 1.5"`.
* The `slow` test suite (real Terraform, 326 tests) cannot be run alongside the fast suite:
  both default to `--basetemp=.pytest_tmp` and collide on Windows. It also fills disk fast --
  `tmp_path_retention_policy = "failed"` in `pyproject.toml` caps it, but run it serially.

---


### 1. Comprehensive Context Tree & Documentation Overhaul
The workspace has been fully documented with **14 dedicated, non-monolithic `CONTEXT-[folder].md` files** covering every subpackage, module, policy, test suite, and tool.
* **Master Map**: Connected to [`CONTEXT-MAP.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/CONTEXT-MAP.md) at the repository root.
* **Line-by-Line Verification**: All context files have been audited against actual code on disk with 100% full-file coverage and `file://` markdown links.

### 2. Core Subsystem Readiness
* **Deploy Gate (`core/governance/plan_gate.py`)**: Enforces `verify` → `plan` → `SHA-256 hash approval` → `apply`. Hash-binds execution to an exact plan artifact.
* **Audit Chain (`core/governance/audit_chain.py`)**: Cryptographic, tamper-evident hash logging in `.agents/logs/audit.jsonl`.
* **Requirements & Synthesis Engine (`core/generation/synthesizer.py`)**: Interrogates requirements via `grill-me`, researches via `architect`, and composes 16 vetted building block modules in `modules/`.
* **FinOps Intelligence (`core/cost/` & `core/reporting/finops_agent.py`)**: Integrates AWS Cost Explorer, anomaly detection, and BCM Pricing Calculator API.
* **Control Plane Console (`app/dashboard_app.py`)**: Live Plotly Dash web dashboard featuring click-to-code architecture mapping to Terraform files.

---

## 🗂️ Workspace Context Directory Map

| Directory | Context File | Description |
| :--- | :--- | :--- |
| **Root Tree** | [`CONTEXT-MAP.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/CONTEXT-MAP.md) | Master context tree & maintenance operating guide |
| `core/` | [`core/CONTEXT-core.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/CONTEXT-core.md) | Governance & synthesis engine index |
| `core/governance/` | [`core/governance/CONTEXT-governance.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/governance/CONTEXT-governance.md) | Deploy gates, approvals, audit logs & source guards |
| `core/generation/` | [`core/generation/CONTEXT-generation.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/generation/CONTEXT-generation.md) | IaC synthesizer, module registry & provenance |
| `core/architecture/` | [`core/architecture/CONTEXT-architecture.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/CONTEXT-architecture.md) | Requirements gates & 6-layer architecture model |
| `core/cost/` | [`core/cost/CONTEXT-cost.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/cost/CONTEXT-cost.md) | BCM Pricing Calculator & pricing catalog |
| `core/reporting/` | [`core/reporting/CONTEXT-reporting.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/reporting/CONTEXT-reporting.md) | `minusctl` CLI, FinOps agent & HCL scanner |
| `core/providers/` | [`core/providers/CONTEXT-providers.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/CONTEXT-providers.md) | Multi-cloud provider abstraction (`aws`, `azure`, `gcp`) |
| `app/` | [`app/CONTEXT-app.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/app/CONTEXT-app.md) | Plotly Dash control plane web console |
| `modules/` | [`modules/CONTEXT-modules.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/modules/CONTEXT-modules.md) | 16 building block Terraform modules |
| `.agents/` | [`.agents/CONTEXT-agents.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/CONTEXT-agents.md) | Agent operating rules & 6 decision skills |
| `docs/` | [`docs/CONTEXT-docs.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/docs/CONTEXT-docs.md) | IAM manifest, security model & docs library |
| `policy/` | [`policy/CONTEXT-policy.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/policy/CONTEXT-policy.md) | Rego policy rules & stage definitions |
| `examples/` | [`examples/CONTEXT-examples.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/examples/CONTEXT-examples.md) | IAM trust policies & BCM usage profiles |
| `tests/` | [`tests/CONTEXT-tests.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/tests/CONTEXT-tests.md) | 57 automated pytest test suites |
| `tools/` | [`tools/CONTEXT-tools.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/tools/CONTEXT-tools.md) | Environment diagnostic tools (`doctor.ps1`) |

---

## ⚡ Quick Start & Common Workflows

### 1. Requirements-First Creation Workflow
```bash
# 1. Gather requirements and generate run workspace
python core/reporting/minusctl.py create "create a data pipeline for ingestion and analytics"

# 2. Synthesize Terraform from requirements and decision records
python core/generation/synthesizer.py "ingest and analytics pipeline" --run <run-id>
```

### 2. Secure Deployment Loop
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

### 3. Launch Control Plane Dashboard
```bash
python app/dashboard_app.py
# Open http://127.0.0.1:8050
```

### 4. Run Diagnostic Checks & Test Suite
```bash
powershell -ExecutionPolicy Bypass -File ./tools/doctor.ps1
pytest tests/
```
