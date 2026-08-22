# Product Requirements Document (PRD) — Enterprise Modular CLI Architecture & Run Lifecycle Governance (v6.0)

| Attribute | Details |
| :--- | :--- |
| **Document ID** | PRD-ARCH-2026-006 (Revision 6.0 — Enterprise CLI Modularization & Context-Aware Execution) |
| **Document Name** | `tasks/prd_v6_enterprise_cli_architecture.md` |
| **Status** | APPROVED ARCHITECTURE SPECIFICATION |
| **Lead Architect** | Matt (Principal Cloud Architect & CLI Design Lead) |
| **Author** | MinusOps Autonomous Architecture Engine |
| **Target Components** | `core/cli/`, `core/reporting/minusctl.py`, `core/reporting/runs.py`, `core/reporting/export.py`, `pyproject.toml` |
| **Target Runtime** | Local Developer Workstation, CI/CD Runners (GitHub Actions, Jenkins), Container Ingress |
| **Date** | August 22, 2026 |

---

## 1. Executive Summary & Problem Statement

As MinusOps transitions from single-script executions into an enterprise-grade multi-cloud governance control plane, the user and agent interaction layer must evolve to meet the gold standards established by modern developer tools such as the Databricks CLI, kubectl, and dbt.

### 1.1 Core Problems Solved
1. **Script Path Proliferation:** Currently, operators and autonomous agents must invoke individual python scripts (`python core/governance/plan_gate.py`, `python core/cost/bcm_pricing_calculator.py`, `python core/reporting/seed.py`). This leaks repository file hierarchies into external interfaces and increases cognitive load.
2. **Multi-Run Targeting & Context Switching:** Enterprises maintain dozens of pipelines across multiple business domains (`marketing`, `finance`, `adtech`). Operators require the ability to target any run directly, switch active workspaces, and inspect comprehensive pipeline attributes without typing 60-character directory paths.
3. **Two-Repository Enterprise Topology:** Clear architectural separation between the central MinusOps governance engine (Repo A) and customer domain repositories (Repo B), supporting automated export of standalone Terraform code, PySpark scripts, Airflow DAGs, and path-isolated GitHub Actions workflows.
4. **Telemetry-Correlated Cloud Drift:** Intelligently correlating out-of-band AWS Console modifications with CloudWatch failure logs (e.g. OOMs, timeouts) and CloudTrail identity to prevent accidental revert outages during deployments.
5. **No Emojis & Clean Professional Output:** All terminal interfaces, logs, and machine-readable outputs must adhere to clean, standardized, professional formatting without emojis or decorative characters.

---

## 2. Modular CLI Architecture (`core/cli/`)

The monolithic `minusctl.py` script is refactored into a modular, extensible package:

```text
core/cli/
├── __init__.py
├── main.py                         # Global CLI entrypoint and global options
├── context.py                      # Session context, project root discovery, and run resolver
├── formatters.py                   # Clean ASCII tables, attribute cards, and structured formatters
└── commands/
    ├── __init__.py
    ├── create.py                   # minusctl create (Grilling -> Synthesis -> Proving)
    ├── runs.py                     # minusctl runs (list, describe, delete, index)
    ├── use.py                      # minusctl use <run-id> (Active session context switcher)
    ├── gate.py                     # minusctl gate (verify, plan, approve, apply)
    ├── cost.py                     # minusctl cost (estimate, prepare, bcm)
    ├── prove.py                    # minusctl prove (Synthetic data seeding & hop verification)
    ├── export.py                   # minusctl export (Multi-repo packaging & isolated CI workflows)
    ├── source.py                   # minusctl source (status, diff, anchor)
    ├── audit.py                    # minusctl audit (verify, log)
    └── doctor.py                   # minusctl doctor (Pre-flight environment diagnostics)
```

---

## 3. Functional Requirements (FR)

### FR-01: Global Context Resolution & Session State
* **Active Session Persistence:** `minusctl use <run-name>` writes the active selection to `.minus/context.json`:
  ```json
  {
    "active_run": "marketing-clickstream-mwaa_20260822_111530",
    "updated_at": "2026-08-22T11:15:30Z"
  }
  ```
