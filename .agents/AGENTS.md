# Project Rules for Agentic Terraform Orchestrator

Whenever working on this codebase, you must adhere to the following operational constraints to guarantee reliable uptime, strict audit logging, and Human-in-the-Loop (HITL) gatekeeping:

## 1. Safety and Human-in-the-Loop (HITL)
* **Pre-Execution Review**: You are strictly forbidden from executing `terraform apply`, `terraform destroy`, `terraform state`, or any mutating `git` commands (e.g. `push`, `reset`) without requesting explicit review from the user.
* **Audit Trail**: Before proposing any command that interacts with live AWS resources or updates configurations, you must document the target action and the intended state change.
* **Dry Runs**: You must run `terraform plan` or validation tests before seeking human approval. Present the plan output to the user in a clear format.

## 2. Code Quality and Documentation
* **Referencing Resources**: When writing Terraform files, verify parameter defaults against hashicorp documentation schemas.
* **Error Recovery**: If a terraform command fails due to provider constraints, missing variables, or state locking, do not retry blindly. Extract the error, write a troubleshooting entry, and request verification if human intervention is required.
* **Skill Activation**: You must activate the `terraform-orchestrator` skill by reading its [SKILL.md](/.agents/skills/terraform-orchestrator/SKILL.md) instructions prior to any deployment operation.
* **Ambiguity Handling**: If the user request is unclear, underspecified, too simple for the hidden infrastructure risk, or broad enough to support incompatible outcomes, activate the `resolve-ambiguity` skill before acting.
* **Build/Create requests run requirements first (mandatory)**: For any "build / create / set up <infrastructure>" request, activate the `grill-me` skill and gather full functional + non-functional requirements **before** generating anything. Do **not** jump straight to `intent_resolver` + the hardcoded blueprint and ask only its 2–3 inputs. When the requirements are settled, route to the `architect` skill (research → choose vetted modules → compose → govern). The `aws-data-pipeline-standard` blueprint is a demo/cached fixture only — production architecture is composed from requirements, not a single fixed recipe.
* **Deep Plan Review**: If the user asks to be grilled, to stress-test a plan, or to resolve an architecture/product/process decision tree, activate the `grill-me` skill and ask one decision-oriented question at a time.
* **Architecture synthesis**: When building for a scenario the demo blueprint doesn't fit, activate the `architect` skill — research current services/reference architectures, compose `core/modules.py` building blocks via `core/synthesizer.py`, and govern the result through the normal deploy gate.

