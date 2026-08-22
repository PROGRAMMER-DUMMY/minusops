# Core Reporting & Inspection Subsystem Context — MinusOps

This document provides an exhaustive, architectural, and operational reference for every file in the `core/reporting/` directory. The `core/reporting/` subsystem is responsible for workspace management, static HCL security and cost scanning, plan inspection, visual architecture diagram rendering, multi-format report bundle generation (HTML, PDF, SVG, JSON), live FinOps intelligence, health diagnostics, cross-platform CLI tool discovery, and the main operator CLI (`minusctl`).

---

## Directory Overview & File Index

- [`core/reporting/__init__.py`](./__init__.py) — Package initialization and doctrine statement.
- [`core/reporting/adopt.py`](./adopt.py) — Brownfield adoption: inventory + scan an existing Terraform directory and bring it under the deploy gate (MINUS-106).
- [`core/reporting/seed.py`](./seed.py) — End-to-end pipeline proof: seed Bronze, run the Glue job, query Gold (MINUS-113). **The only command in this subsystem that mutates AWS.**
- [`core/reporting/doctor.py`](./doctor.py) — Cross-platform environment diagnostics behind `minusctl doctor`; replaces the Windows-only `tools/doctor.ps1`.
- [`core/reporting/finops_agent.py`](./finops_agent.py) — Live cloud cost intelligence, cost anomaly detection, root-cause correlation, and approval-gated notifications (Slack/Jira).
- [`core/reporting/health_checker.py`](./health_checker.py) — Live environment health diagnostics, probing AWS CLI availability, sts identity, S3 bucket accessibility, and Glue job execution states.
- [`core/reporting/minusctl.py`](./minusctl.py) — Primary operator-facing safe CLI wrapper driving intent resolution, run workspace creation, readiness scoring, evidence generation, and policy promotion.
- [`core/reporting/optimize_analyzer.py`](./optimize_analyzer.py) — Per-resource HCL scanner evaluating security (`SEC-*`), cost (`COST-*`), observability (`OBS-*`), and data performance (`DATA-*`) rules, with optional Checkov/Trivy/TFLint external integration.
- [`core/reporting/plan_inspector.py`](./plan_inspector.py) — Plan explorer analyzing `plan.json` for services, resources, IAM roles, source snapshot hashing, and source drift diffing.
- [`core/reporting/reporter.py`](./reporter.py) — Core reporting engine generating versioned, plan-hash-keyed report bundles containing `manifest.json`, `plan.json`, `architecture.svg`, `dataflow.svg`, `plan.pdf`, `cost.pdf`, and `inspect.pdf`.
- [`core/reporting/runs.py`](./runs.py) — Run workspace manager managing isolated run directories under `runs/<run-id>/`, plus the central registry (`runs/index.json`, `runs/INDEX.md`).
- [`core/reporting/export.py`](./export.py) — Packages a run into a domain repository: copies the four deployable directories and, on request, a per-pipeline GitHub Actions workflow.
- [`core/reporting/toolpath.py`](./toolpath.py) — Cross-platform discovery utility for external CLIs (`terraform`, `aws`, headless browsers) without hardcoding user home paths.
- [`core/reporting/cli_diagnostics.py`](./cli_diagnostics.py) — Agent-facing failure formatting: fuzzy run-id resolution, lifecycle stage interception, and the three-part `WHAT FAILED / WHY IT FAILED / ACTION REQUIRED` error (MINUS-157..160).
- [`core/reporting/excel_finops_generator.py`](./excel_finops_generator.py) — Dual-tier FinOps `.xlsx` writer (executive summary + engineering ledger) built on stdlib `zipfile` + OpenXML, no third-party dependency.

---

## Detailed File Specifications

### 1. `core/reporting/__init__.py`
- **File Link:** [`core/reporting/__init__.py`](./__init__.py)
- **Exact Purpose:** Defines `core.reporting` as a Python package.
- **Key Functions/Classes:** None (module-level docstring).
- **Inputs/Outputs:** None.
- **Failure Modes:** N/A.
- **Architectural Role:** Serves as the package entry point for reporting, inspection, and operator tooling.

---

