# AGENTS.md — Operating Guide for CLI Agents

> **Audience:** Any autonomous coding/ops CLI agent working in this repo — `agy` (Antigravity), `codex`, `claude code`, or similar.
> **Purpose:** Tell you (the agent) *what you can do here*, *which tools to reach for*, *when and how to fetch documentation*, and *the safety rules you must never break*.
>
> Read this file first. For `agy`-specific workspace rules see [`.agents/AGENTS.md`](./.agents/AGENTS.md). For the canonical list of doc links, see the **Documentation Redirect Rule** at the bottom and [`docs/information_library.md`](./docs/information_library.md).

---

## 1. What this repo is

A **multi-cloud, framework-deployed ops control plane.** Each enterprise installs it and runs it against their *own* cloud with their *own* credentials — nothing is hosted by us. Two halves:

1. **A cloud-agnostic governance core** (`core/`) — deploy gating, approval, audit, FinOps, and a **provider abstraction** (`core/providers/`) so the same engine runs on AWS, Azure, or GCP. Select the active cloud with the `MINUS_CLOUD` env var (default `aws`).
2. **Infrastructure-as-Code** — `templates/` holds Terraform for real workloads (the AWS medallion pipeline is the first example); `bootstrap/` holds the framework's own governance IAM roles, also Terraform.

The governance core **never calls a cloud CLI directly** — only through a `CloudProvider`. Terraform + the cloud CLI are your universal hands; every change is governed by MFA-gated, plan-bound, audited deploys.

---

## 2. Repository map

```
.
├── AGENTS.md                       # ← you are here (universal agent entry point)
├── README.md  ·  requirements.txt
│
├── core/                           # CLOUD-AGNOSTIC GOVERNANCE ENGINE
│   ├── plan_gate.py                # deploy gate: verify → plan → hash → MFA approve → apply
│   ├── approval.py                 # approval gate: gatekeeper | auto-approve (audited)
│   ├── audit_logger.py             # append-only audit trail (.agents/logs/audit.jsonl)
│   ├── dispatcher.py               # NL query → routes to the right tool
│   ├── finops_agent.py             # live cost intelligence (provider-driven) + gated notify
│   ├── health_checker.py           # live health probes
│   ├── optimize_analyzer.py        # HCL scanner (SEC/COST/OBS) → markdown report
│   ├── budget_calculator.py        # cost estimator (live pricing + offline fallback)
│   └── providers/                  # CLOUD ABSTRACTION — pick via MINUS_CLOUD
│       ├── base.py                 # CloudProvider interface + get_provider()
│       ├── aws.py                  # AWS impl (Cost Explorer / anomalies / tags / identity)
│       └── azure.py · gcp.py       # scaffolds (degrade gracefully until implemented)
│
├── app/dashboard_app.py            # live FinOps console (Plotly Dash, provider-driven)
│
├── templates/aws/medallion-pipeline/   # IaC WORKLOAD TEMPLATE (one example)
│   ├── *.tf  ·  modules/iam_service_role/  ·  etl_scripts/
│
├── bootstrap/aws/                  # framework's own governance IAM (Terraform)
│   └── governance.tf …             # read-only role, MFA-gated deploy role, permissions boundary
│
├── docs/                           # information_library · documentation_ledger
│   │                               #   enterprise_iam_manifest · project_plan
├── tools/doctor.ps1                # env diagnostics
│
├── .agents/                        # agent skill manifests + runtime logs
│   ├── AGENTS.md                   # agy workspace rules (subset of this file)
│   ├── skills/{terraform-orchestrator,pipeline-optimizer}/SKILL.md
│   └── logs/                       # audit.jsonl, reports (gitignored, created on demand)
│
└── .github/workflows/deploy.yml    # CI: fmt → validate → tfsec → plan → apply
```

---

## 3. What you are capable of here

All paths are relative to the repo root. Select the cloud with `MINUS_CLOUD={aws|azure|gcp}` (default `aws`).

