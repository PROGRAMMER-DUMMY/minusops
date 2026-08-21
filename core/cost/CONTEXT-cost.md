# Core Cost Subsystem Context — MinusOps

This document provides an exhaustive, architectural, and operational reference for every file in the `core/cost/` directory. The `core/cost/` subsystem is built on a strict operational doctrine: **MinusOps does not hardcode, invent, or locally extrapolate cost totals or SKU rates.** Enterprise reportable cost evidence is produced solely via the AWS BCM (Billing and Cost Management) Pricing Calculator API (`bcm-pricing-calculator`), while pre-deployment classification and resource mapping are managed via reviewed catalog data.

---

## Directory Overview & File Index

- [`core/cost/__init__.py`](./__init__.py) — Package initialization and doctrine statement.
- [`core/cost/bcm_pricing_calculator.py`](./bcm_pricing_calculator.py) — Gated integration with the AWS BCM Pricing Calculator API, bill scenario modeling, scale curves, and actuals variance tracking.
- [`core/cost/budget_calculator.py`](./budget_calculator.py) — Cost guidance CLI and dispatcher handler ensuring honest guidance without fabricated dollar totals.
- [`core/cost/coverage_audit.py`](./coverage_audit.py) — Fail-closed cost-coverage auditing engine classifying Terraform plan resources into four distinct states.
- [`core/cost/pricing_catalog.py`](./pricing_catalog.py) — Single source of truth for resource-to-service mapping and read-only AWS Price List catalog lookups.
- [`core/cost/pricing_data/aws_resource_map.json`](./pricing_data/aws_resource_map.json) — Reviewed JSON catalog mapping Terraform resource type prefixes to AWS `serviceCode` values and amount derivation models.
- [`core/cost/pricing_data/free_resources.json`](./pricing_data/free_resources.json) — Reviewed allowlist of Terraform resource type prefixes confirmed to carry zero billable AWS SKUs.

---

## Detailed File Specifications

### 1. `core/cost/__init__.py`
- **File Link:** [`core/cost/__init__.py`](./__init__.py)
- **Exact Purpose:** Defines `core.cost` as a Python package and declares the core doctrine: AWS BCM is the only source of reportable numbers.
- **Key Functions/Classes:** None (module-level docstring).
- **Inputs/Outputs:** None.
- **Failure Modes:** N/A.
- **Architectural Role:** Serves as the package entry point for cost estimation and pricing discovery modules.

---

### 2. `core/cost/bcm_pricing_calculator.py`
- **File Link:** [`core/cost/bcm_pricing_calculator.py`](./bcm_pricing_calculator.py)
- **Exact Purpose:** Manages the integration with the AWS BCM Pricing Calculator API (`aws bcm-pricing-calculator`). It handles safe offline payload preparation, gated API execution, automated estimation under valid credentials, scale-curve generation (pricing 1x, 5x, 10x usage), Savings Plan / Reserved Instance commitment modeling via bill scenarios, and actuals comparison via Cost Explorer.
- **Key Functions/Classes:**
  - [`prepare(report_dir, account_id=None, region="us-east-1", rate_type="BEFORE_DISCOUNTS", usage_profile=None, derive=False, assumptions=None)`](./bcm_pricing_calculator.py): Builds `bcm-create-workload-estimate.json`, `bcm-usage.json`, `bcm-assumptions.json`, and `bcm-commands.json` without AWS calls.
  - [`run(report_dir, mode="auto-approve")`](./bcm_pricing_calculator.py): Gated AWS API call (`request_approval`) executing `create-workload-estimate`, `batch-create-workload-estimate-usage`, `get-workload-estimate`, and `list-workload-estimate-usage`.
  - [`auto_estimate(report_dir, region="us-east-1", usage_profile=None)`](./bcm_pricing_calculator.py): Non-blocking automatic estimator that derives usage amounts from plan inventory and blueprint inputs if credentials allow.
  - [`derive_usage(plan, account_id, region, profile=None, assumptions=None)`](./bcm_pricing_calculator.py): Derives monthly usage quantities (DPU-hours, GB-Mo, RPU-hours, etc.) from Terraform plan inventory and inputs using `DEFAULT_ASSUMPTIONS`.
  - [`build_usage(plan, account_id, region, usage_profile=None)`](./bcm_pricing_calculator.py): Builds a conservative BCM usage draft from Terraform plan inventory with explicit `REVIEW_REQUIRED` placeholders.
  - [`scale_curve(report_dir, factors=(1, 5, 10))`](./bcm_pricing_calculator.py): Submits temporary BCM workload estimates at usage multiples (e.g. 1x, 5x, 10x) and records `bcm-scale-curve.json`.
  - [`run_bill_scenario(report_dir, usage_mods=None, commitments=None, mode="auto-approve", name=None)`](./bcm_pricing_calculator.py): Models commitments (Savings Plans/RIs) via `create-bill-scenario` and `create-bill-estimate`.
  - [`fetch_actuals(report_dir, month=None, months_back=6)`](./bcm_pricing_calculator.py): Fetches per-service spend from Cost Explorer via `CloudProvider` and writes `bcm-actuals.json`.
  - [`validate_usage(usage)`](./bcm_pricing_calculator.py): Validates BCM usage payload schemas and flags unpopulated `REVIEW_REQUIRED` placeholders.
