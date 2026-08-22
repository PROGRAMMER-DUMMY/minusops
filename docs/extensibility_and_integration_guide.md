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

from ..context import ContextError, resolve_run
from ..formatters import card, table


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
    # resolve_run() RAISES rather than returning a falsy record: explicit --run, then upward
    # discovery from the cwd, then the stored context, then refusal. Catch it and exit 1 --
    # a truthiness check on the return value is a refusal branch that can never fire, and
    # the real refusal then escapes as an unhandled traceback.
    try:
        run_record = resolve_run(args.run)
    except ContextError as err:
        sys.stderr.write(f"Error: {err}\n")
        return 1

    rows = [(str(i), "42ms") for i in range(1, args.iterations + 1)]
    print(card("BENCHMARK REPORT", [("Run", [("Name", run_record["run_id"])])]))
    print(table(["Iteration", "Duration"], rows))
    return 0
```

### Step 2: Register in `core/cli/main.py`
In [`core/cli/main.py`](../core/cli/main.py):
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
In [`core/generation/modules.py`](../core/generation/modules.py), append the metadata entry:

```python
{
    "id": "ingestion-sqs-dlq",
    "category": "ingestion",
    "title": "Encrypted SQS Dead-Letter Queue for pipeline failure capture",
    "satisfies": ["dead letter", "dlq", "sqs queue"],
    "services": ["Amazon SQS", "AWS KMS"],
    "inputs": ["name_prefix", "tags"],
    "provides": ["queue_arn"],
}
```

All seven keys are required, and the names are not interchangeable with their plain-English
synonyms. `modules.py` and `schema_watch.py` subscript `title`, `satisfies`, `services` and
`provides` directly rather than through `.get()`, so an entry that spells any of them
differently raises `KeyError` inside `match_modules()` -- the module-selection path every
synthesis runs through -- rather than merely failing to match.

`satisfies` is the match vocabulary: phrases a requirement might use for this capability.
`services` is the AWS service names the module provisions, and feeds the same vocabulary.

### Step 4: Update `pyproject.toml`
Add `"modules/<module-id>/*"` to `[tool.setuptools.data-files]` in `pyproject.toml` so the module is packaged into distribution wheels.

### Step 5: Document in `modules/CONTEXT-modules.md`
Add a file entry and architectural description to [`modules/CONTEXT-modules.md`](../modules/CONTEXT-modules.md).

---

## 4. Extension Vector 3: Adding an Outbound Integration Hook

To add a new enterprise notification or ticketing hook (e.g. PagerDuty):

### Step 1: Create Hook Module in `core/integrations/`
Create `core/integrations/pagerduty_hook.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base_hook  # noqa: E402

ROUTING_KEY_ENV = "PAGERDUTY_ROUTING_KEY"
ENQUEUE_URL = "https://events.pagerduty.com/v2/enqueue"


def trigger_incident(summary, severity="error", approval_mode="gatekeeper",
                     secret_arn=None, timeout=base_hook.DEFAULT_TIMEOUT):
    """
    Raise one PagerDuty incident. Returns a result dict; `sent` is False both when approval
    was denied (reason "not_authorized") and when no routing key is configured
    (reason "not_configured").
    """
    def _send():
        # Resolved INSIDE the sender, after the gate: the routing key is a bearer credential,
        # so it is read from the environment or a Secrets Manager ARN and never accepted as
        # a parameter or echoed into a result.
        routing_key = base_hook.resolve_secret(ROUTING_KEY_ENV, secret_arn)
        if not routing_key:
            return base_hook.not_configured(ROUTING_KEY_ENV)
        payload = {
            "routing_key": routing_key,
            "event_action": "trigger",
            "payload": {"summary": summary, "severity": severity,
                        "source": "MinusOps Control Plane"},
        }
        return base_hook.post_json(ENQUEUE_URL, payload, timeout=timeout)

    return base_hook.gated("trigger-pagerduty-incident",
                           f"Severity: {severity}, Summary: {summary}",
                           approval_mode, _send)
```

`base_hook.gated()` is the whole contract: it deduplicates, then applies the approval gate,
then calls `_send()` only if both allow it, and records the audit event. Do not re-implement
those steps -- the ordering is deliberate (deduplication runs BEFORE the prompt so a human is
not asked fifty times about one failing job), and only a delivered alert opens a dedup window.

Note the flat `import base_hook` off `sys.path`. Every hook in `core/integrations/` uses it.
A package-relative import here produces a second module object, and a `monkeypatch` applied to
one is invisible to the other.

### Invariants for Integration Hooks:
* **Bearer Token Security:** Never accept, echo, or log secret API tokens. Resolve them with `base_hook.resolve_secret()`, which accepts an ARN -- an identifier is safe in a log line, a credential is not.
* **Truthful Delivery:** An unconfigured channel must return `{"ok": true, "sent": false}` via `base_hook.not_configured()`. Callers distinguish "refused" and "not wired up" from "failed" by `sent`, never by `ok`.
* **Audit Logging:** Route the send through `base_hook.gated()`, which writes the audit record. Calling `audit_logger.log_audit_event()` directly requires a third `log_dir` argument and is easy to get wrong on the success path.

---

## 5. Extension Vector 4: Adding an Autonomous Subagent

To create a new specialized autonomous subagent in `.agents/subagents/`:

### Step 1: Author Subagent Manifest
Create `.agents/subagents/<name>-agent.md`:

```markdown
---
name: pagerduty-agent
description: Raises P1 pipeline-failure incidents in PagerDuty. Use when a pipeline failure must page the on-call engineer rather than post to a channel.
tools: Bash, Read
model: haiku
---

You raise one PagerDuty incident, report the result, and stop.

Call the hook, never hand-roll an HTTP request:
`python core/integrations/pagerduty_hook.py --summary "<one line>" --severity error`

## Operating Rules
1. Single purpose and immediate termination: raise exactly one incident, then stop.
2. Security: never accept, echo, or log the routing key.
3. Approval gate: the hook routes every dispatch through `base_hook.gated()`. Do not bypass it.
```

The YAML frontmatter is not decoration -- `name`, `description`, `tools` and `model` are how
an agent runtime discovers and registers the subagent. A manifest without it is a markdown
file nothing will load. `description` carries the activation trigger, so write it as the
condition under which the subagent should be chosen.

### Step 2: Register Subagent in `AGENTS.md`
Add the subagent manifest reference to Section 0 ("Mandatory Agent Context") of [`AGENTS.md`](../AGENTS.md).

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
Add the skill activation trigger to Section 0 and Section 3.1 of [`AGENTS.md`](../AGENTS.md).

---

## 7. Extension Vector 6: Maintaining the Context Graph

MinusOps maintains strict file-by-file context documentation across all directories.

### Rules for Context Maintenance:
1. **Never Allow Documentation Drift:** Whenever you create, modify, or delete a file in any folder, open that folder's `CONTEXT-[folder].md` and update the file's description, function signatures, failure modes, and architectural role.
2. **Master Context Map Synchronization:** When adding a new directory or subsystem, register it in [`CONTEXT-MAP.md`](../CONTEXT-MAP.md).
3. **Repo-Relative Markdown Links:** Always reference files by a path relative to the document doing the linking -- [`plan_gate.py`](../core/governance/plan_gate.py), `[main.py](./main.py)`, `[minusctl.py](../reporting/minusctl.py)`. Never use the `file://` scheme and never a leading `/`. A `file:///C:/Users/...` URL names one developer's disk, and browsers will not follow `file://` from an https page, so it is dead both in a fresh clone and on GitHub. A leading `/` resolves against the site root rather than the repository root and 404s the same way.