### 2. `core/reporting/finops_agent.py`
- **File Link:** [`core/reporting/finops_agent.py`](./finops_agent.py)
- **Exact Purpose:** Provides live cloud cost intelligence by interfacing with cloud providers through `providers.base.get_provider()`. Supports spend breakdowns, cost anomaly listing, CloudTrail/tag root-cause correlation, approval-gated alerts (Slack webhooks or Jira tickets), and error budget burn calculation.
- **Key Functions/Classes:**
  - [`cmd_cost()`](./finops_agent.py): Displays spend breakdown by service and month-over-month trends.
  - [`cmd_anomalies()`](./finops_agent.py): Lists active cost anomalies from the active provider.
  - [`cmd_correlate()`](./finops_agent.py): Correlates cost anomalies with CloudTrail mutating events and resource tag owners (AWS only).
  - [`cmd_notify_slack(approval_mode)`](./finops_agent.py): Delegates to `slack_hook.send_slack_notification()` (action `send-slack-alert`), which gates on approval before posting to `SLACK_WEBHOOK_URL`.
  - [`cmd_notify_jira(approval_mode)`](./finops_agent.py): Delegates to `jira_hook.create_change_ticket()` (action `create-jira-ticket`).
  - [`error_budget_minutes(slo_percent, days=30)`](./finops_agent.py): Calculates allowable downtime/delay minutes for a given SLO over a rolling time window. Refuses 100% SLOs as invalid.
  - [`error_budget_burn(slo_percent, consumed_minutes, window_hours=720)`](./finops_agent.py): Calculates error budget burn percentage and returns governance state (`healthy`, `at_risk`, or `feature_freeze`) plus 24h burn rate alerts.
  - [`consumed_minutes_from_runs(total_runs, failed_runs, run_interval_minutes=60)`](./finops_agent.py): Converts failed batch pipeline runs into consumed downtime minutes.
- **Inputs/Outputs:**
  - *Inputs:* CLI flags (`--cost`, `--anomalies`, `--correlate`, `--notify-slack`, `--notify-jira`, `--approval-mode`), environment variables (`SLACK_WEBHOOK_URL`, `JIRA_PROJECT_KEY`).
  - *Outputs:* Formatted stdout console reports or written JSON ticket files in `.agents/logs/`.
- **Failure Modes:**
  - Returns `False` if provider API calls fail or if approval is denied during side-effect execution (`--notify-*`).
- **Architectural Role:** Acts as the live FinOps inspection and alerting agent, ensuring mutating notification side-effects pass through `approval.py`.

---

### 3. `core/reporting/health_checker.py`
- **File Link:** [`core/reporting/health_checker.py`](./health_checker.py)
- **Exact Purpose:** Performs post-deployment smoke tests and environment health diagnostics by checking AWS CLI presence, STS caller identity, S3 bucket accessibility (`s3api head-bucket`), and Glue job execution states (`glue get-job-runs`).
- **Key Functions/Classes:**
  - [`check_aws_cli()`](./health_checker.py): Verifies `aws` CLI binary availability and version.
  - [`check_s3_bucket(bucket_name)`](./health_checker.py): Probes S3 bucket accessibility via `aws s3api head-bucket`.
  - [`check_glue_job_status(job_name)`](./health_checker.py): Queries Glue job run history to check if the last state was `SUCCEEDED`, `RUNNING`, or `STARTING`.
  - [`run_health_checks(log_dir, bronze_bucket=None, silver_bucket=None, gold_bucket=None, job_1=None, job_2=None)`](./health_checker.py): Executes all active health probes and writes `health_report.json`.
- **Inputs/Outputs:**
  - *Inputs:* Target bucket names, Glue job names, and `log_dir`.
  - *Outputs:* Writes `.agents/logs/health_report.json` and returns boolean health status.
- **Failure Modes:**
  - Returns `False` (exit code `1`) if AWS CLI is missing, credentials are invalid, or target resource probes fail.
- **Architectural Role:** Provides post-deploy smoke testing and diagnostic verification for deployed infrastructure.

---

### 4. `core/reporting/minusctl.py`
- **File Link:** [`core/reporting/minusctl.py`](./minusctl.py)
- **Exact Purpose:** The main safe CLI entry point for MinusOps operators. Wraps workflow resolution, run management, source baseline verification, policy rule promotion/demotion, plan inspection, readiness scoring, offline validation (`terraform validate`), and evidence bundle packaging without invoking mutating Terraform apply commands or un-gated cloud actions.
- **Key Functions/Classes:**
  - [`_next_steps(run)`](./minusctl.py): Determines safe next steps based on requirement completeness, decision records, and source drift status.
  - [`_readiness(run)`](./minusctl.py): Scores a run workspace against 15+ enterprise readiness checks (100-point scale).
  - [`_build_package(run)`](./minusctl.py) / [`_write_package(run)`](./minusctl.py): Compiles and writes `enterprise-package.md` and `enterprise-package.json`.
  - [`_prove(run)`](./minusctl.py): End-to-end evidence harness verifying audit-chain integrity (`audit_chain.verify()`), offline governance, and readiness. Writes `evidence.md` and `evidence.json`.
  - [`main(argv=None)`](./minusctl.py): Dispatches subcommands (`create`, `policy`, `runs`, `guard`, `reports`, `next`, `package`, `readiness`, `conformance`, `validate`, `decision`, `accelerator`, `prove`, `audit`, `demo`, `doctor`, `adopt`, `seed`). **`seed --execute` is the one subcommand that reaches AWS**; the module docstring records that exception explicitly rather than leaving the old "does not run cloud CLIs" claim standing.