| Capability | How you do it | Primary tool |
| :--- | :--- | :--- |
| **Provision / change infra** | Edit HCL in `templates/<cloud>/<template>/`, then run the deploy gate (§6.1) | `core/plan_gate.py` + Terraform |
| **Inspect live state** | `aws <service> <describe/list/get>` (or `az`/`gcloud`) — read-only, safe | cloud CLI |
| **Health diagnostics** | `python core/health_checker.py` | cloud CLI probes |
| **Scan infra for issues** | `python core/optimize_analyzer.py --source-dir <dir>` | HCL scanner |
| **Estimate cost** | `python core/budget_calculator.py` (live pricing + offline fallback) | Pricing API |
| **Analyze live spend / anomalies** | `python core/finops_agent.py [--cost \| --anomalies \| --correlate]` (via active provider) | `core/providers/` |
| **View the FinOps console (UI)** | `python app/dashboard_app.py` → http://127.0.0.1:8050 (`pip install -r requirements.txt`) | Plotly Dash |
| **Notify (Slack/Jira), gated** | `core/finops_agent.py --notify-slack \| --notify-jira --approval-mode {gatekeeper\|auto-approve}` | `approval.py` gate |
| **Gate any side effect** | `python core/approval.py --action <a> --details <d> --mode {gatekeeper\|auto-approve}` | HITL / auto + audit |
| **Route a vague request** | `python core/dispatcher.py "<natural language>"` | keyword classifier |
| **Audit an action** | `python core/audit_logger.py --action <a> --details <d>` | append to `audit.jsonl` |
| **Diagnose local env** | `powershell -ExecutionPolicy Bypass -File ./tools/doctor.ps1` | PowerShell |

The **dispatcher** routes to five intents — `HEALTH`, `DEPLOY`, `OPTIMIZE`, `BUDGET`, `FINOPS`. You may also call any tool directly. Note: it classifies by **keyword matching**, not semantics, so prefer calling tools directly when precision matters.

---

## 4. When and how to fetch documentation

You are expected to **verify against official docs rather than rely on memory** for: Terraform resource arguments, AWS CLI command flags, service quotas, and live pricing. The full link catalog lives in [`information_library.md`](./docs/information_library.md) — that is the **redirect target**; always resolve doc lookups through it.

### 4.1 WHEN to fetch (triggers)

Fetch docs **before acting**, not after a failure, whenever you are about to:

- **Write or modify a Terraform resource** → confirm required/optional arguments and defaults against the AWS Provider Registry. Never guess an argument name.
- **Run an unfamiliar AWS CLI command** → confirm the exact subcommand, flags, and `--query`/output shape.
- **Quote or compute a price** → fetch live rates via the Pricing API/CLI; do not hardcode prices you "remember."
- **Hit a provider/CLI error** you don't fully understand → look up the resource/command page and the relevant AWS developer guide before retrying. **Do not retry blindly** (see §5).
- **Design IAM, networking, or encryption** → consult the Well-Architected / service security guides.

If you can answer confidently from a file already in this repo (e.g. an existing `.tf` shows the pattern), you don't need to fetch — reuse the in-repo pattern.

### 4.2 HOW to fetch — construct direct URLs (no UI clicking)

Per [`documentation_ledger.md`](./docs/documentation_ledger.md), these portals have **predictable URL structures**. Build the URL and `WebFetch` it directly instead of crawling a sidebar:

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

These mirror [`.agents/AGENTS.md`](./.agents/AGENTS.md) and [`enterprise_iam_manifest.md`](./docs/enterprise_iam_manifest.md). They are load-bearing — treat them as hard constraints.

1. **No mutating actions without explicit human review.** You are forbidden from running `terraform apply`, `terraform destroy`, `terraform state <mutating>`, `terraform force-unlock`, or mutating `git` (`push`, `reset`, `rebase`) — and any mutating `aws` call (`create-*`, `delete-*`, `put-*`, `modify-*`, `terminate-*`, `run-*`) — until the user has reviewed and approved. Side effects in the agent scripts (notifications, ticket creation) must route through `approval.py` (`gatekeeper` by default; `auto-approve` only when durably authorised — see §6.4).
2. **Read before write.** `aws describe-* / list-* / get-*`, `terraform plan`, `terraform validate`, `head-bucket`, `get-caller-identity` are safe and may be run freely to gather state.
3. **Dry-run first.** Always produce `terraform plan -out=tfplan` (or an API `--dry-run`) and present the diff *before* asking for approval.
4. **Audit every consequential action.** Log it via `audit_logger.py` to `.agents/logs/audit.jsonl` *before* proposing execution.
5. **Pass the security scan.** Before proposing infra changes for the live stack, run `optimize_analyzer.py`; resolve `SEC-*` findings (esp. `SEC-02` wildcard IAM) to zero. No `"Resource": "*"` for S3/KMS/DynamoDB. One dedicated least-privilege role per service (use `modules/iam_service_role`).
6. **Don't retry blindly on failure.** Extract the error, look up the doc (§4), write a troubleshooting note, and ask for help if human intervention is needed.
7. **Deploys go through the plan-gate.** `core/plan_gate.py` enforces verify → plan → **plan-hash** → approve → apply-the-exact-plan, with a full audit trail. Any `.tf` change produces a new hash, which voids the prior approval and forces a fresh review. The gate **never handles secrets** — authenticate via the cloud CLI first (`aws sso login`, or assume the MFA-gated deploy role from `bootstrap/aws/`); MFA is enforced by that role's trust policy and `apply` uses the ambient credential chain. Use it for every infrastructure change.
8. **Verify before deleting/overwriting.** If a target's contents contradict how it was described, surface that instead of proceeding.

