# Examples Context Index

This document provides exhaustive context for all example configurations, sample usage profiles, and IAM reference policies within the [`examples`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/examples) directory.

---

## 1. Cost & Usage Profile Examples

- [`examples/bcm-usage-profile.example.json`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/examples/bcm-usage-profile.example.json): Sample usage profile JSON template used by `core/cost/bcm_pricing_calculator.py` to prepare AWS Billing and Cost Management (BCM) Pricing Calculator workloads and estimates.

---

## 2. Enterprise IAM Reference Policies (`examples/iam/`)

- [`examples/iam/README.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/examples/iam/README.md): Documentation detailing the enterprise IAM deployment roles, trust relationships, and governance principles enforced by MinusOps.
- [`examples/iam/ci-oidc-trust-policy.json`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/examples/iam/ci-oidc-trust-policy.json): Sample OIDC trust policy JSON for GitHub Actions / CI environment authentication without long-lived access keys.
- [`examples/iam/deploy-role-trust-policy.json`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/examples/iam/deploy-role-trust-policy.json): Sample trust policy JSON enforcing MFA requirement for assuming the infrastructure deployment role.
- [`examples/iam/finops-readonly-policy.json`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/examples/iam/finops-readonly-policy.json): IAM policy JSON defining read-only permissions for FinOps cost analysis, Cost Explorer access, and anomaly detection.
