# Product Requirements Document (PRD) — Dynamic Draw.io Architecture Diagram Generator, Universal Provider Stencil Engine & Dedicated Diagramming Agent (v12.0)

| Attribute | Details |
| :--- | :--- |
| **Document ID** | PRD-ARCH-2026-012 (Revision 12.0 — Dynamic Draw.io Architecture Diagram Generator & Dedicated Agent) |
| **Document Name** | `tasks/prd_v12_dynamic_drawio_architecture_diagram_generator_and_agent.md` |
| **Status** | APPROVED SPECIFICATION FOR IMPLEMENTATION |
| **Lead Architect** | Matt (Principal Cloud Architect & CLI Design Lead) |
| **Target Components** | `core/reporting/drawio_generator.py`, `core/cli/commands/diagram.py`, `core/reporting/reporter.py`, `core/reporting/minusctl.py`, `.agents/skills/architecture-diagrammer/SKILL.md`, `tests/test_drawio_generator.py` |
| **Target Audience** | Coding Agent, Platform Engineers, Enterprise Cloud Architects, SecOps Teams |
| **Date** | August 23, 2026 |

---

## 1. Executive Summary & Problem Statement

### 1.1 The Problem
The current architecture diagramming in MinusOps ([`core/reporting/reporter.py`](../core/reporting/reporter.py)) generates a rigid, static 5-column SVG (`architecture.svg`) that resembles a monochrome tabular card grid. While it carries plan metadata and security badges, it suffers from severe limitations:
1. **No Network or Subnet Hierarchy:** Fails to represent real VPC/VNet network perimeters, public ingress subnets, private compute tiers, and data lake storage zones.
2. **Static & Non-Editable:** Operators and cloud architects cannot adjust layout, move components, or present live editable canvases in architectural review meetings.
3. **Tangled Flow Lines:** Connector lines become cluttered and illegible on complex multi-hop pipelines.
4. **Hardcoded Monolithic Logic:** The previous SVG generation relied on rigid heuristics that fail to generalize cleanly across multi-cloud providers (AWS, Azure, GCP, Databricks, Snowflake).

### 1.2 The Solution
This specification delivers a **dynamic, discovery-driven Architecture Diagramming Engine and Dedicated Agent**:
* **100% Python Standard-Library Draw.io Generator (`core/reporting/drawio_generator.py`):** Produces native `.drawio` XML (`<mxGraphModel>`) without heavy external C-binaries (Graphviz) or third-party PyPI packages (`diagrams`).
* **Universal Prefix-Based Provider Stencil Resolver:** Dynamically maps Terraform resource prefixes (`aws_*`, `azurerm_*`, `google_*`, `databricks_*`, `snowflake_*`) to official vector cloud stencils.
* **Dynamic Network & Zone Clustering:** Inspects plan references, tags, and module trees to group resources into VPCs, Availability Zones, Subnets, and Storage Tiers.
* **Automatic Node Sizing & Security Subtitles:** Extracts compute worker counts, memory, engine versions, and KMS/PAB encryption attributes from plan `change.after` metadata.
* **Topological Flow Discovery & Numbered Sequence Badges:** Automatically traces data lineage and labels connectors sequentially (`[1] Ingress` -> `[2] Transform` -> `[3] Quality` -> `[4] Storage` -> `[5] Serving`).
* **Instant 1-Click Browser URL:** Deflates XML via `zlib` to generate self-contained `https://app.diagrams.net/#R<deflated-base64>` links for 1-click zero-install browser canvas viewing.
* **In-Canvas Step Execution Ledger:** Embeds a technical protocol, latency SLA, and safeguard table directly inside the Draw.io canvas.
* **Dedicated Agent & CLI Interface:** Provides `.agents/skills/architecture-diagrammer/SKILL.md` and `minusctl diagram` subcommand.

---

## 2. Core Architectural Invariants (Non-Negotiable)

