# MinusOps Friction, Mismatch & Conflict Ledger (`minusops_friction.md`)

This document catalogs known architectural friction points, potential contract mismatches, gate conflicts, and operational edge cases across the **MinusOps** control plane. It serves as an authoritative reference for operators, architects, and autonomous agents to anticipate, diagnose, and avoid system blocks.

---

## 1. Requirements & Architecture Gating Friction

### 1.1 Regex Parsing vs. Free-Form Human Input
* **Location**: [`core/architecture/requirements.py`](./core/architecture/requirements.py) (`parse_daily_gb`, `parse_budget_usd`)
* **The Conflict**: `requirements.py` uses strict regex extraction to derive numerical values (e.g. `r"(\d+(?:\.\d+)?)\s*(?:gb|gigabytes|tb|terabytes)"`).
* **Friction Mode**: If a user or conversational agent enters volume or cost in non-standard units (e.g., `"500 MB/day"`, `"10k USD per quarter"`, `"negligible volume"`), regex parsing returns `None`.
* **Consequence**: The synthesizer will fall back to conservative defaults (e.g., smallest compute tier) or leave `monthly_budget_usd` un-wired as a `REVIEW_REQUIRED` comment, potentially surprising the operator.

### 1.2 The TerraShark 4-Part Contract & Failure Modes Validation
* **Location**: [`core/architecture/architecture_decision.py`](./core/architecture/architecture_decision.py) (`validate`, `FAILURE_MODES`)
* **The Conflict**: Architecture decision records require explicit lists for `validation`, `rollback`, `assumptions`, and `alternatives`. Furthermore, `failure_modes` must only reference validated IDs from the TerraShark taxonomy (`FM-01` through `FM-05`).
* **Friction Mode**: If an operator or external agent drafts an `architecture_decision.json` and invents custom failure mode codes (e.g., `"FM-06-custom"`, `"RATE_LIMIT"`) or omits `validation`/`rollback`, `validate()` fails.
* **Consequence**: `synthesizer.py` raises `ArchitectureDecisionIncomplete` and halts synthesis until the schema is strictly satisfied.

### 1.3 Team ID Sanitization & S3 Path Partitioning
* **Location**: [`core/architecture/team_resolver.py`](./core/architecture/team_resolver.py) (`validate_team_id`)
* **The Conflict**: Team IDs are strictly validated with `^[a-z0-9][a-z0-9-]{0,62}$` to prevent S3 prefix escaping and IAM pattern injection.
* **Friction Mode**: Enterprise team names containing uppercase letters, underscores, periods, or slashes (e.g., `Data_Engineering`, `Team.Core`, `ops/infra`) will raise `InvalidTeamId`.
* **Workaround**: Team identifiers must be kebab-cased lowercase strings (e.g., `data-engineering`, `team-core`).

---

## 2. IaC Synthesis & Module Composition Conflicts

### 2.1 Fixed Inter-Module Auto-Wiring Assumptions
* **Location**: [`core/generation/synthesizer.py`](./core/generation/synthesizer.py) (`_module_args`, `compose`)
* **The Conflict**: The synthesizer contains built-in wiring rules for canonical topologies (e.g., `storage-medallion-s3` wires bucket names directly into `compute-glue-etl` and `dq-great-expectations`).
* **Friction Mode**: If an architecture selects a non-standard combination—such as `compute-emr-serverless` feeding directly into `warehouse-snowflake-aws` without `storage-medallion-s3`—the auto-wiring logic cannot deduce target bucket parameters.
* **Consequence**: The generated `main.tf` will leave un-wired variables that require manual review or authored glue resources, failing strict `readiness` checks until resolved.

### 2.2 Transform Engine Conflict (`Glue` vs. `dbt`)
* **Location**: [`core/generation/synthesizer.py`](./core/generation/synthesizer.py) (`transform_engine`, `write_dbt_project`)
* **The Conflict**: When `transform_engine` in `architecture_decision.json` is set to `"dbt"`, the synthesizer explicitly drops `compute-glue-etl` (even if requested in `selected_modules`) and demands `query-athena`.
* **Friction Mode**: If an architect intends to use AWS Glue for heavy raw PySpark transforms and dbt for Gold SQL modeling in the *same* repository workspace, setting `"dbt"` will silently purge Glue ETL from the composed output.
* **Workaround**: Specify Glue as the primary transform engine and author secondary dbt models outside the single-engine selector.

