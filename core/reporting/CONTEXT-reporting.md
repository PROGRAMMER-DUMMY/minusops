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
- [`core/reporting/incident_diagnostics.py`](./incident_diagnostics.py) — Turns a raw failure into the four-part resolution report: evidence, root cause, evaluated alternatives, next command, and an impact-driven severity with the routing it implies (PRD v11 FR-05).
- [`core/reporting/serving.py`](./serving.py) — Concrete serving endpoints for the four consumption archetypes, emitted only for infrastructure the stack actually provisioned.
- [`core/reporting/toolpath.py`](./toolpath.py) — Cross-platform discovery utility for external CLIs (`terraform`, `aws`, headless browsers) without hardcoding user home paths.
- [`core/reporting/cli_diagnostics.py`](./cli_diagnostics.py) — Agent-facing failure formatting: fuzzy run-id resolution, lifecycle stage interception, and the three-part `WHAT FAILED / WHY IT FAILED / ACTION REQUIRED` error (MINUS-157..160).
- [`core/reporting/excel_finops_generator.py`](./excel_finops_generator.py) — Dual-tier FinOps `.xlsx` writer (executive summary + engineering ledger) built on stdlib `zipfile` + OpenXML, no third-party dependency.
- [`core/reporting/drawio_generator.py`](./drawio_generator.py) — Draw.io architecture diagrams from a plan: editable mxGraphModel XML, a 1-click deflated browser URL, and the declared-hop ledger.
- [`core/reporting/diagram_check.py`](./diagram_check.py) — Static verification of a generated canvas: dangling edges, escaped containment, overlapping siblings, off-page cells, and the verdict those earn.
- [`core/reporting/stencil_data/`](./stencil_data/) — draw.io's `mxgraph.aws4` shape names, and the script that refreshes them. Names only; no vendor artwork is committed.

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
- **The arrows in `architecture.svg` are the same derivation the draw.io canvas uses**
  ([`declared_hops`](./reporter.py) -> [`discover_data_edges`](./drawio_generator.py)). This
  file built its own: `_pipeline_flow` matched resource NAMES (a bucket whose instance key was
  "bronze", a Glue job called `bronze_to_silver`), `build_pipeline_flow_svg` joined the whole
  slot chain source -> bronze -> glue1 -> silver -> glue2 -> gold -> athena -> results
  whenever the slots were filled, and `_generic_flow` connected the first node of consecutive
  tiers so the picture would have arrows in it. All three are the defect deleted from
  `drawio_generator` in 14ab3f1, and they shipped in an artifact `minusctl` lists as required
  and the console renders under "01 Topology". `_pipeline_flow` was additionally unreachable:
  `build_svg` returns early for the template it was branched on.
- **The layout is still a fixed set of slots; which of them are JOINED is read from the plan.**
  A stack whose Glue job names no source or target path now draws no arrows, which is the
  correct picture of a stack that declares no data path.
- **`tests/test_reporter.py` asserted the fabrication.** `test_pipeline_flow_draws_real_anchored_edges`
  required arrows from a fixture that declares none, so it could only pass while the edges
  were invented. It is now two tests and a fixture that states its hops.
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

### 8c. `core/reporting/seed.py` -- the 5-hop proving harness
- **File Link:** [`core/reporting/seed.py`](./seed.py)
- **Exact Purpose:** Proves a deployed stack actually carries data. A stack that plans cleanly,
  applies cleanly and scores full readiness can still be thirty resources that have never moved a
  byte.
- **Two entry points, one set of hops:** [`seed()`](./seed.py) is the original three-step proof
  behind `minusctl seed`; [`prove_pipeline()`](./seed.py) (PRD-ARCH-2026-007, FR-01) runs all five
  behind `minusctl prove --execute`. Both call the same `_upload` / `_run_job` / `_query`
  primitives, so each hop has one implementation.
