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
