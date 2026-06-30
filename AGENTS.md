# AGENTS.md — Operating Guide for CLI Agents

> **Audience:** Any autonomous coding/ops CLI agent working in this repo — `agy` (Antigravity), `codex`, `claude code`, or similar.
> **Purpose:** Tell you (the agent) *what you can do here*, *which tools to reach for*, *when and how to fetch documentation*, and *the safety rules you must never break*.
>
> Read this file first. Then load the project-local agent context listed in **Mandatory Agent Context** below. For the canonical list of doc links, see the **Documentation Redirect Rule** at the bottom and [`docs/information_library.md`](./docs/information_library.md).

---

## 0. Mandatory Agent Context

Agents that support only a single root instruction file must treat the files below as part of this `AGENTS.md` operating guide. Read the relevant files before acting; do not assume another CLI will auto-discover `.agents/`.

Always read:

- [`.agents/AGENTS.md`](./.agents/AGENTS.md) — workspace safety rules, HITL constraints, and skill activation requirements.

Read these skill files when their trigger applies:

- [`.agents/skills/terraform-orchestrator/SKILL.md`](./.agents/skills/terraform-orchestrator/SKILL.md) — before any deployment, Terraform plan/apply workflow, state lock handling, or infrastructure mutation proposal.
- [`.agents/skills/pipeline-optimizer/SKILL.md`](./.agents/skills/pipeline-optimizer/SKILL.md) — before scanning, optimizing, or proposing remediation for Terraform/data-pipeline infrastructure.
- [`.agents/skills/resolve-ambiguity/SKILL.md`](./.agents/skills/resolve-ambiguity/SKILL.md) — when a request is unclear, underspecified, too broad, too simple for hidden risk, or supports incompatible outcomes.
- [`.agents/skills/grill-me/SKILL.md`](./.agents/skills/grill-me/SKILL.md) — **the mandatory front door for any build/create request**: gather full functional + non-functional requirements (one question at a time) before generating; also when the user asks to be grilled or to resolve a decision tree.
- [`.agents/skills/architect/SKILL.md`](./.agents/skills/architect/SKILL.md) — after requirements are gathered: research current services/reference architectures, choose the best-fit, compose vetted modules (`core/modules.py` + `core/synthesizer.py`), and govern through the deploy gate. The path for any scenario the demo blueprint doesn't fit.

If your agent runtime has explicit skill auto-discovery, these files may load automatically. If not, manually read the matching `SKILL.md` before taking action.

---

## 1. What this repo is

A **multi-cloud, workload-agnostic ops control plane.** Each enterprise installs it and runs it against their *own* cloud with their *own* credentials and their *own* Terraform — nothing is hosted by us, and **no example architecture is bundled**. The repo is purely the engine:

- **A cloud-agnostic governance core** (`core/`) — deploy gating, approval, audit, FinOps, and a **provider abstraction** (`core/providers/`) so the same engine runs on AWS, Azure, or GCP. Select the active cloud with the `MINUS_CLOUD` env var (default `aws`).

Because there is no bundled IaC, **every tool that acts on infrastructure requires an explicit `--dir` / `--source-dir`** — the caller always says *which* Terraform directory to govern. The governance core **never calls a cloud CLI directly** — only through a `CloudProvider`. Terraform + the cloud CLI are your universal hands; every change is governed by MFA-gated, plan-bound, audited deploys.

---

## 2. Repository map

