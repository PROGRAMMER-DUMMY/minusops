---
name: terraform-orchestrator
description: Provides a reliable orchestration workflow for running Terraform deployments with audit logging, automated health checks, and Human-in-the-Loop (HITL) approval workflows using agy.
---

# Agentic Terraform Orchestrator Skill

This skill equips `agy` with the procedures, scripts, and policies needed to safely govern live infrastructure deployments.

## The Secure Orchestration Loop

All infrastructure changes go through **[core/governance/plan_gate.py](/core/governance/plan_gate.py)** — a plan-bound deploy gate that enforces the loop in code (verify → plan → hash → approve → apply), audits every stage, and refuses to apply any plan whose hash you did not approve.

```
  verify  -->  plan  -->  approve (review + confirm)  -->  apply (exact tfplan)
    |           |              |                              |
  fmt+        records       binds approval               applies ONLY the
  validate+   plan-hash     to the plan-hash             approved hash;
  scan                                                   re-plan voids it
```

### Credential model — the gate never handles secrets
Authenticate via the cloud CLI **before** applying — `aws sso login`, or assume your
MFA-gated deploy role into your CLI session. MFA is enforced by that role's trust policy;
`terraform apply` then uses the ambient CLI credential chain.

### Run it
`--dir` is required — point it at whatever Terraform directory you're governing (this is a
workload-agnostic engine; there is no bundled default).
```bash
# stage by stage
python core/governance/plan_gate.py verify  --dir path/to/your/terraform
python core/governance/plan_gate.py plan    --dir path/to/your/terraform
python core/governance/plan_gate.py approve --dir path/to/your/terraform
python core/governance/plan_gate.py apply   --dir path/to/your/terraform

# or all four in sequence (gatekeeper prompts at approve; --mode auto-approve skips the y/N)
python core/governance/plan_gate.py run     --dir path/to/your/terraform
```

### Plan-bound guarantee
Any `.tf` change produces a **new plan hash**, which **voids the prior approval** and
forces a fresh review. `apply` cross-checks the current hash against the approved one and
refuses on mismatch. Every stage is written to `.agents/logs/audit.jsonl`.

### Post-deploy
Run `python core/reporting/health_checker.py` for smoke tests; if a check fails, log it and alert.

---

## Diagnostics & Lock Resolution
* **Lock File Exists (`.terraform.tfstate.lock.info`)**: If Terraform state is locked, do not force-unlock without running `terraform force-unlock <LOCK_ID>` inside the audit log wrapper.
* **Partial Deployments**: If a job fails midway, check the state file, run a plan, and provide a diff of what was created vs what failed.

