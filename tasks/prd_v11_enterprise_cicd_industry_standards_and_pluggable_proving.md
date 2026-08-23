# Product Requirements Document (PRD) — Enterprise CI/CD Pipeline Engine, Industry-Standard Tooling & Pluggable Proving (v11.0)

| Attribute | Details |
| :--- | :--- |
| **Document ID** | PRD-ARCH-2026-011 (Revision 11.0 — Enterprise CI/CD, Industry Standards & Modular Proving) |
| **Document Name** | `tasks/prd_v11_enterprise_cicd_industry_standards_and_pluggable_proving.md` |
| **Status** | APPROVED SPECIFICATION FOR REVIEW & IMPLEMENTATION |
| **Lead Architect** | Matt (Principal Cloud Architect & CLI Design Lead) |
| **Target Components** | `core/generation/cicd.py`, `core/reporting/seed.py`, `core/reporting/incident_diagnostics.py`, `core/cli/commands/export.py`, `core/cli/commands/prove.py` |
| **Target Audience** | Coding Agent, Platform Engineers, Enterprise DevOps Teams, Open-Source Contributors |
| **Date** | August 23, 2026 |

---

## 1. Executive Summary & Core Motivation

Modern enterprise data platforms rely heavily on proven, industry-standard toolchains (**Jenkins, JFrog Artifactory, Docker, Amazon ECR, Trivy, SonarQube, Prometheus, Grafana, and Alertmanager**).

This specification upgrades the MinusOps CI/CD synthesis engine (`core/generation/cicd.py`) and proving harness (`core/reporting/seed.py`) to deliver:
1. **First-Class Dual-Engine CI/CD Generation:** Synthesize production-ready **GitHub Actions workflows** (cloud OIDC) and **Declarative Jenkinsfiles** (private VPC agents + JFrog Artifactory).
2. **Immutable "Build Once, Deploy Many" Artifact Stage:** Package PySpark wheels, dbt bundles, and Docker containers once, sign with SHA256 digests, publish to JFrog Artifactory / Amazon ECR / S3, and pass immutable version tags into Terraform.
3. **Pluggable & User-Configurable UAT Proving Harness:** Transform the proving harness from a hardcoded 5-hop script into a modular hop catalog (Ingestion, ETL, DQ, Quarantine, Query, Lake Formation PII masking, Latency SLA benchmarks, and Custom user scripts).
4. **Automated 3-Tier SRE Incident Routing:** Integrate CloudWatch Logs Insights and CloudTrail correlation with automated severity triage (P1 Outages to PagerDuty/Slack, P2 Data Quality to Teams, P3 FinOps to Outlook).

---

## 2. Functional Requirements (FR)

### FR-01: Dual-Engine CI/CD Synthesis (`core/generation/cicd.py`)
* **FR-01.1 (GitHub Actions):** Synthesize `.github/workflows/deploy.yml` with:
  * AWS OIDC Workload Identity Federation (zero static `AKIA...` keys).
  * 4 parallel shift-left PR lanes (HCL linter, unit/DQ tests, Gitleaks/Trivy security scan, speculative `minusctl gate plan`).
  * Sticky PR comment posting the plan diff, cost delta, and cryptographic SHA256 `plan_hash`.
* **FR-01.2 (Declarative Jenkinsfile):** Synthesize `Jenkinsfile` for private VPC agents with:
  * Ambient AWS IAM Instance Profiles / EKS IRSA authentication.
  * Native JFrog Artifactory integration (`rtUpload`, `rtPublishBuildInfo`).
  * Parallel quality stages and interactive `input` step for human UAT approval before production apply.
* **FR-01.3 (Path-Isolated Triggers):** Workflows must trigger strictly on subpaths (`paths: ['pipelines/<name>/**']`) to prevent monorepo deployment crosstalk.

### FR-02: Immutable Artifact Management
* **FR-02.1:** CI build stage compiles application packages (`.whl`, `.tar.gz`, or Docker images) tagged with git commit SHAs.
* **FR-02.2:** Artifact metadata (SHA256 checksum, build number, git commit) is published to the configured repository (**JFrog Artifactory, Amazon ECR, AWS CodeArtifact, or Versioned S3 Bucket**).
* **FR-02.3:** The synthesized Terraform environment variables (`dev.tfvars`, `uat.tfvars`, `prod.tfvars`) consume the exact immutable artifact URI.

