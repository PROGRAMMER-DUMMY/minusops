# CONTEXT-app.md — Control Plane Console Context

## Overview
`app/` contains the web application control plane for **MinusOps**, implemented in [`app/dashboard_app.py`](./dashboard_app.py) using **Plotly Dash** and Flask. It serves as a unified, fixed-screen operator console for multi-cloud data pipeline delivery, governance, FinOps monitoring, and architectural verification.

---

## Detailed File Breakdown

### [`app/dashboard_app.py`](./dashboard_app.py)

#### 1. Architectural Role in MinusOps
`dashboard_app.py` acts as the graphical control plane interface for operators, data architects, and FinOps engineers. It integrates directly with the governance engine in [`core/`](../core) to surface real-time readiness scores, reference architecture conformance (evaluated against the six-layer analytics model), cost anomaly alerts, source code drift analysis, and interactive architecture visualization.

#### 2. Inputs & Environment Configuration
The application reads configuration from environment variables and local project state:
- **`DASH_PORT`**: Port to bind the dev server (default: `8050`).
- **`DASH_HOST`**: Interface host binding (default: `127.0.0.1`). If set to a non-loopback host (e.g. `0.0.0.0`), token authentication is enforced.
- **`MINUS_DASH_TOKEN`** / **`DASH_TOKEN`**: Shared bearer/cookie/query token required for non-local binds to enforce access control.
- **`MINUS_DASH_DEFAULT_TAB`**: Default active UI tab upon initial load (`overview`, `control`, `optimization`, `reports`, or `readiness`; default: `overview`).
- **`MINUS_CLOUD`**: Selects the active cloud provider via [`core/providers/base.py`](../core/providers/base.py) (default `aws`). Ambient cloud CLI credentials (e.g. `aws configure`) supply live account identity and Cost Explorer data.

#### 3. Outputs & HTTP Endpoints
`dashboard_app.py` exposes the following HTTP endpoints via its underlying Flask server (`app.server`):
- **`/`**: Main Dash interactive SPA dashboard.
- **`/deployment-reports/<report_id>/<path:filename>`**: Serves plan artifacts (`plan.pdf`, `cost.pdf`, `inspect.pdf`, etc.) safely from registered report directories.
- **`/deployment-reports/<report_id>/architecture`**: Interactive HTML/SVG viewer (`_ARCH_PAGE`) with pan/zoom controls, topology vs. data flow toggles, and click-to-code inspection of plan-bound HCL code and resource findings.
- **`/deployment-reports/<report_id>/diff`**: Plaintext HCL diff endpoint showing source drift between the original plan baseline and current disk state.
- **`/deployment-reports/<report_id>/inspect`**: Consolidated HTML review document rendered by [`core/reporting/reporter.py`](../core/reporting/reporter.py).
- **`/deployment-reports/<report_id>/services`**, **`/resources`**, **`/roles`**, **`/files`**: Granular HTML tables detailing services, resource change actions (`create`, `update`, `delete`, `no-op`), IAM roles/policies, and generated report files.
- **`/runs/<run_id>/<filename>`**: Serves run workspace artifacts ([`requirements.json`](../core/architecture/requirements.py), [`architecture_decision.json`](../core/architecture/architecture_decision.py), `enterprise-package.md`, `enterprise-package.json`).
- **`/runs/<run_id>/reports/<report_id>/<filename>`**: Serves run-scoped report assets (`architecture.svg`, `dataflow.svg`, `report.html`, `bcm-assumptions.json`, etc.).

#### 4. Data Assembly & Parallel Execution
- **`_fetch()`**: Hits the active cloud provider via [`core/providers/base.py`](../core/providers/base.py) using a `ThreadPoolExecutor` (3 workers) to run `provider.identity()`, `provider.cost_by_service()`, and `provider.anomalies()` concurrently.
- **`assemble()`**: Caches cloud fetch results for a 45-second TTL (`_TTL`) to prevent redundant AWS API calls during UI navigation.
- **`report_inventory()`**: Discovers generated plan reports across `artifacts/reports`, `.agents/reports`, and `runs/<run_id>/reports`, inspecting `manifest.json` and checking HCL source status via [`core/reporting/plan_inspector.py`](../core/reporting/plan_inspector.py).
- **`run_inventory()`**: Discovers run workspaces in `runs/`, validating requirements via [`core/architecture/requirements.py`](../core/architecture/requirements.py) and decision files via [`core/architecture/architecture_decision.py`](../core/architecture/architecture_decision.py), and calculating readiness via [`core/reporting/minusctl.py`](../core/reporting/minusctl.py).
- **`collect_optimization_findings()`**: Invokes [`core/reporting/optimize_analyzer.py`](../core/reporting/optimize_analyzer.py) to scan Terraform HCL files in active runs for Security (`SEC-*`), Cost (`COST-*`), and Observability (`OBS-*`) issues.

