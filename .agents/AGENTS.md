# Project Rules for Agentic Terraform Orchestrator

Whenever working on this codebase, you must adhere to the following operational constraints to guarantee reliable uptime, strict audit logging, and Human-in-the-Loop (HITL) gatekeeping:

## 1. Safety and Human-in-the-Loop (HITL)
* **Pre-Execution Review**: You are strictly forbidden from executing `terraform apply`, `terraform destroy`, `terraform state`, or any mutating `git` commands (e.g. `push`, `reset`) without requesting explicit review from the user.
* **Audit Trail**: Before proposing any command that interacts with live AWS resources or updates configurations, you must document the target action and the intended state change.
* **Dry Runs**: You must run `terraform plan` or validation tests before seeking human approval. Present the plan output to the user in a clear format.

## 2. Command Surface

* **Invoke `minusctl`, not a script path.** Every capability is a subcommand:
  `create`, `use`, `runs list|describe`, `gate {verify|plan|approve|apply}`, `cost
  {prepare|estimate}`, `source {status|diff|anchor}`, `prove [--execute]`, `export`,
  `audit verify`, `doctor`, plus `next`, `readiness`, `conformance`, `validate`, `package`,
  `decision`, `accelerator`, `policy`, `reports`, `guard`, `adopt`, `seed`, `demo`.
  If `minusctl` is not on PATH, `python -m core.cli.main <command>` is the identical entry
  point -- pyproject maps the console script straight to `core.cli.main:main`. Prefer the
  console script: it is what the docs, the skills and every operator instruction name.
  A handful of capabilities have no subcommand yet (`synthesizer.py author`, `patterns.py
  match`, `pillars.py derive`, `coverage_audit.py`); those are still invoked by path, and
  that list is the backlog of missing subcommands rather than a second supported style.
* **Select the run once.** `minusctl use <run-id>` writes `.minus/context.json`; every
  run-scoped command then defaults to it. With no active run and no explicit `--dir`/`--run`,
  those commands REFUSE. Do not work around that by passing the newest run -- if you are not
  sure which run is meant, ask.
* **`minusctl prove --execute` and `minusctl seed --execute` are the only mutating
  subcommands.** Both route through `approval.py` and land in the audit chain. Everything
  else in the CLI is local-only by contract.
* **No emojis in any output you generate** -- terminal text, log lines, generated markdown,
  or reports.

## 3. Code Quality and Documentation
* **Referencing Resources**: When writing Terraform files, verify parameter defaults against hashicorp documentation schemas.
* **Error Recovery**: If a terraform command fails due to provider constraints, missing variables, or state locking, do not retry blindly. Extract the error, write a troubleshooting entry, and request verification if human intervention is required.
* **Skill Activation**: You must activate the `terraform-orchestrator` skill by reading its [SKILL.md](./skills/terraform-orchestrator/SKILL.md) instructions prior to any deployment operation.
* **Notification Subagents (transport only)**: To send a notification, activate the matching subagent by reading its manifest in [`.agents/subagents/`](./subagents/) -- `slack-agent` (incidents, approval cards), `teams-agent` (data quality, quarantine), `outlook-agent` (executive FinOps email), `confluence-agent` (architecture pages), `jira-agent` (change tickets). Each sends exactly one message through `core/integrations/` and stops. Never accept, echo, or log a webhook URL or token; a denied approval is a denial, not a failure, and is never retried; and `ok` is not `sent` -- an unconfigured channel returns `{"ok": true, "sent": false}`.
* **Ambiguity Handling**: If the user request is unclear, underspecified, too simple for the hidden infrastructure risk, or broad enough to support incompatible outcomes, activate the `resolve-ambiguity` skill before acting.
* **Build/Create requests run requirements first (mandatory)**: For any "build / create / set up <infrastructure>" request, activate the `grill-me` skill and gather full functional + non-functional requirements **before** generating anything. Do **not** jump straight to `intent_resolver` + the hardcoded blueprint and ask only its 2–3 inputs. When the requirements are settled, route to the `architect` skill (research → choose vetted modules → compose → govern). The `aws-data-pipeline-standard` blueprint is a demo/cached fixture only — production architecture is composed from requirements, not a single fixed recipe.
* **Autonomous Execution Standard (No Manual CLI Handoffs)**: You must execute all `minusctl` commands (`minusctl create`, `minusctl use`, `minusctl gate plan`, `minusctl diagram`) directly using your execution tools. Never ask the user to switch to their terminal to run non-mutating commands. Execute them autonomously and report progress.
* **Upfront Roadmap & Order of Operations**: At the start of every build/create lifecycle, present the structured 7-step roadmap (`[1] Grilling -> [2] ADR -> [3] Synthesis -> [4] Draw.io & DAG -> [5] Reflector -> [6] Plan Gate & BCM Cost -> [7] HITL Approval`) so the user has immediate visibility into the complete execution sequence.
* **Grilling completeness -- the 18 pillars**: Ensure the requirements interview covers every applicable pillar, along with the TerraShark failure modes (FM-01..05). Do **not** transcribe the pillars from memory: they live in [`core/architecture/pillars.py`](../core/architecture/pillars.py), which is the single source of truth for each question, its options, the modules it maps to, and the follow-ups conditioned on the answer. Read them with `python core/architecture/pillars.py list` and `... next --answered <keys>`. Derive later questions from earlier answers rather than asking a fixed list -- `python core/architecture/pillars.py derive daily_gb=... partitions_per_day=...` computes what the stated numbers already imply, and returns `determinable: false` with the missing fact rather than a default when they do not.
* **1-Click Diagram Link**: Whenever an architecture diagram is generated, always include the direct 1-click `https://app.diagrams.net/#R...` URL in your response for instant browser viewing.
* **Deep Plan Review**: If the user asks to be grilled, to stress-test a plan, or to resolve an architecture/product/process decision tree, activate the `grill-me` skill and ask one decision-oriented question at a time.
* **Architecture synthesis**: When building for a scenario the demo blueprint doesn't fit, activate the `architect` skill — research current services/reference architectures, compose `core/generation/modules.py` building blocks via `core/generation/synthesizer.py`, and govern the result through the normal deploy gate.
* **Context Graph Maintenance**: Activate the `context-graph` skill whenever adding, refactoring, or moving code to maintain `CONTEXT-MAP.md` and `CONTEXT-[folder].md` without documentation drift.
* **Extensibility & Integrations**: When authoring new CLI subcommands, Terraform modules, outbound integration hooks, or autonomous subagents, activate the `integration-guide` skill and follow [`docs/extensibility_and_integration_guide.md`](../docs/extensibility_and_integration_guide.md).