### 2.3 Promotion Matrix `force_destroy` Invariant
* **Location**: [`core/generation/synthesizer.py`](./core/generation/synthesizer.py) (`_ENV_MATRIX`, `write_env_tfvars`)
* **The Conflict**: `force_destroy` is hardwired in `main.tf` to evaluate `var.environment == "dev"`. It is deliberately excluded from `envs/*.tfvars`.
* **Friction Mode**: An engineer trying to enable `force_destroy = true` in `envs/staging.tfvars` or `envs/prod.tfvars` (e.g., for disposable CI/CD ephemeral stacks) cannot do so via tfvars.
* **Consequence**: Bucket deletion in non-dev tiers will fail if objects are present, requiring explicit manual bucket emptying or local override.

### 2.4 Terraform S3 Native Locking Version Floors
* **Location**: [`core/generation/synthesizer.py`](./core/generation/synthesizer.py) (`_render_backend`)
* **The Conflict**: S3 remote backend generation utilizes S3-native locking (`use_lockfile = true`) instead of legacy DynamoDB lock tables to avoid deprecated HashiCorp patterns.
* **Friction Mode**: S3 native locking requires Terraform CLI `>= 1.9.0` and AWS provider `>= 5.0`.
* **Consequence**: Operators running older Terraform versions (e.g., `1.5.x` through `1.8.x`) will encounter backend initialization errors during `terraform init`.

---

### 2.5 Weak-Match Stopwords in Module Registry
* **Location**: [`core/generation/modules.py`](./core/generation/modules.py) (`_WEAK_STOPWORDS`, `match_modules`)
* **The Conflict**: Single-token scoring ignores ubiquitous terms (`data`, `aws`, `amazon`, `managed`, `pipeline`) to prevent ingestion modules like `ingestion-dms` from falsely matching generic requests like "build a data pipeline".
* **Friction Mode**: If a user's requirement prompt uses only generic terms without explicit workload archetypes (e.g. `"CDC"`, `"database"`, `"SaaS"`, `"partner drops"`), module matching scores may fall below `min_score = 1`.
* **Workaround**: Prompt with domain-specific archetypes (e.g. `"RDS database change data capture"`, `"SFTP partner ingestion"`).

### 2.6 Mandatory Tag Validation via Terraform `check` Blocks
* **Location**: [`core/generation/synthesizer.py`](./core/generation/synthesizer.py) (`variables.tf` check blocks)
* **The Conflict**: Cross-variable validation rules demanding `cost_center` and `data_classification` in staging/prod are implemented as Terraform `check` blocks rather than hard `validation` blocks.
* **Friction Mode**: Terraform `check` blocks emit warnings during `terraform plan` without failing the command natively (retained to maintain compatibility with Terraform `>= 1.5`).
* **Consequence**: The Terraform binary itself will not halt on missing tags; hard enforcement falls entirely on `plan_gate.py`'s static policy scanner.

---

## 3. Deploy Gate, Autonomy & Identity Boundaries

### 3.1 The Plan-Hash Approval Invalidation Trap
* **Location**: [`core/governance/plan_gate.py`](./core/governance/plan_gate.py) (`stage_plan`, `stage_approve`, `stage_apply`)
* **The Conflict**: Deployment approval is cryptographically locked to `sha256(tfplan)`.
* **Friction Mode**: If any modification occurs in the target directory after `plan_gate.py plan` (e.g., auto-formatting via `terraform fmt`, linters updating whitespace, or modifying tags), the subsequent re-plan generates a different hash.
* **Consequence**: The previous human/MFA approval is instantly invalidated (`APPROVAL_VOID`), forcing a complete re-approval cycle.

### 3.2 Two-Person Rule in Production Mode
* **Location**: [`core/governance/plan_gate.py`](./core/governance/plan_gate.py) (`_enforce_production_approval`)
* **The Conflict**: In `policy-mode production`, the gate enforces separation of duties: the applying principal must be distinct from the operator who approved the plan.
* **Friction Mode**: A solo engineer, platform admin, or automated runner executing both `approve` and `apply` using the same STS credentials in production mode will be blocked.
* **Workaround**: Use `--policy-mode dev` in non-production environments or assume distinct approver/deployer IAM roles in production.