```
.
├── AGENTS.md                       # ← you are here (universal agent entry point)
├── README.md  ·  requirements.txt
│
├── core/                           # CLOUD-AGNOSTIC GOVERNANCE ENGINE
│   ├── plan_gate.py                # deploy gate: verify → plan → dir/hash approval → apply
│   ├── approval.py                 # approval gate: gatekeeper | auto-approve (audited)
│   ├── audit_logger.py             # append-only audit trail (.agents/logs/audit.jsonl)
│   ├── dispatcher.py               # NL query → routes to tools or safe blueprint resolution
│   ├── intent_resolver.py          # short creation intent → governed blueprint + required inputs
│   ├── blueprints.py               # approved blueprint registry
│   ├── finops_agent.py             # live cost intelligence (provider-driven) + gated notify
│   ├── health_checker.py           # live health probes
│   ├── optimize_analyzer.py        # HCL scanner (SEC/COST/OBS) → markdown report
│   ├── budget_calculator.py        # cost estimator (BCM Pricing Calculator API required for reports)
│   ├── reporter.py                 # versioned deploy report (plan + cost + architecture), keyed by plan-hash
│   └── providers/                  # CLOUD ABSTRACTION — pick via MINUS_CLOUD
│       ├── base.py                 # CloudProvider interface + get_provider()
│       ├── aws.py                  # AWS impl (Cost Explorer / anomalies / tags / identity)
│       └── azure.py · gcp.py       # scaffolds (degrade gracefully until implemented)
│
├── app/dashboard_app.py            # live FinOps console (Plotly Dash, provider-driven)
│
├── tests/                          # pytest suite (gate hash/approval invariants, scanner rules)
│
├── docs/                           # information_library · documentation_ledger
│   │                               #   enterprise_iam_manifest · architecture_svg_spec · pricing_catalog_support
├── tools/doctor.ps1                # env diagnostics
│
├── .agents/                        # agent skill manifests + runtime logs
│   ├── AGENTS.md                   # agy workspace rules (subset of this file)
│   ├── skills/terraform-orchestrator/SKILL.md
│   ├── skills/pipeline-optimizer/SKILL.md
│   ├── skills/resolve-ambiguity/SKILL.md
│   ├── skills/grill-me/SKILL.md
│   └── logs/                       # audit.jsonl, reports (gitignored, created on demand)
│
└── .github/workflows/deploy.yml    # generic OIDC CI: you pass tf_dir → validate → (gated) plan → apply
```

> **No bundled Terraform.** This repo holds the engine only. You bring the `.tf`; every
> infrastructure tool takes an explicit `--dir` / `--source-dir`. (You are responsible for
> provisioning the governance IAM the gate relies on — a read-only FinOps role and an
> MFA-gated deploy role — in your own account.)

---

## 3. What you are capable of here

All paths are relative to the repo root. Select the cloud with `MINUS_CLOUD={aws|azure|gcp}` (default `aws`).