1. **Standard-Library Only (Zero External Dependencies):** The generator must use exclusively Python standard library (`xml.etree.ElementTree`, `zlib`, `base64`, `urllib.parse`, `json`, `re`). No `graphviz`, no `pygraphviz`, no PyPI `diagrams` dependency.
2. **Zero Hardcoding Invariant:** Resource types, stencils, network boundaries, sizing attributes, and flow sequences must be dynamically derived from `plan.json` or `requirements.json` / `architecture_decision.json`.
3. **Plan-Bound Ground Truth:** Every node in a post-plan diagram must map directly to a physical Terraform address. No hallucinated resources.
4. **Zero-Emoji Doctrine:** Strictly zero emojis in terminal text, log lines, generated XML, markdown documentation, or ledger tables.
5. **Fail-Closed Context & Safe Execution:** CLI commands default to the active session run (`.minus/context.json`) and refuse execution when context is ambiguous.

---

## 3. Functional Requirements (FR)

### FR-01: Dynamic Universal Provider Stencil Resolver
* **FR-01.1 (Prefix Resolution):** Map Terraform resource types to Draw.io official vector stencils using longest-prefix matching:
  * AWS: `aws_s3_*` -> `shape=mxgraph.aws4.s3;fillColor=#E7157B;`
  * AWS Glue: `aws_glue_job` -> `shape=mxgraph.aws4.glue;fillColor=#8C4FFF;`
  * AWS Athena: `aws_athena_*` -> `shape=mxgraph.aws4.athena;fillColor=#8C4FFF;`
  * AWS Step Functions: `aws_sfn_*` -> `shape=mxgraph.aws4.step_functions;fillColor=#E7157B;`
  * AWS EMR: `aws_emr_*` / `aws_emrserverless_*` -> `shape=mxgraph.aws4.emr;fillColor=#8C4FFF;`
  * AWS Kinesis: `aws_kinesis_*` -> `shape=mxgraph.aws4.kinesis;fillColor=#8C4FFF;`
  * AWS Redshift: `aws_redshift_*` -> `shape=mxgraph.aws4.redshift;fillColor=#8C4FFF;`
  * AWS Security/IAM/KMS: `aws_iam_*` / `aws_kms_*` -> `shape=mxgraph.aws4.key;` / `shape=mxgraph.aws4.iam;`
  * Azure: `azurerm_storage_*` -> `shape=mxgraph.azure2.storage;`, `azurerm_function_app` -> `shape=mxgraph.azure2.function;`
  * GCP: `google_storage_*` -> `shape=mxgraph.gcp2.storage;`, `google_bigquery_*` -> `shape=mxgraph.gcp2.bigquery;`
  * Databricks: `databricks_*` -> `shape=mxgraph.aws4.databricks;` or official spark analytics stencil.
  * Snowflake: `snowflake_*` -> `shape=mxgraph.aws4.snowflake;` or data warehouse stencil.
* **FR-01.2 (Category Fallback):** Unrecognized resource types resolve to standardized category stencils based on role (Compute, Storage, Database, Network, Security, Observability).

### FR-02: Dynamic Zone & Container Clustering
* **FR-02.1 (VPC / Network Perimeter):** Detects `aws_vpc`, `azurerm_virtual_network`, or `google_compute_network` and wraps resources in a dashed `#005BA1` perimeter with `fillColor=#E8F4F8`.
* **FR-02.2 (Subnet Tiers):** Discovers and color-codes subnets:
  * Ingress / Public: `#E0F2FE` (Sky Blue)
  * Compute / App: `#F3E8FF` (Lavender)
  * Persistence / Lake Storage: `#FEF3C7` (Amber)
  * Security & IAM Boundary: `#FFE4E6` (Rose)
  * Observability Band: `#ECFDF5` (Emerald)
* **FR-02.3 (Component Collapsing):** Collapses auxiliary configuration resources (e.g. S3 Public Access Block, bucket encryption, versioning, lifecycle rules) into parent resource cards with configuration badges to prevent canvas clutter.

### FR-03: Dynamic Node Sizing & Technical Metadata Extraction
* **FR-03.1 (Compute Attributes):** Parses `change.after` in `plan.json` to extract `worker_type`, `number_of_workers`, `instance_type`, `rpu_capacity`, or `runtime` (e.g. `Glue 4.0 (2x G.1X)` or `8 RPU Serverless`).
* **FR-03.2 (Security Badges):** Extracts encryption settings (`kms_key_id`, `sse_algorithm`) and public exposure flags (`publicly_accessible == false`, `block_public_acls == true`) and renders lock/shield status.
* **FR-03.3 (Storage & Partitioning):** Extracts retention windows, table formats (`Iceberg v2`), and partition keys (`event_date`).