* **Resolution Precedence Hierarchy:** When resolving which run workspace to operate on:
  1. Explicit `--run <name>` flag provided on the command line.
  2. Active run stored in `.minus/context.json`.
  3. Upward directory discovery (if running from within a `runs/<run-id>/` subdirectory).
  4. Most recently created run (`latest`).

### FR-02: Workspace Listing (`minusctl runs list`)
* Formats all available runs in a structured tabular output.
* Highlights the currently active run with an indicator `[*]`.
* Supports filtering flags: `--domain <name>`, `--tier <dev|test|uat|prod>`, `--orchestrator <mwaa|stepfunctions>`, and `--json`.
* **Output Format:**
  ```text
  -------------------------------------------------------------------------------------------------------------------------
  Active  Run Name                                   Domain     Engine    Orchestrator   Cost/Mo    Status
  -------------------------------------------------------------------------------------------------------------------------
  [*]     marketing-clickstream-mwaa_20260822_111530 marketing  Glue 4.0  MWAA Airflow   $248.50    PROVEN_TEST
  [ ]     finance-general-ledger-sfn_20260822_113045 finance    EMR Spot  StepFunction   $412.00    PENDING_APPROVAL
  [ ]     adtech-realtime-bidding_20260821_190840    adtech     Databrcks StepFunction   $1,180.00  APPLIED_PROD
  -------------------------------------------------------------------------------------------------------------------------
  ```

### FR-03: Detailed Attribute Card (`minusctl runs describe <run-name>`)
* Extracts attributes from `run.json`, `requirements.json`, `architecture_decision.json`, `reports/`, and `bcm/`.
* Renders a comprehensive specification card covering Metadata, Architecture, FinOps, Resource Endpoints, and Artifact Paths.
* **Output Format:**
  ```text
  ====================================================================================================
  PIPELINE SPECIFICATION: marketing-clickstream-mwaa_20260822_111530
  ====================================================================================================

  [Metadata]
    Domain:             marketing
    Workload:           clickstream-lakehouse
    Owner:              marketing-data-eng@acme.com
    Created At:         2026-08-22 11:15:30 UTC
    Lifecycle Tier:     test
    Governance Status:  PROVEN_TEST (All pre-flight gates passed)

  [Architecture Attributes]
    Ingestion Source:   Webhook / EventBridge
    Storage Format:     Medallion S3 (Bronze, Silver, Gold, Quarantine)
    Table Format:       Apache Iceberg v2
    Compute Engine:     AWS Glue 4.0 (PySpark, 10x G.1X workers)
    Orchestration:      Amazon MWAA (Apache Airflow 2.8.1)
    Data Quality:       Great Expectations (Suite v3)
    Serving Layer:      Amazon Athena + AWS Glue Catalog Views

  [FinOps & Resource Endpoints]
    Estimated Spend:    $248.50 / month (AWS BCM Pricing Calculator verified)
    Region / Network:   AWS us-east-1 (VPC 10.20.0.0/16, Private Subnets, S3 Gateway Endpoint)
    Bronze Storage:     s3://acme-mktg-bronze-prod-001/
    Gold Storage:       s3://acme-mktg-gold-prod-001/
    Quarantine Storage: s3://acme-mktg-quarantine-prod-001/
    Airflow DAG Path:   s3://acme-mktg-dags-001/dags/data_pipeline_dag.py

  [Artifact Paths]
    Terraform HCL:      runs/marketing-clickstream-mwaa_20260822_111530/terraform/main.tf
    Proving Report:     runs/marketing-clickstream-mwaa_20260822_111530/reports/proving_report.json
    Decision Record:    runs/marketing-clickstream-mwaa_20260822_111530/architecture_decision.json
  ====================================================================================================
  ```

### FR-04: Deploy Gate Actions (`minusctl gate <subcommand>`)
* **Subcommands:**
  * `minusctl gate verify [--run <id>] [--policy-mode <development|production>]`: Runs format, validate, security scanner, and G6 Rego policies.
  * `minusctl gate plan [--run <id>]`: Executes `terraform plan`, computes SHA256 plan hash, runs cloud drift analysis, and records `pending_plan.json`.
  * `minusctl gate approve [--run <id>] --mfa-arn <arn> [--role-arn <role>]`: Records MFA-signed approval checkpoint bound to the plan hash.
  * `minusctl gate apply [--run <id>]`: Verifies hash integrity, applies exact plan, and revokes credentials.
  * `minusctl gate status [--run <id>]`: Displays current stage and gate verdicts.