- **The hop registry (PRD v11 FR-03):** [`HOPS`](./seed.py) is a dict of `_HopSpec` records --
  a lookup table, not an abstract base class with eight subclasses. The hops were already
  functions; composability needs a registry, and `core/` stays standard-library-only.
  `minusctl prove --hops ingest,transform` runs a subset.
  - **`blocking`** answers the failure-strategy question. A blocking hop failing stops the
    rest, because querying Gold after a failed transform returns stale data that reads as
    success. `latency_sla` is non-blocking: a pipeline over budget is slow, not wrong.
  - **`requires`** exists because `quarantine` genuinely consumes `query`'s output -- it
    reconciles injected == gold + quarantined. Selected without `query` the Gold count would
    be absent and the hop would report "1000 injected, 0 reached Gold, 1000 unaccounted for",
    a confident FALSE failure. That selection is refused instead.
  - **Required terraform outputs follow the SELECTION**, not the catalog
    ([`_HOP_OUTPUTS`](./seed.py)): demanding `dq_job_name` before running `ingest,transform`
    would refuse a proof that touches nothing in the data-quality module. The default is
    still all five, so a three-hop stack is refused for the full proof exactly as before.
  - **[`coverage`](./seed.py) is signed alongside the hops.** Every catalog entry appears with
    `PASS`, `FAIL` or `NOT_RUN`. Without it a two-hop run reads like a five-hop one, and the
    signature would attest to coverage the proof never had.
- **The five hops, in the order they break:**
  1. `bronze_ingestion` -- Bronze is empty, so nothing downstream can be true.
  2. `spark_glue_etl` -- the job exits on missing arguments, or 403s when it writes.
  3. `great_expectations_dq` -- the suite never ran, or it ran and reported failures.
  4. `quarantine_verification` -- malformed rows were dropped instead of quarantined.
  5. `athena_serving_query` -- Athena has no catalog database, or the table has no rows.
- **Hop 3 does not import Great Expectations.** GE runs inside the Glue Python-shell job that
  [`modules/dq-great-expectations`](../../modules/dq-great-expectations/main.tf) deploys, which is
  where the data is. This harness starts that job and reads the validation-result JSON it wrote.
  Adding GE (and pandas, and SQLAlchemy) to a control plane whose base install has no runtime
  dependencies would be a heavy price for assertions that already run server-side.
- **Hop 4 is the arithmetic hop.** Injected records must equal Gold rows plus quarantined rows. A
  transform that DROPS malformed rows leaves Gold looking clean and the count looking plausible;
  nothing else in the harness catches it. Because it needs hop 5's Gold count, it is evaluated
  after hop 5 and re-inserted at position 4 so the report reads in pipeline order.
- **A failed hop stops the ones downstream.** Querying Gold after the transform failed returns a
  stale-data answer that reads as success.
- **A failed proof still writes its report.** Evidence of failure is evidence; a report that
  exists only on success cannot be used to argue against a deploy.
- **"Signed" means tamper-evident, not authenticated.** [`_sign()`](./seed.py) is a SHA-256 over
  the canonical payload and [`verify_report()`](./seed.py) re-derives it, so an edited hop no
  longer matches its own digest. It proves the file changed, not who changed it.
- **Still the one mutating command.** Default is plan; `--execute` routes through
  [`approval.py`](../governance/approval.py) with a single prompt naming every side effect.
- **Tests:** [`tests/test_proving_harness.py`](../../tests/test_proving_harness.py) and
  [`tests/test_seed_adopt.py`](../../tests/test_seed_adopt.py).

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


---

### 8d. `core/reporting/incident_diagnostics.py` (PRD v9 FR-01..04)
- **File Link:** [`core/reporting/incident_diagnostics.py`](./incident_diagnostics.py)
- **Exact Purpose:** A Terraform apply error, a YARN OOM kill and a Great Expectations
  assertion failure are three opaque stack traces that all end with an engineer guessing.
  This turns each into evidence, root cause, alternatives with trade-offs, and the exact next
  command.
