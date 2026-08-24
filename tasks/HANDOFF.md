# MinusOps Engineering Handoff Document

| Attribute | Details |
| :--- | :--- |
| **Repository** | `PROGRAMMER-DUMMY/minusops` |
| **Active Branch** | `feat/minusops-enterprise-nextgen-v2` |
| **Latest Commits** | `4721d0d`, `0d4de57`, `85e0cb5` (All PRDs v1 through v11 delivered and certified) |
| **Test Suite Status** | **1,258 fast tests passing**, 90 skipped, 362 slow deselected, **exit code 0** across 92 test files |
| **Working Tree** | Clean, fully pushed to remote origin |
| **Author** | MinusOps Principal Architecture & Engineering Team |
| **Date** | August 23, 2026 |

---

## 1. Executive Overview & System Identity

MinusOps is a **multi-cloud, workload-agnostic operational control plane and Terraform generation engine**. Enterprises install it and run it against their own cloud credentials and infrastructure.

The control plane enforces **plan-bound, MFA-gated, tamper-evident infrastructure delivery**:
* Every mutating action must pass through the deploy gate (`verify` -> `plan` -> `approve` -> `apply`).
* All changes are cryptographically bound to a SHA256 **`plan_hash`**.
* The CLI operates exclusively through the unified **`minusctl`** command surface.

---

## 2. Milestone Deliverables Summary (PRDs v1.0 through v11.0)

All 11 milestone PRDs are delivered, certified with automated tests, and archived in [`tasks/completed/`](./completed/):

```text
  ┌──────────┬──────────────────────────────────────────────────────────────────────────────────────────┐
  │ Release  │ Core Capabilities Delivered                                                              │
  ├──────────┼──────────────────────────────────────────────────────────────────────────────────────────┤
  │ PRD v5.0 │ Semantic run workspaces (`<domain>-<workload>-<orchestrator>_<timestamp>`), atomic index│
  │          │ sync (`runs/index.json`), multi-repo export (`minusctl export`), path-isolated CI/CD.    │
  ├──────────┼──────────────────────────────────────────────────────────────────────────────────────────┤
  │ PRD v6.0 │ Modular CLI package (`core/cli/`), session context switching (`minusctl use`), structured│
  │          │ specification cards (`minusctl runs describe`), fail-closed context hierarchy.           │
  ├──────────┼──────────────────────────────────────────────────────────────────────────────────────────┤
  │ PRD v7.0 │ Databricks-style grouped CLI help, NO_COLOR theme safety, 5-hop synthetic proving harness│
  │          │ (`minusctl prove --execute`), signed `proving_report.json`.                              │
  ├──────────┼──────────────────────────────────────────────────────────────────────────────────────────┤
  │ PRD v8.0 │ 4 new modules (`governance-lakeformation`, `security-iam-scoped`, `dbt-semantic-layer`, │
  │          │ `cube-semantic-layer` -> 29 total), Redshift capacity bounds, Athena partition projection│
  ├──────────┼──────────────────────────────────────────────────────────────────────────────────────────┤
  │ PRD v9.0 │ Intelligent incident diagnostics (`core/reporting/incident_diagnostics.py`), structural  │
  │          │ cost ratios vs hardcoded dollars, 4 serving consumption archetypes.                      │
  ├──────────┼──────────────────────────────────────────────────────────────────────────────────────────┤
  │ PRD v10.0│ Extensibility Guide (`docs/extensibility_and_integration_guide.md`), `context-graph` and │
  │          │ `integration-guide` skills, link sanitization (1,006 local repo-relative links, 0 broken)│
  ├──────────┼──────────────────────────────────────────────────────────────────────────────────────────┤
  │ PRD v11.0│ Dual-engine CI/CD (`core/generation/cicd.py`), JFrog Artifactory & ECR artifact staging,│
  │          │ pluggable proving registry in `seed.py`, dynamic asset-tier driven incident triage.      │
  ├──────────┼──────────────────────────────────────────────────────────────────────────────────────────┤
  │ PRD v12.0│ Dynamic Draw.io architecture diagram generator (`core/reporting/drawio_generator.py`),   │
  │          │ universal stencil mapper, 1-click deflated URLs, CLI `minusctl diagram`, and agent skill.│
  ├──────────┼──────────────────────────────────────────────────────────────────────────────────────────┤
  │ PRD v13.0│ Enterprise Visual Governance Console (`app/console_app.py`), multi-agent execution      │
  │          │ tracing (`agent_tracer.py`), 5-hop data lineage (`lineage_graph.py`), bi-directional      │
  │          │ visual reconciliation (`reconciler.py`), and unified deliverables vault (`vault.py`).   │
  ├──────────┼──────────────────────────────────────────────────────────────────────────────────────────┤
  │ PRD v14.0│ Agent Observability (`agent_tracer.py`), AI Token Economics & Context Pressure          │
  │          │ (`agent_cost_calculator.py`), Interactive Agent Flow Lineage DAG (`agent_flow_graph.py`),│
  │          │ and SOC2/HIPAA cryptographic audit linkage (`audit.jsonl`).                              │
  ├──────────┼──────────────────────────────────────────────────────────────────────────────────────────┤
  │ PRD v15.0│ Agent Execution Guardrails Sandbox (`agent_guardrails.py`), Destructive Command Blocker, │
  │          │ Dynamic Budget Alignment (25% headroom), and Single-Instance Console Lifecycle.         │
  └──────────┴──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Core Architectural Invariants (Non-Negotiable)

Every engineer and AI agent operating on this repository must strictly adhere to these invariants:

1. **Zero Emojis Invariant:** Strictly no emoji or decorative unicode characters in any terminal output, log lines, markdown documentation, or code comments.
2. **Standard Library CLI Core:** `core/cli/` and governance modules must rely exclusively on the Python standard library.
3. **Plan-Bound Deploy Gate:** `minusctl gate apply` executes only against a reviewed and approved SHA256 `plan_hash`. Any source or remote drift voids prior approval and forces re-verification.
4. **Fail-Closed Context Precedence (`core/cli/context.py`):**
   * Precedence: Explicit `--run` flag -> Current directory discovery (`inside runs/<id>/`) -> Stored context (`.minus/context.json`) -> **REFUSAL (Exit 1)**.
   * Never guess or fall back to "most recent run".
5. **Dynamic Incident Severity Doctrine (`incident_diagnostics.py`):**
   * Severity is decided dynamically per-incident based on **Asset Tier (0 to 3)** + **Silent Corruption flag** + **Lake Formation PII hard floor override**, NOT hardcoded by source category.
   * If a run has no declared tier, emit `UNCLASSIFIED` (refuse to guess).
   * Routing is driven by calculated severity: P1 -> PagerDuty, P2 -> Slack/Teams on-call, P3 -> Jira/Outlook, P4 -> log.
6. **Repo-Relative Markdown Links:** All documentation cross-references must use standard repo-relative markdown links (`./main.py`, `../reporting/minusctl.py`), never machine-specific `file://` URIs.

