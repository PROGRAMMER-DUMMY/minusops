---
name: integration-guide
description: Guide for adding new tools, CLI subcommands, Terraform modules, outbound integration hooks, and autonomous subagents to MinusOps. Use whenever creating new capabilities, extending the minusctl command surface, or integrating third-party services.
---

# Integration & Extensibility Skill

This skill guides agents and developers through creating, modifying, and integrating new capabilities into the **MinusOps** control plane without violating core governance invariants.

---

## 1. Golden Rules of MinusOps Extensibility

Before proposing or implementing any extension, verify:
1. **Zero Emojis:** Strictly no emoji characters in any terminal output, logs, or markdown.
2. **Zero Core Dependencies:** Core CLI (`core/cli/`) must use only standard library modules.
3. **Plan-Bound Deploy Safety:** Infrastructure changes must route through `plan_gate.py` (`verify` -> `plan` -> `approve` -> `apply`).
4. **Approval Gating:** Outbound mutating side-effects must route through `approval.py`.
5. **Fail-Closed Context:** Run-scoped commands must use `context.py:resolve_context()` and refuse if context is empty.

---

## 2. Checklists for Common Extension Tasks

### A. Adding a New `minusctl` CLI Subcommand
1. [ ] Create `core/cli/commands/<name>.py` implementing `add_parser(subparsers)` and `run(args)`.
2. [ ] Register in `core/cli/main.py` under `NATIVE`, `COMMAND_GROUPS`, and `COMMAND_HELP`.
3. [ ] Resolve active run via `core/cli/context.py:resolve_context()`.
4. [ ] Format outputs with `core/cli/formatters.py` and respect `NO_COLOR` in `core/cli/theme.py`.
5. [ ] Add unit tests in `tests/test_cli_package.py` and `tests/test_cli_help.py`.
6. [ ] Update [`core/cli/CONTEXT-cli.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/cli/CONTEXT-cli.md) and [`AGENTS.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/AGENTS.md).

### B. Adding a New Terraform Building Block Module
1. [ ] Create `modules/<module-id>/main.tf` with typed variables and explicit outputs.
2. [ ] Add `modules/<module-id>/PROVENANCE.json`.
3. [ ] Register in [`core/generation/modules.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/generation/modules.py) with match keywords and I/O.
4. [ ] Add `"modules/<module-id>/*"` to `[tool.setuptools.data-files]` in `pyproject.toml`.
5. [ ] Add documentation entry in [`modules/CONTEXT-modules.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/modules/CONTEXT-modules.md).
6. [ ] Add module tests in `tests/test_modules.py`.

### C. Adding an Outbound Integration Hook
1. [ ] Create `core/integrations/<service>_hook.py`.
2. [ ] Route dispatch through `approval.request_approval(action=..., details=..., mode=approval_mode)`.
3. [ ] Never accept, echo, or log secret tokens or webhook URLs.
4. [ ] Return `{"ok": true, "sent": false}` when channel is unconfigured.
5. [ ] Append event to audit trail via `audit_logger.log_audit_event()`.
6. [ ] Update [`core/integrations/CONTEXT-integrations.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/integrations/CONTEXT-integrations.md).

### D. Adding an Autonomous Subagent
1. [ ] Create `.agents/subagents/<name>-agent.md` specifying role, tools, and single-dispatch exit rule.
2. [ ] Register manifest in [`AGENTS.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/AGENTS.md) Section 0.