- **Inputs/Outputs:**
  - *Inputs:* CLI subcommands and options (`--run`, `--json`, `--dir`, `--strict`, `--by`, `--reason`).
  - *Outputs:* Formatted stdout output or generated run artifacts (`enterprise-package.md`, `evidence.json`, etc.).
- **Failure Modes:**
  - Returns non-zero exit codes when readiness or conformance checks fail in `--strict` mode, or when audit chain verification fails (`minusctl audit verify`).
- **Architectural Role:** The primary operator interface for managing run workspaces, verifying readiness, and inspecting governance posture safely.

---

### 4a. `core/reporting/doctor.py`
- **File Link:** [`core/reporting/doctor.py`](./doctor.py)
- **Exact Purpose:** Cross-platform pre-flight diagnostics for the local MinusOps environment, bound to `minusctl doctor [--json]` (MINUS-107). Supersedes [`tools/doctor.ps1`](../../tools/doctor.ps1), which only ran under Windows PowerShell and therefore could not run in CI containers, on macOS, or on Linux.
- **Key Functions/Classes:**
  - `diagnose()`: Runs every check and returns `{"ok": bool, "checks": [{"name", "status", "detail", "fix"}]}`. `ok` is `False` if and only if at least one check is `error`.
  - `format_result(result)`: Renders the `[OK]/[WARN]/[ERR]` text report plus a closing `environment ready` / `blocked on: …` line.
  - `main(argv)`: CLI entry point; exit code `0` when `ok`, `1` otherwise.
  - Private checks: `_python_check`, `_cli_check` (terraform, aws, opa, tflint), `_credentials_check`, `_scanner_check`, `_packages_check`.
- **Inputs/Outputs:**
  - *Inputs:* `--json`; the ambient PATH and cloud credential chain. No arguments describe infrastructure — this command never touches a Terraform directory.
  - *Outputs:* Text or JSON to stdout. Writes nothing to disk and mutates nothing.
- **Status semantics (the exit code is bound to these):**
  - `ok` — present and usable.
  - `warn` — a feature degrades but the governed-deploy loop still works: no `opa` (the Rego gate degrades to warn-only), no `checkov`/`trivy` (`MINUS_POLICY_MODE=production` requires one), no `tflint` (provider-level lint findings are skipped), missing `dash`/`plotly` (dashboard only), or **long-term / root credentials**, which "work" but let an unattended auto-approve run mutate real infrastructure.
  - `error` — the loop cannot run: no `terraform`, no AWS CLI, or no valid credentials.
- **Failure Modes:**
  - A tool present on PATH but unable to execute (wrong architecture, broken install) is reported as `ok` with a `(version probe failed: …)` detail rather than crashing the whole run.
  - The credential probe is wrapped: any provider exception becomes an `error` check, never a traceback.
- **Architectural Role:** The pre-flight step of the governed-deploy loop. Reuses [`toolpath.find_tool`](./toolpath.py) for discovery (which refreshes PATH from the Windows registry first) and reaches credentials only through [`providers.base.get_provider()`](../providers/base.py), never by shelling out to `aws` directly, per AGENTS.md §1. Imports `EXTERNAL_SCANNERS` from [`optimize_analyzer.py`](./optimize_analyzer.py) so the scanner list has exactly one definition.
- **Tests:** [`tests/test_doctor.py`](../../tests/test_doctor.py).

---

