# Product Requirements Document (PRD) — Enterprise Data Governance, Semantic Layer Modules & FinOps Ceilings (v8.0)

| Attribute | Details |
| :--- | :--- |
| **Document ID** | PRD-ARCH-2026-008 (Revision 8.0 — Governance Modules, Semantic Layer, Redshift FinOps & Partition Projection) |
| **Document Name** | `tasks/prd_v8_enterprise_governance_and_semantic_modules.md` |
| **Status** | APPROVED ARCHITECTURE SPECIFICATION |
| **Lead Architect** | Matt (Principal Cloud Architect & Governance Lead) |
| **Author** | MinusOps Autonomous Architecture Engine |
| **Target Components** | `modules/`, `core/generation/modules.py`, `modules/consumption-redshift-serverless/`, `modules/query-athena/`, `.agents/skills/grill-me/SKILL.md`, `pyproject.toml` |
| **Target Runtime** | AWS Lake Formation, dbt Semantic Layer, Cube.js, Redshift Serverless, Athena |
| **Date** | August 22, 2026 |

---

## 1. Executive Summary & Problem Statement

A gap analysis between the grilling interrogation engine ([`.agents/skills/grill-me/SKILL.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/skills/grill-me/SKILL.md)) and the physical module catalog ([`core/generation/modules.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/generation/modules.py)) revealed four architectural items required for end-to-end coherence:

1. **Grill-Me vs. Catalog Module Mismatch (Pillars 12 & 13):**
   - The grilling interview asks about Semantic Layers (Pillar 12) and Fine-Grained Data Governance (Pillar 13), referencing four modules: `dbt-semantic-layer`, `cube-semantic-layer`, `governance-lakeformation`, and `security-iam-scoped`.
   - None of these four modules currently exist in `modules/` or in `core/generation/modules.py`. Capturing these requirements in `requirements.json` produces an unbuildable specification during synthesis.

2. **Unbounded Spend Ceiling in Redshift Serverless:**
   - [`modules/consumption-redshift-serverless/main.tf`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/modules/consumption-redshift-serverless/main.tf) defines `base_capacity_rpu` (the floor) but omits `max_capacity` (the ceiling) and usage limit controls (`aws_redshiftserverless_usage_limit`). Under heavy analytical load, Redshift Serverless scales RPUs without an upper bound, violating the repo's strict FinOps predictability doctrine.

3. **Partition Projection in Athena Query Tables:**
   - Partitioned Athena tables currently rely on `MSCK REPAIR TABLE`, which degrades in performance as partition volume grows. Adding native AWS Athena Partition Projection (`projection.enabled = "true"`) enables in-memory partition resolution with zero repair overhead.

4. **Pillar 14 Documentation Alignment:**
   - Align the proving command reference in `grill-me` to `minusctl prove --execute` (the 5-hop form).

---

## 2. Functional Requirements (FR)

### FR-01: Fine-Grained Governance & IAM Modules (Pillar 13)
Create and register two new security and governance modules:

#### A. `modules/governance-lakeformation/`
* **Purpose:** Provisions AWS Lake Formation Tag-Based Access Control (LF-TBAC), data lake settings, and Data Cell Filters (Row-Level Security and Column-Level PII Masking) on Gold analytical tables.
* **Resources:**
  * `aws_lakeformation_data_lake_settings`: Declares Lake Formation administrators.
  * `aws_lakeformation_resource`: Registers S3 Gold bucket with IAM role delegation.
  * `aws_lakeformation_lf_tag`: Defines governance tags (e.g. `Confidentiality=PII|Public`, `Domain=Finance|Marketing`).
  * `aws_lakeformation_permissions`: Grants Tag-Based permissions for Athena and EMR execution roles.
* **Variables:** `gold_bucket_arn`, `admin_iam_role_arns`, `lf_tags`, `tags`.