### FR-05: Multi-Repo Packaging & Export (`minusctl export`)
* Copies self-contained pipeline assets into domain repositories:
  * Copies `terraform/` into `<target-repo>/pipelines/<name>/terraform/`.
  * Copies `src/orchestration/` into `<target-repo>/pipelines/<name>/dags/`.
  * Copies `src/compute/` into `<target-repo>/pipelines/<name>/scripts/`.
  * Copies `configs/` into `<target-repo>/pipelines/<name>/configs/`.
* Generates a dedicated, path-isolated GitHub Actions workflow in `<target-repo>/.github/workflows/<name>-deploy.yml`:
  ```yaml
  name: Deploy Marketing Clickstream Pipeline
  on:
    push:
      branches: [main, dev, uat]
      paths:
        - 'pipelines/clickstream/**'
        - '.github/workflows/clickstream-deploy.yml'
    pull_request:
      branches: [main]
      paths:
        - 'pipelines/clickstream/**'
  ```

### FR-06: Synthetic Data Proving (`minusctl prove`)
* Injects mock data through the complete pipeline:
  1. Ingest mock events into Bronze S3 bucket.
  2. Execute Spark ETL job (AWS Glue / EMR).
  3. Validate Great Expectations data quality assertions.
  4. Query Athena Gold table and verify partition counts.
  5. Write signed `reports/proving_report.json` with step-by-step latency, row counts, and error budget metrics.

### FR-07: Telemetry-Correlated Cloud Drift Intelligence
* When `minusctl gate plan` detects cloud drift on a resource (e.g. Glue worker scaling `G.1X` -> `G.2X`):
  * Queries CloudTrail for identity (`john.doe@acme.com`).
  * Queries CloudWatch error logs for preceding failure signatures (`OutOfMemoryError`).
  * If verified, surfaces empirical evidence:
    ```text
    [gate] cloud drift detected: aws_glue_job.customer_events
      Declared in Git:    WorkerType = G.1X, NumberOfWorkers = 10
      Live in AWS Cloud:  WorkerType = G.2X, NumberOfWorkers = 20
      Telemetry Evidence: JobRun failed at 02:45 UTC with OutOfMemoryError.
      Action: Scaled via AWS Console at 03:00 UTC by john.doe@acme.com.
      Recommendation: Do not revert. Update main.tf to G.2X and run 'minusctl source anchor'.
    ```

---

## 4. Non-Functional Requirements (NFR)

* **NFR-01 (Zero External Dependencies for Core CLI):** The CLI core must run using standard library modules (`argparse`, `pathlib`, `json`, `dataclasses`, `string.Template`, `subprocess`). Enhanced formatting (`rich`) is used when available but degrades gracefully to standard ASCII formatting.
* **NFR-02 (Fast Execution):** Command dispatch, project root discovery, and context resolution must complete in under 50ms locally.
* **NFR-03 (Packaging & Script Entrypoint):** Registered in `pyproject.toml` under `[project.scripts]` as `minusctl = "core.cli.main:main"`.
* **NFR-04 (No Emojis):** All outputs, logs, and reports must be strictly emoji-free and conform to clean enterprise terminal standards.

---

## 5. Technical Advisory & Instructions from Principal Architect (Matt)

### 5.1 Architecture Overview & Directory Layout
"To the Coding Agent implementing this architecture:
We are building a robust, modular CLI interface that replaces the monolithic `minusctl.py` while maintaining complete backward compatibility with existing tests and workflows."

```
core/cli/
├── __init__.py
├── main.py                     # Entrypoint with CLI parser dispatch
├── context.py                  # ContextManager, project root discovery, active run resolver
├── formatters.py               # ASCII table generators, attribute cards, error formatters
└── commands/
    ├── __init__.py
    ├── create.py               # Implements 'minusctl create'
    ├── runs.py                 # Implements 'minusctl runs list' and 'minusctl runs describe'
    ├── use.py                  # Implements 'minusctl use <run-id>'
    ├── gate.py                 # Implements 'minusctl gate {verify|plan|approve|apply|status}'
    ├── cost.py                 # Implements 'minusctl cost {estimate|prepare}'
    ├── prove.py                # Implements 'minusctl prove'
    ├── export.py               # Implements 'minusctl export'
    ├── source.py               # Implements 'minusctl source {status|diff|anchor}'
    ├── audit.py                # Implements 'minusctl audit {verify}'
    └── doctor.py               # Implements 'minusctl doctor'
```