### 3.3 Long-Term vs. Temporary Credential Posture Block
* **Location**: [`core/providers/aws.py`](./core/providers/aws.py) & [`core/governance/plan_gate.py`](./core/governance/plan_gate.py) (`_reject_if_weak_credentials`)
* **The Conflict**: MinusOps evaluates credential key prefixes (`AKIA` = long-term IAM user, `ASIA` = temporary STS session).
* **Friction Mode**: Applying in `policy-mode production` with long-term IAM user access keys (`AKIA`) or root account credentials fails-closed.
* **Consequence**: Operators must authenticate via AWS SSO, IAM Identity Center, or assume an MFA-gated deploy role before executing `apply`.

### 3.4 Non-Interactive TTY Block in Gatekeeper Mode
* **Location**: [`core/governance/approval.py`](./core/governance/approval.py) (`request_approval`)
* **The Conflict**: In default `gatekeeper` mode, `approval.py` verifies that `sys.stdin` is an interactive TTY before prompting for `[y/N]`.
* **Friction Mode**: When invoked inside headless CI/CD runners, cron jobs, or background subagents without a TTY, the approval immediately fails-closed with `DENIED_NO_TTY`.
* **Workaround**: Automated pipelines must explicitly supply `--mode auto-approve` with allowlisted credentials and clean G5/G9 safety classifications.

### 3.5 Out-of-Band Cloud Drift vs. Autonomous Deployment
* **Location**: [`core/governance/cloud_drift.py`](./core/governance/cloud_drift.py) & [`destructive_change_gate.py`](./core/governance/destructive_change_gate.py)
* **The Conflict**: Autonomous deployment (`auto-approve`) is strictly forbidden from reverting out-of-band manual changes or executing non-create actions on stateful/IAM resources.
* **Friction Mode**: If an engineer made an emergency console edit (e.g. modifying an S3 lifecycle rule or IAM trust policy), any automated pipeline attempting to apply HCL will be halted by `cloud_drift.py`.
* **Consequence**: Manual intervention and interactive signoff are mandatory whenever cloud drift is detected.

---

## 4. FinOps, BCM Pricing & Catalog Coverage Edge Cases

### 4.1 BCM Pricing Calculator IAM & Placeholder Blocks
* **Location**: [`core/cost/bcm_pricing_calculator.py`](./core/cost/bcm_pricing_calculator.py) (`run`, `validate_usage`)
* **The Conflict**: MinusOps forbids hardcoded pricing approximations and requires real AWS BCM Pricing Calculator API evidence.
* **Friction Mode**:
  1. If `bcm-usage.json` contains un-substituted `REVIEW_REQUIRED` placeholders (such as unknown read/write throughput or storage tiers), `bcm_pricing_calculator.run()` throws a `RuntimeError`.
  2. If the AWS IAM identity lacks `bcm-pricing-calculator:*` permissions, estimate creation fails.
* **Consequence**: `reporter.py` and `minusctl readiness` mark cost evidence as `UNAVAILABLE`, capping the maximum readiness score.

### 4.2 Fail-Closed Unresolved Resource Types in Strict Mode
* **Location**: [`core/cost/coverage_audit.py`](./core/cost/coverage_audit.py) (`audit`)
* **The Conflict**: `coverage_audit.py` scans all resource types in `plan.json` against [`aws_resource_map.json`](./core/cost/pricing_data/aws_resource_map.json) and [`free_resources.json`](./core/cost/pricing_data/free_resources.json).
* **Friction Mode**: If a newly released AWS resource type or third-party provider resource (e.g. Databricks/Snowflake Terraform providers) is introduced without being added to the catalog, it is marked `unresolved`.
* **Consequence**: `coverage_audit.py` exits with status `1`, blocking the deployment gate in strict compliance modes.

---

## 5. Verification, Seeding & Stage Review Friction

### 5.1 Independent Stage Review Gate Blocks (`reflector.py`)
* **Location**: [`core/governance/reflector.py`](./core/governance/reflector.py)
* **The Conflict**: `reflector.py` runs 5 independent static verification checks against physical files on disk rather than memory mocks.
* **Friction Mode**: If an authored HCL block hardcodes a literal string (e.g. `"arn:aws:s3:::my-bucket"`) where a module reference (e.g. `module.storage_medallion_s3.bucket_arns["bronze"]`) should be wired, or if declared volume outgrows the compute tier, `reflector.py` returns exit code `2` (BLOCKED).
* **Consequence**: Packaging and PR review evidence generation are blocked until the structural wiring is corrected.

