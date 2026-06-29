# MinusOps Security Model & Threat Model

This document states the trust boundaries, the guarantees the control plane enforces,
and — just as importantly — what it does **not** protect against. It is the reference
for an enterprise security review.

---

## 1. Design principles

1. **No secrets in the engine.** MinusOps never mints, stores, or transmits cloud
   credentials. All cloud access uses the operator's own CLI credential chain
   (`aws sso login`, an assumed MFA-gated role, or CI OIDC).
2. **Plan-bound apply.** `apply` runs only the exact plan a human approved, identified
   by the SHA-256 of the planned resource/output changes. Any `.tf` edit changes the
   hash, voids the approval, and forces re-review.
3. **Fail-closed approvals.** With no interactive terminal, the gatekeeper denies.
4. **Tamper-evident audit.** Every consequential action is written to a hash-chained,
   append-only log; modification or deletion is detectable (`minusctl audit verify`).
5. **Least privilege by policy.** The HCL scanner blocks `SEC-*` findings (per-resource);
   the IAM manifest forbids wildcard resources and shared roles.

---

> **Deploy-gate process flow:** see [`deploy_gate_flow.svg`](./deploy_gate_flow.svg) for the
> verify → plan → approve → apply flow with its decision gates and refusal paths.

## 2. Trust boundaries

```
┌─ Operator workstation / CI runner ───────────────────────────────┐
│  MinusOps engine (this repo)                                      │
│   - generates/inspects Terraform, computes plan-hash             │
│   - records approvals + audit chain (local files)                │
│   - NEVER holds credentials                                      │
│        │ uses ambient credential chain                           │
│        ▼                                                          │
│  cloud CLI (aws) ── OIDC / SSO / assumed MFA role ──► Cloud APIs │
└──────────────────────────────────────────────────────────────────┘
```

- **Inside the boundary:** plan-hash binding, approval records, audit chain, policy scan.
- **Outside (delegated):** authentication, MFA, and authorization to call cloud APIs —
  enforced by your IdP and the deploy role's trust policy, not by MinusOps.

---

## 3. Identity & authorization (RBAC)

- **Operator identity** comes from `MINUS_OPERATOR` (wire to your SSO/OIDC subject or CI
  actor); it falls back to the OS user only for local dev.
- **Approver allowlist** comes from `MINUS_APPROVERS` (comma-separated) or
  `.minus/approvers.json`. When set, only listed principals may approve; otherwise the
  gate runs in **open mode**, which is recorded explicitly in the audit log so it can
  never be mistaken for an enforced control.
- Authorization is enforced in both `approval.py` and `plan_gate.py approve`.

### Credential posture (enforced, not assumed)

The product promises *MFA-gated* deploys. The gate makes that real instead of trusting
the operator: at `apply` it inspects the active session and **refuses long-term static
keys or root** (`AKIA*` / IAM-user / root), allowing only **temporary** sessions
(`ASIA*` — SSO, assumed-role, or MFA session token). Combined with the shipped deploy-role
trust policy (`examples/iam/deploy-role-trust-policy.json`, which requires MFA to assume),
a successful apply is guaranteed to have come from an MFA-backed session. The check can be
overridden with `MINUS_ALLOW_STATIC_CREDS=1`, which is recorded in the audit trail as a
downgrade. See `examples/iam/README.md`.

---

## 4. What MinusOps guarantees

| Guarantee | Mechanism | Test |
| :-- | :-- | :-- |
| Apply runs only the reviewed plan | dir+hash-bound approval, re-checked at apply | `test_plan_gate`, `test_gate_e2e` |
| `.tf` drift voids approval | plan-hash recompute + source snapshot | `test_plan_gate`, `test_gate_e2e` |
| Approvals fail-closed without a TTY | `approval.py` TTY check | `test_approval` |
| Only authorized principals approve | `authz` allowlist | `test_authz` |
| Apply requires a temporary (MFA-backed) session | credential-posture check at apply | `test_credentials` |
| Audit trail is tamper-evident | hash chain | `test_audit_chain` |
| Architecture diagram is contract-stable | spec-conformant generator + golden test | `test_reporter` |
| No fabricated costs published | BCM-gated; offline pricing removed | `test_bcm`, `test_reporter` |

---

## 5. What MinusOps does NOT protect against (explicit non-goals)

- **A compromised operator workstation / CI runner.** If the host is owned, the ambient
  credentials and local approval files are exposed. Use ephemeral CI runners and
  short-lived OIDC credentials.
- **Cloud-side IAM misconfiguration.** MinusOps assumes you provisioned a least-privilege,
  MFA-gated deploy role; it cannot compensate for an over-broad role.
- **Malicious Terraform providers / modules.** Supply-chain review of third-party modules
  is out of scope; pin and vet them.
- **Audit-log destruction.** The chain makes tampering *detectable*, not *impossible*.
  Ship `audit.jsonl` to immutable storage / SIEM (CloudTrail, WORM bucket) for retention.
- **Local file ACLs.** Approval records contain no secrets, but protect the workspace.

---

## 6. Recommended hardening for production

- **Human operators: standardize on AWS IAM Identity Center (SSO)** (`aws configure sso`
  / `aws sso login`) — short-lived, MFA-at-login, no long-term secret on disk. This is
  the canonical method; an assumed MFA role is a fallback only.
- Run deploys only from ephemeral CI runners with OIDC; never long-lived keys.
- Set `MINUS_APPROVERS` / `.minus/approvers.json` (never run prod in open mode).
- Forward `.agents/logs/audit.jsonl` to immutable storage and run `audit verify` in CI.
- Require `optimize_analyzer` `SEC-*` = 0 (the gate already blocks); add `--external`
  (checkov/tfsec) for defense in depth.
- Use the signed release wheel + CycloneDX SBOM (see `.github/workflows/release.yml`).
