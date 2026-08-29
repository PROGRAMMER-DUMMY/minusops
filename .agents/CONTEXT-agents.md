# .agents Context Index

This document provides exhaustive context for all rules, guides, and skill manifests located within the [`.agents`](./) directory.

---

## 1. Operating Rules & Core Principles

- [`.agents/AGENTS.md`](./AGENTS.md): Project rules and workspace constraints for CLI agents. Defines Human-in-the-Loop (HITL) gatekeeping requirements, pre-execution review rules, dry-run enforcement (`terraform plan`), mandatory audit logging, skill activation triggers, and requirements-first creation workflows.

---

## 2. Skill Manifests (`.agents/skills/`)

- [`.agents/skills/architect/SKILL.md`](./skills/architect/SKILL.md): Research-driven architecture synthesis skill. Orchestrates research of current cloud services and reference architectures, maps requirements against vetted modules (`core/generation/modules.py`), composes governed HCL via `synthesizer.py`, and passes the plan through the deploy gate.
- [`.agents/skills/grill-me/SKILL.md`](./skills/grill-me/SKILL.md): Requirements gathering and decision tree stress-testing skill. Interrogates functional and non-functional requirements (ISO 25010 / FURPS+), quantifying assumptions and asking targeted questions one at a time before infrastructure generation. Step 3.4 runs the **19 enterprise pillars** across four phases, with ingestion as question one. The pillars themselves are **not** written out in the skill: they live in [`core/architecture/pillars.py`](../core/architecture/pillars.py) and the skill reads them from its CLI, so the document and the generator cannot disagree. That module also derives later questions from earlier answers -- a Glue worker plan, an S3 object-size verdict, a Kinesis shard count -- against published service capacities that are cited next to the arithmetic, and returns `determinable: false` naming the missing fact rather than inventing a default. Step 3.5 adds the TerraShark failure-mode pre-flight (FM-01 identity churn, FM-02 secret exposure, FM-03 blast radius, FM-04 CI drift, FM-05 compliance gate gaps), and the exit criteria state the 4-part ADR output contract (assumptions, tradeoffs, validation, rollback) that `core/architecture/architecture_decision.py` enforces. The stale `aws-data-pipeline-standard` / `--generate` guidance AGENTS.md flags has been removed; the file maps answers to modules and hands off to `architect`, and the only surviving mention of the demo blueprint is an explicit prohibition (asserted by `tests/test_ingestion_modules.py`).
- [`.agents/skills/architecture-diagrammer/SKILL.md`](./skills/architecture-diagrammer/SKILL.md): Generates Draw.io architecture diagrams from a Terraform plan using the standard library only -- provider stencil mapping, zone clustering, topological flow discovery, and the 1-click `app.diagrams.net` URL. Driven by `minusctl diagram`.
- [`.agents/skills/context-graph/SKILL.md`](./skills/context-graph/SKILL.md): Maintains this context tree -- `CONTEXT-MAP.md` and every `CONTEXT-[folder].md` -- whenever code is added, moved, or refactored. Also carries the repo-relative link rule and the zero-emoji format invariants that `tests/test_docs_examples.py` enforces.
- [`.agents/skills/doctor/SKILL.md`](./skills/doctor/SKILL.md): Day-0 environment pre-flight -- CLI binaries and version floors, AWS caller identity and credential posture (temporary vs. long-term keys), the seeded Terraform lock file and shared plugin cache, and the five distinct G9 emulator states. One verdict, exit non-zero only on `error`.
- [`.agents/skills/integration-guide/SKILL.md`](./skills/integration-guide/SKILL.md): Checklists for extending the control plane -- a new `minusctl` subcommand, a new Terraform module, a new outbound integration hook, or a new subagent -- without breaking the governance invariants (zero emojis, stdlib-only core, plan-bound deploys, approval-gated side effects, fail-closed context).
- [`.agents/skills/minusops-loop/SKILL.md`](./skills/minusops-loop/SKILL.md): Complete end-to-end execution loop for creating, modifying, and deploying AWS infrastructure through the plan-bound deploy gate.
- [`.agents/skills/pipeline-optimizer/SKILL.md`](./skills/pipeline-optimizer/SKILL.md): Infrastructure scanning and optimization skill for AWS data pipelines (Databricks, Glue, EMR, Redshift). Analyzes HCL for security, cost, performance, triggers, and observability.
- [`.agents/skills/resolve-ambiguity/SKILL.md`](./skills/resolve-ambiguity/SKILL.md): Ambiguity resolution skill. Used when requests are underspecified or support conflicting implementation paths, framing targeted questions with recommended defaults.
- [`.agents/skills/terraform-orchestrator/SKILL.md`](./skills/terraform-orchestrator/SKILL.md): Reliable execution and orchestration skill for running Terraform operations, verifying plan hashes, enforcing MFA approval, and executing health checks.

---

## 3. Transport Subagent Manifests (`.agents/subagents/`)

Five single-purpose manifests, each dispatching one message through `core/integrations/` and
stopping. They are transport-only: they know how to reach a channel, not which events belong
there. Routing is a customer decision captured by `grill-me` pillar 7 and resolved per team by
`core/architecture/team_resolver.py`.

- `slack-agent.md`: P1 pipeline incidents and plan-approval cards (interactive cards require a plan hash).
- `teams-agent.md`: data-quality failures and quarantine alerts, as Adaptive Cards.
- `outlook-agent.md`: executive FinOps email with the generated workbooks attached. May not state a cost figure it did not read from the artifact.
- `confluence-agent.md`: living architecture pages. Publishes generated markdown; never composes its own.
- `jira-agent.md`: change tickets for a governed deploy. One ticket per invocation -- the hook does not deduplicate, so calling it twice creates two. When Jira is unwired it writes the payload to a local file, which is a file and not a ticket, and must be reported as such.

Canonical location. These are read by whichever agent runtime drives MinusOps, per the
activation rule in `AGENTS.md` -- the same prompt-level convention `.agents/skills/` uses.
A Claude-Code-specific copy under `.claude/agents/` was removed: two byte-identical copies
drift, and the one nothing loads is the one nobody notices going stale.

---

## 4. Execution Guardrail (`.claude/`)

- [`.claude/hooks/guardrails.py`](../.claude/hooks/guardrails.py): The `PreToolUse` adapter that runs [`core/governance/agent_guardrails.py`](../core/governance/agent_guardrails.py) against a real tool call, registered in [`.claude/settings.json`](../.claude/settings.json). Exit 0 allows, exit 2 blocks with the reason shown to the agent, exit 1 is a visible non-blocking error used when the payload cannot be parsed -- a hook that cannot read its input must neither silently allow (which hides the outage) nor block every call (which bricks the session). Bash commands are always checked; writes are checked only when `MINUS_AGENT_RUN_ID` declares a run scope, because `evaluate_write()` refuses every path without one, which is right for an autonomous agent and would otherwise block a developer editing this repo. **Not a sandbox** -- it stops a mistake, not an intent.

---

## 5. Runtime & Artifact Directories (`.agents/`)

- [`.agents/logs/`](./logs/): Directory storing tamper-evident audit logs (`audit.jsonl`), locks, health check reports, budget estimates, and per-directory plan-gate artifacts (`plan_gate/`).
- [`.agents/reports/`](./reports/): Directory reserved for generated deployment and governance reports.
- [`.agents/schema-lint-work/`](./schema-lint-work/): Working directory for HCL/JSON schema linting and workspace validation.
- [`.agents/tf-plugin-cache/`](./tf-plugin-cache/): Local Terraform provider plugin caching directory.

