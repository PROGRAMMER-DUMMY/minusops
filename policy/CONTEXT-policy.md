# Policy Context Index

This document provides exhaustive context for all policy evaluation rules, Rego schemas, and stage definitions located within the [`policy`](./) directory.

---

## 1. Top-Level Policy Configurations

- [`policy/rule_stages.json`](./rule_stages.json): Configuration file defining severity levels (e.g. HIGH, MEDIUM, LOW) and stage gates for static code analysis, policy evaluation, and security checks across the deployment pipeline.

---

## 2. Gatekeeper Policy Rules (`policy/g6/`)

- [`policy/g6/rules.rego`](./g6/rules.rego): Open Policy Agent (OPA) Rego policy file implementing G6 policy evaluation rules for Terraform plans and infrastructure mutations. Enforces safety constraints including IAM wildcard restrictions, encryption requirements, resource tag compliance, and public access blocks.
