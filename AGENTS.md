# AGENTS.md — Operating Guide for CLI Agents

> **Audience:** Any autonomous coding/ops CLI agent working in this repo — `agy` (Antigravity), `codex`, `claude code`, or similar.
> **Purpose:** Tell you (the agent) *what you can do here*, *which tools to reach for*, *when and how to fetch documentation*, and *the safety rules you must never break*.
>
> Read this file first. For `agy`-specific workspace rules see [`.agents/AGENTS.md`](./.agents/AGENTS.md). For the canonical list of doc links, see the **Documentation Redirect Rule** at the bottom and [`aws-medallion-pipeline/information_library.md`](./aws-medallion-pipeline/information_library.md).

---

## 1. What this repo is

An **agentic AWS DevOps control plane** built around an AWS Medallion (Bronze → Silver → Gold) data pipeline. It has two halves:

1. **Infrastructure-as-Code** — real Terraform that provisions the live AWS stack, plus reference templates for other architectures.
2. **An agent toolchain** (`.agents/`) — Python "skills" the agent invokes to deploy safely, scan for cost/security issues, estimate spend, run FinOps analysis, check health, and generate test data.

The long-term goal is a **broad AWS ops copilot** driven primarily through the **AWS CLI** + **Terraform**, governed by Human-in-the-Loop (HITL) gating and audit logging. Treat the AWS CLI and Terraform as your universal hands; treat the scripts in `.agents/skills/` as pre-built capabilities you orchestrate.

---

## 2. Repository map

```
.
├── AGENTS.md                          # ← you are here (universal agent entry point)
├── README.md                          # human setup guide (Terraform + AWS CLI install/config)
├── doctor.ps1                         # Windows env diagnostics (Terraform/AWS CLI/creds/keys)
│
├── .agents/                           # AGENT TOOLCHAIN
│   ├── AGENTS.md                      # agy workspace rules (HITL/safety) — subset of this file
│   ├── dashboard_app.py               # live FinOps console (Plotly Dash): python .agents/dashboard_app.py
│   ├── logs/                          # runtime output: audit.jsonl, health reports (created on demand)
│   └── skills/
│       ├── terraform-orchestrator/    # deploy safely + ops
│       │   ├── SKILL.md
│       │   └── scripts/
│       │       ├── intent_dispatcher.py   # NL query → routes to the right script
│       │       ├── audit_logger.py        # append-only audit trail (.agents/logs/audit.jsonl)
│       │       ├── approval.py            # approval gate: gatekeeper | auto-approve (audited)
│       │       ├── finops_agent.py        # LIVE cost intelligence (ce/cloudtrail/tags) + gated notify
│       │       └── health_checker.py      # live AWS CLI health probes (sts/s3/glue)
│       └── pipeline-optimizer/        # scan + optimize existing infra
│           ├── SKILL.md
│           └── scripts/
│               ├── optimize_analyzer.py   # HCL scanner (SEC/COST/OBS rules) → markdown report
│               └── budget_calculator.py   # multi-service monthly cost estimator (live + offline pricing)
│
├── aws-medallion-pipeline/            # THE LIVE TERRAFORM STACK
│   ├── providers.tf variables.tf outputs.tf
│   ├── s3.tf glue.tf step_functions.tf eventbridge.tf iam.tf observability.tf
│   ├── budgets.tf cost_anomaly.tf
│   ├── modules/iam_service_role/      # reusable least-privilege role module
│   ├── etl_scripts/                   # Glue PySpark jobs (bronze_to_silver, silver_to_gold)
│   ├── information_library.md         # ← CANONICAL DOC INDEX (redirect target)
│   ├── documentation_ledger.md        # how to build direct doc/pricing URLs (no UI clicking)
│   ├── enterprise_iam_manifest.md     # IAM security commandments + MFA gate flow
│   └── project_plan.md                # milestones / status
│
└── .github/workflows/deploy.yml       # CI: fmt → init → validate → tfsec → plan → apply (main only)
```

---

## 3. What you are capable of here

