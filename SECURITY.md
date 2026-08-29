# Security Policy

## Reporting a Vulnerability

MinusOps takes the security of autonomous governance, cryptographic plan-hash verification, and cloud provisioning integrity seriously. If you discover a security vulnerability, please report it privately.

**Please do NOT disclose security vulnerabilities publicly in GitHub Issues or pull requests.**

### How to Report

1. Email your report to **security@minusops.internal** (or open a GitHub Private Vulnerability Advisory).
2. Include a detailed description of the vulnerability, steps to reproduce, and the affected version or commit hash.
3. If applicable, provide a minimal proof-of-concept demonstrating the gate bypass or credential exposure.

### Response Timelines

- **Initial Response:** Within 24 hours.
- **Triage & Status Update:** Within 72 hours.
- **Patch Delivery:** Critical vulnerabilities receive emergency hotfixes within 7 days.

## Security Guarantees & Non-Negotiable Invariants

1. **Zero Ambient Mutation:** The control plane will never execute mutations (terraform apply, destructive cloud changes) without an audited cryptographic human-in-the-loop plan-hash signature.
2. **Bearer Token Isolation:** Webhook URLs, API tokens, and cloud secrets are never echoed, logged, or serialized into audit journals or UI states.
3. **Fail-Closed Gate:** Missing or unmapped policy findings are marked as unexamined rather than approved.