- **Inputs/Outputs:**
  - *Inputs:* `report_dir` containing `plan.json` and `manifest.json`, CLI arguments (`--account-id`, `--region`, `--usage-profile`, `--assume`, `--mode`).
  - *Outputs:* Generates JSON artifacts in `report_dir` (`bcm-usage.json`, `bcm-create-workload-estimate.json`, `bcm-estimate.json`, `bcm-scale-curve.json`, `bcm-scenario-estimate.json`, `bcm-actuals.json`).
- **Failure Modes:**
  - `FileNotFoundError`: Missing `plan.json` in `report_dir` or missing `aws` CLI executable.
  - `RuntimeError`: Unresolved `REVIEW_REQUIRED` placeholders during `run()`, invalid BCM usage payload fields, or failed AWS CLI commands.
- **Architectural Role:** The primary bridge between Terraform plan inventory and AWS-priced cost evidence. Enforces Human-in-the-Loop (HITL) approval via `approval.py` for API calls while allowing safe, non-mutating preparation.

---

### 3. `core/cost/budget_calculator.py`
- **File Link:** [`core/cost/budget_calculator.py`](./budget_calculator.py)
- **Exact Purpose:** Provides honest cost guidance to intent dispatchers requesting budget calculations. Deliberately refuses to invent prices or compute local totals, outputting instructions for running the BCM Pricing Calculator workflow. Derives unit economics ratios ($/GB, $/run) exclusively from evidenced BCM cost figures with explicit source provenance.
- **Key Functions/Classes:**
  - [`cost_guidance()`](./budget_calculator.py): Returns a dictionary stating `reportable: False` and listing the exact `bcm_pricing_calculator.py` commands required to obtain reportable enterprise cost evidence.
  - [`unit_economics(total_usd=None, source=None, gb_processed=None, runs=None)`](./budget_calculator.py): Derives unit economics ratios from an evidenced AWS BCM total. Refuses without an evidenced total and explicit source provenance.
  - [`unit_economics_curve(points, source=None)`](./budget_calculator.py): Derives unit economics ratios for a multi-point scale curve priced by BCM without extrapolating beyond measured points.
  - [`main(argv=None)`](./budget_calculator.py): Command-line entry point that writes `budget_estimation.json` to `.agents/logs/` and outputs guidance formatted as human text or JSON.
