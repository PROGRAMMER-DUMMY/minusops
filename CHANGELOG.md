# Changelog

All notable changes to MinusOps are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow SemVer.

## [0.1.0] — 2026-06-28

First packaged, productionization-focused release.

### Added
- **Packaging & distribution**: `pyproject.toml` with console scripts (`minusctl`,
  `minus-gate`, `minus-resolve`, `minus-workflow`, `minus-bcm`, `minus-runs`,
  `minus-demo`); pip-installable; `Dockerfile` (pinned Terraform + AWS CLI); release
  workflow producing a Sigstore build-provenance attestation + CycloneDX SBOM.
- **Tamper-evident audit log** (`core/audit_chain.py`): hash-chained append-only trail;
  `minusctl audit verify`. All components (gate, approval, audit_logger) share one chain.
- **Approver RBAC** (`core/authz.py`): operator identity from `MINUS_OPERATOR`; approver
  allowlist from `MINUS_APPROVERS` / `.minus/approvers.json`; enforced in the approval
  and deploy gates ("open" mode reported when unconfigured).
- **Credential-posture enforcement**: `apply` refuses long-term static keys / root and
  requires a temporary (SSO / assumed-MFA-role) session, making the MFA-gated promise real
  instead of assumed; override via audited `MINUS_ALLOW_STATIC_CREDS=1`. Ships IAM templates
  under `examples/iam/` (MFA deploy-role trust policy, CI OIDC trust, read-only FinOps).
- **Per-resource policy scanner**: `optimize_analyzer.py` now evaluates rules per
  resource block (fixes whole-file false negatives) and can merge external engines
  (checkov/tfsec) via `--external`.
- **Reporter golden tests** + real-terraform **gate e2e tests**.
- **Cross-platform CI matrix** (Linux/macOS/Windows × py3.10/3.12); CI deploy path now
  enforces the plan-hash gate (`plan_gate`) instead of a bare `plan && apply`.
- Enterprise docs: `docs/security_model.md`, `docs/operations_runbook.md`, `SECURITY.md`,
  and a runnable `examples/bcm-usage-profile.example.json`.

### Added
- **Evidence harness** (`minusctl prove`): one command proves the offline governance chain on
  this environment end-to-end — run generated → deploy-report artifacts present
  (architecture/plan/cost) → **audit-chain intact** → readiness — then reports exactly which
  AWS-gated steps remain (real BCM estimate, real gated apply) given the live **credential
  posture** (flags `long_term`/root: apply needs a temporary SSO session). Writes
  `evidence.md` + `evidence.json` as a hand-off artifact; exit 2 if the offline chain is unproven.
- **Audit-chain legacy handling** (`audit_chain.chain_status` + `audit_chain seal`):
  `chain_status` tolerates a **legacy pre-chaining prefix** (records written before chaining
  existed) while still proving the chained segment is intact — and detects tampering
  (mid-stream hash mismatch, records dropped from the front, or an un-chained record inserted
  after chaining began). `seal` is the one-time migration: it archives a legacy/old-format log to
  `audit.jsonl.<ts>.bak`, commits that file's SHA-256 into a fresh **chain-anchor** record, and
  starts a clean continuous chain — the honest alternative to weakening `verify`.
- **Forecast-vs-actual cost reconciliation**: `minus-bcm actuals --report-dir <dir>` pulls
  per-service **AWS Cost Explorer actuals** (read-only, no gate) for the most recent month with
  spend, writes `bcm-actuals.json`, and rebuilds `cost.pdf` with a **Forecast vs. actual** table
  (BCM forecast vs. CE actual, per-service variance $ and %, total drift). Service names are
  normalized so BCM `serviceCode`s line up with Cost Explorer service names. Both columns are
  real data — no invented numbers; services with an actual but no forecast show variance as n/a
  rather than fabricating one. Pure `reporter.forecast_vs_actual` is unit-tested.
- **Detailed cost report (v2)**: `cost.pdf` now renders summary cards (monthly + **annual ×12**
  + rate basis + priced-at), a **per-service table** (usage, **effective $/unit rate**, monthly,
  **% of total**), **cost-driver bars**, the **usage assumptions** that drove it, and a
  point-in-time/volatility + forecast-vs-actual note. The plan PDF cost section shows a compact
  summary and points to `cost.pdf`. Effective rate = BCM cost ÷ submitted usage (no hardcoded prices).
- **Per-service cost forecast via the AWS BCM Pricing Calculator** (Phases A–D): `prepare
  --derive` builds BCM usage **amounts from the run's blueprint inputs + transparent,
  overridable assumptions** (`--assume key=value`, recorded in `bcm-assumptions.json`);
  `serviceCode` from stable AWS identifiers; catalog fields from the reviewed profile.
  `run` now also calls `list-workload-estimate-usage` to pull **per-service line items**, and
  `reporter.refresh_cost` rebuilds `cost.pdf` with a per-service breakdown + monthly total.
  Rate type is selectable (`BEFORE_DISCOUNTS` / `AFTER_DISCOUNTS` /
  `AFTER_DISCOUNTS_AND_COMMITMENTS`). **No prices in code — AWS BCM prices everything.**
- **Commitment modeling (Phase F)**: `bcm scenario` orchestrates a BCM **bill scenario →
  bill estimate** (Savings Plan / RI modeling) — create-bill-scenario, usage/commitment
  modifications, create-bill-estimate, list line items + commitments — gated and audited.
  Usage/commitment payloads are user-supplied (no guessing); the cost report prefers the
  commitment-aware estimate when present.