| Capability | How you do it | Primary tool |
| :--- | :--- | :--- |
| **Operator workflow** | `python core/minusctl.py create "<request>" --input owner=<team> --input daily_data_gb=<n> --generate`, then `python core/minusctl.py next`, `readiness`, and `package` | Safe unified CLI |
| **Create run workspace** | `python core/runs.py new --blueprint <id> --request "<request>"` | `core/runs.py` |
| **Resolve request to run** | `python core/workflow.py resolve "<request>" --input owner=<team> --input daily_data_gb=<n> --generate` | `core/workflow.py` |
| **No-cloud demo** | `python core/demo.py governed-data-pipeline --owner data-platform --daily-data-gb 50` | Generates run Terraform + synthetic plan report without Terraform/AWS |
| **Provision / change infra** | Generate or edit HCL in `runs/<run-id>/terraform/`, then run the deploy gate (§6.1) | `core/plan_gate.py` + Terraform |
| **Detect manual source edits** | `python core/source_guard.py status --dir runs/<run-id>/terraform` and `python core/source_guard.py diff --dir runs/<run-id>/terraform` | Generated-source baseline guard |
| **Inspect generated reports** | `python core/plan_inspector.py services --latest`, `resources --latest`, `roles --latest`, `diff --latest` | Report and drift explorer |
| **Inspect live state** | `aws <service> <describe/list/get>` (or `az`/`gcloud`) — read-only, safe | cloud CLI |
| **Health diagnostics** | `python core/health_checker.py` | cloud CLI probes |
| **Scan infra for issues** | `python core/optimize_analyzer.py --source-dir <dir>` | HCL scanner |
| **Estimate cost** | `python core/budget_calculator.py` (BCM Pricing Calculator API required for reportable costs) | Pricing API |
| **Prepare BCM estimate** | `python core/bcm_pricing_calculator.py prepare --report-dir runs/<run-id>/reports/<plan-hash> --account-id <account>` (no AWS calls) | BCM payload generator |
| **Run BCM estimate** | `python core/bcm_pricing_calculator.py run --report-dir runs/<run-id>/reports/<plan-hash> --mode gatekeeper` (AWS-side effect; approval required) | BCM Pricing Calculator API |
| **Analyze live spend / anomalies** | `python core/finops_agent.py [--cost \| --anomalies \| --correlate]` (via active provider) | `core/providers/` |
| **View the FinOps console (UI)** | `python app/dashboard_app.py` → http://127.0.0.1:8050 (`pip install -r requirements.txt`) | Plotly Dash |
| **Notify (Slack/Jira), gated** | `core/finops_agent.py --notify-slack \| --notify-jira --approval-mode {gatekeeper\|auto-approve}` | `approval.py` gate |
| **Gate any side effect** | `python core/approval.py --action <a> --details <d> --mode {gatekeeper\|auto-approve}` | HITL / auto + audit |
| **Resolve creation intent** | `python core/intent_resolver.py "create a data pipeline"` | blueprint resolver |
| **Validate blueprints** | `python core/intent_resolver.py --validate-blueprints` | blueprint schema validator |
| **Route a vague request** | `python core/dispatcher.py "<natural language>"` | resolver + keyword classifier |
| **Clarify ambiguous work** | Read `.agents/skills/resolve-ambiguity/SKILL.md`, then ask one targeted question with a recommended answer | `resolve-ambiguity` skill |
| **Stress-test a plan** | Read `.agents/skills/grill-me/SKILL.md`, then interview one decision at a time until major branches are resolved | `grill-me` skill |
| **Audit an action** | `python core/audit_logger.py --action <a> --details <d>` | append to tamper-evident `audit.jsonl` |
| **Verify the audit chain** | `python core/minusctl.py audit verify` (or `python core/audit_chain.py verify`) | hash-chain integrity check |
| **Diagnose local env** | `powershell -ExecutionPolicy Bypass -File ./tools/doctor.ps1` | PowerShell |

The **dispatcher** routes operational requests to five tool intents — `HEALTH`, `DEPLOY`, `OPTIMIZE`, `BUDGET`, `FINOPS`. Creation requests such as "create a data pipeline" are first passed through `intent_resolver.py`, which recommends a governed blueprint and required inputs without generating or deploying infrastructure. You may also call any tool directly.

### 3.1 Project-local decision skills

Use the repo-local skills under `.agents/skills/` when the user's request is unclear or design-heavy:

- **`grill-me`** — **the mandatory front door for build/create requests.** Gather full functional + non-functional requirements (functional who/what/how + the ISO 25010 / FURPS+ non-functional checklist, quantified, MoSCoW-scoped), one question at a time with a recommended default; cross-question contradictions and flag missing pieces. Also use for stress-testing a plan.
- **`architect`** — after `grill-me`, for any scenario the demo blueprint doesn't fit: research current services / reference architectures, choose the best-fit, **compose vetted modules** into governed Terraform, and run it through the deploy gate. Replaces hand-writing a blueprint per scenario.
- **`resolve-ambiguity`** — for genuinely ambiguous points (which cloud/region, an incompatible-outcomes fork). One targeted question with a recommendation. Not a substitute for `grill-me`'s requirements interrogation.

Do not use these skills to slow down clear, low-risk work — but a request to *provision infrastructure* is never low-risk, so it always starts with `grill-me`.

### 3.2 Architecture synthesis (composition over monolithic blueprints)