### 4b. `core/reporting/seed.py`
- **File Link:** [`core/reporting/seed.py`](./seed.py)
- **Exact Purpose:** prove an applied stack actually carries data (MINUS-113). A deployed stack that has never carried a byte is not a working pipeline, it is 30 resources that plan cleanly -- the 2026-08-17 run scored 100/100 readiness while its Glue job crashed on its first argument, because nothing ever ran it.
- **Three steps, in the order things break:** `_upload` (Bronze is empty, so nothing downstream can be true) -> `_run_job` (the job exits on missing arguments, MINUS-109, or 403s on write, MINUS-108) -> `_query` (Athena has no catalog database or no rows, MINUS-110).
- **Safety contract:**
  - Default is **plan**: prints the exact AWS CLI commands and sends nothing. `--execute` is required to act.
  - `--execute` requests **one** approval through [`approval.py`](../governance/approval.py) naming every side effect (bucket, job, database) -- not three prompts an operator learns to click through. Gatekeeper by default, fail-closed without a TTY, audited either way.
  - Bucket names come from `terraform output`, never re-derived from `name_prefix`: they contain the AWS account id and the run hash, and string surgery there seeds the wrong bucket and reports success.
- **Failure semantics:** a Gold table that is queryable but **empty raises**, because the transform ran and produced nothing -- reporting that as success is the false green this command exists to prevent. A failed Glue run surfaces AWS's own `ErrorMessage` verbatim, since `SystemExit` and `AccessDenied` are different diagnoses.
- **Tests:** [`tests/test_seed_adopt.py`](../../tests/test_seed_adopt.py).

---

### 4c. `core/reporting/adopt.py`
- **File Link:** [`core/reporting/adopt.py`](./adopt.py)
- **Exact Purpose:** bring an existing (brownfield) Terraform directory under governance (MINUS-106). Enterprises do not start from an empty directory; the question is "what is in here, and what has to change before the gate accepts it".
- **Read-then-anchor, in that order:** `inventory()` (resources, modules, providers, backends, parsed from source rather than state -- adoption happens before anyone has been trusted with the state file) -> `optimize_analyzer.scan_hcl_files` -> optional `source_guard.write_baseline`.
- **`--anchor` is opt-in and is the only write.** Anchoring claims what is on disk is the reviewed starting point; doing it automatically during a look-around would silently bless whatever was there, including the wildcard IAM policy the scan is about to report.
- **Reports stateful types separately** (`aws_s3_bucket`, `aws_rds_cluster`, `aws_kms_key`, ...): destroy there is data loss, not an inconvenience, and that decides how carefully the first governed plan has to be read.
- **`ok` is False when SEC findings exist.** The production-mode gate blocks on them, so calling the adoption successful would set up a surprise later.
- Never touches AWS, never runs Terraform, never modifies a `.tf` file.
- **Tests:** [`tests/test_seed_adopt.py`](../../tests/test_seed_adopt.py).


---

### 4d. `core/reporting/cli_diagnostics.py`
- **File Link:** [`core/reporting/cli_diagnostics.py`](./cli_diagnostics.py)
- **Exact Purpose:** format failures for the agent that is usually reading them (MINUS-157..160). An agent cannot infer "run the previous step" from `FileNotFoundError: requirements.json`, so every failure answers three questions in a fixed order: what failed, why, and the literal next command.
- **Why it is not inside `minusctl.py`:** the run lifecycle spans two entry points -- `minusctl` owns create/next/readiness, `plan_gate` owns plan/approve/apply. A prerequisite check living in only one of them can intercept only half the mistakes. Both import this module.
- **Key Functions:**
  - `format_agent_error(title, reason, fix_command, context=None)` / `fail(...)`: the three-part block. `fix_command` must be copy-pasteable as written -- callers resolve run ids and paths first, because a "fix" containing a placeholder is a fourth problem.
  - `suggest_runs(run_id)` / `resolve_run_or_fail(run_id=None, command=...)`: `difflib` fuzzy match at `_FUZZY_CUTOFF = 0.6`, capped at `_MAX_SUGGESTIONS = 3`. The cutoff is deliberately narrow: at a looser value two run ids sharing a date prefix "match" while differing everywhere else.
  - `get_run_description_tip(run_root)` / `describe_run(run)` / `recent_runs(limit)`: attach the run's own prompt text to a suggestion, ANSI- and control-character sanitized (`_CONTROL`, `_TIP_MAX`).
  - `missing_prerequisite(run_root, ...)` / `require_stage(run, command, ...)` / `missing_plan_prerequisite(tf_dir)`: walk `_LIFECYCLE` and intercept a command whose prior stage never produced its artifact.
  - `epilog(examples, requires=(), produces=(), next_step="")`: argparse epilog builder so each subcommand states its own inputs and outputs.
- **Inputs & Outputs:** *Inputs:* run ids, run roots, Terraform directories; reads `runs.py` for discovery. *Outputs:* text to stderr; returns `2` so a call site can `return fail(...)` directly.
- **Dependencies:** stdlib (`difflib`, `json`, `os`, `sys`) plus `runs.py`. No cloud calls, no writes.
- **Tests:** [`tests/test_cli_diagnostics.py`](../../tests/test_cli_diagnostics.py).

