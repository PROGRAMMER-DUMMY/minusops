# Enterprise IAM Security & Service Constraints Manifesto

This manifesto defines the strict IAM boundaries, service role isolation, and security constraints required for production-grade AWS deployments.

---

## 🔒 1. Core Security Commandments
1. **Zero Shared Roles**: Every AWS service (Glue, Step Functions, EventBridge, SageMaker, Lambda) must have its own dedicated execution role. Service roles must never be shared.
2. **Strict Trust Policies**: The `assume_role_policy` must only list the specific service principal (e.g. `glue.amazonaws.com`).
3. **No Wildcard Resource Permissions**: Policies must target specific resource ARNs. The use of `"Resource": "*"` is forbidden for S3, KMS, and DynamoDB actions.
4. **Least-Privilege Action Scopes**: Services are restricted to the minimum required API actions (e.g. `StartJobRun` instead of `*Job*`).

---

## 🔑 2. Native Multi-Factor Authentication (MFA) Enforcement

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

### B. Out-of-Workspace Verification & Injection
1. The operator runs the gatekeeper with their MFA device ARN:
   ```bash
   python hitl_gatekeeper.py --plan-file "tfplan" --mfa-arn "arn:aws:iam::123456789012:mfa/deploy-operator"
   ```
2. The gatekeeper prompts `Enter 6-digit AWS MFA Code:`.
3. It calls `aws sts get-session-token` to validate the code. If successful, AWS returns temporary session keys (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`) which are valid for up to 12 hours.
4. The gatekeeper writes these credentials securely into the `approved.token` file outside the workspace.
5. The `terraform_wrapper.py` reads the token, injects the temporary session credentials into the environment variables, runs `terraform apply tfplan` (passing the MFA condition check), and deletes the token.

---

## 🧭 3. Service Role Matrix & Constraints

| Service | Principal (Trust Policy) | Action Permissions | Resource Constraints |
| :--- | :--- | :--- | :--- |
| **AWS Glue** | `glue.amazonaws.com` | `s3:GetObject`<br>`s3:PutObject`<br>`s3:DeleteObject`<br>`s3:ListBucket` | Restricted strictly to Bronze, Silver, Gold, and Glue Assets S3 bucket ARNs. |
| **AWS Step Functions** | `states.amazonaws.com` | `glue:StartJobRun`<br>`glue:GetJobRun`<br>`glue:StartCrawler` | Restricted to target Glue Job ARNs and Crawler ARNs. |
| **AWS EventBridge** | `events.amazonaws.com` | `states:StartExecution` | Restricted strictly to the Orchestrator State Machine ARN. |
| **SageMaker (ML)** | `sagemaker.amazonaws.com` | `s3:GetObject` | Restricted strictly to the Gold model S3 bucket prefix (`s3://gold-.../models/`). |

---

## 🛠️ 4. Verification & Compliance Scans
Our security scanner ([**`optimize_analyzer.py`**](/.agents/skills/pipeline-optimizer/scripts/optimize_analyzer.py)) runs continuous security audits:
* **Rule `SEC-02`**: Automatically flags any IAM policy that declares `"Resource": "*"` or wildcard statements, preventing loose policy exposures.
* **Auto-Remediation**: Before any terraform configuration is proposed to the HITL gatekeeper, it must pass the security validation check with zero warnings.