| Capability | How you do it | Primary tool |
| :--- | :--- | :--- |
| **Provision / change AWS infra** | Edit HCL in `aws-medallion-pipeline/`, then run the orchestration loop (§6) | Terraform CLI |
| **Inspect live AWS state** | `aws <service> <describe/list/get>` — read-only, safe to run freely | AWS CLI v2 |
| **Health diagnostics** | `python .agents/skills/terraform-orchestrator/scripts/health_checker.py` | AWS CLI probes |
| **Scan infra for issues** | `python .agents/skills/pipeline-optimizer/scripts/optimize_analyzer.py --source-dir <dir>` | HCL regex scanner |
| **Estimate cost** | `python .agents/skills/pipeline-optimizer/scripts/budget_calculator.py` (live Pricing API + offline fallback) | AWS Pricing API |
| **Analyze live spend / anomalies** | `python .agents/skills/terraform-orchestrator/scripts/finops_agent.py [--cost \| --anomalies \| --correlate]` | `aws ce` / `cloudtrail` / tagging |
| **View the FinOps console (UI)** | `python .agents/dashboard_app.py` → http://127.0.0.1:8050 (`pip install -r requirements.txt` first) | Plotly Dash |
| **Notify (Slack/Jira), gated** | `finops_agent.py --notify-slack \| --notify-jira --approval-mode {gatekeeper\|auto-approve}` | `approval.py` gate |
| **Gate any side effect** | `python .agents/skills/terraform-orchestrator/scripts/approval.py --action <a> --details <d> --mode {gatekeeper\|auto-approve}` | HITL / auto + audit |
| **Route a vague request** | `python .agents/skills/terraform-orchestrator/scripts/intent_dispatcher.py "<natural language>"` | keyword classifier |
| **Audit an action** | `python .agents/skills/terraform-orchestrator/scripts/audit_logger.py --action <a> --details <d>` | append to `audit.jsonl` |
| **Diagnose local env** | `powershell -ExecutionPolicy Bypass -File .\doctor.ps1` | PowerShell |

The **intent dispatcher** routes to five intents — `HEALTH`, `DEPLOY`, `OPTIMIZE`, `BUDGET`, `FINOPS`. As the agent you may also call any script directly when you already know the intent. Note: the dispatcher classifies by **keyword matching**, not semantics, so prefer calling scripts directly when precision matters.

---

## 4. When and how to fetch documentation

You are expected to **verify against official docs rather than rely on memory** for: Terraform resource arguments, AWS CLI command flags, service quotas, and live pricing. The full link catalog lives in [`information_library.md`](./aws-medallion-pipeline/information_library.md) — that is the **redirect target**; always resolve doc lookups through it.

### 4.1 WHEN to fetch (triggers)

Fetch docs **before acting**, not after a failure, whenever you are about to:

- **Write or modify a Terraform resource** → confirm required/optional arguments and defaults against the AWS Provider Registry. Never guess an argument name.
- **Run an unfamiliar AWS CLI command** → confirm the exact subcommand, flags, and `--query`/output shape.
- **Quote or compute a price** → fetch live rates via the Pricing API/CLI; do not hardcode prices you "remember."
- **Hit a provider/CLI error** you don't fully understand → look up the resource/command page and the relevant AWS developer guide before retrying. **Do not retry blindly** (see §5).
- **Design IAM, networking, or encryption** → consult the Well-Architected / service security guides.

If you can answer confidently from a file already in this repo (e.g. an existing `.tf` shows the pattern), you don't need to fetch — reuse the in-repo pattern.

### 4.2 HOW to fetch — construct direct URLs (no UI clicking)

Per [`documentation_ledger.md`](./aws-medallion-pipeline/documentation_ledger.md), these portals have **predictable URL structures**. Build the URL and `WebFetch` it directly instead of crawling a sidebar:

| Need | URL formula |
| :--- | :--- |
| **Terraform resource** | `https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/<type_without_aws_prefix>` |
| **Terraform data source** | `https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/<type_without_aws_prefix>` |
| **AWS CLI command** | `https://awscli.amazonaws.com/v2/documentation/api/latest/reference/<service>/<action>.html` |
| **Live price (CLI)** | `aws pricing get-products --service-code <Code> --filters "Type=TERM_MATCH,Field=<f>,Value=<v>" --region us-east-1` |
| **Raw price index (JSON)** | `https://pricing.us-east-1.amazonaws.com/offers-v1.0/aws/<ServiceCode>/current/index.json` |

Examples: `aws_glue_job` → `.../resources/glue_job`; `aws s3api head-bucket` → `.../reference/s3api/head-bucket.html`.

### 4.3 Fetch decision flow

```
Need a fact about an AWS resource / CLI flag / price?
        │
        ├─ Is it already demonstrated in an existing repo file?  ──► reuse that pattern (no fetch)
        │
        ├─ Is it a Terraform resource arg?  ──► registry.terraform.io/.../resources/<type>
        ├─ Is it an AWS CLI flag/shape?     ──► awscli.../reference/<service>/<action>.html
        ├─ Is it a price?                   ──► aws pricing get-products  (fallback: offline catalog)
        └─ Is it design/security guidance?  ──► Well-Architected / service dev guide (via information_library.md)
```

---

## 5. Safety rules (non-negotiable)

These mirror [`.agents/AGENTS.md`](./.agents/AGENTS.md) and [`enterprise_iam_manifest.md`](./aws-medallion-pipeline/enterprise_iam_manifest.md). They are load-bearing — treat them as hard constraints.