- **Clickable architecture → code + findings**: the dashboard architecture viewer is now
  interactive — click any service box to open a Service inspector showing the exact
  plan-bound Terraform (from the report's source snapshot) plus that resource's findings,
  with **IDE-style Terraform syntax highlighting** (self-contained HCL highlighter, no CDN)
  and hidden scrollbars. A governance drill-down no SaaS diagram tool can do.
- **Deploy-gate process-flow diagram** (`reporter.build_gate_flow_svg` →
  `docs/deploy_gate_flow.svg`): deterministic, self-contained flowchart of
  verify → plan → approve → apply, styled with process-flow conventions (start/end pills,
  numbered automated/manual steps, decision **diamonds** with Yes/No branches, dashed
  **exception paths** into a REFUSED sink, shape/color legend) in the MinusOps palette.

### Fixed
- **`plan_inspector.find_report`** now returns the NEWEST complete report when several runs
  share a plan-hash (was returning the first/oldest, serving a stale diagram).
- **Dashboard report routes** `/resources` (was 404 — referenced a non-existent `service`
  key) and `/services` (rendered raw dicts) now work and render clean tables.
- **`plan_inspector.find_report`** no longer returns an incomplete report dir that shares a
  plan-hash with a complete one (caused all data routes to 404 when multiple runs existed);
  it now prefers a report with `manifest.json` + `plan.json`. Regression test added.
- **Dashboard scrollbar hidden** on tab panes (content still scrolls).
- **Architecture edges use orthogonal (right-angle) routing** — control edges route through
  the inter-lane channel and column alleys instead of cutting diagonally across the diagram.

### Changed
- **Architecture diagram generalized to any plan**: the generic (non-blueprint) layout now
  **collapses a service + its config resources into one node** (e.g. an S3 bucket with its
  versioning/lifecycle/encryption/public-access-block) via `_collapse_components`, so arbitrary
  Terraform renders as a clean service topology with icons, locks, and the governance overlay —
  no longer a pile of near-duplicate cards, and no longer reliant on the one blueprint.
- **Diagram presentation polish**: subtle blueprint grid background and a summary-card
  DEPLOYMENT POSTURE row (resources/services/encryption/findings/context/apply), in the
  MinusOps palette — adopting professional-diagram conventions while staying deterministic
  and self-contained.
- **Information-dense diagram**: each component box now carries a config detail line
  (S3 `KMS·versioned·lifecycle`, KMS `CMK·rotation`, IAM `N roles · M policies`, Glue
  `Spark ETL`, etc.), and a **DEPLOYMENT POSTURE** strip summarizes resources/counts,
  services, encryption, findings, owner·env·region (from plan variables), and approval state.
- **Inline service icons** (generic glyphs drawn as SVG paths — bucket, gears, magnifier,
  key, shield, bell, coin, workflow, book, inbox) replace the plain dots in both layouts,
  tinted on-palette. Recognizable like a SaaS diagram tool, but fully self-contained (no
  external images, no AWS-trademarked assets, embeds in the offline PDF).
- **Architecture diagram redrawn as a real flow/topology** for the pipeline blueprint
  (`build_pipeline_flow_svg`): a left-to-right RUNTIME DATA FLOW lane (Source → Bronze →
  Glue → Silver → Glue → Gold → Athena → Results) over an ORCHESTRATION & GOVERNANCE lane,
  with per-bucket config collapsed into single service boxes — replacing the tier-column
  "pile" (which remains the generic fallback for arbitrary plans).
- **Architecture diagram upgraded to spec v2 with a governance overlay (novel).** The
  generated SVG now draws real node-anchored data-flow edges (medallion path for the
  pipeline blueprint; control edges dashed), shows `for_each` instance labels
  (bronze/silver/gold), marks KMS-encrypted nodes with a lock, and **overlays each node's
  security/cost/observability findings as badges + a machine-readable `data-findings`
  attribute** — so the diagram doubles as the security/cost review surface, bound to the
  plan-hash. Tiering fixed (`aws_s3_object`/`aws_athena_*` → storage). `docs/architecture_svg_spec.md`
  bumped to v2; new golden tests in `tests/test_reporter.py`.
- **Dashboard redesigned as a fixed-screen, tabbed console** — top tabs (Overview /
  Optimization / Reports / Readiness) replace the long vertical scroll; the masthead and
  tab bar stay fixed and each section is its own viewport (internal scroll only on overflow).
- **Dashboard surfaces cost/security/observability optimization findings** as distinct
  per-category panels (`collect_optimization_findings` + `optimization_panels`), so the
  optimization the engine detects is visible on the live console instead of only in a
  markdown report. Covered by `tests/test_dashboard.py`.
- **`budget_calculator` is now BCM-only**: removed the unreliable live SKU price-lookup
  (wrong-region `usageType` filters); it returns honest cost guidance + the BCM commands
  and never fabricates a total. AWS BCM Pricing Calculator is the single cost source.
- **Architecture SVG now conforms to its binding spec on every path** (fixed
  `viewBox 0 0 1280 760`, the nine named groups, `data-address`/`data-action` on every
  node). The bespoke non-conformant pipeline diagram was removed.
- Cross-platform tool discovery (`core/toolpath.py`) replaces the personal hardcoded
  Windows paths and import-time registry side effects in `plan_gate`, `budget_calculator`,
  and `bcm_pricing_calculator`.
- `health_checker` now resolves the AWS CLI via shared discovery.
- `doctor.ps1` checks the Python runtime instead of stale SSH keys; uses `Get-CimInstance`.

### Removed
- Dead code and the hardcoded `OFFLINE_PRICING` table in `budget_calculator.py`; it now
  performs only honest live supporting-price lookups and never fabricates a total.