#### 5. Interface Layout & Component Architecture
The UI is organized into a fixed masthead and five core tabs:
- **Masthead**: Displays the MinusOps brand mark, global pipeline run selector dropdown (`#global-run-select`), masked account ID (`_redact_account`), refresh status, and manual refresh button (`#refresh-btn`).
- **Overview Tab**:
  - Selected Run Banner (`selected_run_banner`): Displays active run ID, user prompt, cloud provider, readiness score, and BCM cost status.
  - KPI Strip (`kpi`): High-level cards for Readiness (`/100`), Reference Conformance (`/100`), Plan Changes (`+create ~update -delete`), and Cost Evidence (`$/mo`).
  - Monthly Spend Panel (`monthly_spend_panel`): Plotly bar chart (`trend_line`) of trailing monthly AWS spend from Cost Explorer.
  - Spend by Service Panel (`spend_service_panel`): Horizontal bar chart (`spend_bar`) highlighting top service spenders.
  - Reference Conformance Panel (`conformance_panel`): Displays 6-layer analytics model coverage chips and Well-Architected gap findings.
  - Plan Composition Donut Chart (`plan_action_donut`): Interactive pie/donut chart of plan actions.
  - Spend Anomalies Ledger (`anomaly_panel`, `ledger`): Cost Anomaly Detection list showing impact amount, severity, and tagged owner.
- **Control Tab**:
  - Artifact Editor Panel (`control_editor_panel`): Form interface to view gate statuses (`requirements`, `decision`, `terraform`, `report`) and edit `architecture_decision.json` fields (architecture name, summary, selected modules, official doc sources, assumptions, risks, **validation**, **rollback**, **failure modes**, alternatives). `validation` and `rollback` are required by the decision gate, so the editor cannot produce a complete record without them; a failure-mode id outside `FM-01..FM-05` is rejected with a `ValueError` surfaced in the action status.
  - Control Action Callback (`_control_action`): Supports saving decisions (`write_control_decision`) or generating starter lakehouse files via [`core/generation/accelerators.py`](../core/generation/accelerators.py).
  - Run Cards (`control_run_card`): Lists recent runs with step-by-step CLI commands required to advance them through synthesis and deploy gating.
- **Optimization Tab**:
  - Findings Panels (`optimization_panels`): Grouped cards for Security, Cost, and Observability findings detected by [`core/reporting/optimize_analyzer.py`](../core/reporting/optimize_analyzer.py).
  - What-If Scenarios Panel (`scenario_shortcuts_panel`): Scale curve results table (`_scale_curve_table`), one-click trigger buttons for AWS pricing scale curves and actuals fetch (`_whatif_action` calling [`core/cost/bcm_pricing_calculator.py`](../core/cost/bcm_pricing_calculator.py)), and terminal command references.
- **Reports Tab**:
  - Architecture Data Flow Panel (`architecture_panel`): Embedded SVG diagram (`dataflow.svg` / `architecture.svg`) with direct link to the interactive click-to-code viewer.
  - Deployment Reports Inventory (`deployment_reports_panel`, `report_card`): List of generated plan reports with links to rendered PDFs (`plan.pdf`, `cost.pdf`, `inspect.pdf`).
- **Readiness Tab**:
  - Cross-Run Trend Table (`_run_trend_table`): Table comparing run readiness scores, conformance, volume tiers, monthly spend forecasts, and unit economics (`$/GB`).
  - Readiness Cards (`run_readiness_card`): Tabbed selector displaying run blockers, warnings, score breakdowns, and package artifact links.

#### 6. Visual Design System
- **Palette**: Warm dark mode theme defined in `C` (`bg: #14110f`, `bg_elev: #1c1714`, `panel: rgba(40, 33, 30, 0.62)`, `terracotta: #d95d39`, `sand: #d4a373`, `sage: #8da189`, `text: #fbf7f4`, `muted: #b09c93`).
- **Typography**: Google Fonts loaded via CDN: `Outfit` (Headings/Display), `Inter` (Body text), `JetBrains Mono` (Code/Metrics).
- **Security & Port Binding**: Pure Python WSGI application via Werkzeug. Checks socket availability (`_port_in_use`) and enforces authentication (`_enforce_dashboard_auth`, `_persist_dashboard_token`) when bound to external network interfaces.
