# IAM templates for MinusOps

You provision these in **your** account. They make the engine's promises enforceable:
the deploy role requires MFA at assume-time, so by the time `minus-gate apply` runs the
session is already MFA-backed — and the gate independently refuses long-term/static keys
(see `docs/security_model.md`).

| File | Purpose |
| :-- | :-- |
| `deploy-role-trust-policy.json` | Trust policy for a human-assumed deploy role — **requires MFA** to assume. |
| `ci-oidc-trust-policy.json` | Trust policy for the same role assumed by GitHub Actions via **OIDC** (no static keys), scoped to the protected `production` environment. |
| `finops-readonly-policy.json` | Least-privilege **read-only** permissions for cost/anomaly/health/BCM-lookup. |

## Wiring

1. **Deploy role** — create a role (e.g. `MinusDeploy`), attach the *permissions* your
   Terraform needs (least privilege for the resources you manage), and set its trust
   policy to `deploy-role-trust-policy.json` (humans) and/or `ci-oidc-trust-policy.json`
   (CI). Scope the human `Principal` to your operator group, not account root.
2. **State protection** — on your remote-state S3 bucket, add the MFA `Deny` from
   `docs/enterprise_iam_manifest.md` §2.A so state writes require MFA too.
3. **FinOps role** — create a read-only role with `finops-readonly-policy.json` for the
   dashboard / `finops_agent` / `health_checker`.
4. **Operators authenticate via IAM Identity Center (SSO)** — the recommended standard
   (`aws configure sso` / `aws sso login`); assuming the deploy role with MFA is a fallback
   only. Then run the gate, which verifies the session is temporary and refuses long-term
   keys unless `MINUS_ALLOW_STATIC_CREDS=1` (audited downgrade).

> Replace every `<ACCOUNT_ID>`, `<ORG>`, `<REPO>` placeholder. These are starting points —
> tighten resource scopes to your workload before production.