- **Inputs/Outputs:**
  - *Inputs:* Optional `--log-dir`, `--json`, and legacy sizing arguments (`--service`, `--scale`).
  - *Outputs:* Writes `.agents/logs/budget_estimation.json` and outputs guidance text/JSON.
- **Failure Modes:** Writes to disk errors if `--log-dir` is unwriteable.
- **Architectural Role:** Handles `BUDGET` intent dispatch requests honestly, ensuring agents or automation do not hallucinate cost totals when AWS BCM pricing has not been run.

---

### 4. `core/cost/coverage_audit.py`
- **File Link:** [`core/cost/coverage_audit.py`](./coverage_audit.py)
- **Exact Purpose:** Implements the fail-closed cost-coverage audit gate. Every resource type discovered in a Terraform plan is classified into one of four auditable states to prevent resources from silently vanishing from cost reports.
- **Key Functions/Classes:**
  - [`classify(plan, provider=None)`](./coverage_audit.py): Classifies plan resources into `auto_priced`, `catalog_mapped_needs_usage`, `confirmed_free`, or `unresolved`. Routinely calls the provider abstraction (`providers.base.get_provider()`).
  - [`audit(report_dir, provider=None)`](./coverage_audit.py): Loads `plan.json` from `report_dir`, runs `classify()`, attaches UTC timestamps and recent `schema_watch` report metadata, and writes `bcm-coverage.json`.
  - [`_latest_schema_watch_report(repo_root=None, tracked_provider="aws")`](./coverage_audit.py): Read-only helper fetching recent Terraform provider schema drift findings.
- **Inputs/Outputs:**
  - *Inputs:* `report_dir` containing `plan.json`.
  - *Outputs:* Writes `bcm-coverage.json` in `report_dir`.
- **Failure Modes:**
  - `FileNotFoundError`: Missing `plan.json` in `report_dir`.
  - Exit code `1` in CLI mode if any resource type lands in `unresolved`.
- **Architectural Role:** Acts as a compliance and coverage gate for `plan_gate.py`, guaranteeing that unmapped Terraform resource types are surfaced as actionable gaps rather than omitted from cost analysis.

---

### 5. `core/cost/pricing_catalog.py`
- **File Link:** [`core/cost/pricing_catalog.py`](./pricing_catalog.py)
- **Exact Purpose:** Serves as the single source of truth for mapping Terraform resource types to AWS `serviceCode` values. Replaces disparate lookup tables across reporting tools and provides read-only live AWS Price List catalog lookups.
- **Key Functions/Classes:**
  - [`resolve_resource_type(tf_type)`](./pricing_catalog.py): Matches a Terraform resource type against `aws_resource_map.json` using longest-prefix matching.
  - [`confirmed_free(tf_type)`](./pricing_catalog.py): Checks if a Terraform resource type is listed in `free_resources.json`.
  - [`entry_for_service_code(service_code)`](./pricing_catalog.py): Reverse lookup returning the resource map entry for a given `serviceCode`.
  - [`rate_citation_for_service_code(service_code)`](./pricing_catalog.py): Returns reviewed AWS Price List catalog rate facts for unestimated services.
  - [`service_display_name(tf_type)`](./pricing_catalog.py): Returns human-readable service display names (e.g. "Amazon S3", "AWS Glue").
  - [`file_hint(tf_type)`](./pricing_catalog.py): Returns the standard `.tf` file hint (e.g. `s3.tf`, `glue.tf`) for legacy flat-file generator lookups.
  - [`list_service_codes(refresh=False)`](./pricing_catalog.py): Read-only AWS CLI query (`aws pricing describe-services`) caching service codes to `.agents/cache/aws_service_codes.json`.
  - [`lookup_dimensions(service_code, region="us-east-1", refresh=False)`](./pricing_catalog.py): Read-only AWS CLI query (`aws pricing get-attribute-values`) fetching valid `usageType` and `operation` values.