---

### 10. `core/reporting/excel_finops_generator.py`
- **File Link:** [`core/reporting/excel_finops_generator.py`](./excel_finops_generator.py)
- **Exact Purpose:** writes two `.xlsx` workbooks for two different readers: `executive_project_summary.xlsx` (one row per project -- total spend, MoM delta, percentage rise, accountable lead) and `pipeline_detailed_ledger.xlsx` (one row per pipeline and service component, with root cause and remediation).
- **No third-party dependency by construction:** the workbook is assembled as OpenXML parts (`_build_content_types_xml`, `_build_workbook_xml`, `_build_styles_xml`, `_build_sheet_xml`) and zipped with stdlib `zipfile`. This keeps the base install dependency-free, matching the README's stdlib-only claim; adding `openpyxl` would break it.
- **Key Functions:**
  - `generate_executive_project_summary_excel(output_path, project_records)`
  - `generate_pipeline_detailed_ledger_excel(output_path, pipeline_records)`
  - `generate_both_enterprise_reports(reports_dir)`: writes both and returns their paths.
  - Private OpenXML part builders plus `_col_name(n)` for column letters.
- **Inputs & Outputs:** *Inputs:* record dictionaries supplied by the caller, output directory. *Outputs:* two `.xlsx` files.
- **Dependencies:** imported lazily inside [`finops_agent.py`](./finops_agent.py) so the import cost is paid only on the export path. It renders numbers it is given; it does not query AWS and does not derive cost -- BCM and Cost Explorer remain the only sources of reportable figures.

---

### 5. `core/reporting/optimize_analyzer.py`
- **External tooling (MINUS-137):** `run_external_scanners()` runs `checkov`, `trivy`, and `tflint` when each is on PATH, merging their findings under `External:<tool>`. `EXTERNAL_SCANNERS = ("checkov", "trivy")` deliberately **excludes tflint**: TFLint lints provider-level correctness (invalid instance types, deprecated arguments, unused declarations), not compliance, so having it installed must never satisfy `--policy-mode production`'s scanner requirement -- otherwise a linter silently replaces the security gate. TFLint exit codes 1 and 2 both still emit valid JSON and are not treated as run failures (same reasoning as Trivy's exit 32); only unparseable or absent stdout becomes a `POLICY-EXT` scanner error. Without `tflint --init` the AWS ruleset plugin is absent and only the built-in terraform rules run, which is useful rather than a hard prerequisite.
- **File Link:** [`core/reporting/optimize_analyzer.py`](./optimize_analyzer.py)
- **Exact Purpose:** Per-resource HCL static scanner evaluating Terraform configurations for security (`SEC-*`), cost (`COST-*`), observability (`OBS-*`), and data performance (`DATA-*`) vulnerabilities. Optionally invokes external scanners (`checkov`, `trivy config`).
- **Key Functions/Classes:**
  - [`resource_blocks(content)`](./optimize_analyzer.py) / [`data_blocks(content)`](./optimize_analyzer.py): Robust brace-matching AST-style parsers extracting resource and data blocks from HCL text.
  - [`scan_hcl_files(source_dir)`](./optimize_analyzer.py): Scans all `.tf` files under `source_dir` against native rules (e.g. S3 Public Access Block `SEC-01`, wildcard IAM `SEC-02`, unencrypted Redshift `SEC-03`, unencrypted MSK `SEC-04`, cross-account External ID `SEC-05`, cross-region data transfer `COST-04`, missing S3 VPC endpoint `COST-05`, Glue job bookmarks `DATA-01`, Glue partitioning `DATA-02`, Athena scan cutoff `DATA-03`).
  - [`run_external_scanners(source_dir, required=False)`](./optimize_analyzer.py): Executes `checkov` and `trivy config` if present on PATH and merges findings.
  - [`blocking_findings(findings, external_blocking=False)`](./optimize_analyzer.py): Filters findings that must block deployment (`SEC-*` native rules, or external findings in production mode).
  - [`generate_report(findings, output_dir)`](./optimize_analyzer.py): Writes `optimization_report.md`.
- **Inputs/Outputs:**
  - *Inputs:* `source_dir` containing HCL files, `--policy-mode` (`dev` or `production`).
  - *Outputs:* Writes `optimization_report.md` and returns exit code `2` if blocking findings exist.
