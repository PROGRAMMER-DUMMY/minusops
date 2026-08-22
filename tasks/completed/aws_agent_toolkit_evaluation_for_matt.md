# Architecture Evaluation: AWS Agent Toolkit Integration & Adoption Strategy

| Attribute | Details |
| :--- | :--- |
| **Document ID** | EVAL-AWS-AGENT-TOOLKIT-2026-001 |
| **Source Repository** | [`https://github.com/aws/agent-toolkit-for-aws`](https://github.com/aws/agent-toolkit-for-aws) |
| **Official Documentation** | [`https://aws.amazon.com/products/developer-tools/agent-toolkit-for-aws/`](https://aws.amazon.com/products/developer-tools/agent-toolkit-for-aws/) |
| **Reviewer** | Matt (Principal Cloud Architect & Governance Lead) |
| **Review Standard** | Ponytail Anti-Overengineering (YAGNI) & Zero-Trust Blast-Radius Governance |
| **Target Integration** | `core/providers/aws.py` & `core/architecture/discovery.py` |
| **Date** | 2026-08-21 |

---

## 1. Executive Summary & Architectural Verdict

The **Agent Toolkit for AWS** is Amazon's official suite of **Model Context Protocol (MCP) servers, modular agent skills, and developer tooling** designed to enable AI coding agents (Claude Code, Antigravity, Cursor, Codex) to interact with AWS services natively.

### The Verdict:
* **Adopt selectively as an upstream Provider Sensor:** We should leverage its live documentation retrieval, regional capability lookups, and lazy-loading skills pattern to enhance `core/architecture/discovery.py` and `doctor.py`.
* **Reject its mutation and provisioning path:** We strictly reject direct mutating API calls via `aws___run_script` or CDK/CloudFormation bias. All infrastructure mutations MUST continue to route through MinusOps's deterministic **Terraform Plan-Gate (`plan_gate.py`)**, **IAM Permissions Boundary**, and **Two-Person STS Rule**.

---

## 2. What We Can Take (With Concrete Proof & Source Mappings)

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│ 4 PROVEN ADOPTION POINTS FROM AWS AGENT TOOLKIT                                             │
├──────────────────────────┬─────────────────────────────────────┬────────────────────────────┤
│ AWS Toolkit Component    │ Source Proof (Repo/MCP Endpoint)    │ MinusOps Integration Point │
├──────────────────────────┼─────────────────────────────────────┼────────────────────────────┤
│ 1. Live Doc Retrieval    │ `aws___search_documentation` &      │ Wire into `discovery.py` & │
│    (Zero Hallucination)  │ `aws___read_documentation`          │ `architect` skill lookup   │
├──────────────────────────┼─────────────────────────────────────┼────────────────────────────┤
│ 2. Regional Capability   │ `aws___get_regional_availability` & │ Pre-flight validation gate │
│    & Quota Verification  │ `aws___list_regions`                │ in `plan_gate.py verify`   │
├──────────────────────────┼─────────────────────────────────────┼────────────────────────────┤
│ 3. Lazy-Loading Skills   │ `/skills/<domain>/SKILL.md`         │ Modular recipes in         │
│    Context Pattern       │ lazy-loading manifest structure     │ `core/generation/modules/` │
├──────────────────────────┼─────────────────────────────────────┼────────────────────────────┤
│ 4. 1-Click Setup Wizard  │ `aws configure agent-toolkit` CLI   │ Diagnostic check in        │
│    (Developer UX)        │ interactive configuration wizard    │ `core/reporting/doctor.py` │
└──────────────────────────┴─────────────────────────────────────┴────────────────────────────┘
```

---

### Deep-Dive on Adoption Items:

### 2.1 Live AWS Documentation & Parameter Search
* **Source Proof:**
  * Tool endpoint: `aws___search_documentation`
  * Tool endpoint: `aws___read_documentation`
  * *Where Seen:* AWS Agent Toolkit MCP Server specification (`/mcp-server/`).
* **Why It Matters:**
  * LLMs frequently hallucinate deprecated Terraform resource arguments or obsolete AWS CLI flags (e.g. guessing Glue 4.0 parameter names or Athena query limits).
* **How MinusOps Adopts It:**
  * Update [`core/architecture/discovery.py`](../../core/architecture/discovery.py) to check if the AWS MCP Server is running locally. If active, query `aws___search_documentation` for the authoritative parameter schema before synthesizing HCL modules.

---

### 2.2 Pre-Flight Regional Service Availability Matrix
* **Source Proof:**
  * Tool endpoint: `aws___get_regional_availability`
  * Tool endpoint: `aws___list_regions`
* **Why It Matters:**
  * New features (e.g., S3 Replication Time Control, Glue 4.0 Flex execution, Apache Iceberg REST catalog) are not universally available in every AWS region (e.g., `ap-south-1` vs `us-gov-west-1`).
* **How MinusOps Adopts It:**
  * In `plan_gate.py verify`, run a pre-flight regional check against `aws___get_regional_availability` to assert that all selected architecture modules are supported in `aws:RequestedRegion` *before* running `terraform plan`.

---

### 2.3 Context-Efficient Lazy-Loading Skills Pattern
* **Source Proof:**
  * Directory layout: `/skills/<category>/SKILL.md`
  * Tool endpoint: `aws___retrieve_skill`
  * *Where Seen:* [`https://github.com/aws/agent-toolkit-for-aws/tree/main/skills`](https://github.com/aws/agent-toolkit-for-aws)
* **Why It Matters (Ponytail Alignment):**
  * Ingesting 50 different service runbooks into the primary system prompt causes severe context bloat. The AWS toolkit keeps the system prompt lean and retrieves specific domain runbooks only on demand.
* **How MinusOps Adopts It:**
  * We maintain this exact pattern across `.agents/skills/` and `core/generation/modules.py`—the main `grill-me` and `synthesizer.py` loop loads only the specific building block module needed for the matched scenario.

---

### 2.4 1-Click Day-0 Pre-Flight Diagnostics (`doctor.py`)
* **Source Proof:**
  * AWS CLI Command: `aws configure agent-toolkit`
  * *Where Seen:* Official AWS Developer Tools landing page.
* **Why It Matters:**
  * Streamlines developer onboarding. Rather than manually configuring Claude Code, Antigravity, or Cursor MCP JSON files, running `aws configure agent-toolkit` sets up authenticated local credentials in one shot.
* **How MinusOps Adopts It:**
  * In [`core/reporting/doctor.py`](../../core/reporting/doctor.py), add a diagnostic probe under `check_environment()`:
    * `[PASS] AWS Agent Toolkit MCP server detected on localhost` OR
    * `[INFO] Run 'aws configure agent-toolkit' to enable live AWS MCP doc search.`

---

## 3. What We Explicitly REJECT (And Why)

| AWS Toolkit Feature | Why Matt & MinusOps Reject It | Architectural Invariant Preserved |
| :--- | :--- | :--- |
| **`aws___run_script` for Provisioning** | Directly executing Python/boto3 scripts to mutate AWS resources bypasses IaC state tracking and auditability. | **Strict IaC Only:** All mutations must go through Terraform `plan_gate.py` with SHA-256 hash binding. |
| **AWS CDK / CloudFormation Bias** | The toolkit primarily promotes CloudFormation and CDK, locking the platform to AWS. | **Multi-Cloud Foundation:** MinusOps uses Terraform HCL, allowing the same control plane to govern Azure, GCP, Snowflake, and Databricks. |
| **Unconstrained Shell Execution** | The toolkit rules encourage agents to run ad-hoc CLI commands. | **4-Tier Zero-Trust Guardrail:** Prompts cannot restrain shell access; IAM Permissions Boundaries (`DenyDelete`) must enforce limits. |

---

## 4. Integration Blueprint with MinusOps

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ MINUSOPS CONTROL PLANE (Governance, FinOps, Plan-Gate)                      │
│ • core/governance/plan_gate.py (SHA-256 Hash Binding & Two-Person STS Rule) │
│ • core/reporting/excel_finops_generator.py (Attributed MoM Variance)        │
│ • core/generation/synthesizer.py (Terraform Multi-Provider Synthesis)       │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ (Calls for Read-Only Sensor Data)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ AWS AGENT TOOLKIT MCP SERVER (Live AWS Sensory Layer)                       │
│ • aws___search_documentation (Live schema search for discovery.py)          │
│ • aws___get_regional_availability (Pre-flight checks in plan_gate.py)       │
│ • aws___retrieve_skill (Lazy-loaded AWS Well-Architected runbooks)          │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Summary Recommendation for Implementation

1. **Keep Core Governance Unchanged:** Do not modify `plan_gate.py` security invariants.
2. **Add MCP Client Hook in `core/providers/aws.py`:** Add optional capability to query `aws___search_documentation` and `aws___get_regional_availability` when the local MCP server is running.
3. **Update `doctor.py`:** Detect `agent-toolkit` availability as an optional enhancement for AWS discovery.
