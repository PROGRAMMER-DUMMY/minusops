# CONTEXT-MAP.md — Master Project Context Tree & Maintenance Guide

## Overview
`CONTEXT-MAP.md` is the central navigation tree and operating standard for context documentation across **MinusOps**. 

Every directory in this repository maintains a dedicated, file-by-file **`CONTEXT-[folder].md`** document detailing the purpose, interfaces, failure modes, and architectural roles of every file inside it.

---

## Master Context Tree

```
MinusOps (Workspace Root)
│
├── CONTEXT-MAP.md                          # Master context tree & maintenance guide (You are here)
├── HANDOFF.md                              # Live status, project progress & handoff ledger
├── NextStackHelper.md                      # TerraShark analysis & next-gen stack integration guide
│
├── core/                                   # GOVERNANCE & IAC SYNTHESIS ENGINE
│   ├── CONTEXT-core.md                     # Core package index & subpackage mapping
│   ├── governance/
│   │   └── CONTEXT-governance.md           # Plan gates, approvals, audit chains, drift & source guards
│   ├── generation/
│   │   └── CONTEXT-generation.md           # Synthesizer, module registry, provenance & pattern stores
│   ├── architecture/
│   │   └── CONTEXT-architecture.md         # Requirements gates, architecture decisions, team resolver & model
│   ├── cost/
│   │   └── CONTEXT-cost.md                 # BCM Pricing Calculator, catalog resolution & pricing audits
│   ├── reporting/
│   │   └── CONTEXT-reporting.md            # minusctl CLI, reporter, optimize_analyzer & finops_agent
│   ├── integrations/
│   │   └── CONTEXT-integrations.md         # Approval-gated outbound hooks: Slack, Teams, SMTP, Confluence, Jira
│   └── providers/
│       └── CONTEXT-providers.md            # CloudProvider contract, AWS implementation & cloud scaffolds
│
├── app/                                    # CONTROL PLANE CONSOLE
│   └── CONTEXT-app.md                      # Plotly Dash UI, click-to-code SVG inspector & HTTP routes
│
├── modules/                                # INFRASTRUCTURE BUILDING BLOCKS
│   └── CONTEXT-modules.md                  # 24 Terraform modules (VPC, S3, Glue, EMR, DBX, MSK, Snowflake, MWAA)
│
├── .agents/                                # AGENT OPERATING SYSTEM
│   └── CONTEXT-agents.md                   # AGENTS.md rules & 7 decision skills (grill-me, architect, doctor, etc.)
│
├── docs/                                   # SPECIFICATIONS & MANIFESTS
│   └── CONTEXT-docs.md                     # IAM manifest, security model, info library & phase scopes
│
├── policy/                                 # POLICY-AS-CODE RULES
│   └── CONTEXT-policy.md                   # OPA Rego rules & rule_stages.json stage definitions
│
├── examples/                               # REFERENCE EXAMPLES
│   └── CONTEXT-examples.md                 # IAM trust policies & BCM usage profile examples
│
├── tests/                                  # AUTOMATED TEST SUITE
│   └── CONTEXT-tests.md                    # pytest suite, categorized by domain
│
├── .github/                                # CI / PR AUTOMATION
│   ├── actions/pr-reviewer/action.yml      # Composite PR reviewer (plan + scan + cost comment)
│   └── workflows/                          # deploy · pr-review · ci · release · schema-watch
│
└── tools/                                  # OPERATIONAL TOOLS
    └── CONTEXT-tools.md                    # doctor.ps1 (superseded by `minusctl doctor`)
```

---

## Operating Guide: How to Maintain & Refer to Context Files

### 1. How to Refer to Context Files
* **Before making changes**: Prior to modifying any code in a directory, read the corresponding `CONTEXT-[folder].md` file to understand all dependencies, failure modes, and safety invariants.
* **Clickable Links**: All file references in context documentation MUST use GitHub-style markdown links with the `file://` URI scheme (e.g., [`plan_gate.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/governance/plan_gate.py)).

### 2. How to Update Context Files
* **Atomic Updates**: Any code change, refactor, or feature addition MUST include an immediate update to the corresponding `CONTEXT-[folder].md` file in the same task.
* **What to Update**:
  - New or modified functions/classes.
  - Changes to input parameters, defaults, or return types.
  - New failure modes or error handling logic.
  - Updates to line numbers or structural dependencies.

### 3. How to Add New Context Files
* When creating a **new directory** or **subpackage**:
  1. Create a `CONTEXT-[folder].md` inside that directory.
  2. Document every file within the folder exhaustively (no high-level placeholders or half-reads).
  3. Register the new context file in [`CONTEXT-MAP.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/CONTEXT-MAP.md).

### 4. Keeping Context Files Up to Date
* **No Spec Drift**: Context files are source-controlled artifacts and must never drift from actual python/HCL implementation.
* **Audit Discipline**: Subagents or developers modifying code must run a context verification check to ensure all referenced line numbers and signatures match disk reality.

---

## Project Connections
* **Current Status & Handoff**: See [`HANDOFF.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/HANDOFF.md) for live progress, active architecture decisions, and current execution milestones.
* **Operating Rules**: See [`.agents/AGENTS.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/AGENTS.md) for non-negotiable workspace safety constraints.