- **Failure Modes:**
  - Returns exit code `2` if `SEC-*` findings are detected or if external scanners are required but missing in production mode.
- **Architectural Role:** Formulate the native static analysis and policy-enforcement layer invoked by `plan_gate.py verify` before planning.

---

### 6. `core/reporting/plan_inspector.py`
- **File Link:** [`core/reporting/plan_inspector.py`](./plan_inspector.py)
- **Exact Purpose:** Plan explorer and drift detection engine for generated reports. Inspects `plan.json` and `manifest.json` under report directories to extract service breakdowns, resource lists, IAM roles, source file hashes, and unified source diffs.
- **Key Functions/Classes:**
  - [`load_report(report_id)`](./plan_inspector.py): Locates and loads `manifest.json` and `plan.json` for a report hash or `latest`.
  - [`resource_rows(plan)`](./plan_inspector.py): Extracts normalized resource change rows (`address`, `type`, `name`, `action`, `after`, `owner_file`).
  - [`services(plan)`](./plan_inspector.py): Groups plan resource rows by service display name using `pricing_catalog.service_display_name()`.
  - [`iam_roles(plan)`](./plan_inspector.py): Extracts IAM roles, policies, and policy attachments.
  - [`source_hashes(source_dir)`](./plan_inspector.py): Computes SHA-256 digests for source files.
  - [`write_source_snapshot(source_dir, report_dir)`](./plan_inspector.py): Copies non-secret source files into `source_snapshot/` and writes `source_hashes.json`.
  - [`source_status(report_id)`](./plan_inspector.py): Compares `source_hashes.json` against current disk state to report `CURRENT` or `STALE`.
  - [`diff_source(report_id)`](./plan_inspector.py): Generates unified diffs between `source_snapshot/` and current disk source files.
- **Inputs/Outputs:**
  - *Inputs:* `report_id` (or `latest`), Terraform source directories.
  - *Outputs:* Formatted JSON or text reports for `list`, `services`, `resources`, `roles`, `files`, and `diff`.
- **Failure Modes:**
  - `FileNotFoundError`: Missing report directory or `manifest.json`.
- **Architectural Role:** Enables human-readable inspection of Terraform plans and enforces source drift detection across saved report bundles.

---

### 7. `core/reporting/reporter.py`
- **File Link:** [`core/reporting/reporter.py`](./reporter.py)
- **Exact Purpose:** Core report generation engine. After a `terraform plan -out=tfplan`, it creates a versioned, plan-hash-keyed report bundle in `artifacts/reports/<hash[:12]>/` or `runs/<run-id>/reports/<hash[:12]>/`. Generates SVG architecture diagrams, dataflow diagrams, HTML reports, and headless-browser PDF documents.
- **Key Functions/Classes:**
  - [`generate(dir_)`](./reporter.py) / [`generate_from_plan_json(dir_, plan_json_path, template=None)`](./reporter.py): Main entry points loading `tfplan` / `plan.json` and calling `_generate_report_bundle()`.
  - [`plan_hash(data)`](./reporter.py): Computes canonical SHA-256 hash over plan `resource_changes` and `output_changes` (matches `plan_gate.py`).
  - [`build_svg(rows, template, cloud, short_hash, ts, findings=None, plan=None)`](./reporter.py): Renders tiered architecture diagram (`architecture.svg`) with encryption markers and finding overlays.
  - [`build_pipeline_flow_svg(rows, template, cloud, short_hash, ts, findings=None, plan=None)`](./reporter.py): Specialized medallion flow renderer for `aws-data-pipeline-standard`.
  - [`build_dataflow_svg(rows, template, cloud, short_hash, ts, findings=None, plan=None, ...)`](./reporter.py): Renders v3 lakehouse data-flow diagram (`dataflow.svg`) with capacity annotations from BCM pricing.
  - [`build_html(...)`](./reporter.py): Assembles 13-section comprehensive HTML report (`report.html`).
  - [`build_cost_html(template, cloud, short_hash, ts, cost)`](./reporter.py): Assembles detailed standalone cost report (`cost.html`).
  - [`build_inspect_html(manifest, plan, report_files=(), drift_status="CURRENT", diff_text="", ...)`](./reporter.py): Assembles printable inspection report (`inspect.html`).
  - [`render_pdf(html_path, pdf_path)`](./reporter.py): Renders PDFs via Chrome/Edge DevTools Protocol (CDP) printToPDF with fallback to a built-in pure-Python PDF generator (`_write_builtin_pdf`).
  - [`refresh_cost(report_dir)`](./reporter.py): Re-renders cost HTML/PDF and updates `manifest.json` after BCM pricing completes.
