# The IAM split

MinusOps runs in your account with your credentials. These files are what bound it.

## The one idea

**The agent holds a role that can read and plan. It never holds the role that applies.**

Everything else here is arrangement around that. A command-level guardrail ships with this
repo and it is genuinely useful, but measured against one destructive action expressed five
ways it catches three -- the misses are interpreter paths, and no allowlist of binaries
closes an interpreter. What holds is the credential.

## Files

| File | What it is |
| :--- | :--- |
| `onboarding-template.yaml` | **Start here.** One CloudFormation stack creates both roles, the boundary and the state backend in your account. |
| `plan-role-policy.json` | The agent's role. Read, plan, hold the state lock. No mutation, no role assumption beyond two named read roles. |
| `apply-role-policy.json` | The role CI assumes on an approved plan. Mutating, with the escalation caps below. |
| `permissions-boundary.json` | The ceiling on every role Terraform creates. |
| `organization-scp.json` | Attach at the OU. The only control no principal in the account can override. |
| `deploy-role-trust-policy.json`, `ci-oidc-trust-policy.json` | Who may assume the apply role. |
| `finops-readonly-policy.json` | Unrelated: the read-only role for cost reporting. |

## Why the apply role is not least-privilege in the usual sense

To build a pipeline, Terraform needs `iam:CreateRole` and `iam:AttachRolePolicy`. Anything
holding those can mint a role with `*:*` and use it. **There is no least-privilege Terraform
role that can still create IAM.**

So the design does not withhold `CreateRole`. It caps what gets created:

1. **Every role it creates carries the boundary.** `iam:CreateRole` is conditioned on
   `iam:PermissionsBoundary`, so a create without it is denied.
2. **Role writes are confined to `/minusops-managed/`.** The plan role, the apply role and
   every platform role are out of reach.
3. **The boundary cannot be detached or edited** by the role it bounds. A boundary the
   bounded principal can rewrite is decoration.
4. **`iam:PassRole` is scoped by `iam:PassedToService`.** Unscoped, an agent passes an admin
   role to a Lambda it created, invokes it, and inherits that authority without ever holding
   a destructive permission itself. This is the escalation most designs miss.

## Two things the research got wrong, corrected here

**`sts:AssumeRole` is scoped, not denied outright.** A blanket deny closes the provider
`assume_role` escalation and also makes hub-and-spoke unplannable -- a cross-account
lakehouse plans through aliased providers. The plan role may assume exactly the named
cross-account *read* roles and nothing else.

**The SCP does not deny `s3:PutBucketVersioning`.** Suspending versioning removes the undo,
so it looks like it belongs. But enabling and suspending are the **same API call** with no
condition key separating them, and `modules/storage-medallion-s3/main.tf` declares
`aws_s3_bucket_versioning` -- so that deny fails `terraform apply` on every new bucket the
primary storage module creates. Catch suspension with an AWS Config rule instead. A published
template that breaks bucket creation on day one is worse than no template.

`organization-scp.json` records every deliberate omission under `_not_denied_deliberately`,
because an omission that looks like an oversight gets "fixed" by the next reader.

## On MFA

`RequireMfaOnApply` defaults to **false**, and that is not laziness.

`aws:MultiFactorAuthPresent` is absent or false for IAM Identity Center, SAML and OIDC
sessions -- AWS STS receives no MFA assertion from the identity provider. A trust policy
requiring it **denies an SSO operator**, even after a hardware key prompt. That statement is
sourced from AWS documentation and has not been measured here; see the limits below.

And where it does populate, it propagates: a session derived from MFA-authenticated
credentials carries the flag through role chaining. An agent running in your authenticated
shell inherits it and can assume the apply role with no prompt.

**MFA at assume time proves MFA happened somewhere in the session. It is not per-action
consent.** What makes the separation real is the credential simply not existing in the
agent's environment. Verify the behaviour against your own directory before relying on it:

    python examples/iam/verify-mfa-condition.py --live

It creates one permissionless role carrying the condition, tries to assume it, reports the
result and deletes the role. To measure an elevated IAM-user session and the chaining claim
in one pass, add a TOTP code:

    python examples/iam/verify-mfa-condition.py --live --chain --mfa-code 123456

That elevates via sts:GetSessionToken, assumes the role, then re-assumes it from the
resulting session to see whether the flag was inherited or re-checked.

### What has been measured, and what has not

**Measured, 2026-08-26.** A development account, signed in as an IAM user with long-lived
access keys: **DENIED**. That is the unelevated case rather than the SSO one -- access keys
carry no MFA flag whether or not a device is enrolled -- and it is the case most CI runners
and most local shells are in. Turning the condition on there locks out every session that has
not called `sts:GetSessionToken` with a code first, which is the failure that gets diagnosed
as "MFA is broken" and fixed by deleting the condition.

**Not measured: the SSO case.** An IAM Identity Center session requires an *organization*
instance -- an account instance cannot grant AWS account access at all -- and creating one
converts a standalone account into an AWS Organizations management account, which ends free
tier eligibility immediately. The development account this was written against is on free
tier, so the SSO claim above stays documentation-sourced. Anyone running an organization
already can measure it in a minute, and the result is worth contributing back.

**Not measured: the propagation claim.** That the flag survives role chaining is asserted
above and not tested. Testing it needs an MFA device enrolled on an IAM user, which is free
and needs no organization.

**Not exercised: `organization-scp.json`.** Service control policies require AWS
Organizations with all features enabled. The file is written against the AWS SCP schema and
has never been attached to a live organization.

## What is still not covered

- **State is equivalent to control.** Whoever writes `terraform.tfstate` decides what
  Terraform believes it owns. The bucket policy in the template denies writes from anything
  but the apply role; treat that file as the crown jewel it is.
- **A human approving a 400-resource diff is rubber-stamping.** The machine review carries
  it -- SEC scan, conformance, BCM cost, and the plan hash binding what was approved to what
  runs. The gate's forced impact statement exists for the same reason.
- **Binding a credential to a specific plan hash cannot be done in a trust policy.** An IAM
  trust policy matches claims in the token, and no CI provider issues a token carrying a
  digest you choose. `core/governance/apply_broker.py` closes as much of this as is
  reachable without running an identity provider: the release check re-derives the hash in
  CI and refuses a mismatched, stale, unattributed or self-approved plan. It runs where the
  agent is not, which is a weaker claim than a cryptographic one.