The production path is **requirements → research → compose → govern**, not a single fixed recipe
(every company differs on orchestrator, architecture pattern, data-quality, schema enforcement):

| Step | Tool |
| :--- | :--- |
| Gather requirements | `grill-me` skill |
| Resolve authoritative sources for a service | `python core/discovery.py <topic> --resource <aws_type>` (Registry/CLI/pricing URLs) |
| Match requirements to vetted modules | `python core/modules.py match "<requirements>"` |
| Compose modules into a governed Terraform workspace | `python core/synthesizer.py "<requirements>" [--module <id>]` |
| Govern the composed Terraform | the §6.1 deploy gate (`plan_gate verify/plan/approve/apply`) |
| Reuse an approved composition | `python core/patterns.py match "<requirements>"`; capture after approval with `patterns.py capture` |

`aws-data-pipeline-standard` (`core/blueprints.py`, `terraform_generator.py`) is the **demo/cached
fixture** that powers `minusctl demo` and the golden tests — not the production generator.

---

## 4. When and how to fetch documentation

You are expected to **verify against official docs rather than rely on memory** for: Terraform resource arguments, AWS CLI command flags, service quotas, live pricing, secure architecture, and provider-specific design guidance. The full link catalog lives in [`information_library.md`](./docs/information_library.md) — that is the **redirect target**; always resolve doc lookups through it.

### 4.1 WHEN to fetch (triggers)

Fetch docs **before acting**, not after a failure, whenever you are about to:

- **Write or modify a Terraform resource** → confirm required/optional arguments and defaults against the AWS Provider Registry. Never guess an argument name.
- **Run an unfamiliar AWS CLI command** → confirm the exact subcommand, flags, and `--query`/output shape.
- **Quote or compute a price** → fetch live rates via the Pricing API/CLI; do not hardcode prices you "remember."
- **Hit a provider/CLI error** you don't fully understand → look up the resource/command page and the relevant AWS developer guide before retrying. **Do not retry blindly** (see §5).
- **Design IAM, networking, encryption, storage, retention, or analytics access** → consult the Well-Architected / service security guides.
- **Use a provider or CLI not already covered by the repo** → find its official docs, verify the local version first, then add the source to [`information_library.md`](./docs/information_library.md) and any direct URL pattern to [`documentation_ledger.md`](./docs/documentation_ledger.md).

If you can answer confidently from a file already in this repo (e.g. an existing `.tf` shows the pattern), you don't need to fetch — reuse the in-repo pattern.

### 4.2 HOW to fetch — construct direct URLs (no UI clicking)

Per [`documentation_ledger.md`](./docs/documentation_ledger.md), these portals have **predictable URL structures**. Build the URL and `WebFetch` it directly instead of crawling a sidebar:

| Need | URL formula |
| :--- | :--- |
| **Provider discovery** | `https://registry.terraform.io/browse/providers` |
| **Terraform resource** | `https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/<type_without_aws_prefix>` |
| **Terraform data source** | `https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/<type_without_aws_prefix>` |
| **AWS CLI landing/reference** | `https://docs.aws.amazon.com/cli/latest/` |
| **AWS CLI command** | `https://awscli.amazonaws.com/v2/documentation/api/latest/reference/<service>/<action>.html` |
| **BCM estimate (CLI)** | `aws bcm-pricing-calculator create-workload-estimate ...` then add usage lines and read the workload estimate |
| **Supporting live price (CLI)** | `aws pricing get-products --service-code <Code> --filters "Type=TERM_MATCH,Field=<f>,Value=<v>" --region us-east-1` |
| **Raw price index (JSON)** | `https://pricing.us-east-1.amazonaws.com/offers-v1.0/aws/<ServiceCode>/current/index.json` |
| **Secure architecture guidance** | `https://developer.hashicorp.com/well-architected-framework` |

Examples: `aws_glue_job` → `.../resources/glue_job`; `aws s3api head-bucket` → `.../reference/s3api/head-bucket.html`.

