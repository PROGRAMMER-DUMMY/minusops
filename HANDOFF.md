# HANDOFF.md — Architecture & Diagramming Engine Refactor

> **Audience:** Claude Code / Claude Agent / Autonomous Ops Agents  
> **Workspace:** `C:\Users\shubh\PycharmProjects\MinusTeraformCli`  
> **Active Run:** `analytics-enterprise-serverless-lakehouse_20260829_065151`  
> **Live Console URL:** `http://127.0.0.1:8050` (`minusctl console`)  
> **Created:** 2026-08-29

---

## 1. Executive Context & Mission

MinusOps is an enterprise, workload-agnostic cloud ops control plane that governs Terraform deployments through a cryptographic plan-hash deploy gate (`minusctl gate`), FinOps cost estimation (`minusctl cost`), and automated architecture diagramming (`minusctl diagram`).

Your mission is to **refactor and modernize the two architecture visualization engines**:
1. **Data Flow SVG Engine (`dataflow.svg` in `core/reporting/reporter.py`)**: Update from dark theme (`#14110f`) to the brand-aligned **MinusOps Monad Light Theme** (`#fbf7f4` background, terracotta `#d95d39` accents, clean pastel containers, high-contrast typography).
2. **Draw.io mxGraph Engine (`architecture.drawio` in `core/reporting/drawio_generator.py`)**: Refactor layout to follow a clean horizontal 6-tier medallion data flow (Ingestion $\to$ Raw/Bronze $\xrightarrow{\text{Glue}}$ Silver/CDM $\xrightarrow{\text{Glue}}$ Gold/ARD $\to$ Consumption $\to$ Governance). Consolidate bucket attachments (versioning, lifecycle, SSE) so they do not duplicate into 20 stacked cards.

---

## 2. Key Codebase Files & Subsystems

| File Path | Role & What to Change |
| :--- | :--- |
| [`core/reporting/reporter.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/reporting/reporter.py) | **`build_dataflow_svg()`**: Update color palette to Monad Light theme (`#fbf7f4`, `#ffffff`, `#d95d39`, `#1e293b`, `#64748b`). |
| [`core/reporting/drawio_generator.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/reporting/drawio_generator.py) | **`generate_drawio_from_plan()`**: Produce clean Left-to-Right medallion swimlanes with official AWS-4 stencils, consolidated storage cards, and collision-free geometry. |
| [`core/architecture/architecture_model.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_model.py) | Shared 6-layer resource classification taxonomy (`ingestion`, `storage`, `catalog`, `processing`, `consumption`, `governance`). |
| [`tests/test_drawio_generator.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/tests/test_drawio_generator.py) | Unit test suite guarding 1-click URL round-trip, XML validity, and non-overlapping layout invariants. |

---

## 3. Reference Architecture Specifications & Assets

The user has provided three primary reference sources:

1. **Friend's Production Healthcare ARD Lakehouse (`IMG_1487`)**:
   * File path: `C:\Users\shubh\Downloads\imp_prod\IMG_1487.HEIC`
   * Architecture topology:
     * `Foundational Layer`: US Data Hub + RWDEx $\to$ AWS Landing.
     * `Integrated Layer`: Airflow/Matillion pushdown $\to$ Databricks Spark Job Cluster $\to$ Staging $\to$ Integration $\to$ **9 OMOP CDM tables**.
     * `ARD Layer`: Matillion pushdown $\to$ Databricks Analytical Cluster $\to$ **6 Domain ARD grains** $\to$ **5 Solution ARD Gold Marts**.
     * `Governance & Controls`: S3 Glacier Lifecycle, Metadata (Delta), Decoupled BRMS (Python+Matillion), 5-Hop Lineage (Collibra), KMS CMK, IAM roles, CloudWatch.
2. **AWS Serverless Big Data Reference Architecture**:
   * 6-Layer Layout: Ingestion $\to$ Storage & Processing (Medallion S3) $\to$ Cataloging & Governance $\to$ Consumption (Athena, Redshift, QuickSight, SageMaker) $\to$ Security & Monitoring.
3. **Deep Research Blueprint**:
   * File path: `C:\Users\shubh\Downloads\Enterprise Architecture Deep Research Blueprint.md`

---

## 4. Execution Steps for Claude Agent

```bash
# 1. Select the active workload run
minusctl use analytics-enterprise-serverless-lakehouse_20260829_065151

# 2. Run unit tests before making edits
pytest tests/test_drawio_generator.py tests/test_reporter.py

# 3. Implement the light-theme palette in build_dataflow_svg() (reporter.py)
# 4. Refactor drawio_generator.py for clean medallion layout & bucket consolidation

# 5. Regenerate and verify the diagram
minusctl diagram

# 6. Verify full test suite passes
pytest tests/
```

---

## 5. Non-Negotiable Invariants

1. **Zero Emojis:** Do not emit emojis in CLI outputs, code comments, logs, or generated markdown.
2. **Standard Library Only in `drawio_generator.py`:** No third-party graphing libraries (strictly `xml.etree.ElementTree`, `zlib`, `base64`, `urllib.parse`).
3. **1-Click URL Compatibility:** The `#R` fragment must use standard Base64 + zlib deflate (-15 wbits) so `https://app.diagrams.net/#R...` opens in browser with zero errors.