---

## 4. Repository Directory Map

```text
.
├── AGENTS.md                                   # Root agent operating guide (minusctl mandated)
├── CONTEXT-MAP.md                              # Master context navigation tree (mapping all 18 subsystems)
├── requirements.txt                            # Root dependencies (Plotly Dash, boto3, etc.)
├── pyproject.toml                              # Packaging configuration (minusctl CLI entry point)
│
├── core/                                       # CLOUD-AGNOSTIC GOVERNANCE & CONTROL ENGINE
│   ├── cli/                                    # Modular CLI package (Databricks-style grouped help, theme, context)
│   │   ├── commands/                           # First-class subcommands (cost, gate, runs, source, use)
│   │   ├── context.py                          # Fail-closed session context resolver
│   │   ├── formatters.py                       # ASCII table, card, and money formatters
│   │   ├── main.py                             # Master CLI parser and dispatcher
│   │   └── theme.py                            # ANSI styling respecting NO_COLOR, TERM=dumb, isatty
│   ├── generation/                             # IaC & Pipeline Synthesis Engine
│   │   ├── cicd.py                             # Dual-engine CI/CD generator (GitHub Actions OIDC & Jenkins + Artifactory)
│   │   ├── modules.py                          # 29-module vetted building block catalog
│   │   ├── patterns.py                         # Pattern capture and reuse registry (min_score=3)
│   │   ├── schema_watch.py                     # Provider schema deprecation watcher
│   │   └── synthesizer.py                      # 2,100+ line master HCL & pipeline compiler
│   ├── governance/                             # State-Aware Governance & Deploy Gate
│   │   ├── plan_gate.py                        # 4-stage deploy orchestrator (verify -> plan -> approve -> apply)
│   │   ├── approval.py                         # Approval gate (gatekeeper vs auto-approve)
│   │   ├── audit_chain.py                      # Cryptographic tamper-evident hash log
│   │   ├── cloud_drift.py                      # Out-of-band CloudTrail drift detector
│   │   └── source_guard.py                     # Generated-source baseline verification
│   ├── reporting/                              # Inspection, Proving, Diagnostics & FinOps
│   │   ├── incident_diagnostics.py             # Dynamic severity triage & 4-part failure resolver
│   │   ├── seed.py                             # Pluggable modular proving harness (`minusctl prove --execute`)
│   │   ├── finops_agent.py                     # Spend breakdown, anomaly correlation, error budget burn
│   │   ├── health_checker.py                   # AWS CLI, STS, S3, and Glue live health probes
│   │   ├── minusctl.py                         # Operator CLI handler & readiness/package compiler
│   │   └── runs.py                             # Semantic run workspace manager
│   ├── integrations/                           # Outbound Notification & Ticketing Hooks
│   │   ├── base_hook.py                        # Shared gated() decorator & bearer-token credential resolution
│   │   ├── slack_hook.py                       # Slack Block Kit approval cards & P1 alerts
│   │   ├── teams_hook.py                       # Microsoft Teams data quality & quarantine cards
│   │   ├── outlook_hook.py                     # SMTP/Outlook FinOps email with .xlsx attachment
│   │   ├── jira_hook.py                        # Jira change-ticket creator
│   │   └── confluence_hook.py                  # Confluence living architecture publisher
│   └── providers/                              # Multi-Cloud Provider Abstraction (MINUS_CLOUD)
│       ├── base.py                             # CloudProvider interface contract
│       └── aws.py                              # AWS implementation (Cost Explorer, STS, CloudTrail, S3)
│
├── modules/                                    # 29 VETTED TERRAFORM BUILDING BLOCKS
│   ├── storage-medallion-s3/                   # S3 Bronze/Silver/Gold with deterministic unique naming
│   ├── compute-glue-etl/                       # Serverless Glue 4.0 Spark batch ETL
│   ├── compute-emr-serverless/                 # EMR Serverless Graviton Spark
│   ├── governance-lakeformation/               # Lake Formation LF-TBAC & PII masking
│   ├── security-iam-scoped/                    # Least-privilege scoped reader roles with External-ID
│   ├── dbt-semantic-layer/                     # MetricFlow metric manifest definitions
│   └── cube-semantic-layer/                    # Cube headless semantic layer with Redis cache
│
├── .agents/                                    # AGENT OPERATING OS
│   ├── AGENTS.md                               # Workspace safety invariants & command surface rules
│   ├── skills/                                 # 9 Specialized Decision Skills
│   │   ├── grill-me/                           # Mandatory front-door requirements interview
│   │   ├── architect/                          # Research-driven architecture synthesis
│   │   ├── terraform-orchestrator/             # Plan/apply deploy execution
│   │   ├── context-graph/                      # File-by-file context synchronization
│   │   ├── integration-guide/                  # Extensibility checklists
│   │   └── doctor/                             # Day-0 pre-flight environment diagnostics
│   └── subagents/                              # 4 Single-Dispatch Transport Subagents
│       ├── slack-agent.md                      # P1 incidents & approval cards
│       ├── teams-agent.md                      # Data quality alerts
│       ├── outlook-agent.md                    # Executive FinOps emails
│       └── confluence-agent.md                 # Architecture documentation pages
│
├── docs/                                       # SPECIFICATIONS & GUIDES
│   ├── extensibility_and_integration_guide.md  # Canonical developer guide for adding tools/modules/commands
│   ├── enterprise_iam_manifest.md              # IAM roles and policy templates
│   └── PROGRESS.md                             # Historical milestones and engineering progress ledger
│
├── tasks/                                      # TASK SPECIFICATIONS
│   ├── HANDOFF.md                              # This document
│   └── completed/                              # 18 Completed PRDs and task specifications (v3 through v11)
│
└── tests/                                      # AUTOMATED PYTEST SUITES (92 test files, 1,258 passing tests)
```