- **Inputs/Outputs:**
  - *Inputs:* Terraform directory with `tfplan` or `plan.json`.
  - *Outputs:* Generates immutable report folder containing `manifest.json`, `plan.json`, `architecture.svg`, `dataflow.svg`, `report.html`, `cost.html`, `plan.pdf`, `cost.pdf`, `inspect.pdf`, and updates `INDEX.md`.
- **Failure Modes:**
  - Returns `None` / exit code `1` if `terraform show -json tfplan` fails or `plan.json` is unparseable.
- **Architectural Role:** The central reporting engine of MinusOps, turning raw binary Terraform plans into immutable, versioned, multi-format audit bundles.

---

### 8. `core/reporting/runs.py`
- **File Link:** [`core/reporting/runs.py`](./runs.py)
- **Exact Purpose:** Manages isolated run workspaces under `runs/<run-id>/`, and maintains the central registry that makes the set of runs readable from one file.
- **Two id shapes, both valid (PRD-ARCH-2026-005, FR-01):** a run created with a `name` gets `<domain>-<name>-<orchestrator>_<YYYYMMDD_HHMMSS>`; a run created without one keeps the original `<YYYYMMDD-HHMMSS>-<blueprint>`. Nothing parses an id to find a run — [`list_runs()`](./runs.py) discovers workspaces by the presence of `run.json` — so the two coexist with no migration and no aliasing.
- **Key Functions/Classes:**
  - [`new_run(blueprint, request, cloud, name, domain, orchestrator, owner, target_repo)`](./runs.py): Creates the directory tree (`terraform/`, `reports/`, `bcm/`), writes `run.json`, then calls `sync_index()`.
  - [`sync_index()`](./runs.py): Rebuilds `runs/index.json` and `runs/INDEX.md` from the `run.json` files on disk. Rebuilt rather than appended, so a hand-deleted run drops out and a corrupt `run.json` costs only its own row.
  - [`_atomic_write(path, text)`](./runs.py): Temp file in the same directory plus `os.replace`. Two runs created in parallel both rewrite the registry; an in-situ write lets a reader catch a truncated `index.json`.
  - [`list_runs()`](./runs.py) / [`latest_run()`](./runs.py) / [`get_run(run_id)`](./runs.py): Discovery by exact id or prefix.
- **The cost column is null, never zero.** `estimated_monthly_cost` is reported only from evidenced BCM figures carried on the run. A `0.0` default would read as "this pipeline is free" on the one page executives open. Same doctrine as [`core/cost/budget_calculator.py`](../cost/budget_calculator.py).
- **Inputs/Outputs:**
  - *Inputs:* Blueprint name, user request string, active cloud provider, optional semantic metadata.
  - *Outputs:* Workspace directory tree, `run.json`, `runs/index.json`, `runs/INDEX.md`.
- **Failure Modes:** Returns empty list or `None` if `runs/` does not exist or `run.json` is corrupted; a corrupt run is skipped by both `list_runs()` and the registry rather than failing either.
- **Architectural Role:** Workspace isolation for synthesized Terraform, BCM payloads, and deploy report bundles — plus the one enumerable index of everything MinusOps has generated.
- **Tests:** [`tests/test_runs.py`](../../tests/test_runs.py).

---

### 8b. `core/reporting/export.py`
- **File Link:** [`core/reporting/export.py`](./export.py)
- **Exact Purpose:** The handover from control plane to domain repository (PRD-ARCH-2026-005, FR-03/FR-04). Copies `terraform/`, `dags/`, `scripts/`, `configs/` from a run into `<target-repo>/<dest-dir>/`, and optionally writes `<target-repo>/.github/workflows/<pipeline>-deploy.yml`.
- **What it does NOT copy is part of the contract:** `reports/`, `bcm/` and `run.json` are control-plane evidence. Shipping them would couple the domain team to a tool they do not run. What lands is plain Terraform that `terraform init && terraform apply` handles with no MinusOps runtime present (NFR-01).
- **Key Functions/Classes:**
  - [`export_run(run_root, target_repo, dest_dir, generate_workflow, pipeline_name, region)`](./export.py): Validates, copies, optionally renders the workflow, audits, returns a manifest.
  - [`_resolve_dest(target_repo, dest_dir)`](./export.py): `--dest-dir` is operator-typed and joined onto a repo root. Checked against the *resolved real path*, because `os.path.join` silently discards the root when the second argument is absolute and `..` walks out of it.
  - [`_safe_name(value)`](./export.py): The pipeline name becomes a filename under `.github/workflows/`; anything outside `[A-Za-z0-9][A-Za-z0-9._-]*` can forge a path.
  - [`_copy_tree(src, dest)`](./export.py): Replaces rather than merges. A resource dropped from the run must disappear from the domain repo — a leftover `.tf` file is one `terraform apply` away from recreating infrastructure the architecture no longer declares.
  - [`_audit(manifest)`](./export.py): NFR-03. Never raises: the files are already on disk by then, so failing here would report a failure that did not happen.
