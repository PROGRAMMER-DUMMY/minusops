# Product Requirements Document (PRD) — Unified CLI Package, 5-Hop Proving Harness & Agent Command Modernization (v7.0)

| Attribute | Details |
| :--- | :--- |
| **Document ID** | PRD-ARCH-2026-007 (Revision 7.0 — Unified CLI, 5-Hop Proving Harness & AGENTS.md Modernization) |
| **Document Name** | `tasks/prd_v7_unified_cli_and_proving_harness.md` |
| **Status** | APPROVED ARCHITECTURE SPECIFICATION |
| **Lead Architect** | Matt (Principal Cloud Architect & CLI Design Lead) |
| **Author** | MinusOps Autonomous Architecture Engine |
| **Target Components** | `core/cli/`, `core/reporting/seed.py`, `core/reporting/export.py`, `AGENTS.md`, `.agents/AGENTS.md` |
| **Target Runtime** | Local Workstation, CI/CD Matrix, Container Ingress |
| **Date** | August 22, 2026 |

---

## 1. Executive Summary & Context

The coding agent successfully completed the implementation of PRD v5.0 (Semantic Runs, Central Index Registry, Multi-Repo Export, and Telemetry Drift Correlation) across 4 commits with 1,079 tests passing across 88 test files.

This specification (PRD v7.0) establishes the next evolutionary step for MinusOps:
1. **The 5-Hop Proving Harness (`minusctl prove` / enhanced `seed.py`):** Upgrades the current 3-hop verification into an enterprise 5-hop test harness (Bronze Ingestion -> Spark ETL -> Great Expectations DQ -> Quarantine Verification -> Athena Query & Signed Evidence Report).
2. **Modular CLI Package (`core/cli/`):** Full implementation of the `core/cli/` package with `context.py` session management, `minusctl use <run-id>`, `minusctl runs list`, and `minusctl runs describe`.
3. **Documentation & Agent Modernization:** Migrating all legacy script paths across `AGENTS.md`, `.agents/AGENTS.md`, and skill manifests to the unified `minusctl` command suite.

---

## 2. Functional Requirements (FR)

### FR-01: 5-Hop End-to-End Proving Harness (`minusctl prove` / `seed.py`)
Upgrades `core/reporting/seed.py` to execute and verify 5 sequential data hops:

```text
[ Hop 1: Ingestion ] ──► [ Hop 2: Transformation ] ──► [ Hop 3: Data Quality ] ──► [ Hop 4: Quarantine ] ──► [ Hop 5: Serving & Report ]
 • Uploads mock JSON      • Triggers PySpark Glue       • Great Expectations       • Verifies bad rows       • MSCK REPAIR TABLE
 • Checks PutObject       • Polls until SUCCEEDED       • Suite assertions         • Segregated in S3        • SELECT COUNT(*) on Athena
 • Records upload size    • Measures job runtime        • Asserts 100% valid       • Valid rows in Gold      • Generates proving_report.json
```

1. **Hop 1 (Ingest):** Uploads mock synthetic records into Bronze S3 bucket (`s3://<bronze-bucket>/events/`). Verifies HTTP 200 PutObject.
2. **Hop 2 (Transform):** Triggers AWS Glue / EMR Spark ETL job (`aws glue start-job-run`). Polls execution state until `SUCCEEDED` and captures job runtime in seconds.
3. **Hop 3 (Data Quality):** Evaluates Great Expectations suite assertions against Silver/Gold partitions. Records assertion pass rate and column validations.
4. **Hop 4 (Quarantine Routing):** Verifies that injected malformed records land in `s3://<quarantine-bucket>/` and valid records land in Gold without dropping data.
5. **Hop 5 (Serving Query & Signed Report):** Executes Athena `MSCK REPAIR TABLE` and `SELECT COUNT(*)` on Gold tables. Generates signed `reports/<plan-hash>/proving_report.json`.