- **Severity is computed, never looked up (PRD v11 FR-05).** The rejected design mapped the
  alert's SOURCE to a fixed level and channel -- outage P1, data quality P2, FinOps P3. Two of
  this module's own rules break under it: `DQ-QUARANTINE-01` is silent, unrecoverable data loss
  that the mapping caps at a Teams message, and `TF-IAM-CONSISTENCY-01` is a self-healing retry
  that it pages someone for at 3am. Over-paging and under-reacting from one table.
  [`assess_severity()`](./incident_diagnostics.py) instead applies, in order: a regulated-data
  override to P1, the run's declared asset tier as the baseline, one level worse for a silent
  failure (wrong-but-plausible output has already been acted on), and one level better for a
  transient one. [`ROUTES`](./incident_diagnostics.py) is keyed on the computed severity, so a
  P1 cost anomaly pages exactly like a P1 outage.
  - **An undeclared tier yields `UNCLASSIFIED`, not a default.** Same doctrine as the
    unmatched-error path: business impact cannot be established, and a severity nobody can
    justify from declared facts is worse than none because it looks authoritative enough to act
    on. The tier comes from the run record's `tier` field; nothing infers one from a run's shape.
  - **Severity is assessed even when no rule matched.** "We do not know what broke" and "we do
    not know how much it matters" are separate questions, and a PII exposure is a P1 whether or
    not a signature recognised the stack trace.
- **Declarative rule table** ([`FAILURE_RULES`](./incident_diagnostics.py)), per Matt's ruling
  of 2026-08-22: a list of frozen `FailureRule` dataclasses rather than an if/elif chain, so
  adding a signature is a data edit and each rule is independently testable. Order matters --
  the first match wins -- so specific patterns sit above general ones.
- **Three refusals, each enforced by a test:**
  - **It does not guess.** An unrecognised error returns `matched: False` with the raw
    evidence and no root cause. A confident wrong diagnosis is worse than none: the report
    looks authoritative, so the engineer follows it instead of reading the error.
  - **It does not invent a price.** PRD v9 NFR-03 asked for "verified AWS pricing rates (e.g.
    $0.44/DPU-hour)". That is a us-east-1 list price, wrong in eu-west-1 and wrong after any
    repricing, and exactly the fabricated figure
    [`budget_calculator.py`](../cost/budget_calculator.py) exists to refuse. What IS durable
    is the RATIO -- a G.2X worker carries twice the DPUs of a G.1X in every region, forever --
    so options carry `cost_multiplier` and the report points at `minusctl cost estimate` for
    dollars. [`_rate_citation()`](./incident_diagnostics.py) surfaces a dated catalog citation
    if one ever exists for the service.
  - **It does not touch the network by default.** Pure regex over a string, no subprocess at
    all: this runs on a laptop with no credentials, mid-incident. Telemetry is caller-injected
    and fail-open, the same contract as [`cloud_drift`](../governance/cloud_drift.py).
- **Every rule offers at least two paths and at least one at `cost_multiplier == 1.0`.** If
  the only way out of every incident were to spend more, the engine would be a sales funnel.
- **Tests:** [`tests/test_incident_diagnostics.py`](../../tests/test_incident_diagnostics.py).

### 8e. `core/reporting/serving.py` (PRD v9 section 3)
- **File Link:** [`core/reporting/serving.py`](./serving.py)
- **Exact Purpose:** The concrete address for each of the four consumption archetypes --
  `ad_hoc_sql` (Athena JDBC), `data_warehouse` (Redshift Serverless), `semantic_layer`, and
  `reverse_etl` (the S3 stage) -- so an analyst does not reconstruct them from Terraform
  outputs by hand.
- **An endpoint is emitted only when the stack provisioned it AND every part of the address is
  known.** [`_require()`](./serving.py) drops the whole endpoint if any part is missing. A
  Redshift connection string for a stack with no Redshift fails at connect time and the
  analyst blames the tool; a half-built `jdbc:awsathena://AwsRegion=None;...` is worse, because
  it looks plausible enough to paste.