### FR-04: Topological Flow Discovery & Numbered Sequence Badges
* **FR-04.1 (Reference Graph Traversal):** Inspects resource reference bindings (`module.<x>.outputs`, input references) to build directed edges between producers, transformers, and consumers.
* **FR-04.2 (Sequence Hops):** Traces the primary data path and labels connector edges with sequential numbered markers (`[1] Ingest`, `[2] Transform`, `[3] Quality Check`, `[4] Store`, `[5] Serving`).
* **FR-04.3 (Orthogonal Routing):** Enforces `edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;` for crisp 90-degree lines without diagonal crossings.

### FR-05: 1-Click Deflated URL Encoding
* **FR-05.1 (Standard-Library Deflater):** Encodes XML into `https://app.diagrams.net/#R<deflated-base64>` using raw DEFLATE (headerless `-15` window bits) and URL-safe Base64 encoding.
* **FR-05.2 (Zero Network Call):** Generates URLs entirely offline with zero external API calls.

### FR-06: In-Canvas Step Execution Ledger
* **FR-06.1 (Embedded Ledger Cell):** Renders a structured specification table cell directly on the Draw.io canvas containing:
  * Sequence Hop (`[1]`, `[2]`, etc.)
  * Source & Destination components
  * Communication Protocol (HTTPS, JDBC, Spark In-Memory, AMQP)
  * Latency SLA Budget
  * Technical Safeguards & Encryption
* **FR-06.2 (Markdown Ledger Export):** Exports matching Markdown flow table for PR comments and deployment reports.

### FR-07: Operator CLI Surface (`minusctl diagram`)
* **FR-07.1:** Expose `minusctl diagram` with flags:
  * `--run <run-id>`: Target run workspace (defaults to active session context).
  * `--dir <tf-dir>`: Explicit Terraform directory.
  * `--format {all,drawio,url,ledger,svg}`: Selection of output artifacts (default: `all`).
  * `--out-dir <path>`: Target output directory (defaults to `reports/<plan-hash>/` or `runs/<run-id>/reports/`).
  * `--json`: Structured JSON output containing file paths and the 1-click URL.

### FR-08: Dedicated Skill & Agent Manifest
* **FR-08.1:** Register `.agents/skills/architecture-diagrammer/SKILL.md` for interactive architecture synthesis, diagram generation, and reviews.
* **FR-08.2:** Provide prompt templates for generating pre-plan and post-plan Draw.io blueprints.

---

## 5. Delivery Work Packages & Test Plan

| Work Package | Target Files | Delivered Scope |
| :--- | :--- | :--- |
| **WP-01** | `core/reporting/drawio_generator.py` | Complete stdlib Draw.io XML generator, dynamic stencil mapper, cluster discovery, flow edge router, and URL deflater. |
| **WP-02** | `core/cli/commands/diagram.py`, `core/cli/main.py` | First-class `minusctl diagram` CLI command with run-context resolution and multi-format outputs. |
| **WP-03** | `core/reporting/reporter.py` | Integrate Draw.io XML and 1-click URL generation into standard deploy report bundles. |
| **WP-04** | `.agents/skills/architecture-diagrammer/SKILL.md` | Dedicated architecture diagramming skill manifest. |
| **WP-05** | `tests/test_drawio_generator.py`, `tests/test_cli_package.py` | Full automated test suite verifying XML validity, dynamic discovery, URL encoding, CLI flags, and stdlib-only constraints. |

---

## 6. Acceptance Criteria (Sign-Off Invariants)

1. **Zero External Dependencies:** `core/reporting/drawio_generator.py` must import exclusively from Python standard library (`import xml.etree.ElementTree, zlib, base64, urllib.parse, json, re, os, sys`).
2. **Zero Hardcoded Nodes:** Tested across 3 diverse plan fixtures (AWS Lakehouse, Ingestion SFTP/Webhook, and Redshift BI); all resource titles, stencils, clusters, and edges must be derived dynamically from `plan.json`.
3. **URL Round-Trip Compression:** Decompressing the generated `app.diagrams.net/#R<deflated-base64>` payload must yield the exact original XML string.
4. **All Tests Passing:** `pytest tests/test_drawio_generator.py` and the full repo test suite (`pytest`) must pass with 100% exit code 0.
5. **Clean Zero-Emoji Outputs:** All outputs and documentation must strictly adhere to the zero-emoji invariant.