### 5.2 Zero-Row False Green Prevention (`seed.py`)
* **Location**: [`core/reporting/seed.py`](./core/reporting/seed.py) (`_query`, `seed_pipeline`)
* **The Conflict**: `seed.py` validates end-to-end data flow (Bronze upload -> Glue job run -> Athena query).
* **Friction Mode**: If the transform finishes without errors but writes zero rows to the Gold table, `seed.py` treats it as a hard failure (`Query returned 0 rows`), preventing false green pipeline approvals.
* **Workaround**: Ensure sample data fixtures under `tests/fixtures/sample.json` satisfy schema contracts and filter criteria.

---

## 6. Concurrency, State Locking & Cross-Platform Friction

### 6.1 Append-Only Audit Log Lock Contention
* **Location**: [`core/governance/audit_chain.py`](./core/governance/audit_chain.py) (`_AppendLock`)
* **The Conflict**: MinusOps implements OS-native file region locking (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX) with a 10-second timeout.
* **Friction Mode**: If multiple parallel CLI commands, Dash dashboard background threads (`app/dashboard_app.py`), and CI processes attempt to write audit records simultaneously during high-throughput operations, lock contention can cause `TimeoutError`.
* **Workaround**: Ensure audit operations execute quickly and processes do not hold open file descriptors across network shares.

### 6.2 Local Cloud Emulator (G9) Fidelity Gaps
* **Location**: [`core/governance/ephemeral_apply.py`](./core/governance/ephemeral_apply.py) (LocalStack / MiniStack / Floci)
* **The Conflict**: G9 ephemeral apply validates plan execution against local emulators.
* **Friction Mode**: Local emulators often lag behind real AWS API schemas (e.g., advanced Glue 4.0 arguments, Lake Formation tags, fine-grained KMS policies).
* **Consequence**: An ephemeral apply may fail on LocalStack despite being completely valid on real AWS, blocking autonomous auto-approve pathways.

---

## 7. Context Graph Maintenance Invariant

### 7.1 Documentation Drift vs. Strict Context Verification
* **Location**: [`CONTEXT-MAP.md`](./CONTEXT-MAP.md) & [`.gemini/config/skills/context-graph/SKILL.md`](C:/Users/shubh/.gemini/config/skills/context-graph/SKILL.md)
* **The Conflict**: Every directory maintains a synchronized `CONTEXT-[folder].md`. Changes to function signatures, imports, or failure modes require an immediate atomic documentation update.
* **Friction Mode**: Developers or external tools that refactor code without updating the corresponding `CONTEXT-[folder].md` will cause context graph drift checks to fail, misinforming future agent invocations.

---

## Summary Matrix

| Subsystem | Potential Conflict / Friction | Fail-Safe Behavior | Recommended Resolution |
| :--- | :--- | :--- | :--- |
| **Requirements** | Non-standard volume/cost strings | Regex returns `None`, defaults applied | Use standard units (`GB`, `TB`, `USD`) |
| **Architecture** | Custom failure modes or missing rollback | `ArchitectureDecisionIncomplete` | Follow TerraShark 4-part contract (`FM-01..05`) |
| **Synthesis** | Setting `"dbt"` drops `compute-glue-etl` | Glue dropped; Athena required | Select Glue as primary if both are required |
| **Synthesis** | Single-token generic prompts | Keyword match under scores | Use specific archetype keywords |
| **Deploy Gate** | Post-plan file edits or formatting | Approval voided (`APPROVAL_VOID`) | Run formatting before `plan_gate.py plan` |
| **Deploy Gate** | Same approver and deployer in prod | Gate rejects self-approval | Use separate STS role sessions in production |
| **Deploy Gate** | Long-term `AKIA` keys in production | Rejects weak credentials | Use temporary `ASIA` STS / SSO sessions |
| **FinOps** | Unmapped resource in `coverage_audit` | Exit code 1 (Unresolved resource) | Add resource prefix to `aws_resource_map.json` |
| **CI / Automation**| Running `gatekeeper` mode without TTY | Fail-closed (`DENIED_NO_TTY`) | Supply `--mode auto-approve` with valid IAM |
| **Stage Review** | Hardcoded literals instead of module ref | `reflector.py` exits 2 (BLOCKED) | Wire outputs from upstream module references |
| **Pipeline Seed**| Zero records written to Gold lake | `seed.py` raises failure | Verify input fixture data schema |
| **Audit Chain** | High-concurrency simultaneous writes | 10-second `TimeoutError` | Keep write lock durations minimal |