### 4.2.1 Version matching

Before using external docs for a CLI or provider, check the configured/local version when feasible:

- AWS CLI: `aws --version`; if the online docs differ, prefer `aws <service> <command> help` or the installed botocore service model for exact input shapes.
- Terraform CLI/provider: `terraform version`, `.terraform.lock.hcl`, and `terraform providers`; prefer docs matching the locked provider version for a target `--dir`.
- Other CLIs/providers: use their official version command and official docs. If the source is missing from the repo, add it to the library before relying on it.

### 4.3 Fetch decision flow

```
Need a fact about an AWS resource / CLI flag / price?
        │
        ├─ Is it already demonstrated in an existing repo file?  ──► reuse that pattern (no fetch)
        │
        ├─ Is it a Terraform resource arg?  ──► registry.terraform.io/.../resources/<type>
        ├─ Is it an AWS CLI flag/shape?     ──► awscli.../reference/<service>/<action>.html
        ├─ Is it reportable cost?           ──► BCM Pricing Calculator API (gated; no offline pricing)
        ├─ Is it a supporting SKU price?    ──► aws pricing get-products
        ├─ Is it secure architecture?       ──► HashiCorp/AWS Well-Architected docs
        └─ Is it a new provider/CLI?        ──► official source discovery, version check, then update docs library
```

---

## 5. Safety rules (non-negotiable)

These mirror [`.agents/AGENTS.md`](./.agents/AGENTS.md) and [`enterprise_iam_manifest.md`](./docs/enterprise_iam_manifest.md). They are load-bearing — treat them as hard constraints.

1. **No mutating actions without explicit human review.** You are forbidden from running `terraform apply`, `terraform destroy`, `terraform state <mutating>`, `terraform force-unlock`, or mutating `git` (`push`, `reset`, `rebase`) — and any mutating `aws` call (`create-*`, `delete-*`, `put-*`, `modify-*`, `terminate-*`, `run-*`) — until the user has reviewed and approved. Side effects in the agent scripts (notifications, ticket creation) must route through `approval.py` (`gatekeeper` by default; `auto-approve` only when durably authorised — see §6.4).
2. **Read before write.** `aws describe-* / list-* / get-*`, `terraform plan`, `terraform validate`, `head-bucket`, `get-caller-identity` are safe and may be run freely to gather state.
3. **Dry-run first.** Always produce `terraform plan -out=tfplan` (or an API `--dry-run`) and present the diff *before* asking for approval.
4. **Audit every consequential action.** Log it via `audit_logger.py` to `.agents/logs/audit.jsonl` *before* proposing execution.
5. **Pass the security scan.** Before proposing infra changes for the live stack, run `optimize_analyzer.py --source-dir <your-dir>`; resolve `SEC-*` findings (esp. `SEC-02` wildcard IAM) to zero. No wildcard `Resource = "*"` for S3/KMS/DynamoDB. Prefer one dedicated least-privilege role per service.
6. **Don't retry blindly on failure.** Extract the error, look up the doc (§4), write a troubleshooting note, and ask for help if human intervention is needed.
7. **Deploys go through the plan-gate.** `core/plan_gate.py` enforces verify → plan → **directory-bound plan-hash approval** → apply-the-exact-plan, with a full audit trail. Any `.tf` change produces a new hash, which voids the prior approval and forces a fresh review. Approval records are stored per Terraform directory and plan hash so concurrent plans cannot overwrite each other. The gate **never handles secrets** — authenticate via the cloud CLI first (`aws sso login`, or assume your MFA-gated deploy role); MFA is enforced by that role's trust policy and `apply` uses the ambient credential chain. Use it for every infrastructure change.
8. **Verify before deleting/overwriting.** If a target's contents contradict how it was described, surface that instead of proceeding.

---

## 6. Core workflows

### 6.1 Secure deployment loop (`core/plan_gate.py`)

