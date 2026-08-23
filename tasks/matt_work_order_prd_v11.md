# Masterclass Review & Work Order Authorization (Matt)

| Attribute | Details |
| :--- | :--- |
| **Document ID** | WO-ARCH-2026-011 |
| **From** | Matt (Principal Cloud Architect & CLI Design Lead) |
| **To** | Coding Agent |
| **Subject** | Work Order & PRD v11 Architectural Alignment |
| **Status** | WORK ORDER AUTHORIZED (Step 1 Sanitizer & Steps 2-4 Deltas) |
| **Date** | August 23, 2026 |

---

## 1. Architectural Commendation on the Review

This review is exceptional systems engineering. Spotting that Phase 1 was 50% delivered, catching the `pipeline_name` sequential replacement / YAML injection defect, replacing an over-engineered 8-class ABC with a lean function registry `HOPS = {...}`, and correcting the non-existent CLI target files prevents technical debt before a single line of code is written.

Your proposal for **FR-05** is accepted verbatim:
1. Use the run's declared `tier` (`runs.py`).
2. Add `silent: bool` to `FailureRule`.
3. Apply the Lake Formation / PII hard floor (any PII/masking breach = immediate P1).
4. Route on calculated severity, not source tool.
5. If tier is undeclared, emit `UNCLASSIFIED — needs human triage` (refusing to guess).

---

## 2. Work Order: Implementation Steps

Please proceed with implementation in the following 5 focused steps:

---

### Step 1: Fix `pipeline_name` Sanitizer & Switch `cicd.py` to `string.Template`
* In `core/generation/cicd.py`:
  * Validate `pipeline_name` at the boundary with `^[a-z0-9][a-z0-9-]{0,62}$` (refuse with `ValueError` otherwise).
  * Refactor `_fill()` to use `string.Template.safe_substitute()` to eliminate sequential replacement collisions.
  * Add mutation test asserting `render_pipeline_workflow('a"b')` raises.

---

### Step 2: Implement FR-02 (Artifact Staging in `cicd.py`)
* Add `--artifact-repo {artifactory|ecr|codeartifact|s3}` to `render_pipeline_workflow()` and `render_jenkinsfile()`.
* Conditionally emit Jenkins Artifactory steps (`rtUpload`, `rtPublishBuildInfo`) **only** when `--artifact-repo artifactory` is active.
* Add YAML parsing assertion (`yaml.safe_load`) in `tests/test_cicd.py` to verify rendered workflows are syntactically valid.

---

### Step 3: Implement FR-03 (Modular Proving Registry in `core/reporting/seed.py`)
* Implement the function registry: `HOPS = {"ingest": _hop_ingest, "transform": _hop_transform, "dq": _hop_dq, "quarantine": _hop_quarantine, "query": _hop_query, "latency_sla": _hop_latency, "pii": _hop_pii}`.
* Support `blocking: bool` on each hop (ingest/transform are blocking; latency_sla is non-blocking).
* Record `not_run`, `passed`, and `failed` explicitly in `reports/<plan-hash>/proving_report.json`.
* Preserve `seed()` 3-hop contract and add `--hops` flag to `minusctl.py` prove command.

---

### Step 4: Implement FR-05 (Dynamic Incident Severity Engine in `incident_diagnostics.py`)
* Add `silent: bool` to `FailureRule`.
* Implement dynamic severity evaluation combining `run.tier` (Tier 0 to Tier 3), `rule.silent`, and `has_pii` signals.
* Emit `UNCLASSIFIED` when `tier` is missing.
* Update routing table: P1 -> PagerDuty, P2 -> Slack/Teams on-call, P3 -> Jira/Outlook, P4 -> log.

---

### Step 5: Test Suite Execution & Invariant Verification
* Run fast test suite: `pytest` (1,227+ tests passing, exit code 0).
* Verify zero emojis across all newly touched files.
