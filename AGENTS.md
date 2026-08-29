# AGENTS.md — Operating Guide for Autonomous CLI Agents

> **Audience:** Any autonomous coding/ops CLI agent working in this repo — gy (Antigravity), codex, claude code, or similar.
> **Purpose:** Authoritative operating instructions: active workflows, tools, documentation resolution, and non-negotiable governance invariants.
>
> Read this file first. Then load the project-local agent context listed in **Mandatory Agent Context** below. For doc lookups, see [docs/information_library.md](./docs/information_library.md).

---

## 0. Mandatory Agent Context & Execution Standard

Agents supporting only a single root instruction file must treat the files below as part of this AGENTS.md guide:

### Core Workspace Rules
- [.agents/AGENTS.md](./.agents/AGENTS.md) — workspace safety rules, HITL constraints, zero-emoji policy, and autonomous execution standards.

### Autonomous Lifecycle Roadmap (The 7-Step Sequence)
For any infrastructure creation or modification lifecycle, follow the strict 7-step sequence:
1. **[1] Requirements Grilling (grill-me)** — Gather functional + non-functional requirements across all 19 pillars (core/architecture/pillars.py), one question at a time with recommended defaults.
2. **[2] ADR Formulation (rchitecture_decision.json)** — Formulate and record the Architecture Decision Record with explicit rationale and trade-offs.
3. **[3] Architecture Synthesis (synthesizer.py)** — Compose vetted modules (core/generation/modules.py) into governed Terraform.
4. **[4] Diagram & Lineage Generation (diagram_generator.py)** — Generate Draw.io architecture diagrams and data lineage, providing 1-click browser view links (https://app.diagrams.net/#R...).
5. **[5] Reflector Review (eflector.py)** — Run independent stage review to verify readiness and schema conformance.
6. **[6] Plan Gate & BCM Costing (minusctl gate & minusctl cost)** — Execute erify -> plan (producing a SHA256 plan hash) and run BCM Pricing Calculator cost estimation.
7. **[7] Human-in-the-Loop Approval & Audited Apply** — Present the plan diff, cost forecast, and diagram for human approval before executing apply.

### Decision & Execution Skills (.agents/skills/)
Activate the relevant skill when its trigger applies:
- [.agents/skills/grill-me/SKILL.md](./.agents/skills/grill-me/SKILL.md) — **The mandatory front door for any build/create request**: gather full functional + non-functional requirements (19 pillars) before generating Terraform.
- [.agents/skills/architect/SKILL.md](./.agents/skills/architect/SKILL.md) — Research current cloud services/reference architectures, compose vetted modules, and govern through the deploy gate.
- [.agents/skills/terraform-orchestrator/SKILL.md](./.agents/skills/terraform-orchestrator/SKILL.md) — Before any deployment, Terraform plan/apply workflow, state lock handling, or infrastructure mutation proposal.
- [.agents/skills/pipeline-optimizer/SKILL.md](./.agents/skills/pipeline-optimizer/SKILL.md) — Before scanning, optimizing, or proposing remediation for Terraform/data-pipeline infrastructure.
- [.agents/skills/resolve-ambiguity/SKILL.md](./.agents/skills/resolve-ambiguity/SKILL.md) — When a request is unclear, underspecified, too broad, or presents incompatible trade-offs.
- [.agents/skills/doctor/SKILL.md](./.agents/skills/doctor/SKILL.md) — Pre-flight environment diagnostics (binaries, credentials, lockfiles, connectors).
- [.agents/skills/context-graph/SKILL.md](./.agents/skills/context-graph/SKILL.md) — When maintaining, auditing, or synchronizing file-by-file context trees (CONTEXT-MAP.md).
- [.agents/skills/integration-guide/SKILL.md](./.agents/skills/integration-guide/SKILL.md) — Before adding or modifying CLI subcommands, Terraform modules, outbound hooks, or subagents.

### Transport Subagents (.agents/subagents/)
To send outbound notifications, activate the matching single-shot transport subagent. Each dispatches exactly one message through core/integrations/ and terminates:
- [.agents/subagents/slack-agent.md](./.agents/subagents/slack-agent.md) — P1 pipeline incidents and plan-approval cards.
- [.agents/subagents/teams-agent.md](./.agents/subagents/teams-agent.md) — Data-quality failures and quarantine alerts.
- [.agents/subagents/outlook-agent.md](./.agents/subagents/outlook-agent.md) — Executive FinOps email reports with attached spreadsheets.
- [.agents/subagents/confluence-agent.md](./.agents/subagents/confluence-agent.md) — Living architecture documentation pages.
- [.agents/subagents/jira-agent.md](./.agents/subagents/jira-agent.md) — Governed change-management tickets (one ticket per invocation).

**Three Inviolable Rules for Subagents:**
1. Never accept, echo, or log a webhook URL or token: they are bearer credentials and the hook resolves them securely.
2. A denied approval is a terminal denial, not a failure, and is never retried.
3. An unconfigured connector returns {"ok": true, "sent": false} — reporting delivery on ok alone is a false positive.

---

## 1. Control Plane Architecture

MinusOps is a **workload-agnostic cloud ops control plane.** Each enterprise installs and executes it against their *own* cloud with their *own* credentials and their *own* Terraform — nothing is hosted externally, and **no example architecture is bundled**.

- **Governance Core (core/)** — Deploy gating, cryptographic plan-hash approval, audit logging, BCM FinOps pricing, and a provider abstraction (core/providers/).
- **Active Provider (core/providers/aws.py)** — AWS is the primary production provider (selected via MINUS_CLOUD=aws). The provider interface (core/providers/base.py) enforces a strict fail-closed contract on unconfigured clouds.
- **Run-Centric Context (.minus/context.json)** — Workspaces are isolated per workload run (uns/<run-id>/). Selecting an active run with minusctl use <run-id> automatically anchors gate, cost, source, prove, xport, and diagram commands without typing manual directory flags.

---

## 2. Repository Map

`
.
├── AGENTS.md                       # Master operating guide for autonomous agents
├── README.md  ·  requirements.txt  ·  LICENSE  ·  SECURITY.md  ·  CONTRIBUTING.md
│
├── core/                           # CLOUD GOVERNANCE & SYNTHESIS ENGINE
│   ├── cli/                        # Unified minusctl command surface (main.py, context.py, commands/*)
│   ├── governance/                 # Deploy gate (plan_gate.py), approval.py, audit_logger.py, reflector.py
│   ├── generation/                 # Synthesizer (synthesizer.py), modules.py, patterns.py, workflow.py
│   ├── architecture/               # 19-pillar engine (pillars.py), discovery.py, diagram_generator.py
│   ├── cost/                       # BCM Pricing Calculator (bcm_pricing_calculator.py), coverage_audit.py
│   ├── reporting/                  # doctor.py, finops_agent.py, health_checker.py, plan_inspector.py, runs.py
│   ├── integrations/               # base_hook.py, slack_hook.py, teams_hook.py, jira_hook.py, confluence_hook.py, outlook_hook.py
│   └── providers/                  # Cloud provider abstraction (base.py), AWS implementation (aws.py)
│
├── app/console_app.py              # Visual governance console (Plotly Dash, Monad Design System)
│
├── deploy/                         # 24/7 console cluster runtime assets
│   ├── k8s/                        # Kubernetes / EKS manifests (serviceaccount, deployment, service, ingress)
│   └── ecs/                        # AWS ECS Fargate manifests (task-definition.json, service.json, iam_roles.tf)
│
├── tests/                          # Automated pytest test suites
├── docs/                           # information_library.md, documentation_ledger.md, enterprise_iam_manifest.md
└── .agents/                        # Skill manifests, subagent transports, and audit logs
`

---

## 3. Autonomous Capabilities & Unified CLI Surface

Execute capabilities using the minusctl command surface (python -m core.cli.main if not installed on PATH):

| Capability | Command | Primary Module |
| :--- | :--- | :--- |
| **Workspace Creation** | minusctl create "<request>" --name <workload> --domain <domain> | core/reporting/runs.py |
| **Active Run Selection** | minusctl use <run-id> | core/cli/context.py |
| **Workspace Inspection** | minusctl runs list, minusctl runs describe | core/reporting/runs.py |
| **Deploy Gate Lifecycle** | minusctl gate {verify\|plan\|approve\|apply\|status} | core/governance/plan_gate.py |
| **BCM Cost Estimation** | minusctl cost {prepare\|estimate} | core/cost/bcm_pricing_calculator.py |
| **Cost Coverage Audit** | minusctl cost coverage | core/cost/coverage_audit.py |
| **Architecture Synthesis** | minusctl author <resource_type> --file <path> [--justification <text>] | core/generation/synthesizer.py |
| **Pattern Registry** | minusctl pattern {list\|match\|capture} | core/generation/patterns.py |
| **Fact Inference** | minusctl derive daily_gb=100 partitions_per_day=24 | core/architecture/pillars.py |
| **Draw.io Diagramming** | minusctl diagram [--run <id>] | core/architecture/diagram_generator.py |
| **Source Drift Baseline** | minusctl source {status\|diff\|anchor} | core/reporting/source_guard.py |
| **5-Hop Data Proof** | minusctl prove [--execute] (Execute mutates AWS; approval-gated) | core/reporting/seed.py |
| **Environment Pre-flight**| minusctl doctor [--json] | core/reporting/doctor.py |
| **Stage Reflector** | python core/governance/reflector.py --run-root runs/<id> | core/governance/reflector.py |
| **Handoff Export** | minusctl export --target-repo <path> --dest-dir pipelines/<name> | core/reporting/export.py |
| **Incident Diagnosis** | minusctl diagnose [--run <id>] [--error "<text>"] | core/reporting/incident_diagnostics.py |
| **Visual Console** | minusctl console -> http://127.0.0.1:8050 | pp/console_app.py |

---

## 4. Documentation Lookup Protocol

Verify parameters against official provider schemas rather than relying on memory. Lookup URLs follow deterministic formulas:

| Need | Target URL Formula |
| :--- | :--- |
| **Terraform AWS Resource** | https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/<type_without_aws_prefix> |
| **Terraform AWS Data Source** | https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/<type_without_aws_prefix> |
| **AWS CLI Command** | https://awscli.amazonaws.com/v2/documentation/api/latest/reference/<service>/<action>.html |
| **BCM Pricing API** | ws bcm-pricing-calculator create-workload-estimate ... |
| **Well-Architected Guidance** | https://developer.hashicorp.com/well-architected-framework |

**Resolution Order:**
1. In-repo validated pattern (modules/).
2. Catalog entry in [docs/information_library.md](./docs/information_library.md).
3. Direct URL lookup via [docs/documentation_ledger.md](./docs/documentation_ledger.md).

---

## 5. Non-Negotiable Safety Rules & Invariants

1. **Zero Ambient Mutation:** Never execute 	erraform apply, 	erraform destroy, destructive S3 mutations, or mutating git commands without explicit human approval.
2. **Directory-Bound Plan-Hash Integrity:** All deploys must flow through minusctl gate {verify|plan|approve|apply}. An approval is strictly bound to a SHA256 plan hash; any configuration change voids the approval and forces a fresh review.
3. **No Credential Leaks:** Bearer tokens, webhook URLs, and cloud secrets must never be echoed, printed in logs, stored in git, or displayed in the console UI.
4. **No Emojis in Agent Output:** Never emit unicode emojis in CLI messages, logs, markdown artifacts, or UI components.
5. **Fail-Closed Gate:** Unmapped security checks or unpriced billable resources are reported as explicit gaps rather than silently approved.