- **Nothing is reconstructed from a name_prefix.** Every value comes from `terraform
  output`-derived JSON, the same reasoning [`seed.read_outputs()`](./seed.py) carries.
- **[`as_yaml()`](./serving.py) is hand-rolled**, not PyYAML: `core/` has no runtime
  dependencies and the shape is flat and fixed. It carries addresses only -- these files are
  committed to a domain repository, so anything secret-shaped in them is a secret in git.
- **Used by:** the `[Serving Endpoints & Consumption]` card section in
  [`core/cli/commands/runs.py`](../cli/commands/runs.py) and the `connections.yaml` +
  `queries/sample_queries.sql` scaffold in [`export.py`](./export.py).
- **Tests:** [`tests/test_serving_topology.py`](../../tests/test_serving_topology.py).

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

### 11. `core/reporting/drawio_generator.py`
- **File Link:** [`core/reporting/drawio_generator.py`](./drawio_generator.py)
- **Exact Purpose:** renders one `plan.json` as a Draw.io canvas -- editable mxGraphModel
  XML, a 1-click `app.diagrams.net/#R` URL, and the hop ledger.
- **An edge is data movement, never a Terraform dependency.**
  [`discover_data_edges`](./drawio_generator.py) reads a fixed set of data-carrying
  arguments ([`_DATA_ARGUMENTS`](./drawio_generator.py)): Glue `--source_path` and
  `--target_path`, the Athena result `output_location`, the Firehose destination. When the
  value is unknown until apply, that argument's own `references` entry in `configuration`
  resolves it -- only that argument's, never the resource's reference set. Edges used to
  come from every declared reference, so the KMS key that encrypts a bucket appeared to
  send it data, and the ledger dressed each such edge in a protocol and a latency budget
  chosen by substring-matching the target address. A plan that declares no data flow now
  reports none rather than fifty guesses.
- **The ledger states hop, source and target.** Nothing else; a transport claim the plan
  does not make is the same defect as an invented price.
- **The deployment page states placement and says so.** It carries no arrows, on purpose:
  declared movement is on the Logical page and drawing it twice invites the two to disagree.
  A page of forty resources with no arrows and no explanation reads as a diagram that failed
  to find any, so [`_DEPLOYMENT_NOTE`](./drawio_generator.py) says which reading is intended
  -- the same doctrine as the logical legend's line about an absent arrow.
- **Regional services wrap into a labelled band below the VPC**, sized to a roughly square
  block. Three per row put forty services fourteen rows deep beside an empty half-canvas; a
  region is not a side column. The band's own label says these sit in the account and outside
  the VPC, which is where S3, KMS and Athena actually are.
- **One colour language, and it is AWS's.** A tile's fill is the service category AWS itself
  uses, which means the same thing on every AWS diagram anyone has read. The bands carried a
  second scheme on top of it: a Glue job is analytics purple, and inside a coloured
  PROCESSING band it was a purple tile in an orange box asserting two different things about
  one resource. Bands are structure now, drawn in one neutral grey. `_LAYER_COLORS` survives
  for the legend.
- **A node is labelled with the role that decided its band**
  ([`node_label`](./drawio_generator.py), [`_ROLE_LABELS`](./drawio_generator.py)). The icon
  already states the service, so "S3 Bucket / medallion_buckets" said the product twice and
  the purpose never. The role comes from the same `classify_role` call the layout uses, so
  the label and the placement cannot disagree -- which is exactly what happened while
  [`_role_of`](./drawio_generator.py) passed an empty instance key and every medallion zone
  read as a generic Store while being placed on the spine as a zone.
- **Cataloging and security wrap to their own width, not the spine's**
  ([`_reference_columns`](./drawio_generator.py)). Nothing in an inventory is read left to
  right, so inheriting the medallion's three columns only made those bands tall: 33 security
  resources became four columns and nine rows, and the canvas came out 800 wide by 2400 tall.
  One column count is decided for both before either is placed, so they line up with each
  other rather than each following a different neighbour.
