# MinusOps Extensibility & Integration Guide

This guide provides the canonical, step-by-step engineering procedures for adding new capabilities, custom tools, CLI subcommands, Terraform modules, outbound hooks, and autonomous subagents to **MinusOps**.

It is written for both **human software engineers** and **autonomous AI CLI agents** (`agy`, `codex`, `claude code`).

---

## 1. Core Architectural Invariants (Read First)

Every extension to MinusOps must honor these non-negotiable rules:

1. **Zero Emojis:** Strictly no emojis or decorative unicode glyphs in any terminal text, log outputs, code comments, or documentation.
2. **Zero Core Dependencies:** Core CLI (`core/cli/`) and governance modules must rely exclusively on the Python standard library (`argparse`, `pathlib`, `json`, `dataclasses`, `string.Template`, `subprocess`).
3. **Plan-Bound Deploy Safety:** Any code that creates, alters, or destroys infrastructure must route through `core/governance/plan_gate.py` (`verify` -> `plan` -> `approve` -> `apply`). Never run un-gated mutating cloud API calls.
4. **Approval & Audit Gating:** Outbound side-effects (Slack/Teams/Email/Jira webhooks, mutating cloud calls) must route through `core/governance/approval.py` and write to the tamper-evident audit chain (`audit_chain.py`).
5. **Fail-Closed Context Resolution:** Commands that act on a run workspace must resolve the active run via `core/cli/context.py:resolve_context()` and fail closed if no context is active. Never silently guess the newest run.
6. **Documentation Integrity:** Whenever a file is added or modified in a directory, the corresponding `CONTEXT-[folder].md` file must be updated in the same commit.

---

## 2. Extension Vector 1: Adding a New `minusctl` CLI Command

To add a new first-class subcommand to `minusctl` (e.g. `minusctl benchmark`):

### Step 1: Create the Command Handler Module
Create `core/cli/commands/<subcommand>.py` (e.g. `core/cli/commands/benchmark.py`):

```python
import argparse
import sys
from ..context import resolve_context
from ..formatters import format_section_header

def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "benchmark",
        help="Run performance benchmark across data pipeline runs.",
    )
    parser.add_argument("--run", help="Target run workspace name")
    parser.add_argument("--iterations", type=int, default=5, help="Number of benchmark iterations")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output")
    return parser

def run(args: argparse.Namespace) -> int:
    ctx = resolve_context(args.run)
    if not ctx.active_run:
        sys.stderr.write("Error: No active run set. Run 'minusctl use <run-id>' or pass '--run <run-id>'.\n")
        return 1
    
    # Execute domain logic...
    print(format_section_header("BENCHMARK REPORT"))
    print(f"Active Run: {ctx.active_run}")
    return 0
```

### Step 2: Register in `core/cli/main.py`
In [`core/cli/main.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/cli/main.py):
1. Import the command handler: `from .commands import benchmark`
2. Add to `NATIVE`: `"benchmark": benchmark,`
3. Add to `COMMAND_GROUPS` under the appropriate category (e.g. `Cost and verification`).
4. Add to `COMMAND_HELP`: `"benchmark": "Run performance benchmark across data pipeline runs.",`

### Step 3: Add Unit Tests
Add a test in `tests/test_cli_package.py` verifying that `minusctl benchmark --help` and dispatch work correctly.

---

## 3. Extension Vector 2: Adding a New Terraform Building Block Module

To add a new cloud building block to the 29-module catalog in `modules/`:

### Step 1: Create Module Directory and HCL
Create `modules/<module-id>/main.tf` with parameterized variables and explicit outputs:

```hcl
variable "name_prefix" {
  type        = string
  description = "Resource naming prefix"
}

variable "tags" {
  type    = map(string)
  default = {}
}

# Define resources with Well-Architected encryption and private endpoints...
resource "aws_sqs_queue" "dead_letter" {
  name                      = "${var.name_prefix}-dlq"
  kms_master_key_id         = "alias/aws/sqs"
  message_retention_seconds = 1209600 # 14 days
  tags                      = var.tags
}

output "queue_arn" {
  description = "ARN of the dead-letter queue"
  value       = aws_sqs_queue.dead_letter.arn
}
```

### Step 2: Create `PROVENANCE.json`
Create `modules/<module-id>/PROVENANCE.json` documenting upstream source and provider compatibility.

### Step 3: Register in `core/generation/modules.py`
In [`core/generation/modules.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/generation/modules.py), append the metadata entry:

```python
{
    "id": "ingestion-sqs-dlq",
    "category": "ingestion",
    "description": "Encrypted SQS Dead-Letter Queue for pipeline failure capture",
    "match_keywords": ["dead letter", "dlq", "sqs queue"],
    "inputs": ["name_prefix", "tags"],
    "outputs": ["queue_arn"],
}
```

### Step 4: Update `pyproject.toml`
Add `"modules/<module-id>/*"` to `[tool.setuptools.data-files]` in `pyproject.toml` so the module is packaged into distribution wheels.

### Step 5: Document in `modules/CONTEXT-modules.md`
Add a file entry and architectural description to [`modules/CONTEXT-modules.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/modules/CONTEXT-modules.md).

---

## 4. Extension Vector 3: Adding an Outbound Integration Hook

To add a new enterprise notification or ticketing hook (e.g. PagerDuty):

### Step 1: Create Hook Module in `core/integrations/`
Create `core/integrations/pagerduty_hook.py`:

```python
import os
import urllib.request
import json
from ..governance import approval, audit_logger

def trigger_incident(summary: str, severity: str = "error", approval_mode: str = "gatekeeper") -> dict:
    routing_key = os.environ.get("PAGERDUTY_ROUTING_KEY")
    if not routing_key:
        return {"ok": True, "sent": False, "reason": "PAGERDUTY_ROUTING_KEY not configured"}

    # Human-in-the-Loop approval gate
    approved = approval.request_approval(
        action="trigger-pagerduty-incident",
        details=f"Severity: {severity}, Summary: {summary}",
        mode=approval_mode
    )
    if not approved:
        return {"ok": False, "sent": False, "reason": "Approval denied"}

    # Dispatch webhook
    payload = json.dumps({
        "routing_key": routing_key,
        "event_action": "trigger",
        "payload": {"summary": summary, "severity": severity, "source": "MinusOps Control Plane"}
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://events.pagerduty.com/v2/enqueue",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            audit_logger.log_audit_event("pagerduty-incident-triggered", {"summary": summary})
            return {"ok": True, "sent": True, "status": resp.status}
    except Exception as exc:
        return {"ok": False, "sent": False, "error": str(exc)}
```

### Invariants for Integration Hooks:
* **Bearer Token Security:** Never accept, echo, or log secret API tokens.
* **Truthful Delivery:** An unconfigured channel must return `{"ok": true, "sent": false}`.
* **Audit Logging:** Every dispatched notification must append an event to the audit trail.

---

## 5. Extension Vector 4: Adding an Autonomous Subagent

To create a new specialized autonomous subagent in `.agents/subagents/`:

### Step 1: Author Subagent Manifest
Create `.agents/subagents/<name>-agent.md`:

```markdown
# PagerDuty Incident Subagent

> **Role:** PagerDuty On-Call Incident Dispatcher
> **Purpose:** Dispatches P1 pipeline failure incidents to PagerDuty with approval gating.

## Operating Rules
1. Single Purpose & Immediate Termination: Dispatch exactly one PagerDuty alert and terminate immediately.
2. Security: Never accept or log routing keys.
3. Approval Gate: Route all dispatches through `approval.py`.
```

### Step 2: Register Subagent in `AGENTS.md`
Add the subagent manifest reference to Section 0 ("Mandatory Agent Context") of [`AGENTS.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/AGENTS.md).

---

## 6. Extension Vector 5: Adding an Agent Skill

To add a new workflow skill in `.agents/skills/`:

### Step 1: Create Skill Folder and Manifest
Create `.agents/skills/<skill-name>/SKILL.md`:

```markdown
---
name: my-skill
description: Clear description of when this skill activates and what it accomplishes.
---

# My Custom Agent Skill

## Procedures & Step-by-Step Instructions
1. Step 1...
2. Step 2...
```

### Step 2: Register in `AGENTS.md`
Add the skill activation trigger to Section 0 and Section 3.1 of [`AGENTS.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/AGENTS.md).

---

## 7. Extension Vector 6: Maintaining the Context Graph

MinusOps maintains strict file-by-file context documentation across all directories.

### Rules for Context Maintenance:
1. **Never Allow Documentation Drift:** Whenever you create, modify, or delete a file in any folder, open that folder's `CONTEXT-[folder].md` and update the file's description, function signatures, failure modes, and architectural role.
2. **Master Context Map Synchronization:** When adding a new directory or subsystem, register it in [`CONTEXT-MAP.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/CONTEXT-MAP.md).
3. **Clickable GitHub Markdown Links:** Always use `file://` links for file references (e.g. [`plan_gate.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/governance/plan_gate.py)).