```
1. Verify   →  plan_gate.py verify  --dir <template>   (fmt + validate + optimize_analyzer scan)
2. Plan     →  plan_gate.py plan    --dir <template>   (terraform plan -out=tfplan + record dir-bound plan-hash)
3. Approve  →  plan_gate.py approve --dir <template> --mfa-arn <arn> [--role-arn <deploy-role>]
                                                       (review + MFA → one-shot session bound to the hash)
4. Apply    →  plan_gate.py apply   --dir <template>   (hash must match → apply tfplan → creds wiped)
5. Verify   →  health_checker.py                       (post-deploy smoke tests)

   Any .tf change → new plan-hash → prior approval void → fresh MFA required.
   Plan/approval state is scoped by Terraform directory to avoid cross-workload collisions.
   `plan_gate.py run …` chains all stages; `--mode auto-approve` skips the y/N (still MFA + hash-bound).
```

### 6.2 Optimization loop (from `pipeline-optimizer` SKILL)

```
1. Scan       →  optimize_analyzer.py --source-dir <dir>   → artifacts/review/optimization_report.md
2. Present    →  show the markdown findings table to the user
3. Refactor   →  on approval, draft a Terraform plan applying the fixes  → re-enter 6.1
```

### 6.3 Cost estimation

```
budget_calculator.py        # cost guidance only — never computes or hardcodes a total
# Reportable enterprise costs require AWS BCM Pricing Calculator API evidence.
# BCM estimate creation is an AWS-side effect and must be explicitly approved.
# If BCM pricing is unavailable, show "cost unavailable" and the required bcm-pricing-calculator commands.

# Safe BCM workflow:
# 1. prepare: write bcm-create-workload-estimate.json, bcm-usage.json, bcm-commands.json
# 2. review: replace REVIEW_REQUIRED usageType/operation/account fields with approved values
# 3. run: pass through approval.py, create the BCM workload estimate, add usage, read the estimate
```

### 6.4 FinOps investigation (live account — `finops_agent.py`)

```
--cost          # spend by service + month-over-month   (aws ce get-cost-and-usage)
--anomalies     # active cost anomalies                 (aws ce get-anomalies — see cost_anomaly.tf)
--correlate     # root-cause via CloudTrail + tag owner  (aws cloudtrail lookup-events, tagging api)
--notify-slack | --notify-jira   --approval-mode {gatekeeper | auto-approve}
```

Read flags (`--cost/--anomalies/--correlate`) are safe and need no approval. The
`--notify-*` actions are **side effects** and always pass through `approval.py`.

**Approval modes (the gate for every side effect):**
- `gatekeeper` — require explicit human approval; **fail-closed** if no interactive terminal.
- `auto-approve` — proceed unattended (still audited to `audit.jsonl`).

Use `gatekeeper` by default. Only use `auto-approve` for low-risk, idempotent actions the
user has durably authorised (e.g. a scheduled read-only report). Never `auto-approve`
infrastructure mutations — those still go through the §6.1 deployment loop.

### 6.5 Deploy report & architecture diagram

After a plan, a versioned **deploy report** is produced under `runs/<run-id>/reports/<plan-hash>/`
when the Terraform directory is inside a run workspace; otherwise it falls back to
`artifacts/reports/<plan-hash>/`. The report contains a plan summary of what's
added/changed/destroyed, live-pricing cost status, the architecture
diagram, Plan PDF, Cost PDF, and raw JSON evidence). The report is **keyed by plan-hash**, so each
report is tied to exactly one plan; `git` versions the `.tf`, the plan-hash versions the report.

