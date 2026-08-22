# Architecture Evaluation: HashiCorp Terraform MCP Server Integration

| Attribute | Details |
| :--- | :--- |
| **Document ID** | EVAL-TERRAFORM-MCP-2026-001 |
| **Source URL** | [`https://developer.hashicorp.com/terraform/mcp-server`](https://developer.hashicorp.com/terraform/mcp-server) |
| **GitHub Repository** | [`https://github.com/hashicorp/terraform-mcp-server`](https://github.com/hashicorp/terraform-mcp-server) |
| **Lead Reviewer** | Matt (Principal Cloud Architect & Governance Lead) |
| **Target Components** | `core/generation/synthesizer.py`, `core/architecture/discovery.py`, `.agents/skills/architect/` |
| **Date** | 2026-08-21 |

---

## 1. Executive Summary & Why This Matters for MinusOps

Because MinusOps generates and governs **Terraform HCL infrastructure across multiple clouds**, the **HashiCorp Terraform MCP Server** is even more directly applicable to our core engine than the AWS-only toolkit.

### The Core Problem It Solves:
When AI agents author Terraform (`.tf`), they frequently hallucinate:
1. Deprecated arguments (e.g. `acl = "private"` in `aws_s3_bucket` instead of `aws_s3_bucket_acl`).
2. Missing required parameters on new provider major versions (e.g., AWS Provider v5.x vs v4.x).
3. Schema mismatches on third-party providers (Snowflake, Databricks, AzureRM, GCP).

---

## 2. Key Capabilities of HashiCorp Terraform MCP Server

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4 CORE POWERS OF THE TERRAFORM MCP SERVER                                   │
├──────────────────────────┬──────────────────────────────────────────────────┤
│ MCP Capability           │ Architectural Value for MinusOps                 │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ 1. Live Registry Schema  │ Real-time query of exact provider resource &     │
│    Introspection         │ data source schemas (required/optional blocks).  │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ 2. Universal Multi-Cloud │ Covers AWS, Azure, GCP, Snowflake, Databricks,   │
│    Provider Coverage     │ Cloudflare, Vault, Kubernetes (Registry API).    │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ 3. Verified Module &     │ Pulls official vetted HCL module blocks and      │
│    Syntax Examples       │ production patterns directly into synthesizer.   │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ 4. Policy Discovery      │ Inspects organizational Sentinel / OPA rules     │
│    (Compliance AST)      │ to prevent compliance violations pre-synthesis.  │
└──────────────────────────┴──────────────────────────────────────────────────┘
```

---

## 3. How MinusOps Leverages the Terraform MCP Server

### 3.1 Zero-Hallucination HCL Synthesis (`core/generation/synthesizer.py`)
* Before writing `.tf` files in `runs/<run-id>/terraform/`, `synthesizer.py` and the `architect` skill query the Terraform MCP Server:
  * *"What are the exact valid arguments for `aws_glue_job` in AWS Provider 5.x?"*
  * The MCP server returns the exact AST schema (e.g. `timeout`, `number_of_workers`, `execution_class`, `glue_version`).
  * **Result:** HCL generation succeeds on the very first try without syntax trial-and-error.

### 3.2 Pre-Plan Validation Shield (`plan_gate.py verify`)
* Instead of waiting for `terraform init` and `terraform validate` to fail on a typo, MinusOps runs an AST schema comparison against the Terraform MCP server, catching invalid arguments locally in milliseconds.

### 3.3 Multi-Cloud / Third-Party Provider Synthesis
* When an architect asks for a **Databricks on AWS** or **Snowflake + S3** pipeline:
  * The agent uses the Terraform MCP Server to pull the exact `databricks_job` or `snowflake_table` schema directly from `registry.terraform.io`.

---

## 4. The Unified 3-Tier AI Infrastructure Stack

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. MINUSOPS CONTROL PLANE (Governance, FinOps, Blast-Radius Safety)         │
│ • 4-Tier Zero-Trust Guardrails (plan_gate.py + prevent_destroy + IAM)      │
│ • Attributed MoM FinOps Intelligence (excel_finops_generator.py)            │
│ • Two-Person STS Rule & S3 WORM Compliance Immutability                     │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ (Governs & Directs)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. HASHICORP TERRAFORM MCP SERVER (Universal IaC Schema & Authoring)        │
│ • Real-time registry.terraform.io schema introspection                      │
│ • Multi-Provider HCL argument validation (AWS, Azure, Snowflake, Databricks)│
│ • Verified module syntax patterns (Feeds synthesizer.py & architect skill)  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ (Interacts with)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. AWS AGENT TOOLKIT MCP SERVER (Live AWS Runtime & Telemetry Sensor)       │
│ • Live AWS Doc Search (aws___search_documentation)                          │
│ • Regional Quotas & Availability (aws___get_regional_availability)          │
│ • CloudWatch / CloudTrail Observability (Feeds finops_agent.py & doctor.py) │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Architectural Recommendation for Matt

1. **Document Both MCP Integrations as Optional Sensory Plugins:**
   * **HashiCorp Terraform MCP Server:** The primary HCL authoring and schema validation sensor.
   * **AWS Agent Toolkit MCP Server:** The live AWS runtime and CloudWatch telemetry sensor.
2. **Preserve MinusOps Invariants:** Neither MCP server has permission to run un-gated applies or destructive teardowns. The **MinusOps Plan-Gate** remains the sole, immutable gatekeeper for infrastructure mutations.