- **`flow_bottom` measures every band placed so far.** It filtered on bands starting at the
  top row, which excluded storage -- storage sits below processing -- so the security band
  was laid straight through it. The consumption band had been stretched to the full flow
  height and was hiding the collision; sizing it to its one tile exposed it. The checker's
  `sibling_overlap` caught it.
- **The external sender is drawn level with the flow it feeds**, not with the catalog band
  above it.
- **Processing sits above storage, not beside it.** The spine alternated zone, transform,
  zone along one row, which reads as a line of tiles and says nothing about direction. Each
  transform is offset half a slot so it lands between the two zones it moves data between,
  and the hops read as a zigzag. A bucket `stage_rank` does not recognise gets a support row
  rather than the spine: an Athena results bucket sorted to the end and drew as the stage
  after Gold.
- **Bands are containers and own what they hold** ([`_reparent`](./drawio_generator.py)).
  They were decoration -- nodes at absolute coordinates that happened to fall inside them --
  so dragging a band in draw.io left its contents behind. Node geometry is now relative to
  its band, which is why the test helper resolves band origins before comparing positions
  across bands.
- **The account boundary is drawn only when there is something inside it**
  ([`_boundary`](./drawio_generator.py)). An empty AWS Cloud box is a picture of an account
  with no resources in it.
- **External senders come from the interview, never from the plan**
  ([`external_actors`](./drawio_generator.py)). A plan states the resources inside the
  account and cannot state what is outside it, so the actor is drawn because an operator
  answered pillar 1 and is labelled with their answer. With no answer there is no actor: a
  generic box captioned "Source" asserts something nobody said. It is wired to an ingestion
  resource only when there is exactly one -- with two, the plan does not say which one the
  partner reaches, and a guess there is the same fabrication as an invented hop. It carries
  no AWS stencil, because nothing outside the account is an AWS service.
- **Edges declare which side they leave and enter**
  ([`_edge_anchors`](./drawio_generator.py)). Unanchored, draw.io leaves sideways before
  turning, which routes a vertical hop through the boxes either side of it.
- **The legend says what an ABSENT arrow means** ([`_append_legend`](./drawio_generator.py)):
  the plan declares no path, not that none exists at runtime. That is the reading this canvas
  most often loses.
- **An edge carries a `kind`, and there are two.** `data` is a declared hop; `describes` is
  a catalog database's `location_uri`, a table's `storage_descriptor.location` or a Lake
  Formation registration's `arn` -- each naming the storage it records. A catalog does not
  send anything to a bucket, so the two are drawn differently: `describes` is thinner,
  dashed, open-headed, in the catalog band's purple, labelled "describes" rather than
  numbered. Numbering it would invite a reader to trace a sequence that is not one, and
  `walkthrough_steps` counts only `data` for the same reason.
- **`_BUCKET_TO_BUCKET` is for resources that name both ends and are neither.** A
  replication configuration is not a place data rests; it states that one bucket copies to
  another, so the hop is drawn between the two buckets and the configuration itself is not on
  it.
- **`command.script_location` is deliberately absent from `_DATA_ARGUMENTS`.** It appears ten
  times across the plans in `runs/`, every one pointing at the bronze bucket -- because that
  is where the SCRIPT lives. Reading it would draw "bronze feeds this job with data" about a
  bucket holding its code. A test asserts the exact edge set of a plan that carries one.
- **Three spatial roles, not six columns** ([`layout_positions`](./drawio_generator.py)),
  following the AWS analytics reference architecture: flow layers left to right, cataloging
  above the spine, security and monitoring in a full-width band beneath it that carries no
  edges. The medallion zones are ordered by
  [`architecture_model.stage_rank`](../architecture/architecture_model.py) and the
  transforms sit between the zones they move data across, so the spine is derived from
  stage and role rather than from resource names. Orchestration sits under the spine.
