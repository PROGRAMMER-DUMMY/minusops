# NextStackHelper.md — TerraShark & Awesome-TF Next-Gen Stack Integration Guide

> **Reference Repositories:**  
> • [`LukasNiessen/terrashark`](https://github.com/LukasNiessen/terrashark) — Pre-flight failure-mode AI governance skill  
> • [`shuaibiyy/awesome-tf`](https://github.com/shuaibiyy/awesome-tf) — Curated catalog of Terraform & OpenTofu ecosystem tools  
> **Target Architecture:** MinusOps Control Plane & Governance Engine  
> **Master Context Tree:** [`CONTEXT-MAP.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/CONTEXT-MAP.md)

---

## 1. Executive Summary & Overview

**MinusOps** is a workload-agnostic, multi-cloud infrastructure control plane and governance engine. To ensure MinusOps' next stack iterations incorporate the best-of-breed practices from the open-source IaC ecosystem, this guide synthesizes insights from two foundational repositories:

1. **TerraShark** (`LukasNiessen/terrashark`): Provides a **pre-flight failure-mode diagnostic workflow** for AI coding assistants, preventing anti-patterns (`count` vs `for_each`, secret leaks, address churn) *before* HCL generation.
2. **Awesome-TF** (`shuaibiyy/awesome-tf`): Provides a curated ecosystem catalog of **linting**, **security**, **IAM permission calculation**, **refactoring**, **cost estimation**, and **testing** utilities.

Combining these two pillars with MinusOps' **plan-bound deployment gates** ([`plan_gate.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/governance/plan_gate.py)) and **tamper-evident audit chains** ([`audit_chain.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/governance/audit_chain.py)) produces an end-to-end, zero-trust infrastructure stack.

---

## 2. TerraShark Failure-Mode Taxonomy (FM-01 to FM-05)

TerraShark categorizes infrastructure risks into 5 primary failure modes:

| ID | Failure Mode | Focus & Scope | Primary Risk Vectors & Diagnostic Rules |
| :--- | :--- | :--- | :--- |
| **FM-01** | **Identity Churn** | Addressing instability & refactor breakage | `count` indexing on mutable lists, missing `moved {}` blocks during refactors, keys derived from plan-unknown data. |
| **FM-02** | **Secret Exposure** | Credential leakage in state, logs & artifacts | Hardcoded variable defaults, assuming `sensitive = true` protects state, printing raw plan JSON in CI artifacts. |
| **FM-03** | **Blast Radius** | Oversized stacks & weak isolation boundaries | Monolithic root modules, shared state files across environments (`dev` & `prod` in 1 state), missing state locks. |
| **FM-04** | **CI Drift** | Pipeline drift & un-reviewed applies | Floating provider/runtime versions, uncommitted `.terraform.lock.hcl`, re-running `plan` on apply step instead of applying reviewed `tfplan`. |
| **FM-05** | **Compliance Gate Gaps** | Unenforced organizational policies | Static compliance docs instead of automated CI policy gates (Checkov, OPA/Rego), missing audit trails, blanket `ignore_changes`. |

---

## 3. Key Ecosystem Tools from Awesome-TF (`shuaibiyy/awesome-tf`)

The `awesome-tf` ecosystem offers specialized tools that enhance specific lifecycle stages of MinusOps:

```
                                  AWESOME-TF ECOSYSTEM MAP
                                             │
      ┌──────────────────┬───────────────────┼───────────────────┬──────────────────┐
      ▼                  ▼                   ▼                   ▼                  ▼
Static Linters        Security          IAM Permissions     Refactoring        Cost & Testing
(TFLint / HCL2)    (Checkov / Trivy)        (Pike)            (tfedit)     (Infracost / Terratest)
```

| Category | Featured Tool | Ecosystem Capabilities | MinusOps Integration Point |
| :--- | :--- | :--- | :--- |
| **Static Linters** | **TFLint** | Catches provider-specific errors (invalid AWS instance types, missing required tags, deprecated arguments). | Extend [`optimize_analyzer.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/reporting/optimize_analyzer.py) to parse TFLint AST rules alongside regex passes. |
| **Security Scanners** | **Checkov / Trivy / Terrascan** | Scans HCL for CIS benchmarks, SOC 2 compliance, unencrypted buckets, and open security groups. | Native integration in [`optimize_analyzer.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/reporting/optimize_analyzer.py) and [`rego_gate.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/governance/rego_gate.py). |
| **IAM Permission Generator** | **Pike** | Scans Terraform configuration files to calculate the minimum IAM policy required to deploy the stack. | Enforces least-privilege policies in [`docs/enterprise_iam_manifest.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/docs/enterprise_iam_manifest.md) & [`authz.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/governance/authz.py). |
| **Refactoring Utilities** | **tfedit** | Automated refactoring of HCL code structures, block renames, and attribute updates. | Automates HCL cleanups in [`synthesizer.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/generation/synthesizer.py) during module upgrades. |
| **Cost Forecasting** | **Infracost** | Generates speculative cost diffs directly on pull requests. | Complements MinusOps' live AWS BCM Pricing Calculator integration ([`bcm_pricing_calculator.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/cost/bcm_pricing_calculator.py)). |
| **Testing Harnesses** | **Terratest & `.tftest.hcl`** | End-to-end integration testing in Go or native HCL test blocks. | Extends MinusOps automated test suite in [`tests/`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/tests). |
| **CI/CD Drivers** | **Digger / Terrateam / Terrakube** | Orchestrates PR-driven Terraform execution inside GitHub Actions or GitLab CI. | Guides headless OIDC pipeline workflows in [`.github/workflows/deploy.yml`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.github/workflows/deploy.yml). |

---

## 4. Context Architecture Comparison: TerraShark vs. MinusOps

| Metric / Dimension | TerraShark (`LukasNiessen/terrashark`) | MinusOps (`CONTEXT-MAP.md` + `CONTEXT-[folder].md`) |
| :--- | :--- | :--- |
| **Primary Pivot** | **Risk / Failure Mode** (Identity Churn, Secrets, etc.) | **Codebase Architecture / Directory Path** |
| **Initial Footprint** | ~600 tokens (`SKILL.md`) | ~100 lines ([`CONTEXT-MAP.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/CONTEXT-MAP.md)) |
| **Loading Trigger** | Diagnostic risk classification | Target folder of code modification |
| **Documentation Scope** | General IaC anti-patterns & Terraform best practices | Project-specific APIs, line numbers, & state invariants |
| **Sync Discipline** | Static skill reference files | Enforced atomic updates on code edits |

---

## 5. Unified Next-Gen Architecture & Integration Pipeline

```
                 UNIFIED MINUSOPS NEXT-GEN GOVERNANCE PIPELINE
                 
┌──────────────────────┐    ┌──────────────────────┐    ┌──────────────────────┐
│       grill-me       │───►│      architect       │───►│    synthesizer.py    │
│  (Requirements +     │    │  (Failure-Mode-First │    │ (Context Assembly +  │
│   Failure Profiling) │    │   Output Contract)   │    │  `moved` & tfedit)   │
└──────────────────────┘    └──────────────────────┘    └──────────────────────┘
                                                                   │
                                                                   ▼
┌──────────────────────┐    ┌──────────────────────┐    ┌──────────────────────┐
│    Plan Deploy Gate  │◄───│     rego_gate.py     │◄───│ optimize_analyzer.py │
│ (MFA + Plan-Hash App)│    │(Plan Destructive     │    │ (Checkov + TFLint +  │
└──────────────────────┘    │ Identity Churn Rules)│    │  Pike Permission Scans)│
                            └──────────────────────┘    └──────────────────────┘
```

### 1. [`grill-me` Skill](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/skills/grill-me/SKILL.md) Enhancements
* **Failure-Mode Interrogation**: Explicitly profile FM-01..FM-05 risks during requirement gathering.
* **Refactor Mode Capture**: Capture `old_address -> new_address` tuples for refactor projects to drive downstream `moved {}` generation.

### 2. [`architect` Skill](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/skills/architect/SKILL.md) Enhancements
* **TerraShark-Style Output Contract**: Require candidate architecture proposals to specify Blast Radius limits, Identity Churn risk mitigation, state migration plans, and rollback notes.
* **Pike Least-Privilege IAM Integration**: Run `Pike` analysis to attach auto-calculated least-privilege IAM policy manifests alongside proposed module graphs.

### 3. [`synthesizer.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/generation/synthesizer.py) Enhancements
* **Automated `moved {}` Engine**: Automatically generate `moved.tf` declarations during refactoring or module upgrades.
* **Pre-Flight Anti-Pattern Checklists**: Inject TerraShark checklists (prohibiting `count` on dynamic resources, enforcing `sensitive = true`) before committing HCL to run workspaces.

### 4. [`optimize_analyzer.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/reporting/optimize_analyzer.py) Enhancements
* **Multi-Tool AST & Scanner Pass**: Combine native regex passes with `TFLint` (provider validation), `Checkov` (compliance), and `Pike` (IAM permissions).
* **Categorize under FM-01..FM-05**: Group findings in `optimization_report.md` under TerraShark's 5 Failure Modes.

### 5. [`rego_gate.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/governance/rego_gate.py) Enhancements
* **OPA Rego `CHURN-01` Rule**: Flag un-indexed `["delete", "create"]` actions on persistent data resources (`aws_s3_bucket`, `aws_rds_cluster`, `aws_dynamodb_table`) as **HIGH severity blocking findings**.

---

## 6. Summary Action Plan

1. **Integrate TerraShark Skill & Checklists**: Add `terrashark` as a reference skill under `.agents/skills/terrashark/`.
2. **Integrate Awesome-TF Tools**: Ingest `TFLint`, `Pike`, and `Checkov` into [`optimize_analyzer.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/reporting/optimize_analyzer.py).
3. **Automate `moved {}` Blocks**: Add native `moved` block generation in [`synthesizer.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/generation/synthesizer.py) to prevent address churn data loss during refactoring.