### FR-03: Pluggable & Modular UAT Proving Harness (`core/reporting/seed.py`)
* **FR-03.1 (Hop Catalog):** Support composable execution of modular proving hops:
  * `hop_ingest`: Seeds mock payload to S3 Bronze / Kinesis / Kafka.
  * `hop_transform`: Triggers Glue 4.0 / EMR Serverless / Databricks job.
  * `hop_data_quality`: Executes Great Expectations contracts or schema validations.
  * `hop_quarantine`: Injects malformed record and verifies dead-letter drop routing.
  * `hop_query`: Executes Athena / Redshift SQL queries against Gold tables.
  * `hop_pii_masking`: Assumes unauthorized consumer role to verify Lake Formation column masking.
  * `hop_latency_sla`: Benchmarks end-to-end processing time against latency budget.
  * `hop_custom_script`: Executes user-supplied shell or Python test scripts.
* **FR-03.2 (Proving Strategies):** Support user-selected proving strategies:
  * `full_5hop`: Default 5-hop end-to-end proof.
  * `custom_hops`: Array of specified hops from the catalog.
  * `health_probe`: Lightweight read-only connectivity check (`minusctl doctor`).
  * `manual_uat`: Skips automated proving, immediately triggering stakeholder notification.
* **FR-03.3 (Tamper-Evident Proving Report):** Writes signed JSON evidence to `reports/<plan-hash>/proving_report.json` with hop exit statuses, latencies, and output hashes.

### FR-04: Multi-Tier Promotion & Human Governance Gate
* **FR-04.1 (Dev Sandbox):** Automated deployment upon PR merge for early integration testing.
* **FR-04.2 (UAT Staging):** Automated deployment + execution of configured proving suite.
* **FR-04.3 (Human Production Gate):** Mandatory Human-in-the-Loop approval gate (GitHub Environment reviewer or Jenkins `input` step).
* **FR-04.4 (Two-Person Rule):** Production deployment enforces separation of duties; the PR author cannot self-approve.
* **FR-04.5 (Plan-Hash Bound Apply):** Production apply refuses execution if the active plan hash diverges from the verified UAT plan hash.

### FR-05: Incident Classification & Telemetry Routing
* **FR-05.1:** Automatically categorize operational failures via `incident_diagnostics.py`:
  * **P1 (Critical Outage):** Hard job failure, KMS access denied, S3 403, out-of-band drift -> Routes to **PagerDuty + Slack**.
  * **P2 (Data Quality / SLA):** Great Expectations failure, quarantine spike > 2% -> Routes to **Microsoft Teams / Slack**.
  * **P3 (FinOps Anomaly):** Spend anomaly > 20%, unpinned deprecation -> Routes to **Outlook (.xlsx email)**.

---

## 3. Non-Functional Invariants (NFR)

* **NFR-01 (Strict Zero Emojis):** Absolutely no emojis in terminal outputs, generated workflows, log files, or reports.
* **NFR-02 (Standard Library Core):** Generation engine must use Python standard library (`pathlib`, `json`, `dataclasses`, `string.Template`).
* **NFR-03 (Plan-Bound Safety):** Production infrastructure changes must route through `plan_gate.py` with plan-hash verification.
* **NFR-04 (Zero Stored Secrets):** No static cloud credentials in workflow files or repos; all cloud access must use AWS OIDC or ambient IAM instance profiles.

---

## 4. Detailed Implementation Tasks for Coding Agent

### Phase 1: Core CI/CD Engine Upgrades (`core/generation/cicd.py`)
- [ ] **Task 1.1:** Refactor `core/generation/cicd.py` to support dual-engine templates: `_GITHUB_WORKFLOW_TEMPLATE` and `_JENKINS_WORKFLOW_TEMPLATE`.
- [ ] **Task 1.2:** Implement `ArtifactConfig` dataclass supporting `jfrog_artifactory`, `amazon_ecr`, `aws_codeartifact`, and `s3_bucket`.
- [ ] **Task 1.3:** Implement 4-lane parallel PR checks in both GitHub Actions and Jenkinsfile.
- [ ] **Task 1.4:** Implement 3-tier promotion logic (`dev` -> `uat` with proving -> `prod` with human gate).
- [ ] **Task 1.5:** Wire `--engine {github|jenkins}` and `--artifact-repo {artifactory|ecr|s3}` into `core/cli/commands/export.py`.

