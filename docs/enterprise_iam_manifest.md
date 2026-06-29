# Enterprise IAM Security & Service Constraints Manifesto

This manifesto defines the strict IAM boundaries, service role isolation, and security constraints required for production-grade AWS deployments.

---

## 1. Core Security Commandments
1. **Zero Shared Roles**: Every AWS service (Glue, Step Functions, EventBridge, SageMaker, Lambda) must have its own dedicated execution role. Service roles must never be shared.
2. **Strict Trust Policies**: The `assume_role_policy` must only list the specific service principal (e.g. `glue.amazonaws.com`).
3. **No Wildcard Resource Permissions**: Policies must target specific resource ARNs. The use of `"Resource": "*"` is forbidden for S3, KMS, and DynamoDB actions.
4. **Least-Privilege Action Scopes**: Services are restricted to the minimum required API actions (e.g. `StartJobRun` instead of `*Job*`).

---

## 2. Native Multi-Factor Authentication (MFA) Enforcement

In production environments, all mutating infrastructure deployments must be authorized by a human operator using a physical MFA device. We enforce this via two layers:

### A. AWS IAM Policy Condition
We attach a `Deny` condition if MFA is not present. This prevents direct API access to critical resources (like our state bucket) unless the session credentials were authenticated with an active MFA device:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyS3AccessWithoutMFA",
      "Effect": "Deny",
      "Action": [
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": [
        "arn:aws:s3:::production-terraform-state-bucket/*"
      ],
      "Condition": {
        "BoolIfExists": {
          "aws:MultiFactorAuthPresent": "false"
        }
      }
    }
  ]
}
```

### B. Credential model — MFA at the CLI, never in our code
The framework **does not mint, inject, or store credentials**. MFA is enforced one level up,
at authentication time, so by the time `terraform apply` runs the session is already MFA-backed:

1. The operator authenticates their **cloud CLI** with MFA before applying — either:
   * `aws sso login` (IAM Identity Center; no long-term secret lands on disk), or
   * assume your **MFA-gated deploy role** (its trust policy requires
     `aws:MultiFactorAuthPresent = true`), e.g.
     `aws sts assume-role --role-arn <MinusDeploy> --serial-number <mfa-arn> --token-code <code>`
     and load the returned session into the CLI profile.
2. `python core/plan_gate.py approve` reviews the exact plan and records a **hash-bound approval**
   (hash + caller identity + timestamp — no secrets).
3. `python core/plan_gate.py apply` re-checks the plan hash, confirms an active session via the
   provider's `identity()`, and runs `terraform apply tfplan` using the **ambient CLI credential
   chain**. Any `.tf` change voids the approval and forces a fresh review.

---

## 3. Service Role Matrix & Constraints

> *Illustrative.* The rows below show how the principles apply to a data-pipeline workload
> (the engine ships no bundled architecture). Map the same pattern onto your own services.

| Service | Principal (Trust Policy) | Action Permissions | Resource Constraints |
| :--- | :--- | :--- | :--- |
| **AWS Glue** | `glue.amazonaws.com` | `s3:GetObject`<br>`s3:PutObject`<br>`s3:DeleteObject`<br>`s3:ListBucket` | Restricted strictly to Bronze, Silver, Gold, and Glue Assets S3 bucket ARNs. |
| **AWS Step Functions** | `states.amazonaws.com` | `glue:StartJobRun`<br>`glue:GetJobRun`<br>`glue:StartCrawler` | Restricted to target Glue Job ARNs and Crawler ARNs. |
| **AWS EventBridge** | `events.amazonaws.com` | `states:StartExecution` | Restricted strictly to the Orchestrator State Machine ARN. |
| **SageMaker (ML)** | `sagemaker.amazonaws.com` | `s3:GetObject` | Restricted strictly to the Gold model S3 bucket prefix (`s3://gold-.../models/`). |

---

## 4. Verification & Compliance Scans
Our security scanner ([**`optimize_analyzer.py`**](/core/optimize_analyzer.py)) runs continuous security audits:
* **Rule `SEC-02`**: Automatically flags any IAM policy that declares `"Resource": "*"` or wildcard statements, preventing loose policy exposures.
* **Auto-Remediation**: Before any terraform configuration is proposed to the HITL gatekeeper, it must pass the security validation check with zero warnings.