#### Schema of `proving_report.json`:
```json
{
  "run_name": "marketing-clickstream-mwaa_20260822_111530",
  "proven_at": "2026-08-22T13:30:00Z",
  "status": "PASS",
  "total_latency_seconds": 254.2,
  "hops": [
    {
      "hop": 1,
      "name": "bronze_ingestion",
      "target": "s3://acme-mktg-bronze-prod-001/events/",
      "records_injected": 1000,
      "status": "PASS",
      "latency_seconds": 1.4
    },
    {
      "hop": 2,
      "name": "spark_glue_etl",
      "job_name": "marketing-clickstream-etl",
      "status": "PASS",
      "latency_seconds": 218.0
    },
    {
      "hop": 3,
      "name": "great_expectations_dq",
      "assertions_passed": 14,
      "assertions_failed": 0,
      "status": "PASS",
      "latency_seconds": 12.3
    },
    {
      "hop": 4,
      "name": "quarantine_verification",
      "clean_records_routed_gold": 980,
      "malformed_records_quarantined": 20,
      "status": "PASS",
      "latency_seconds": 2.1
    },
    {
      "hop": 5,
      "name": "athena_serving_query",
      "query": "SELECT COUNT(*) FROM marketing_gold.customer_events",
      "rows_returned": 980,
      "status": "PASS",
      "latency_seconds": 20.4
    }
  ]
}
```

### FR-02: Complete `core/cli/` Package Implementation
* Implements the modular CLI package structure:
  ```text
  core/cli/
  ├── __init__.py
  ├── main.py                     # Entrypoint & CLI parser dispatch
  ├── context.py                  # CLIContext, workspace root finder, active run resolver
  ├── formatters.py               # ASCII tables, spec cards, finding formatters
  └── commands/
      ├── __init__.py
      ├── create.py               # minusctl create
      ├── runs.py                 # minusctl runs list / describe
      ├── use.py                  # minusctl use <run-id>
      ├── gate.py                 # minusctl gate {verify|plan|approve|apply}
      ├── cost.py                 # minusctl cost {estimate|prepare}
      ├── prove.py                # minusctl prove [--execute]
      ├── export.py               # minusctl export --target-repo <path>
      ├── source.py               # minusctl source {status|diff|anchor}
      ├── audit.py                # minusctl audit {verify}
      └── doctor.py               # minusctl doctor
  ```

### FR-03: Context Switching & Active Workspace Resolution
* `minusctl use <run-name>` persists the selection in `.minus/context.json`.
* `minusctl runs list` marks the active workspace with `[*]`.
* `minusctl runs describe <run-name>` outputs the full structured ASCII specification card.
* All gate, cost, prove, and export commands default to the active run when `--run` is omitted.

### FR-04: Documentation & `AGENTS.md` Modernization
* Replaces legacy script invocations across `AGENTS.md`, `.agents/AGENTS.md`, and skill manifests with clean `minusctl` subcommands.

---

## 3. Non-Functional Requirements (NFR)

* **NFR-01 (Zero Emojis):** Strictly zero emoji characters across all CLI outputs, help menus, error messages, and generated markdown reports.
* **NFR-02 (Zero External Dependencies for CLI Core):** Standard library implementation (`argparse`, `pathlib`, `json`, `dataclasses`, `string.Template`, `subprocess`).
* **NFR-03 (FinOps Metric Integrity):** `estimated_monthly_cost` remains `null` until live BCM Pricing Calculator evidence is attached.
* **NFR-04 (Atomic File Safety):** All index and context writes utilize atomic temporary file replacements (`os.replace`).

---

## 4. Acceptance Criteria

1. **AC-01:** `minusctl prove --run <id> --execute` completes all 5 hops and writes signed `proving_report.json`.
2. **AC-02:** `minusctl use <run-id>` persists active run in `.minus/context.json` and updates `minusctl runs list` marker to `[*]`.
3. **AC-03:** `minusctl runs describe <run-id>` renders the full structured ASCII specification card.
4. **AC-04:** `minusctl gate plan` executes against the active run without requiring `--dir`.
5. **AC-05:** `pyproject.toml` registers `minusctl = "core.cli.main:main"` as the official console script entrypoint.
6. **AC-06:** All legacy script paths in `AGENTS.md` and `.agents/AGENTS.md` are updated to `minusctl` subcommands.
7. **AC-07:** Full test suite passes with zero regressions (`pytest tests/`).