- **Local file copy only.** No AWS call, no Terraform invocation. The mutating path stays behind [`plan_gate.py`](../governance/plan_gate.py).
- **Dependencies:** [`core/generation/cicd.py`](../generation/cicd.py) for `render_pipeline_workflow`; `core/governance/audit_logger.py` lazily.
- **Tests:** [`tests/test_export.py`](../../tests/test_export.py), plus the CLI path in [`tests/test_minusctl.py`](../../tests/test_minusctl.py).

---

### 9. `core/reporting/toolpath.py`
- **File Link:** [`core/reporting/toolpath.py`](./toolpath.py)
- **Exact Purpose:** Performs cross-platform, environment-safe discovery of external CLI binaries (`terraform`, `aws`, `checkov`, `trivy`, headless browsers). On Windows, it refreshes process PATH from the System/User registry to discover newly installed tools (e.g. via WinGet or MSI).
- **Key Functions/Classes:**
  - [`find_tool(name, extra_candidates=())`](./toolpath.py): Finds absolute path to an executable by searching PATH and standard installation paths (e.g. `Program Files`, WinGet package directories).
  - [`ensure_external_tools()`](./toolpath.py): Idempotent initialization ensuring PATH includes Windows registry additions.
  - [`_refresh_windows_path()`](./toolpath.py): Reads Windows registry keys (`HKLM` / `HKCU`) to update `os.environ["PATH"]`.
- **Inputs/Outputs:**
  - *Inputs:* Command name string (e.g. `"terraform"`, `"aws"`).
  - *Outputs:* Absolute file path string or `None`.
- **Failure Modes:** Gracefully catches registry read errors on restricted environments and falls back to standard `shutil.which`.
- **Architectural Role:** Guarantees reliable CLI binary discovery across Windows and POSIX environments without hardcoding user home paths.

---

## Subsystem Architecture & Execution Pipeline

```
                     ┌───────────────────────────────┐
                     │   Operator Request / Intent   │
                     └──────────────┬────────────────┘
                                    │
                                    ▼
                     ┌───────────────────────────────┐
                     │  core/reporting/minusctl.py   │
                     └──────┬─────────────────┬──────┘
                            │                 │
           ┌────────────────┘                 └────────────────┐
           ▼                                                   ▼
┌──────────────────────────────┐                   ┌──────────────────────────────┐
│  core/reporting/runs.py      │                   │optimize_analyzer.py (scan)   │
└──────────┬───────────────────┘                   └──────────────┬───────────────┘
           │                                                      │
           ▼                                                      ▼
┌──────────────────────────────┐                   ┌──────────────────────────────┐
│  Terraform Plan (tfplan)     │                   │  plan_gate.py (verify/plan)  │
└──────────┬───────────────────┘                   └──────────────┬───────────────┘
           │                                                      │
           └───────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │ core/reporting/reporter.py   │
                    └──────────────┬───────────────┘
                                   │
      ┌────────────────────────────┼────────────────────────────┐
      ▼                            ▼                            ▼
┌──────────┐                 ┌──────────┐                 ┌──────────┐
│ SVG/HTML │                 │ PDF/JSON │                 │Manifest  │
└──────────┘                 └──────────┘                 └──────────┘
```

1. **Run Initialization:** `minusctl create` invokes `workflow.resolve_to_run()`, which uses `runs.py` to create `runs/<run-id>/`.
2. **Pre-Plan Optimization:** `optimize_analyzer.py` performs AST-style per-resource static scans over HCL code, producing `optimization_report.md`.
3. **Plan & Report Bundle:** Once `terraform plan -out=tfplan` executes via `plan_gate.py`, `reporter.py` reads `tfplan`, generates `architecture.svg`, `dataflow.svg`, `report.html`, `cost.html`, invokes CDP to render `plan.pdf` / `cost.pdf` / `inspect.pdf`, and creates `manifest.json`.
4. **Drift & Inspection:** `plan_inspector.py` calculates SHA-256 hashes of source files into `source_hashes.json` and monitors for post-plan source drift.