- **Inputs/Outputs:**
  - *Inputs:* Terraform resource types or AWS service codes; live AWS Pricing API queries.
  - *Outputs:* Mapped dictionaries, service display names, or cached catalog JSON files.
- **Failure Modes:**
  - `FileNotFoundError`: Missing `aws_resource_map.json` or `free_resources.json` under `pricing_data/`, or missing `aws` CLI when executing live catalog queries.
  - `RuntimeError`: Non-zero return code from `aws pricing` subcommands.
- **Architectural Role:** Centralizes resource-to-service mappings for `bcm_pricing_calculator.py`, `plan_inspector.py`, `reporter.py`, and `coverage_audit.py`.

---

### 6. `core/cost/pricing_data/aws_resource_map.json`
- **File Link:** [`core/cost/pricing_data/aws_resource_map.json`](./pricing_data/aws_resource_map.json)
- **Exact Purpose:** Structured, committed JSON mapping table connecting Terraform resource type prefixes (e.g. `aws_glue`, `aws_s3`, `aws_kms`) to AWS Price List `serviceCode` values, display names, `.tf` file hints, verification statuses (`verified: true/false`), and amount derivation models.
- **Key Fields:** `prefixes` array containing `prefix`, `service_code`, `display_name`, `file_hint`, `verified`, `amount_model`, and `rate_citation`.
- **Architectural Role:** Drives deterministic pricing lookup and automatic amount derivation across the cost subsystem.

---

### 7. `core/cost/pricing_data/free_resources.json`
- **File Link:** [`core/cost/pricing_data/free_resources.json`](./pricing_data/free_resources.json)
- **Exact Purpose:** Structured, committed allowlist of Terraform resource type prefixes (e.g. `aws_iam_role`, `aws_security_group`, `aws_subnet`, `aws_kms_alias`) verified to carry zero billable AWS SKUs regardless of scale.
- **Key Fields:** `prefixes` array containing `prefix`, `display_name`, and `note`.
- **Architectural Role:** Prevents non-billable structural resources (IAM, VPC subnets, route tables) from flagging as unresolved gaps during `coverage_audit.py` scans.

---

## Inter-Module Dependencies & Data Flow

```
                     ┌───────────────────────────────┐
                     │ pricing_data/                 │
                     │  ├── aws_resource_map.json    │
                     │  └── free_resources.json      │
                     └──────────────┬────────────────┘
                                    │
                                    ▼
                     ┌───────────────────────────────┐
                     │   core/cost/pricing_catalog   │
                     └──────┬─────────────────┬──────┘
                            │                 │
           ┌────────────────┘                 └────────────────┐
           ▼                                                   ▼
┌──────────────────────────────┐                   ┌──────────────────────────────┐
│  core/cost/coverage_audit    │                   │core/cost/bcm_pricing_calc    │
└──────────────────────────────┘                   └──────────────┬───────────────┘
                                                                  │
                                                                  ▼
                                                   ┌──────────────────────────────┐
                                                   │ core/reporting/reporter.py   │
                                                   └──────────────────────────────┘
```

1. **Catalog Resolution:** Both `coverage_audit.py` and `bcm_pricing_calculator.py` query `pricing_catalog.py`, which loads `aws_resource_map.json` and `free_resources.json` using longest-prefix matching.
2. **Audit Classification:** `coverage_audit.py` reads a plan's `plan.json` and queries `providers.base.get_provider()` (which delegates to `pricing_catalog.py` for AWS) to verify 100% cost coverage.
3. **Usage Derivation & Estimation:** `bcm_pricing_calculator.py` processes `plan.json`, derives usage quantities based on `DEFAULT_ASSUMPTIONS` and plan variables, generates BCM JSON payloads, and calls the AWS BCM Pricing Calculator API upon approval.
4. **Report Refresh:** Once BCM pricing completes, `bcm_pricing_calculator.run()` triggers `reporter.refresh_cost()`, updating `cost.html`, `cost.pdf`, and `manifest.json`.