#### B. `modules/security-iam-scoped/`
* **Purpose:** Least-privilege IAM policies, KMS decrypt permissions, and cross-account read roles for downstream BI and data science consumers.
* **Resources:**
  * `aws_iam_policy`: Granular `s3:GetObject`, `kms:Decrypt`, and `athena:StartQueryExecution` policies.
  * `aws_iam_role`: Consumer role with external ID anti-confused-deputy condition.
* **Variables:** `name_prefix`, `gold_bucket_arn`, `kms_key_arn`, `trusted_external_principals`.

---

### FR-02: Semantic Layer Integration Modules (Pillar 12)
Create and register two new semantic layer scaffolding modules:

#### A. `modules/dbt-semantic-layer/`
* **Purpose:** Generates code-native dbt Semantic Layer / MetricFlow configuration manifests.
* **Artifacts:**
  * `models/semantic_models.yml`: Defines semantic entities, dimensions, and measures on Gold tables.
  * `models/metrics.yml`: Defines standardized metrics (e.g. `monthly_active_users`, `net_revenue`).
  * `dbt_project.yml`: Configures Athena/Snowflake adapter with partition-aware query routing.

#### B. `modules/cube-semantic-layer/`
* **Purpose:** Headless universal semantic layer scaffolding with SQL API and REST/GraphQL endpoints.
* **Artifacts:**
  * `cube/schema/`: Metric and dimension cubes in JavaScript/YAML.
  * `cube/cube.js`: Pre-aggregation and Redis cache configuration.
  * Terraform module provisioning container definition for EKS/ECS hosting.

---

### FR-03: Redshift Serverless FinOps Spend Ceilings
Update [`modules/consumption-redshift-serverless/`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/modules/consumption-redshift-serverless/main.tf):
* Add `max_capacity` variable with `default = 128` (or 256) and validation enforcing `max_capacity >= var.base_capacity_rpu`.
* Attach `aws_redshiftserverless_usage_limit` resource:
  * Limits RPU-hours per day/month.
  * `action_type`: `log` or `deactivate` to prevent runaway analytical billing.

---

### FR-04: Athena Partition Projection
Update [`modules/query-athena/`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/modules/query-athena/main.tf) and table DDL generators:
* Add partition projection table properties in Glue Catalog / Athena DDL:
  * `projection.enabled = "true"`
  * `projection.date.type = "date"`
  * `projection.date.range = "2020/01/01,NOW"`
  * `projection.date.format = "yyyy/MM/dd"`
  * `storage.location.template = "s3://${gold_bucket}/events/${date}/"`

---

### FR-05: Module Registry & Documentation Synchronization
1. Register all four new modules in `core/generation/modules.py` with inputs, outputs, and requirements matching rules.
2. Add all new module directories to `pyproject.toml` under `[tool.setuptools.data-files]`.
3. Update `.agents/skills/grill-me/SKILL.md` Pillar 14 to reference `minusctl prove --execute`.

---

## 3. Non-Functional Requirements (NFR)

* **NFR-01 (Zero Emojis):** Strictly no emojis across all HCL, Python scripts, YAML files, and documentation.
* **NFR-02 (Deterministic Synthesis):** Module matching and composition must execute with zero external network dependencies.
* **NFR-03 (Packaging Completeness):** All newly added modules must pass `validate_modules()` in `test_runs.py` and wheel smoke tests.

---

## 4. Acceptance Criteria

1. **AC-01:** `modules.match("Lake Formation row level security with dbt semantic layer")` returns `governance-lakeformation` and `dbt-semantic-layer`.
2. **AC-02:** `consumption-redshift-serverless` declares explicit `max_capacity` and `usage_limit`.
3. **AC-03:** `query-athena` table definitions include partition projection properties.
4. **AC-04:** `.agents/skills/grill-me/SKILL.md` Pillar 14 references `minusctl prove --execute`.
5. **AC-05:** Full test suite passes cleanly with zero regressions.