---

## 5. Daily Operator Command Cheat Sheet

All operations run via `minusctl` (or `.venv\Scripts\minusctl.exe` / `python -m core.cli.main`):

```bash
# 1. Workspace & Lifecycle
minusctl create "governed lakehouse for clickstream" --name clickstream --domain marketing --orchestrator mwaa
minusctl use marketing-clickstream-mwaa_20260823_110000
minusctl runs list
minusctl runs describe
minusctl next

# 2. Deploy Gate & Governance
minusctl gate verify
minusctl gate plan
minusctl gate approve --role-arn arn:aws:iam::123456789012:role/MinusOps-Deployer
minusctl gate apply
minusctl gate status

# 3. Cost & Proving
minusctl cost prepare --account-id 123456789012
minusctl cost estimate --mode gatekeeper
minusctl prove --execute
minusctl prove --hops ingest,transform,query --execute

# 4. Diagnostics & Source Guard
minusctl diagnose --run <run-id> --with-telemetry
minusctl source status
minusctl source diff
minusctl doctor

# 5. Delivery & Export
minusctl export --target-repo ../marketing-analytics --dest-dir pipelines/clickstream --generate-workflow --engine jenkins --artifact-repo artifactory
minusctl package
minusctl audit verify
```

---

## 6. How to Verify Environment & Test Suite

To verify the test suite on any machine or CI container:

```powershell
# Run the fast automated test suite (1,258 tests across 92 files)
.venv\Scripts\python.exe -m pytest

# Run documentation example linter
.venv\Scripts\python.exe -m pytest tests/test_docs_examples.py

# Run CI/CD synthesis tests
.venv\Scripts\python.exe -m pytest tests/test_cicd.py

# Run modular proving harness tests
.venv\Scripts\python.exe -m pytest tests/test_proving_harness.py
```

---

## 7. Immediate Next Steps for the Next Incoming Agent

1. **Active State:** The codebase is fully certified and stable. Zero outstanding regressions.
2. **Clean Tasks Root:** All completed tasks and PRDs are archived in [`tasks/completed/`](./completed/).
3. **Incoming Directives:** When authoring new features or responding to user requests, always activate the relevant skill (`grill-me` for new build requests, `architect` for composition, `context-graph` when modifying files, `integration-guide` when adding commands/modules).