**Enterprise report format is binding:**
- Every major report section starts on a new PDF page.
- Include a report index/table of contents near the front.
- Use consistent page background, bordered panels, padding, and margins across plan and cost PDFs.
- Plan PDFs must include metadata, blueprint inputs, architecture, services/resources, IAM/security/governance, cost status, Terraform package structure, outputs, approval/drift status, planned changes, and artifact index.
- Cost PDFs must use AWS BCM Pricing Calculator API evidence. Do not publish offline fallback pricing. BCM estimate creation/update is an AWS-side effect and must be explicitly approved. If BCM pricing is unavailable, show "cost unavailable" plus the required `aws bcm-pricing-calculator ...` commands.
- New reports include reviewable BCM payload files. Do not run `bcm_pricing_calculator.py run` until the user approves the exact AWS-side estimate creation and all `REVIEW_REQUIRED` placeholders are resolved.
- Generated Terraform workspaces include `.minus/baseline.json` and `.minus/source_snapshot/`.
  Use `source_guard.py status|diff --dir <terraform-dir>` to show manual edits before a plan
  exists. After a report exists, `plan_inspector.py status|diff --latest` compares the current
  Terraform files against the plan-bound source snapshot.

**The architecture diagram is LLM-generated and MUST conform to the spec — this is binding:**
- [`docs/architecture_svg_spec.md`](./docs/architecture_svg_spec.md) — the structure, tiers,
  node schema, palette, and must-haves. **Any agent (agy, Claude, Codex) that draws the diagram
  follows this exactly**, so the output is structurally identical across tools.
- [`docs/architecture_svg_skeleton.svg`](./docs/architecture_svg_skeleton.svg) — start from this
  empty frame; inject nodes/edges/module-boxes into the fixed tier groups. Do not move bands,
  rename ids, or change the palette. Run the §8 self-check before emitting.

---

## 7. Environment & conventions

- **Active cloud:** set `MINUS_CLOUD={aws|azure|gcp}` (default `aws`). The governance core, FinOps agent, and dashboard all read it and route through `core/providers/`. AWS is fully implemented; azure/gcp are scaffolds that degrade gracefully.
- **OS:** cross-platform (Windows / macOS / Linux). Default shell here is **PowerShell**; a Bash (POSIX) tool is also available — use the right syntax per shell.
- **Credentials:** never handled by our code. The cloud CLI's own credential chain is used (`aws sso login` / `aws configure` / assumed role). Prefer SSO so no long-term secret lands on disk.
- **Approver RBAC:** set `MINUS_OPERATOR` (the acting principal; wire to SSO/OIDC or CI actor) and `MINUS_APPROVERS` (comma-separated allowlist) or `.minus/approvers.json`. With no allowlist the gates run in recorded "open" mode — never use open mode for production. See [`docs/security_model.md`](./docs/security_model.md) and [`docs/operations_runbook.md`](./docs/operations_runbook.md).
- **Region/defaults:** none are bundled — your Terraform owns its own region, environment, and tagging. The engine reads `MINUS_CLOUD` and your CLI's configured region; it does not inject provider defaults.
- **Git:** this **is** a git repo. Work on a branch; commit/push only when asked.
- **Before touching infra:** run `./tools/doctor.ps1` to confirm Terraform, the cloud CLI, and credentials are present.

---

## 8. Documentation Redirect Rule

**Whenever you need AWS / Terraform / pricing documentation, do not search from memory or the open web first — resolve the lookup through the repo's curated index:**

-> **[`docs/information_library.md`](./docs/information_library.md)** — ranked, curated catalog of every official portal (Terraform Registry & CLI, AWS CLI v2, AWS service dev guides, Pricing Calculator, Well-Architected, Glue/EMR/Databricks/Step Functions/Athena).

-> **[`docs/documentation_ledger.md`](./docs/documentation_ledger.md)** — the URL-construction formulas in §4.2 for jumping straight to a resource/command/price page without UI clicking.

Resolution order for any doc need:
1. **In-repo pattern** (existing `.tf` / script) → reuse it.
2. **`information_library.md`** → pick the authoritative portal for the topic.
3. **`documentation_ledger.md` formula** → build the direct URL and `WebFetch` it.
4. Only if all the above miss → general web search.
```