1. **No mutating actions without explicit human review.** You are forbidden from running `terraform apply`, `terraform destroy`, `terraform state <mutating>`, `terraform force-unlock`, or mutating `git` (`push`, `reset`, `rebase`) — and any mutating `aws` call (`create-*`, `delete-*`, `put-*`, `modify-*`, `terminate-*`, `run-*`) — until the user has reviewed and approved. Side effects in the agent scripts (notifications, ticket creation) must route through `approval.py` (`gatekeeper` by default; `auto-approve` only when durably authorised — see §6.4).
2. **Read before write.** `aws describe-* / list-* / get-*`, `terraform plan`, `terraform validate`, `head-bucket`, `get-caller-identity` are safe and may be run freely to gather state.
3. **Dry-run first.** Always produce `terraform plan -out=tfplan` (or an API `--dry-run`) and present the diff *before* asking for approval.
4. **Audit every consequential action.** Log it via `audit_logger.py` to `.agents/logs/audit.jsonl` *before* proposing execution.
5. **Pass the security scan.** Before proposing infra changes for the live stack, run `optimize_analyzer.py`; resolve `SEC-*` findings (esp. `SEC-02` wildcard IAM) to zero. No `"Resource": "*"` for S3/KMS/DynamoDB. One dedicated least-privilege role per service (use `modules/iam_service_role`).
6. **Don't retry blindly on failure.** Extract the error, look up the doc (§4), write a troubleshooting note, and ask for help if human intervention is needed.
7. **MFA / HITL gatekeeper is external.** The `hitl_gatekeeper.py` referenced by the orchestrator skill and IAM manifest lives **outside this workspace** (`~/.gemini/antigravity-cli/scratch/bin`). If it is absent, **stop and ask the user** rather than executing the apply yourself — do not improvise around the gate.
8. **Verify before deleting/overwriting.** If a target's contents contradict how it was described, surface that instead of proceeding.

---

## 6. Core workflows

### 6.1 Secure deployment loop (from `terraform-orchestrator` SKILL)

```
1. Validate   →  terraform fmt -check && terraform validate
2. Plan       →  terraform plan -out=tfplan
3. Scan       →  optimize_analyzer.py  (resolve SEC/COST/OBS findings)
4. Audit      →  audit_logger.py --action ... --details ...
5. HITL gate  →  present plan + wait for explicit APPROVED  (gatekeeper is external — see §5.7)
6. Execute    →  terraform apply tfplan        (ONLY after approval)
7. Verify     →  health_checker.py             (post-deploy smoke tests; alert on failure)
```

### 6.2 Optimization loop (from `pipeline-optimizer` SKILL)

```
1. Scan       →  optimize_analyzer.py --source-dir <dir>   → .agents/logs/optimization_report.md
2. Present    →  show the markdown findings table to the user
3. Refactor   →  on approval, draft a Terraform plan applying the fixes  → re-enter 6.1
```

### 6.3 Cost estimation

```
budget_calculator.py --service <GLUE|EMR_SERVERLESS|DATABRICKS|REDSHIFT> --scale N --duration M --runs-daily R --s3-gb G
# Queries the live AWS Pricing API first; falls back to an offline us-east-1 catalog.
# Output marks each rate as "Live AWS API" or "Offline Cache".
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

---

## 7. Environment & conventions

- **OS:** Windows 11. Default shell is **PowerShell**; a Bash (POSIX) tool is also available. Use the right syntax per shell.
- **Region/defaults:** `us-east-1`, `environment = "dev"`, bucket suffix in `variables.tf`. Provider applies `default_tags` (Environment/Project/ManagedBy) — rely on those rather than re-tagging each resource.
- **Pricing:** scripts try the live AWS Pricing API first and fall back to an offline `us-east-1` catalog; surfaced output marks which source was used (`Live AWS API` vs `Offline Cache`).
- **Git:** this directory is **not a git repo** yet. If version control is needed, initialize on a branch — never assume a remote exists.
- **Before touching infra:** run `doctor.ps1` to confirm Terraform, AWS CLI, and credentials are present.

---

## 8. 📍 Documentation Redirect Rule

**Whenever you need AWS / Terraform / pricing documentation, do not search from memory or the open web first — resolve the lookup through the repo's curated index:**

➡️ **[`aws-medallion-pipeline/information_library.md`](./aws-medallion-pipeline/information_library.md)** — ranked, curated catalog of every official portal (Terraform Registry & CLI, AWS CLI v2, AWS service dev guides, Pricing Calculator, Well-Architected, Glue/EMR/Databricks/Step Functions/Athena).

➡️ **[`aws-medallion-pipeline/documentation_ledger.md`](./aws-medallion-pipeline/documentation_ledger.md)** — the URL-construction formulas in §4.2 for jumping straight to a resource/command/price page without UI clicking.

Resolution order for any doc need:
1. **In-repo pattern** (existing `.tf` / script) → reuse it.
2. **`information_library.md`** → pick the authoritative portal for the topic.
3. **`documentation_ledger.md` formula** → build the direct URL and `WebFetch` it.
4. Only if all the above miss → general web search.
```
