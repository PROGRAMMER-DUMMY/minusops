# Documentation Context Index

This document provides exhaustive context for all documentation, architecture specifications, governance scopes, and visual assets within the [`docs`](./) directory.

---

## 1. Governance Specs & Architecture Core

- [`docs/architecture_svg_skeleton.svg`](./architecture_svg_skeleton.svg): Visual SVG template for dynamic architecture diagram generation.
- [`docs/architecture_svg_spec.md`](./architecture_svg_spec.md): Technical specification for generating architecture SVG diagrams.
- [`docs/architecture_synthesis.md`](./architecture_synthesis.md): Architecture synthesis specification for module composition and requirements mapping.
- [`docs/audit_chain_lock_fix_scope.md`](./audit_chain_lock_fix_scope.md): Technical scope for fixing concurrency locks in the audit log hash chain.
- [`docs/deploy_gate_flow.svg`](./deploy_gate_flow.svg): Diagram illustrating the verify -> plan -> approve -> apply deployment gate pipeline.
- [`docs/documentation_ledger.md`](./documentation_ledger.md): Official URL formula lookup ledger for AWS CLI and HashiCorp provider documentation.
- [`docs/enterprise_iam_manifest.md`](./enterprise_iam_manifest.md): Enterprise IAM role definition manifest and least-privilege boundary rules.
- [`docs/information_library.md`](./information_library.md): Redirect index and authoritative link catalog for external cloud and Terraform documentation.
- [`docs/operations_runbook.md`](./operations_runbook.md): Operational guide for driving control plane tasks, finops investigations, and troubleshooting.
- [`docs/pricing_catalog_support.md`](./pricing_catalog_support.md): Technical design for AWS BCM Pricing Calculator payload generation and cost estimation.
- [`docs/security_model.md`](./security_model.md): Security architecture and threat model covering plan-hash isolation and MFA deployment gating.

---

## 2. Phase Scopes & Engineering Milestones

- [`docs/phase4_scope.md`](./phase4_scope.md): Engineering milestone specification for Phase 4 deliverables.
- [`docs/phase5_scope.md`](./phase5_scope.md): Engineering milestone specification for Phase 5 deliverables.
- [`docs/phase6_scope.md`](./phase6_scope.md): Engineering milestone specification for Phase 6 deliverables.
- [`docs/phase6_step1_authoring_scope.md`](./phase6_step1_authoring_scope.md): Scope specification for Phase 6 Step 1 HCL authoring capabilities.
- [`docs/phase6_step5_teardown_scope.md`](./phase6_step5_teardown_scope.md): Scope specification for Phase 6 Step 5 teardown and regression harness.
- [`docs/phase7_generation_engine_plan.md`](./phase7_generation_engine_plan.md): Master architecture plan for Phase 7 infrastructure generation engine.
- [`docs/phase7_item1_module_unit_scope.md`](./phase7_item1_module_unit_scope.md): Scope specification for Phase 7 Item 1 modular block synthesis.
- [`docs/phase7_item5_authoring_scope.md`](./phase7_item5_authoring_scope.md): Scope specification for Phase 7 Item 5 dynamic HCL authoring.
- [`docs/g2_scope.md`](./g2_scope.md): Governance scope specification for G2 capability gates.
- [`docs/g5_autonomy_boundary_scope.md`](./g5_autonomy_boundary_scope.md): Governance scope specification for G5 autonomy boundary controls.
- [`docs/g6_scope.md`](./g6_scope.md): Governance scope specification for G6 policy enforcement gates.
- [`docs/g6_iam_extension_scope.md`](./g6_iam_extension_scope.md): Governance scope specification for G6 IAM policy analysis extensions.
- [`docs/generation_engine_ground_truth_survey.md`](./generation_engine_ground_truth_survey.md): Ground truth survey evaluating synthesized HCL correctness.
- [`docs/generation_pivot_first_proof_2026-07-18.md`](./generation_pivot_first_proof_2026-07-18.md): Validation proof for requirements-driven module generation engine.

---

## 3. Project Management, Roadmap & Guides

- [`docs/OPERATOR_ONBOARDING_GUIDE.md`](./OPERATOR_ONBOARDING_GUIDE.md): Step-by-step onboarding for the three control-plane modes -- local CLI quickstart, CI/CD OIDC integration, and containerized EKS/ECS hosting -- plus integration wiring and how to read a transport result (`sent`, never `ok`).
- [`docs/PROGRESS.md`](./PROGRESS.md): Live progress tracker recording completed features and test suite status.
- [`docs/project_plan.md`](./project_plan.md): Comprehensive project implementation plan and component roadmap.
- [`docs/REMAINING_WORK.md`](./REMAINING_WORK.md): Remaining task backlog and future feature roadmap.
- `docs/REPO_MAP.md`: Detailed repository layout map explaining module responsibilities. Not
  linked, and not in the repository: it is a generated point-in-time snapshot that
  `.gitignore` deliberately keeps local because it goes stale in source control. Regenerate
  it on demand rather than following a link that resolves only on the machine that made it.
- [`docs/walkthrough.md`](./walkthrough.md): User walkthrough guide covering CLI workflows and Plotly Dash control plane UI.

---

## 4. Subdirectories & Media Assets

### Superpowers Architecture Specs (`docs/superpowers/`)
- [`docs/superpowers/plans/2026-07-18-knowledge-layer-spine.md`](./superpowers/plans/2026-07-18-knowledge-layer-spine.md): Technical plan for knowledge layer graph storage and indexing.
- [`docs/superpowers/plans/2026-07-22-requirements-driven-preplan.md`](./superpowers/plans/2026-07-22-requirements-driven-preplan.md): Technical plan for pre-plan requirements gathering and synthesis.
- [`docs/superpowers/specs/2026-07-21-generation-engine-cutover-design.md`](./superpowers/specs/2026-07-21-generation-engine-cutover-design.md): Design specification for cutover to requirements-first module synthesizer.

### Visual Demo & Screenshots (`docs/demo/` & `docs/walkthrough/`)
- [`docs/demo/minusops-requirements.cast`](./demo/minusops-requirements.cast): Asciinema terminal recording of interactive requirements gathering.
- [`docs/demo/minusops-requirements.svg`](./demo/minusops-requirements.svg): Vector SVG rendering of terminal requirements session.
- Walkthrough UI Screenshots: [`01-overview.png`](./walkthrough/01-overview.png), [`02-optimization.png`](./walkthrough/02-optimization.png), [`03-reports.png`](./walkthrough/03-reports.png), [`04-readiness.png`](./walkthrough/04-readiness.png), [`05-architecture.png`](./walkthrough/05-architecture.png), [`06-architecture-code.png`](./walkthrough/06-architecture-code.png), [`07-resources.png`](./walkthrough/07-resources.png), [`08-services.png`](./walkthrough/08-services.png), [`09-plan-report.png`](./walkthrough/09-plan-report.png), [`10-cost-report.png`](./walkthrough/10-cost-report.png).