- **A bucket is one node.** Versioning, public-access block, SSE, lifecycle, replication and
  object-lock resources fold into the bucket they configure
  ([`fold_badges`](./drawio_generator.py)) and become badges on it, resolved through that
  resource's own `bucket` reference -- reading the bucket's attributes instead would badge
  an unencrypted bucket whenever a sibling SSE resource existed for a different one.
- **URL encoding is the published one:** `encodeURIComponent` then raw deflate then standard
  base64. `atob` rejects the URL-safe alphabet and `decodeURIComponent` throws on a bare
  percent sign, so both are round-tripped against a decoder that imitates diagrams.net.
- **Inputs/Outputs:** *Inputs:* a `terraform show -json` plan dict. *Outputs:*
  `{"xml", "url", "ledger", "ledger_markdown"}`.
- **Dependencies:** standard library plus
  [`architecture_model.py`](../architecture/architecture_model.py) for classification and
  stage ranking. No cloud call, no Terraform invocation, no third-party graphing library.
- **Failure Modes:** an unreadable plan yields an empty canvas rather than raising;
  [`parse_graph`](./drawio_generator.py) returns empty node and edge maps on unparseable XML.
- **Tests:** [`tests/test_drawio_generator.py`](../../tests/test_drawio_generator.py).

---

### 12. `core/reporting/diagram_check.py`
- **File Link:** [`core/reporting/diagram_check.py`](./diagram_check.py)
- **Exact Purpose:** decide whether a generated canvas says what it claims. draw.io renders
  whatever it is handed: an edge naming a cell that does not exist draws nothing, a child
  whose geometry escapes its container draws outside the box, and two bands at the same
  offset draw through each other. All three open cleanly and all three are wrong.
- **Severity decides the verdict**, not the check: `error` gives FAIL, `warning` gives WARN,
  `note` leaves PASS. An unlabeled edge still points somewhere real, so it is wrong without
  being broken, and a node no edge touches is frequently correct.
- **It reads every page.** [`parse_graph`](./drawio_generator.py) deliberately reads only the
  first, because reconciliation compares operator edits against the logical page. Containment
  is only expressible on the deployment page, so a checker that skipped it would verify the
  half of the document with no nesting in it.
- **Containers and their contents are never compared for overlap**
  ([`_check_overlap`](./diagram_check.py)). Nesting is the point of the deployment page;
  comparing a box with what it holds would report every correct containment as a collision.
  Siblings of the same class are compared, which is what catches two bands drawn through
  each other.
- **Geometry is resolved through the parent chain before anything is compared**
  ([`_absolute`](./diagram_check.py)). mxGraph coordinates are relative to the parent and the
  bands are nested inside the account boundary, so comparing a top-level cell's box against a
  band's own box compares two coordinate systems -- which put an external sender drawn at
  x=80, outside a boundary starting at x=290, inside the catalog band whose RELATIVE x was 20.
- **An external sender is not counted as a resource with no data movement.** It is not in the
  plan; listing it beside resources that declare no hop reads as a defect in the stack rather
  than a statement about what sits outside it.
- **Isolated nodes are grouped by the band that holds them**
  ([`_band_of`](./diagram_check.py)), read off the drawing rather than the plan. Twenty
  addresses in one list is a wall nobody reads; the band is what says whether an absent edge
  is expected -- governance carries none by design, a warehouse in the consumption band
  carrying none is a finding.
- **Two defects it found on its first run against the repository's own plans:** the logical
  page was sized before the walkthrough was appended, leaving six of seven steps past the
  bottom edge, and the deployment page was sized by a constant while its content grew from
  the resource count, running 490px over.
- **Every `mxgraph.aws4` name is checked against draw.io's published list**
  ([`stencil_data/aws4_shapes.txt`](./stencil_data/aws4_shapes.txt), 1037 names). An
  unresolvable name renders as a blank tile and reports nothing, which is how `iam`,
  `glue_crawler`, `subnets` and both partner stencils shipped: five wrong names, every IAM
  role in every diagram drawing blank. The list holds names only -- AWS Architecture Icons
  may be used to draw diagrams and may not be redistributed, so draw.io renders the icon
  from its own library and this repository ships no artwork.