### Phase 2: Modular Proving Harness Refactor (`core/reporting/seed.py` / `proving.py`)
- [ ] **Task 2.1:** Implement modular `ProvingHop` abstract base class and concrete hop implementations (`IngestHop`, `TransformHop`, `DataQualityHop`, `QuarantineHop`, `QueryHop`, `PIIMaskingHop`, `LatencySLAHop`, `CustomScriptHop`).
- [ ] **Task 2.2:** Update `core/reporting/seed.py` to parse proving configuration from `runs/<run-id>/requirements.json` or CLI flags.
- [ ] **Task 2.3:** Add `--hops` and `--config` arguments to `minusctl prove` command handler (`core/cli/commands/prove.py`).
- [ ] **Task 2.4:** Ensure `proving_report.json` captures per-hop execution status, runtime duration, and cryptographic output digests.

### Phase 3: Telemetry & Incident Severity Triage
- [ ] **Task 3.1:** Extend `core/reporting/incident_diagnostics.py` to return structured `IncidentSeverity` (`P1`, `P2`, `P3`) and recommended notification target.
- [ ] **Task 3.2:** Wire automated routing logic into notification hooks (`core/integrations/`).

### Phase 4: Test Suite & Verification
- [ ] **Task 4.1:** Author comprehensive unit tests in `tests/test_cicd_synthesis.py` verifying GitHub Actions and Jenkinsfile generation.
- [ ] **Task 4.2:** Author unit tests in `tests/test_proving_customization.py` verifying hop composability and custom hop execution.
- [ ] **Task 4.3:** Run full test suite: verify 1,224+ tests passing with exit code 0.
- [ ] **Task 4.4:** Verify zero emojis across all newly authored files.

---

## 5. Acceptance Criteria (Definition of Done)

1. **AC-01:** `core/generation/cicd.py` synthesizes complete, syntactically valid `.github/workflows/deploy.yml` and `Jenkinsfile` configurations.
2. **AC-02:** `minusctl prove --hops ingest,transform,query --execute` executes only the requested hops and emits a signed `proving_report.json`.
3. **AC-03:** `minusctl export --generate-workflow --engine jenkins --artifact-repo artifactory` outputs a production-ready Jenkinsfile with Artifactory upload steps.
4. **AC-04:** All unit tests pass cleanly with 100% green exit code 0.
5. **AC-05:** Zero emojis or decorative unicode characters in any newly created files.

---

## 6. Architectural Directives & Coding Agent Review Work Order (Matt)

**To:** Coding Agent  
**From:** Matt (Principal Cloud Architect & CLI Design Lead)  
**Subject:** Architectural Review of PRD v11.0 (Enterprise CI/CD Engine, Industry Standards & Pluggable Proving)  
**Status:** **SUBMITTED FOR CODING AGENT AUDIT & REVIEW**

### Review Instructions for Coding Agent

Please conduct a thorough architectural review of this specification and provide your analysis on the following points:

#### Review Point 1: CI/CD Engine & Template Architecture
* Review the dual-engine abstraction in `core/generation/cicd.py`.
* Verify whether the `__TOKEN__` placeholder substitutions are robust against Jenkins Groovy `${...}` and GitHub Actions `${{ ... }}` syntax collisions.
* Confirm the Jenkins declarative pipeline structure meets enterprise standards for Jenkins agents running in private VPCs.

#### Review Point 2: Modular Proving Architecture
* Review the proposed `ProvingHop` design in `core/reporting/seed.py`.
* Evaluate how to structure the execution context and state passing between hops (e.g. passing the generated S3 mock key from `IngestHop` to `TransformHop`).
* Determine what failure handling strategy should be applied when a non-critical extension hop (like Latency SLA) fails versus a critical hop (like Ingestion).

#### Review Point 3: CLI Surface & Backward Compatibility
* Inspect `minusctl prove` and `minusctl export`.
* Ensure that the new flags (`--engine`, `--artifact-repo`, `--hops`) are fully backward-compatible with existing 5-hop runs and legacy `seed` commands.

#### Review Point 4: Implementation Phasing & TDD Plan
* Review the 4-phase task breakdown in Section 4.
* Propose your test plan (unit test files, mocking strategies for Artifactory/Jenkins/AWS calls, and mutation checks).

