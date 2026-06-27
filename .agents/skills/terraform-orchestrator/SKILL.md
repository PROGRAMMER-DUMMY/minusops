---
name: terraform-orchestrator
description: Provides a reliable orchestration workflow for running Terraform deployments with audit logging, automated health checks, and Human-in-the-Loop (HITL) approval workflows using agy.
---

# Agentic Terraform Orchestrator Skill

This skill equips `agy` with the procedures, scripts, and policies needed to safely govern live infrastructure deployments.

## 📋 The Secure Orchestration Loop

Whenever deploying or modifying infrastructure, you must follow this 5-stage pipeline:

```
  +--------------+     +------------------+     +------------------+
  |  1. Validate | --> | 2. Generate Plan | --> | 3. Audit Logging |
  +--------------+     +------------------+     +------------------+
                                                         |
                                                         v
  +--------------+     +------------------+     +------------------+
  |  6. Check    | <-- |   5. Execute     | <-- |   4. HITL Gate   |
  |  Health/Logs |     |   (Tf Apply)     |     |   (User Review)  |
  +--------------+     +------------------+     +------------------+
```

### 1. Validate & Lint
* Ensure Terraform syntax is correct and formatted:
  ```bash
  terraform fmt -check
  terraform validate
  ```

### 2. Generate Plan
* Save the plan to an execution file:
  ```bash
  terraform plan -out=tfplan
  ```

### 3. Log Audit Record
* Execute the [audit_logger.py](/.agents/skills/terraform-orchestrator/scripts/audit_logger.py) script. This script reads the planned changes and registers them in the local log file `C:\Users\operator\PycharmProjects\MinusTeraformCli\.agents\logs\audit.jsonl` to ensure all actions are fully auditable.
  ```bash
  python .agents/skills/terraform-orchestrator/scripts/audit_logger.py --action "deploy-medallion-pipeline" --details "Deploying Bronze, Silver, Gold S3 buckets and Glue jobs"
  ```

### 4. Human-in-the-Loop (HITL) Gate
* Block execution and present the detailed plan changes to the user.
* Trigger the [hitl_gatekeeper.py](/.agents/skills/terraform-orchestrator/scripts/hitl_gatekeeper.py) script to prompt the user (or system supervisor) for authorization:
  ```bash
  python .agents/skills/terraform-orchestrator/scripts/hitl_gatekeeper.py --plan-file "tfplan"
  ```

### 5. Execute Action
* Only after the gatekeeper yields `APPROVED`, run:
  ```bash
  terraform apply tfplan
  ```

### 6. Health & Uptime Diagnostics
* Perform post-deployment smoke tests. Verify that the created resources are online. If any health test fails, log it and sound a rollback alert.

---

## 🛠️ Diagnostics & Lock Resolution
* **Lock File Exists (`.terraform.tfstate.lock.info`)**: If Terraform state is locked, do not force-unlock without running `terraform force-unlock <LOCK_ID>` inside the audit log wrapper.
* **Partial Deployments**: If a job fails midway, check the state file, run a plan, and provide a diff of what was created vs what failed.
