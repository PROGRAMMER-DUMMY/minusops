# Documentation Context Index

This document provides exhaustive context for all documentation, architecture specifications, governance scopes, and visual assets within the [`docs`](./) directory.

---

## 1. Governance Specs & Architecture Core

- [`docs/architecture_svg_skeleton.svg`](./architecture_svg_skeleton.svg): Visual SVG template for dynamic architecture diagram generation.
- [`docs/architecture_svg_spec.md`](./architecture_svg_spec.md): Technical specification for generating architecture SVG diagrams.
- [`docs/architecture_synthesis.md`](./architecture_synthesis.md): Architecture synthesis specification for module composition and requirements mapping.
- [`docs/deploy_gate_flow.svg`](./deploy_gate_flow.svg): Diagram illustrating the verify -> plan -> approve -> apply deployment gate pipeline.
- [`docs/documentation_ledger.md`](./documentation_ledger.md): Official URL formula lookup ledger for AWS CLI and HashiCorp provider documentation.
- [`docs/enterprise_iam_manifest.md`](./enterprise_iam_manifest.md): Enterprise IAM role definition manifest and least-privilege boundary rules.
- [`docs/information_library.md`](./information_library.md): Redirect index and authoritative link catalog for external cloud and Terraform documentation.
- [`docs/operations_runbook.md`](./operations_runbook.md): Operational guide for driving control plane tasks, finops investigations, and troubleshooting.
- [`docs/pricing_catalog_support.md`](./pricing_catalog_support.md): Technical design for AWS BCM Pricing Calculator payload generation and cost estimation.
- [`docs/security_model.md`](./security_model.md): Security architecture and threat model covering plan-hash isolation and MFA deployment gating.

---

## 2. Phase Scopes & Engineering Milestones


---

## 3. Project Management, Roadmap & Guides

- [`docs/OPERATOR_ONBOARDING_GUIDE.md`](./OPERATOR_ONBOARDING_GUIDE.md): Step-by-step onboarding for the three control-plane modes -- local CLI quickstart, CI/CD OIDC integration, and containerized EKS/ECS hosting -- plus integration wiring and how to read a transport result (`sent`, never `ok`).
- [`docs/PROGRESS.md`](./PROGRESS.md): Live progress tracker recording completed features and test suite status.
- `docs/REPO_MAP.md`: Detailed repository layout map explaining module responsibilities. Not
  linked, and not in the repository: it is a generated point-in-time snapshot that
  `.gitignore` deliberately keeps local because it goes stale in source control. Regenerate
  it on demand rather than following a link that resolves only on the machine that made it.
- [`docs/walkthrough.md`](./walkthrough.md): User walkthrough guide covering CLI workflows and Plotly Dash control plane UI.

---

## 4. Subdirectories & Media Assets

### Superpowers Architecture Specs (`docs/superpowers/`)

### Visual Demo & Screenshots (`docs/demo/` & `docs/walkthrough/`)
- [`docs/demo/minusops-requirements.cast`](./demo/minusops-requirements.cast): Asciinema terminal recording of interactive requirements gathering.
- [`docs/demo/minusops-requirements.svg`](./demo/minusops-requirements.svg): Vector SVG rendering of terminal requirements session.
- Walkthrough UI Screenshots: [`01-overview.png`](./walkthrough/01-overview.png), [`02-optimization.png`](./walkthrough/02-optimization.png), [`03-reports.png`](./walkthrough/03-reports.png), [`04-readiness.png`](./walkthrough/04-readiness.png), [`05-architecture.png`](./walkthrough/05-architecture.png), [`06-architecture-code.png`](./walkthrough/06-architecture-code.png), [`07-resources.png`](./walkthrough/07-resources.png), [`08-services.png`](./walkthrough/08-services.png), [`09-plan-report.png`](./walkthrough/09-plan-report.png), [`10-cost-report.png`](./walkthrough/10-cost-report.png).