### 5.2 Implementation Contracts & Signatures

#### A. Context Manager (`core/cli/context.py`)
```python
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any
import json
import os

@dataclass
class CLIContext:
    root_dir: Path
    runs_dir: Path
    active_run: Optional[str]
    cloud: str
    debug: bool

def resolve_context(explicit_run: Optional[str] = None) -> CLIContext:
    root = find_workspace_root()
    runs_dir = root / "runs"
    active = explicit_run or get_active_run(root) or get_latest_run(runs_dir)
    return CLIContext(
        root_dir=root,
        runs_dir=runs_dir,
        active_run=active,
        cloud=os.environ.get("MINUS_CLOUD", "aws"),
        debug=os.environ.get("MINUS_DEBUG", "0") == "1",
    )
```

#### B. Runs Command Dispatcher (`core/cli/commands/runs.py`)
```python
def handle_runs_list(ctx: CLIContext, domain: Optional[str] = None, json_mode: bool = False) -> int:
    runs = list_all_runs(ctx.runs_dir, domain_filter=domain)
    if json_mode:
        print(json.dumps(runs, indent=2))
        return 0
    render_runs_table(runs, active_run=ctx.active_run)
    return 0

def handle_runs_describe(ctx: CLIContext, run_name: Optional[str] = None, json_mode: bool = False) -> int:
    target = run_name or ctx.active_run
    if not target:
        print("Error: No active run found. Specify --run <name> or run 'minusctl use <name>'.")
        return 1
    data = load_run_spec(ctx.runs_dir / target)
    if json_mode:
        print(json.dumps(data, indent=2))
        return 0
    render_attribute_card(data)
    return 0
```

#### C. Context Switcher (`core/cli/commands/use.py`)
```python
def handle_use(ctx: CLIContext, run_name: str) -> int:
    target_dir = ctx.runs_dir / run_name
    if not target_dir.is_dir():
        print(f"Error: Run '{run_name}' does not exist in {ctx.runs_dir}")
        return 1
    set_active_run(ctx.root_dir, run_name)
    print(f"Active workspace set to: {run_name}")
    return 0
```

### 5.3 Invariants to Uphold
1. **Zero External Core Dependency:** Never require third-party libraries for core execution. Use Python standard library modules. If `rich` is installed, use it for formatting; if not, fall back to built-in ASCII string formatters.
2. **Fail-Closed Security:** Mutating actions (`apply`, `seed --execute`) must pass through `approval.py` and require explicit operator confirmation unless durable authorization is proven.
3. **No Emojis:** Strictly avoid all unicode emoji glyphs in terminal outputs, documentation, and reports.
4. **Preserve Legacy Tests:** Maintain compatibility with existing test suites in `tests/test_minusctl.py` and `tests/test_runs.py`.

---

## 6. Acceptance Criteria

1. **AC-01 (Context Switching):** Running `minusctl use <run-name>` updates `.minus/context.json`, and subsequent `minusctl gate plan` commands operate on that run without requiring `--dir` or `--run`.
2. **AC-02 (Runs Table):** `minusctl runs list` outputs a clean ASCII table with the active run marked by `[*]`.
3. **AC-03 (Attribute Card):** `minusctl runs describe <run-name>` renders the complete structured specification card with metadata, architecture attributes, FinOps spend, and artifact paths.
4. **AC-04 (Multi-Repo Export):** `minusctl export --run <name> --target-repo <path> --dest-dir pipelines/<name>` copies all code assets and scaffolds `.github/workflows/<name>-deploy.yml` with path-based triggers.
5. **AC-05 (Telemetry Correlation):** `minusctl gate plan` surfaces preceding CloudWatch failure logs when an AWS resource was scaled out-of-band.
6. **AC-06 (No Emojis):** Verified that zero emoji characters appear in any CLI output, help screen, or generated report.