- **Inputs/Outputs:** *Inputs:* `.drawio` XML text. *Outputs:*
  `{"verdict", "pages", "counts", "findings"}`, and `format_report()` renders the ASCII
  block (NFR-01).
- **Dependencies:** standard library only. It reads XML, never a plan, so it cannot inherit
  the generator's view of what the diagram was supposed to be.
- **Failure Modes:** malformed XML is itself a FAIL finding rather than an exception, because
  a checker that raises on the input it exists to reject reports nothing.
- **Tests:** [`tests/test_diagram_check.py`](../../tests/test_diagram_check.py).

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
---

### 12. `core/reporting/lineage_graph.py`
- **File Link:** [`core/reporting/lineage_graph.py`](./lineage_graph.py)
- **Exact Purpose:** dataset-to-dataset lineage for a governed medallion pipeline. The
  architecture diagram answers "what exists and how is it wired"; this answers "where does a
  record go, and what happens to it on the way". A Glue job and a bucket are one edge on the
  topology and three hops in the lineage, and it is the lineage an auditor asks for.
- **A node is emitted only when the stack provisions the thing it stands for.** Drawing the
  medallion pattern for a run with no data-quality module would put a quality gate and a
  quarantine branch on the page for controls that do not exist, and an auditor reads a
  rendered control as a control.
- **Attributes are held to the same standard as nodes.** Everything in `_NODES` is the
  PATTERN's default; a supplied plan replaces it with what that plan states -- the bucket
  name, the SSE algorithm, the lifecycle days -- and each node carries `facts_source`. A fact
  the plan never states (partitioning, table format) is dropped rather than left showing the
  pattern's value under a "plan" label.
- **Masking is the sharpest case:** `masking.enforced` is False unless Lake Formation
  actually governs the stack, because "this column is masked" is a compliance claim and the
  only thing that makes it true is a service enforcing it.
- **Tests:** [`tests/test_lineage_graph.py`](../../tests/test_lineage_graph.py).

---

### 13. `core/reporting/agent_flow_graph.py`
- **File Link:** [`core/reporting/agent_flow_graph.py`](./agent_flow_graph.py)
- **Exact Purpose:** compiles what [`agent_tracer.trace()`](../governance/agent_tracer.py)
  recorded into a directed acyclic graph the console can draw -- one node per pipeline stage,
  one edge per handoff, a per-node status from a fixed vocabulary.
- **It emits DATA, never markup.** A module returning HTML would have made the renderer
  choice on the console's behalf, and would then have to be trusted to escape everything it
  interpolates.
- **Failure Modes:** inherits the tracer's two-state rule. A stage with no audit evidence is
  NOT_RUN, never absent from the graph and never green.
- **Tests:** [`tests/test_agent_flow.py`](../../tests/test_agent_flow.py).

---

### 14. `core/reporting/vault.py`
- **File Link:** [`core/reporting/vault.py`](./vault.py)
- **Exact Purpose:** the deliverables and compliance vault -- the catalog of what a run
  produced, and the signed bundle an auditor is handed.
- **A document catalog is read as an inventory**, so its dangerous failure is not crashing:
  it is listing `proving_report.json` for a run that was never proven, because the reader
  concludes the proof exists. The catalog therefore always describes what COULD exist for a
  run and marks each entry present or absent, with a size only on the ones that are there.
- **The bundle follows the same rule:** it archives only files that exist, refuses to produce
  an empty zip rather than handing an auditor a zip full of nothing, and computes its
  manifest digests over the bytes that actually shipped. A signature over anything other than
  the archived content is worse than no signature.
- **Tests:** [`tests/test_vault.py`](../../tests/test_vault.py).