---

## 6. Core workflows

### 6.1 Secure deployment loop (`core/plan_gate.py`)

```
1. Verify   →  plan_gate.py verify  --dir <template>   (fmt + validate + optimize_analyzer scan)
2. Plan     →  plan_gate.py plan    --dir <template>   (terraform plan -out=tfplan + record plan-hash)
3. Approve  →  plan_gate.py approve --dir <template> --mfa-arn <arn> [--role-arn <deploy-role>]
                                                       (review + MFA → one-shot session bound to the hash)
4. Apply    →  plan_gate.py apply   --dir <template>   (hash must match → apply tfplan → creds wiped)
5. Verify   →  health_checker.py                       (post-deploy smoke tests)

   Any .tf change → new plan-hash → prior approval void → fresh MFA required.
   `plan_gate.py run …` chains all stages; `--mode auto-approve` skips the y/N (still MFA + hash-bound).
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

### 6.5 Deploy report & architecture diagram

After a plan, a versioned **deploy report** is produced under `.agents/reports/<plan-hash>/`
(plan summary of what's added/changed/destroyed, per-run + monthly cost, the architecture
diagram, an HTML report, and a rendered PDF). The report is **keyed by plan-hash**, so each
report is tied to exactly one plan; `git` versions the `.tf`, the plan-hash versions the report.

**The architecture diagram is LLM-generated and MUST conform to the spec — this is binding:**
- 📐 [`docs/architecture_svg_spec.md`](./docs/architecture_svg_spec.md) — the structure, tiers,
  node schema, palette, and must-haves. **Any agent (agy, Claude, Codex) that draws the diagram
  follows this exactly**, so the output is structurally identical across tools.
- 🧩 [`docs/architecture_svg_skeleton.svg`](./docs/architecture_svg_skeleton.svg) — start from this
  empty frame; inject nodes/edges/module-boxes into the fixed tier groups. Do not move bands,
  rename ids, or change the palette. Run the §8 self-check before emitting.

---

## 7. Environment & conventions

- **Active cloud:** set `MINUS_CLOUD={aws|azure|gcp}` (default `aws`). The governance core, FinOps agent, and dashboard all read it and route through `core/providers/`. AWS is fully implemented; azure/gcp are scaffolds that degrade gracefully.
- **OS:** cross-platform (Windows / macOS / Linux). Default shell here is **PowerShell**; a Bash (POSIX) tool is also available — use the right syntax per shell.
- **Credentials:** never handled by our code. The cloud CLI's own credential chain is used (`aws sso login` / `aws configure` / assumed role). Prefer SSO so no long-term secret lands on disk.
- **Region/defaults:** templates default to `us-east-1`, `environment = "dev"`; the medallion stack applies `default_tags` — rely on those rather than re-tagging.
- **Git:** this **is** a git repo. Work on a branch; commit/push only when asked.
- **Before touching infra:** run `./tools/doctor.ps1` to confirm Terraform, the cloud CLI, and credentials are present.

---

## 8. 📍 Documentation Redirect Rule

**Whenever you need AWS / Terraform / pricing documentation, do not search from memory or the open web first — resolve the lookup through the repo's curated index:**

➡️ **[`docs/information_library.md`](./docs/information_library.md)** — ranked, curated catalog of every official portal (Terraform Registry & CLI, AWS CLI v2, AWS service dev guides, Pricing Calculator, Well-Architected, Glue/EMR/Databricks/Step Functions/Athena).

➡️ **[`docs/documentation_ledger.md`](./docs/documentation_ledger.md)** — the URL-construction formulas in §4.2 for jumping straight to a resource/command/price page without UI clicking.

Resolution order for any doc need:
1. **In-repo pattern** (existing `.tf` / script) → reuse it.
2. **`information_library.md`** → pick the authoritative portal for the topic.
3. **`documentation_ledger.md` formula** → build the direct URL and `WebFetch` it.
4. Only if all the above miss → general web search.
```
