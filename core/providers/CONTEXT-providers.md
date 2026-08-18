# Provider Subsystem Architecture Context: `core/providers/`

This document details the provider access layer for the MinusOps governance core. The governance engine (FinOps, dashboard, coverage audits) interacts with cloud providers strictly through this provider abstraction—never calling cloud CLIs directly from higher-level modules.

---

## Executive Overview & Provider Abstraction Model

MinusOps operates on an AWS-focused cloud provider architecture. Historical multi-cloud scaffolds (`azure.py`, `gcp.py`) and single-implementation abstract base classes were removed from scope to prioritize deep, zero-guess AWS pricing and credential posture guarantees.

```
┌─────────────────────────────────────────────────────────┐
│         Governance Core / FinOps / Dashboard            │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
                 get_provider("aws") [base.py]
                             │
                             ▼
                    AWSProvider [aws.py]
          ┌──────────────────┼──────────────────┐
          │                  │                  │
          ▼                  ▼                  ▼
   sts caller identity   Cost Explorer   Price List Catalog
  (temporary vs static) (cost & anomalies) (pricing_catalog)
```

---

## File Details

### 1. [`base.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/base.py)

- **Exact Purpose**: Defines the provider entry point (`get_provider()`), active cloud identifier (`active_cloud()`), and documents the canonical return schemas relied upon by the FinOps agent, dashboard, and deploy gates.
- **Key Functions / Classes**:
  - [`active_cloud()`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/base.py#L27): Returns `"aws"`. Hardcoded label function used across deployment reports and audit manifests.
  - [`get_provider(name=None)`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/base.py#L33): Factory function returning an instance of `AWSProvider`. Validates that `name` (if passed) is `"aws"` (case-insensitive); raises `ValueError` otherwise.
- **Inputs & Outputs**:
  - *Inputs*: Provider name string (optional, must be `"aws"` or `None`).
  - *Outputs*: Instance of `AWSProvider` (from `core.providers.aws`).
- **Failure Modes**:
  - Raises `ValueError(f"Unknown cloud provider: {name!r} (this build is AWS-only)")` if any provider other than `"aws"` is requested.
- **Architectural Role**: Central factory and interface contract definition for provider isolation across MinusOps.

---

### 2. [`aws.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/aws.py)

- **Exact Purpose**: AWS provider implementation interacting with AWS services via the local AWS CLI (`aws`) credential chain and the offline `pricing_catalog` engine. Handles STS identity verification, credential posture classification, Cost Explorer data, Cost Anomaly Detection, resource tagging lookups, and pre-deploy pricing resolution.
- **Key Functions / Classes**:
  - [`classify_credentials(arn, access_key_id=None)`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/aws.py#L12): Classifies active session security posture as `"temporary"` (ASIA key prefix / assumed-role / federated), `"long_term"` (AKIA key prefix / IAM user), `"root"` (account root), or `"unknown"`.
  - [`run_aws(args, timeout=20)`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/aws.py#L38): Subprocess helper running `aws` CLI commands in list form (no shell). Captures output and parses JSON responses safely.
  - [`AWSProvider`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/aws.py#L63): Primary provider class implementing:
    - [`identity()`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/aws.py#L67): Returns `(account_id, connected_bool)` via `sts get-caller-identity`.
    - [`credential_posture()`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/aws.py#L73): Returns dict with `connected`, `arn`, `account`, `type` (`"temporary"`, `"long_term"`, `"root"`, `"unknown"`).
    - [`cost_by_service(months_back=6)`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/aws.py#L86): Queries Cost Explorer (`ce get-cost-and-usage`) for unblended monthly spend grouped by service.
    - [`anomalies(days_back=60)`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/aws.py#L109): Queries Cost Explorer (`ce get-anomalies`) for detected cost anomalies.
    - [`owner(resource_hint)`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/aws.py#L130): Resolves owner/team tags via `resourcegroupstaggingapi get-resources`.
    - [`list_billable_services()`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/aws.py#L147): Wraps `pricing_catalog.list_service_codes()`.
    - [`resolve_resource_type(tf_type)`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/aws.py#L152): Wraps `pricing_catalog.resolve_resource_type(tf_type)`.
    - [`lookup_usage_dimensions(service, filters=None)`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/aws.py#L156): Wraps `pricing_catalog.lookup_dimensions()`.
    - [`confirmed_free(tf_type)`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/aws.py#L161): Wraps `pricing_catalog.confirmed_free()`.
- **Inputs & Outputs**:
  - *Inputs*: Service parameters, time ranges, resource hints, Terraform resource types.
  - *Outputs*: Structured dictionaries and tuples representing identity, credential security posture, monthly cost breakdowns, anomaly lists, owner tags, and pricing metadata.
- **Failure Modes**:
  - Returns `(False, None, error_msg)` if `aws` CLI executable is missing (`FileNotFoundError`) or times out (`subprocess.TimeoutExpired`).
  - `credential_posture()` returns `connected: False` if STS caller identity fails.
  - `cost_by_service()` returns `{"ok": False, "error": err, "months": []}` if Cost Explorer API call fails or is disabled in the account.
- **Architectural Role**: Realized implementation of the AWS cloud abstraction, bridging local CLI sessions and Cost Explorer APIs to governance gates.

---

### 3. [`__init__.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/__init__.py)

- **Exact Purpose**: Exposes `get_provider` and `active_cloud` at the package root level (`core.providers`).
- **Key Functions / Classes**:
  - Re-exports `get_provider` and `active_cloud` from `core.providers.base`.
- **Inputs & Outputs**: N/A.
- **Failure Modes**: N/A.
- **Architectural Role**: Package interface header for imports across `core/`.

---

### 4. Historical Note on `azure.py` & `gcp.py`

- **Status**: Removed / Not Present in Active Codebase.
- **Rationale & Architectural Decision**:
  - Early architectural drafts included placeholder multi-cloud scaffolds (`azure.py`, `gcp.py`).
  - To maintain strict non-guessing cost estimation guarantees (via `pricing_catalog.py` and `coverage_audit.py`) and robust credential posture checks (STS temporary session enforcement), multi-cloud scope was narrowed to focus exclusively on deep, production-grade AWS governance.
  - Consequently, `base.py` enforces AWS-only execution (`get_provider("aws")`), and `azure.py` / `gcp.py` scaffolds were removed to prevent false claims of multi-cloud support. Any call attempting to request non-AWS providers immediately fails-closed with a `ValueError`.
