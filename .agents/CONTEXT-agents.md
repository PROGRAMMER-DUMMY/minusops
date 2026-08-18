# .agents Context Index

This document provides exhaustive context for all rules, guides, and skill manifests located within the [`.agents`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents) directory.

---

## 1. Operating Rules & Core Principles

- [`.agents/AGENTS.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/AGENTS.md): Project rules and workspace constraints for CLI agents. Defines Human-in-the-Loop (HITL) gatekeeping requirements, pre-execution review rules, dry-run enforcement (`terraform plan`), mandatory audit logging, skill activation triggers, and requirements-first creation workflows.

---

## 2. Skill Manifests (`.agents/skills/`)

- [`.agents/skills/architect/SKILL.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/skills/architect/SKILL.md): Research-driven architecture synthesis skill. Orchestrates research of current cloud services and reference architectures, maps requirements against vetted modules (`core/generation/modules.py`), composes governed HCL via `synthesizer.py`, and passes the plan through the deploy gate.
- [`.agents/skills/grill-me/SKILL.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/skills/grill-me/SKILL.md): Requirements gathering and decision tree stress-testing skill. Interrogates functional and non-functional requirements (ISO 25010 / FURPS+), quantifying assumptions and asking targeted questions one at a time before infrastructure generation. Step 3.4 adds the 7 data-engineering pillars (ingestion source, storage & format, compute engine, orchestration, data quality, serving layer, alert routing), each mapped to the catalog modules its answers imply, with ingestion as question one. Step 3.5 adds the TerraShark failure-mode pre-flight (FM-01 identity churn, FM-02 secret exposure, FM-03 blast radius, FM-04 CI drift, FM-05 compliance gate gaps), and the exit criteria state the 4-part ADR output contract (assumptions, tradeoffs, validation, rollback) that `core/architecture/architecture_decision.py` enforces. The stale `aws-data-pipeline-standard` / `--generate` guidance AGENTS.md flags has been removed; the file now maps answers to modules and hands off to `architect`, and the only surviving mention of the demo blueprint is an explicit prohibition (asserted by `tests/test_ingestion_modules.py`).
- [`.agents/skills/minusops-loop/SKILL.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/skills/minusops-loop/SKILL.md): Complete end-to-end execution loop for creating, modifying, and deploying AWS infrastructure through the plan-bound deploy gate.
- [`.agents/skills/pipeline-optimizer/SKILL.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/skills/pipeline-optimizer/SKILL.md): Infrastructure scanning and optimization skill for AWS data pipelines (Databricks, Glue, EMR, Redshift). Analyzes HCL for security, cost, performance, triggers, and observability.
- [`.agents/skills/resolve-ambiguity/SKILL.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/skills/resolve-ambiguity/SKILL.md): Ambiguity resolution skill. Used when requests are underspecified or support conflicting implementation paths, framing targeted questions with recommended defaults.
- [`.agents/skills/terraform-orchestrator/SKILL.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/skills/terraform-orchestrator/SKILL.md): Reliable execution and orchestration skill for running Terraform operations, verifying plan hashes, enforcing MFA approval, and executing health checks.

---

## 3. Runtime & Artifact Directories (`.agents/`)

- [`.agents/logs/`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/logs): Directory storing tamper-evident audit logs (`audit.jsonl`), locks, health check reports, budget estimates, and per-directory plan-gate artifacts (`plan_gate/`).
- [`.agents/reports/`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/reports): Directory reserved for generated deployment and governance reports.
- [`.agents/schema-lint-work/`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/schema-lint-work): Working directory for HCL/JSON schema linting and workspace validation.
- [`.agents/tf-plugin-cache/`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/.agents/tf-plugin-cache): Local Terraform provider plugin caching directory.

