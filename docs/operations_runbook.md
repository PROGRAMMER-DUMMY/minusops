# MinusOps Operations Runbook

A task-oriented guide for operators and platform admins. For the deep agent guide see
[`AGENTS.md`](../AGENTS.md); for trust boundaries see [`security_model.md`](./security_model.md).

---

## 1. Install

```bash
# From a built/released wheel (preferred for clients):
pip install minusops-<version>-py3-none-any.whl     # optional: minusops[dashboard]

# Or from source:
pip install .            # console scripts: minusctl, minus-gate, minus-resolve, ...

# Or the self-contained container (pinned terraform + aws CLI baked in):
docker build -t minusops .
docker run --rm -v "$PWD:/work" -w /work minusops minusctl --help
```

Verify the local toolchain:

```bash
powershell -ExecutionPolicy Bypass -File ./tools/doctor.ps1     # Windows
minus-gate verify --dir path/to/terraform                       # any OS
```

## 2. Configure identity & RBAC (do this before production)

```bash
export MINUS_OPERATOR="alice@corp"                  # wire to SSO/OIDC subject or CI actor
export MINUS_APPROVERS="alice@corp,bob@corp"        # or commit .minus/approvers.json
```

With no allowlist the gate runs in **open mode** (single-operator dev) and says so in the
audit log. Never run production in open mode.

## 3. Authenticate the cloud CLI (the engine never stores secrets)

**Recommended (the standard for human operators): AWS IAM Identity Center (SSO).**
Short-lived credentials, MFA enforced at login, nothing long-term on disk.

```bash
aws configure sso                   # one-time: start URL, region, account, role
aws sso login                       # each session
```

> Fallback only if Identity Center is unavailable: assume your MFA-gated deploy role
> (`examples/iam/deploy-role-trust-policy.json`) into the CLI session. CI uses OIDC.

**The gate enforces this.** At `apply` it refuses long-term static keys (`aws configure`
access keys) and root, allowing only temporary sessions (SSO is the canonical source).
To override in a controlled break-glass case: `MINUS_ALLOW_STATIC_CREDS=1` (recorded in
the audit trail as a downgrade).

## 4. Govern a change end to end

```bash
minus-gate verify  --dir path/to/terraform   # fmt + validate + per-resource security scan
minus-gate plan    --dir path/to/terraform   # records plan-hash + versioned deploy report
minus-gate approve --dir path/to/terraform   # review + RBAC + MFA-backed session → hash-bound approval
minus-gate apply   --dir path/to/terraform   # applies ONLY the approved hash; one-shot
# or: minus-gate run --dir ... [--mode auto-approve]
```

Any `.tf` edit after `plan` changes the hash and voids the approval — re-run `plan`/`approve`.

## 5. Create a governed workspace from intent (no deploy)

```bash
minusctl create "create a governed AWS data pipeline" --input owner=data-platform --input daily_data_gb=50 --generate
minusctl next            # safe next steps
minusctl readiness       # enterprise readiness score
minusctl guard diff      # show manual edits vs the generated baseline
minusctl package         # write the enterprise handoff package
```

## 6. Reportable cost via AWS BCM (no fabricated totals)

```bash
# 1. prepare reviewable payloads (no AWS calls)
minus-bcm prepare --report-dir runs/<run-id>/reports/<plan-hash> --account-id <acct>
# 2. supply reviewed usage (see examples/bcm-usage-profile.example.json), then run (gated, AWS side effect)
minus-bcm run --report-dir runs/<run-id>/reports/<plan-hash> --mode gatekeeper
```

## 7. Verify the audit trail (tamper-evidence)

```bash
minusctl audit verify                       # or: python core/audit_chain.py verify
# In CI/retention: forward .agents/logs/audit.jsonl to immutable storage / SIEM.
```

## 8. Live FinOps

```bash
minusctl  # (dashboard) → python app/dashboard_app.py → http://127.0.0.1:8050
python core/finops_agent.py --cost      # spend by service (read-only, safe)
```

## 9. Incident: "apply refused"

| Symptom | Cause | Fix |
| :-- | :-- | :-- |
| `PLAN CHANGED since approval` | `.tf` edited after approve | re-run `plan` + `approve` |
| `no approval on record` | apply before approve, or hash drift | run `approve` for the current plan |
| `not an authorized approver` | operator not in `MINUS_APPROVERS` | add principal or use an authorized identity |
| `no active cloud session` | CLI not authenticated | `aws sso login` / assume the deploy role |
| `source changed after this plan` | files edited post-plan | re-run `plan` |
