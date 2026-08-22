# Product Requirements Document (PRD) — Enterprise Extensibility, Custom Tooling & Agent Integration Guide (v10.0)

| Attribute | Details |
| :--- | :--- |
| **Document ID** | PRD-ARCH-2026-010 (Revision 10.0 — Extensibility Framework, Custom Integrations & Agent Skills) |
| **Document Name** | `tasks/prd_v10_enterprise_extensibility_and_developer_guide.md` |
| **Status** | APPROVED ARCHITECTURE SPECIFICATION |
| **Lead Architect** | Matt (Principal Cloud Architect & CLI Design Lead) |
| **Author** | MinusOps Autonomous Architecture Engine |
| **Target Components** | `docs/extensibility_and_integration_guide.md`, `.agents/skills/context-graph/`, `.agents/skills/integration-guide/`, `core/cli/`, `core/integrations/`, `modules/` |
| **Target Audience** | Enterprise Engineers, Open-Source Contributors, Autonomous CLI Agents (`agy`, `codex`, `claude code`) |
| **Date** | August 22, 2026 |

---

## 1. Executive Summary & Problem Statement

As MinusOps expands across enterprise data platforms, teams require clear, standardized protocols to customize, extend, and plug in new capabilities without violating core governance invariants (plan-hash gating, zero un-gated mutations, audit logging, zero-dependency CLI core, and emoji-free terminal output).

This specification establishes:
1. **The Extensibility Framework (`docs/extensibility_and_integration_guide.md`):** A comprehensive, authoritative manual detailing how human engineers and AI CLI agents author and wire new capabilities across 6 core extension vectors.
2. **Two Repo-Local Agent Skills:**
   * **`context-graph` (`.agents/skills/context-graph/SKILL.md`):** Protocol for reading, auditing, and maintaining file-by-file context trees (`CONTEXT-MAP.md` and `CONTEXT-[folder].md`) with zero documentation drift.
   * **`integration-guide` (`.agents/skills/integration-guide/SKILL.md`):** Operational rules and step-by-step procedures for adding new tools, CLI subcommands, Terraform modules, notification hooks, and subagents.
3. **Automated Link & Reference Integrity:** Formal CI checking rules ensuring internal documentation and task links never break during repository reorganization.

---

## 2. The 6 Enterprise Extension Vectors

```text
                                [ MinusOps Extensibility Hub ]
                                               │
         ┌──────────────────┬──────────────────┼──────────────────┬──────────────────┐
         ▼                  ▼                  ▼                  ▼                  ▼
  [ Vector 1: CLI ]  [ Vector 2: Mod ]  [ Vector 3: Hook ] [ Vector 4: Agent ] [ Vector 5: Skill ]
  New `minusctl`     New Terraform      New Outbound       New Autonomous      New Local Agent
  Subcommand in      Building Block in  Integration in     Subagent in         Workflow Skill in
  `core/cli/`        `modules/`         `integrations/`    `.agents/subagents/``.agents/skills/`
```

### Vector 1: Adding a New `minusctl` CLI Command
* **Files to touch:**
  1. Create `core/cli/commands/<subcommand>.py` implementing `add_parser(subparsers)` and `run(args)`.
  2. Register in `core/cli/main.py` under `NATIVE`, `COMMAND_GROUPS`, and `COMMAND_HELP`.
  3. Wire active run resolution via `core/cli/context.py:resolve_context()`.
  4. Add unit test in `tests/test_cli_package.py` and `tests/test_cli_help.py`.

### Vector 2: Adding a New Terraform Building Block Module
* **Files to touch:**
  1. Create `modules/<module-id>/main.tf` with parameterized inputs and outputs.
  2. Add `PROVENANCE.json` documenting upstream source, provider version, and security validations.
  3. Register in `core/generation/modules.py` (declaring requirement matching keywords, inputs, and outputs).
  4. Add module directory to `pyproject.toml` under `[tool.setuptools.data-files]`.
  5. Add module entry to `modules/CONTEXT-modules.md`.
  6. Add unit test in `tests/test_modules.py`.

### Vector 3: Adding an Outbound Integration / Notification Hook
* **Files to touch:**
  1. Create `core/integrations/<service>_hook.py` (e.g. `pagerduty_hook.py`, `webhook_hook.py`).
  2. Route all mutating/outbound side-effects through `core/governance/approval.py` with action key and audit details.
  3. Enforce bearer-token security: never accept, echo, or log secret tokens/webhook URLs.
  4. Check delivery boolean (`{"ok": true, "sent": bool}`) before reporting success.
  5. Document in `core/integrations/CONTEXT-integrations.md`.

### Vector 4: Adding an Autonomous Notification Subagent
* **Files to touch:**
  1. Create `.agents/subagents/<name>-agent.md` defining role, system prompt, tool constraints, and single-dispatch exit rule.
  2. Register in `AGENTS.md` and `.agents/AGENTS.md` Section 0.

### Vector 5: Adding a Decision / Workflow Agent Skill
* **Files to touch:**
  1. Create `.agents/skills/<skill-name>/SKILL.md` with YAML frontmatter (`name`, `description`) and step-by-step procedures.
  2. Register trigger in `AGENTS.md` Section 3.1 and `.agents/AGENTS.md`.

### Vector 6: Maintaining the Context Graph
* **Files to touch:**
  1. Update `CONTEXT-[folder].md` whenever modifying code in a directory.
  2. Register any new directories in `CONTEXT-MAP.md`.

---

## 3. Functional Requirements (FR)

* **FR-01 (Authoritative Guide):** Deliver `docs/extensibility_and_integration_guide.md` containing detailed step-by-step templates and code examples for all 6 extension vectors.
* **FR-02 (`context-graph` Skill):** Create `.agents/skills/context-graph/SKILL.md` specifying context audit procedures, cross-reference rules, and link validation standards.
* **FR-03 (`integration-guide` Skill):** Create `.agents/skills/integration-guide/SKILL.md` providing operational checklists for agents adding CLI commands, modules, hooks, and tools.
* **FR-04 (`AGENTS.md` & Root Index Sync):** Update `AGENTS.md`, `.agents/AGENTS.md`, and `CONTEXT-MAP.md` to index the two new skills.

---

## 4. Non-Functional Invariants (NFR)

* **NFR-01 (Strict Zero Emojis):** Absolutely no emojis across all markdown guides, skill manifests, docstrings, terminal outputs, and code comments.
* **NFR-02 (Standard Library Core):** Extensions to core CLI and governance modules must rely strictly on Python standard library to preserve zero-dependency lightweight installation.
* **NFR-03 (Plan-Bound Safety):** Any extension that creates or mutates infrastructure must route through `core/governance/plan_gate.py` and `core/governance/approval.py`.

---

## 5. Acceptance Criteria

1. **AC-01:** `docs/extensibility_and_integration_guide.md` exists and covers all 6 extension vectors with complete code examples.
2. **AC-02:** `.agents/skills/context-graph/SKILL.md` exists and satisfies the context maintenance protocol.
3. **AC-03:** `.agents/skills/integration-guide/SKILL.md` exists and provides step-by-step implementation checklists.
4. **AC-04:** `CONTEXT-MAP.md` and `AGENTS.md` reference the new skills.
5. **AC-05:** Full test suite passes cleanly with zero regressions.
