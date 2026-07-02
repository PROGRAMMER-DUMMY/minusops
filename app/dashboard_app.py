"""
MinusOps Console — governed data-pipeline delivery (Plotly Dash).

The overview leads with the pipeline itself: run readiness, reference-architecture
conformance, the plan-derived architecture diagram, plan composition, and the cost
gate. Account spend (Cost Explorer / Cost Anomaly Detection via finops_agent) is one
compact evidence panel. No mock data — everything degrades to honest empty states
when AWS credentials are not configured or nothing has been generated yet.

Cross-platform — runs the same on Windows, macOS, and Linux (pure Python + the
werkzeug dev server; no OS-specific calls).

Run:
    pip install -r requirements.txt          # (pip3 / python3 on macOS & Linux)
    python app/dashboard_app.py          # then open http://127.0.0.1:8050

Optional environment overrides:
    DASH_PORT=8060   # use a different port if 8050 is taken
    DASH_HOST=0.0.0.0  # expose on the LAN only with MINUS_DASH_TOKEN set
    MINUS_DASH_TOKEN=...  # optional bearer/query-token auth; required for non-local binds
"""
import os
import sys
import html as html_lib
import json
import time
import datetime
import hmac
from concurrent.futures import ThreadPoolExecutor

# Talk to the active cloud only through the provider abstraction (core/ package).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "core")
sys.path.insert(0, SCRIPTS)
from providers.base import get_provider, active_cloud  # noqa: E402
import plan_inspector  # noqa: E402
import runs as run_store  # noqa: E402
import minusctl  # noqa: E402
import requirements as reqgate  # noqa: E402
import architecture_decision as archdec  # noqa: E402
import accelerators  # noqa: E402

import dash  # noqa: E402
from dash import dcc, html, Input, Output, State, ctx  # noqa: E402
import plotly.graph_objects as go  # noqa: E402

# ---------------------------------------------------------------------------
# Design tokens — a warm, dark operator console (dusk, not cream).
# ---------------------------------------------------------------------------
C = {
    "bg":         "#14110f",
    "bg_elev":    "#1c1714",
    "panel":      "rgba(40, 33, 30, 0.62)",
    "line":       "rgba(217, 93, 57, 0.16)",
    "terracotta": "#d95d39",
    "terra_soft": "#e8825f",
    "sand":       "#d4a373",
    "sage":       "#8da189",
    "text":       "#fbf7f4",
    "muted":      "#b09c93",
    "faint":      "#6f635c",
}
DISPLAY = "'Outfit', sans-serif"
BODY = "'Inter', sans-serif"
MONO = "'JetBrains Mono', monospace"


# ---------------------------------------------------------------------------
# Data assembly (live cloud, via the active CloudProvider — aws | azure | gcp)
# ---------------------------------------------------------------------------
def derive_severity(impact):
    if impact >= 100:
        return "CRITICAL"
    if impact >= 25:
        return "HIGH"
    return "MODERATE"


def _fetch():
    """Hit the active cloud once, with the independent calls running in parallel."""
    provider = get_provider()
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_id = ex.submit(provider.identity)
        f_cost = ex.submit(provider.cost_by_service)
        f_anom = ex.submit(provider.anomalies)
        account, connected = f_id.result()
        cost = f_cost.result()
        anomalies_raw, anom_err = f_anom.result()

    anomalies = []
    for a in (anomalies_raw or []):
        owner = provider.owner(a["service"]) if a.get("service") else None
        anomalies.append({
            "id": a["id"], "service": a["service"], "date": a["date"],
            "impact": a["impact"], "severity": derive_severity(a["impact"]),
            "owner": owner,
        })

    return {
        "account": account, "connected": connected, "cloud": provider.name,
        "cost_ok": cost["ok"], "cost_err": cost["error"], "months": cost["months"],
        "anomalies": anomalies, "anom_err": anom_err,
    }


# Short TTL cache so back-to-back loads / navigations don't re-hit AWS every time.
_CACHE = {"ts": 0.0, "data": None}
_TTL = 45  # seconds
REPORT_ROOTS = [
    os.path.join(ROOT, "artifacts", "reports"),
    os.path.join(ROOT, ".agents", "reports"),
]


def report_roots():
    roots = list(REPORT_ROOTS)
    runs_root = os.path.join(ROOT, "runs")
    if os.path.isdir(runs_root):
        for name in sorted(os.listdir(runs_root), reverse=True):
            path = os.path.join(runs_root, name, "reports")
            if os.path.isdir(path):
                roots.append(path)
    return roots


def assemble(force=False):
    now = time.time()
    if not force and _CACHE["data"] is not None and (now - _CACHE["ts"]) < _TTL:
        return _CACHE["data"]
    data = _fetch()
    _CACHE["data"] = data
    _CACHE["ts"] = now
    return data


def report_inventory(run_id=None):
    """Return generated deployment reports, preferring product artifacts over agent internals."""
    reports = {}
    for root in report_roots():
        if not os.path.isdir(root):
            continue
        root_parts = os.path.normpath(root).split(os.sep)
        root_run_id = ""
        if "runs" in root_parts:
            idx = root_parts.index("runs")
            if len(root_parts) > idx + 1:
                root_run_id = root_parts[idx + 1]
        if run_id and root_run_id and not (root_run_id == run_id or root_run_id.startswith(str(run_id))):
            continue
        for name in os.listdir(root):
            path = os.path.join(root, name)
            manifest_path = os.path.join(path, "manifest.json")
            if not os.path.isdir(path) or not os.path.exists(manifest_path):
                continue
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    manifest = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            short = manifest.get("short") or name
            if short in reports:
                continue
            files = [file for file in manifest.get("files", []) if os.path.exists(os.path.join(path, file))]
            try:
                status = plan_inspector.source_status(short)
            except Exception:
                status = {"status": "UNKNOWN", "stale": False, "reason": "source status unavailable"}
            reports[short] = {
                "short": short,
                "path": path,
                "run_id": root_run_id,
                "template": manifest.get("template", "unknown"),
                "generated_at": manifest.get("generated_at", "unknown"),
                "counts": manifest.get("counts", {}),
                "cost": manifest.get("cost", {}),
                "files": files,
                "source": "run" if "\\runs\\" in root or "/runs/" in root else ("artifacts" if "artifacts" in root else "agent-runtime"),
                "status": status,
            }
    return sorted(reports.values(), key=lambda r: r["generated_at"], reverse=True)


def collect_optimization_findings(limit=3, run_id=None):
    """Run the per-resource scanner over the most recent run workspaces.

    Returns the SEC/COST/OBS findings (each tagged with its run_id) so the dashboard
    can surface optimization opportunities the engine already detects but otherwise
    only writes to a markdown report.
    """
    import optimize_analyzer  # core/ is already on sys.path
    findings = []
    try:
        runs_list = run_store.list_runs()
    except Exception:
        return findings
    selected = []
    for run in runs_list:
        rid = run.get("run_id", "")
        if run_id and not (rid == run_id or rid.startswith(str(run_id))):
            continue
        selected.append(run)
    for run in selected[:limit]:
        tf_dir = run.get("terraform_dir")
        if not tf_dir or not os.path.isdir(tf_dir):
            continue
        try:
            for finding in optimize_analyzer.scan_hcl_files(tf_dir):
                findings.append({**finding, "run_id": run.get("run_id")})
        except Exception:
            continue
    return findings


def _read_json_file(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _row_for_run(rows, run_id=None):
    if not rows:
        return None
    if run_id:
        for row in rows:
            rid = row["run"].get("run_id", "")
            if rid == run_id or rid.startswith(str(run_id)):
                return row
    return rows[0]


def _selected_report(row):
    if not row:
        return None
    latest = (row.get("readiness") or {}).get("latest_report") or {}
    if latest.get("path"):
        manifest = _read_json_file(os.path.join(latest["path"], "manifest.json")) or {}
        return {
            "short": latest.get("id") or manifest.get("short", ""),
            "path": latest.get("path"),
            "counts": manifest.get("counts", {}),
            "cost": manifest.get("cost", {}),
            "generated_at": manifest.get("generated_at", ""),
            "files": manifest.get("files", []),
            "status": row.get("readiness", {}).get("source", {}),
        }
    reports = report_inventory(row["run"].get("run_id"))
    return reports[0] if reports else None


def _plan_resource_rows(report):
    if not report:
        return []
    plan = _read_json_file(os.path.join(report.get("path", ""), "plan.json"))
    rows = []
    for change in (plan or {}).get("resource_changes", []):
        actions = change.get("change", {}).get("actions", [])
        rows.append({
            "address": change.get("address", ""),
            "type": change.get("type", ""),
            "action": "+".join(actions) if actions else "unknown",
            "service": plan_inspector.service_for_type(change.get("type", "")),
        })
    return rows


def _service_counts(report):
    counts = {}
    for row in _plan_resource_rows(report):
        if row["action"] == "no-op":
            continue
        counts[row["service"]] = counts.get(row["service"], 0) + 1
    return counts


def run_inventory(limit=8):
    """Return recent run workspaces with readiness status for the dashboard."""
    rows = []
    try:
        items = run_store.list_runs()
    except Exception:
        return rows
    for item in items[:limit]:
        try:
            readiness = minusctl._readiness(item)
        except Exception as exc:
            readiness = {
                "status": "UNKNOWN",
                "score": 0,
                "blockers": [{"name": "readiness unavailable", "detail": str(exc), "fix": "Inspect the run from the CLI."}],
                "warnings": [],
                "reports": [],
            }
        root = item.get("root", "")
        requirements_path = os.path.join(root, reqgate.FILENAME)
        decision_path = os.path.join(root, archdec.FILENAME)
        requirements_ok, _ = reqgate.validate(reqgate.load(requirements_path) or {})
        decision_ok, _ = archdec.validate(archdec.load(decision_path) or {})
        package_md = os.path.join(root, "enterprise-package.md")
        package_json = os.path.join(root, "enterprise-package.json")
        rows.append({
            "run": item,
            "readiness": readiness,
            "requirements_path": requirements_path if os.path.exists(requirements_path) else None,
            "requirements_ok": requirements_ok,
            "decision_path": decision_path if os.path.exists(decision_path) else None,
            "decision_ok": decision_ok,
            "package_md": package_md if os.path.exists(package_md) else None,
            "package_json": package_json if os.path.exists(package_json) else None,
        })
    return rows


# ---------------------------------------------------------------------------
# Plotly theming
# ---------------------------------------------------------------------------
def _base_layout(height):
    return dict(
        height=height,
        margin=dict(l=8, r=12, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=BODY, color=C["muted"], size=12),
        hoverlabel=dict(bgcolor=C["bg_elev"], font=dict(family=MONO, color=C["text"])),
        showlegend=False,
    )


def _money_tickformat(max_value):
    """Adaptive tick format so five identical '$0.00' ticks never happen on small spend."""
    if max_value >= 100:
        return ",.0f"
    if max_value >= 1:
        return ",.2f"
    return ",.4f"


def spend_bar(month):
    """Spend by service — horizontal bars with EMPHASIS: the top spender carries the
    accent hue, the rest are de-emphasized (identity is in the labels, not a hue cycle)."""
    items = sorted((month or {}).get("by_service", {}).items(), key=lambda r: r[1])[-8:]
    fig = go.Figure()
    if items:
        labels = [s.replace("Amazon", "").replace("AWS", "").strip() for s, _ in items]
        vals = [v for _, v in items]
        hi = max(vals)
        colors = [C["terracotta"] if v == hi else C["muted"] for v in vals]
        fig.add_bar(
            x=vals, y=labels, orientation="h", marker=dict(color=colors),
            text=[f"${v:,.2f}" if hi < 100 else f"${v:,.0f}" for v in vals],
            textposition="outside",
            textfont=dict(family=MONO, color=C["text"], size=11),
            hovertemplate="%{y}: $%{x:,.2f}<extra></extra>",
        )
    lay = _base_layout(max(180, 34 * max(len(items), 1) + 20))
    lay.update(
        margin=dict(l=10, r=56, t=8, b=8),          # room for outside value labels
        xaxis=dict(visible=False),
        yaxis=dict(tickfont=dict(family=MONO, color=C["muted"], size=11), automargin=True),
        bargap=0.4,
    )
    fig.update_layout(**lay)
    try:
        fig.update_layout(barcornerradius=3)
    except Exception:
        pass
    return fig


def trend_line(months):
    """Monthly spend — thin single-hue columns (magnitude by month; a spline over
    near-zero months fabricates a curve, so bars it is — the Cost Explorer form).
    Micro-spend (< 1¢ max) hides the axis (every tick would read $0.00) and
    direct-labels the non-zero bars instead."""
    fig = go.Figure()
    vals = [m["total"] for m in (months or [])]
    mx = max(vals) if vals else 0
    micro = 0 < mx < 0.01
    def _micro_label(v):
        if not v:
            return ""
        s = f"${v:.6f}".rstrip("0").rstrip(".")
        return s if s != "$0" else "< $0.000001"

    if months:
        fig.add_bar(
            x=[m["month"] for m in months], y=vals,
            marker=dict(color=C["terracotta"]),
            text=[_micro_label(v) for v in vals] if micro else None,
            textposition="outside" if micro else None,
            textfont=dict(family=MONO, color=C["muted"], size=10) if micro else None,
            cliponaxis=False,                       # outside labels must not be cut at the plot edge
            hovertemplate="%{x}: $%{y:,.6f}<extra></extra>" if micro
            else "%{x}: $%{y:,.2f}<extra></extra>",
        )
    lay = _base_layout(200)
    lay.update(
        margin=dict(l=10, r=18, t=18, b=34),        # keep month + outside labels inside the card
        bargap=0.45,
        xaxis=dict(tickfont=dict(family=MONO, color=C["faint"], size=11),
                   showgrid=False, showline=False),
        yaxis=(dict(visible=False) if micro else
               dict(tickprefix="$", tickformat=_money_tickformat(mx), nticks=4,
                    tickfont=dict(family=MONO, color=C["faint"], size=11),
                    gridcolor=C["line"], zeroline=False)),
    )
    fig.update_layout(**lay)
    try:
        fig.update_layout(barcornerradius=3)
    except Exception:
        pass
    return fig


def plan_action_donut(report):
    counts = (report or {}).get("counts") or {}
    palette = {"create": C["sage"], "update": C["sand"], "delete": C["terracotta"], "no-op": C["faint"]}
    # Zero-value slices only stack unreadable "x 0" labels on the rim — drop them.
    present = [(label, counts.get(label, 0)) for label in palette if counts.get(label, 0)]
    fig = go.Figure()
    if present:
        fig.add_pie(
            labels=[label for label, _ in present],
            values=[v for _, v in present],
            hole=.64,
            marker=dict(colors=[palette[label] for label, _ in present],
                        line=dict(color=C["bg"], width=2)),
            textinfo="label+value",
            textfont=dict(family=MONO, color=C["text"], size=12),
            hovertemplate="%{label}: %{value}<extra></extra>",
        )
    lay = _base_layout(220)
    lay.update(margin=dict(l=10, r=10, t=10, b=10))
    fig.update_layout(**lay)
    return fig


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
def kpi(label, value, sub=None, tone="text"):
    return html.Div(className="kpi", children=[
        html.Div(label, className="kpi-label"),
        html.Div(value, className="kpi-value", style={"color": C[tone]}),
        html.Div(sub or "", className="kpi-sub"),
    ])


def panel(title, eyebrow, body):
    return html.Section(className="panel", children=[
        html.Div(className="panel-head", children=[
            html.Span(eyebrow, className="eyebrow"),
            html.H2(title, className="panel-title"),
        ]),
        body,
    ])


def ledger_entry(a):
    tone = {"CRITICAL": C["terracotta"], "HIGH": C["sand"], "MODERATE": C["sage"]}[a["severity"]]
    owner = a["owner"] or "owner unresolved — check tags"
    return html.Div(className="ledger-entry", style={"borderLeftColor": tone}, children=[
        html.Div(className="ledger-top", children=[
            html.Span(a["service"].replace("Amazon", "").replace("AWS", "").strip(),
                      className="ledger-service"),
            html.Span(f"+${a['impact']:,.0f}", className="ledger-impact",
                      style={"color": tone}),
        ]),
        html.Div(className="ledger-meta", children=[
            html.Span(a["date"]),
            html.Span("·", className="dot"),
            html.Span(a["severity"], style={"color": tone, "fontWeight": 600}),
        ]),
        html.Div(owner, className="ledger-owner"),
    ])


def ledger(anomalies):
    if not anomalies:
        return html.Div(className="empty sage", children=[
            html.Div("No anomalies", className="empty-title"),
            html.Div("Spend is within expected bounds.", className="empty-sub"),
        ])
    return html.Div(className="ledger", children=[ledger_entry(a) for a in anomalies])


def report_link(short, filename, label):
    return html.A(label, href=f"/deployment-reports/{short}/{filename}",
                  target="_blank", className="report-link")


def report_card(report):
    counts = report["counts"]
    short = report["short"]
    files = set(report["files"])
    status = report.get("status", {})
    status_text = status.get("status", "UNKNOWN")
    status_class = "stale" if status.get("stale") else "current"
    links = [html.A("Architecture", href=f"/deployment-reports/{short}/architecture",
                    target="_blank", className="report-link")]
    for filename, label in [("plan.pdf", "Plan PDF"), ("cost.pdf", "Cost PDF")]:
        if filename in files:
            links.append(report_link(short, filename, label))
    links.append(html.A("Diff", href=f"/deployment-reports/{short}/diff",
                        target="_blank", className="report-link"))
    for view in ("services", "resources", "roles", "files"):
        links.append(html.A(view.title(), href=f"/deployment-reports/{short}/{view}",
                            target="_blank", className="report-link"))
    return html.Div(className="report-card", children=[
        html.Div(className="report-main", children=[
            html.Div(report["template"], className="report-title"),
            html.Div(className="report-meta", children=[
                html.Span(short),
                html.Span("source " + report["source"]),
                html.Span(report["generated_at"]),
                html.Span(status_text, className=f"report-status {status_class}"),
            ]),
        ]),
        html.Div(className="report-counts", children=[
            html.Span(f"+{counts.get('create', 0)}"),
            html.Span(f"~{counts.get('update', 0)}"),
            html.Span(f"-{counts.get('delete', 0)}"),
        ]),
        html.Div(className="report-links", children=links or [
            html.Span("No rendered files found", className="report-missing")
        ]),
    ])


def latest_report_summary(report):
    counts = report["counts"]
    status = report.get("status", {})
    return html.Div(className="latest-report", children=[
        html.Div(className="eyebrow", children="latest report"),
        html.Div(report["template"], className="latest-title"),
        html.Div(className="latest-meta", children=[
            html.Span(report["short"]),
            html.Span(report["generated_at"]),
            html.Span(status.get("status", "UNKNOWN")),
        ]),
        html.Div(className="latest-counts", children=[
            html.Span(f"{counts.get('create', 0)} create"),
            html.Span(f"{counts.get('update', 0)} update"),
            html.Span(f"{counts.get('delete', 0)} delete"),
        ]),
    ])


def run_readiness_card(item):
    run = item["run"]
    readiness = item["readiness"]
    status = readiness.get("status", "UNKNOWN")
    tone = "ready" if status == "READY" else "blocked" if status == "BLOCKED" else "evidence"
    blockers = readiness.get("blockers", [])
    warnings = readiness.get("warnings", [])
    latest = readiness.get("latest_report") or {}
    report_id = latest.get("id")
    report_path = latest.get("path")
    links = []
    if report_id and report_path:
        for filename, label in [
            ("architecture.svg", "Architecture"),
            ("dataflow.svg", "Data flow"),
            ("report.html", "Report HTML"),
            ("plan.pdf", "Plan PDF"),
            ("cost.pdf", "Cost PDF"),
            ("bcm-assumptions.json", "BCM Assumptions"),
        ]:
            if os.path.exists(os.path.join(report_path, filename)):
                links.append(html.A(label, href=f"/runs/{run['run_id']}/reports/{report_id}/{filename}",
                                    target="_blank", className="report-link"))
    if item.get("package_md"):
        links.append(html.A("Package MD", href=f"/runs/{run['run_id']}/enterprise-package.md",
                            target="_blank", className="report-link"))
    if item.get("package_json"):
        links.append(html.A("Package JSON", href=f"/runs/{run['run_id']}/enterprise-package.json",
                            target="_blank", className="report-link"))
    if item.get("requirements_path"):
        label = "Requirements OK" if item.get("requirements_ok") else "Requirements"
        links.append(html.A(label, href=f"/runs/{run['run_id']}/requirements.json",
                            target="_blank", className="report-link"))
    else:
        links.append(html.Span("requirements missing", className="report-missing"))
    if item.get("decision_path"):
        label = "Decision OK" if item.get("decision_ok") else "Decision"
        links.append(html.A(label, href=f"/runs/{run['run_id']}/architecture_decision.json",
                            target="_blank", className="report-link"))
    else:
        links.append(html.Span("decision missing", className="report-missing"))
    if not item.get("package_md"):
        links.append(html.Span("run: python core/minusctl.py package", className="report-missing"))
    first_issue = (blockers or warnings or [{}])[0]
    return html.Div(className=f"run-card {tone}", children=[
        html.Div(className="run-main", children=[
            html.Div(run.get("run_id", "unknown"), className="run-title"),
            html.Div(className="run-meta", children=[
                html.Span(run.get("blueprint", "-")),
                html.Span(run.get("cloud", "-")),
                html.Span("req ok" if item.get("requirements_ok") else "req open"),
                html.Span("decision ok" if item.get("decision_ok") else "decision open"),
                html.Span(f"reports {len(readiness.get('reports', []))}"),
                html.Span(readiness.get("source", {}).get("status", "UNKNOWN")),
            ]),
        ]),
        html.Div(className="readiness-score", children=[
            html.Span(status),
            html.Strong(f"{readiness.get('score', 0)}/100"),
        ]),
        html.Div(className="readiness-issue", children=[
            html.Span(first_issue.get("name", "ready for review")),
            html.Small(first_issue.get("fix", "No blocking readiness issue detected.")),
        ]),
        html.Div(className="report-links", children=links),
    ])


def _gate_status(label, ok, present=True):
    tone = "ok" if ok else "open" if present else "missing"
    value = "complete" if ok else "open" if present else "missing"
    return html.Div(className=f"gate-status {tone}", children=[
        html.Span(label),
        html.Strong(value),
    ])


def _command_line(text):
    return html.Code(text, className="command-line")


def _split_control_lines(value):
    if not value:
        return []
    raw = str(value).replace(",", "\n").splitlines()
    return [item.strip() for item in raw if item.strip()]


def _find_dashboard_run(run_id):
    if not run_id:
        return None
    for item in run_store.list_runs():
        if item.get("run_id") == run_id or item.get("run_id", "").startswith(str(run_id)):
            return item
    return None


def write_control_decision(run, selected_architecture="", decision_summary="", modules_text="",
                           sources_text="", assumptions_text="", risks_text="", alternatives_text="",
                           decided_by="dashboard"):
    decision_path = os.path.join(run["root"], archdec.FILENAME)
    requirements_file = os.path.join(run["root"], reqgate.FILENAME)
    data = archdec.load_or_template(decision_path, requirements_file=requirements_file)
    if selected_architecture:
        data["selected_architecture"] = selected_architecture.strip()
    if decision_summary:
        data["decision_summary"] = decision_summary.strip()
    module_ids = _split_control_lines(modules_text)
    if module_ids:
        known = {item["id"] for item in archdec.module_registry.list_modules()}
        unknown = [module_id for module_id in module_ids if module_id not in known]
        if unknown:
            raise ValueError("unknown module id(s): " + ", ".join(unknown))
        data["selected_modules"] = module_ids
    for field, text in (("sources", sources_text), ("assumptions", assumptions_text), ("risks", risks_text)):
        values = _split_control_lines(text)
        if values:
            data[field] = values
    alternatives = []
    for line in (alternatives_text or "").splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|", 2)]
        if len(parts) != 3 or not all(parts):
            raise ValueError("alternatives must use: name | decision | reason")
        alternatives.append({"name": parts[0], "decision": parts[1], "reason": parts[2]})
    if alternatives:
        data["alternatives"] = alternatives
    archdec.save(decision_path, data, decided_by=decided_by)
    ok, missing = archdec.validate(data)
    return {"path": decision_path, "record": data, "ok": ok, "missing": missing}


def control_editor_panel(rows, selected_run_id=None):
    selected_row = _row_for_run(rows, selected_run_id)
    options = [
        {"label": row["run"].get("run_id", "run"), "value": row["run"].get("run_id")}
        for row in rows
    ]
    latest = selected_row["run"] if selected_row else {}
    decision = archdec.load(os.path.join(latest.get("root", ""), archdec.FILENAME)) or {}
    modules_value = "\n".join(decision.get("selected_modules") or [
        "storage-medallion-s3",
        "compute-glue-etl",
        "orchestrator-stepfunctions",
        "dq-great-expectations",
        "schema-registry-glue",
        "query-athena",
        "governance-observability",
    ])
    sources_value = "\n".join(decision.get("sources") or [])
    assumptions_value = "\n".join(decision.get("assumptions") or [])
    risks_value = "\n".join(decision.get("risks") or [])
    alternatives_value = "\n".join(
        f"{item.get('name', '')} | {item.get('decision', '')} | {item.get('reason', '')}"
        for item in (decision.get("alternatives") or [])
        if item.get("name") or item.get("reason")
    )
    return html.Div(className="control-editor", children=[
        html.Div(className="control-editor-head", children=[
            html.Div("Artifact editor", className="control-editor-title"),
            html.Div("Write requirements and architecture-decision evidence before synthesis.", className="control-editor-sub"),
        ]),
        html.Div(className="control-editor-gates", children=[
            _gate_status("requirements", (selected_row or {}).get("requirements_ok"), bool((selected_row or {}).get("requirements_path"))),
            _gate_status("decision", (selected_row or {}).get("decision_ok"), bool((selected_row or {}).get("decision_path"))),
            _gate_status("terraform", os.path.exists(os.path.join(latest.get("terraform_dir", ""), "minus-generated.json")),
                         os.path.isdir(latest.get("terraform_dir", ""))),
            _gate_status("report", bool(((selected_row or {}).get("readiness") or {}).get("latest_report")), True),
        ]),
        html.Div(className="control-form-grid", children=[
            html.Label(className="field-label", children=[
                html.Span("Run"),
                dcc.Dropdown(id="control-run-select", options=options,
                             value=latest.get("run_id"), clearable=False, className="control-select"),
            ]),
            html.Label(className="field-label", children=[
                html.Span("Selected architecture"),
                dcc.Input(id="control-architecture", value=decision.get("selected_architecture", ""),
                          placeholder="AWS governed lakehouse with Step Functions orchestration",
                          className="control-input"),
            ]),
            html.Label(className="field-label wide", children=[
                html.Span("Decision summary"),
                dcc.Textarea(id="control-summary", value=decision.get("decision_summary", ""),
                             placeholder="Why this architecture fits the gathered requirements.",
                             className="control-textarea"),
            ]),
            html.Label(className="field-label", children=[
                html.Span("Selected modules"),
                dcc.Textarea(id="control-modules", value=modules_value,
                             placeholder="One module id per line", className="control-textarea small"),
            ]),
            html.Label(className="field-label", children=[
                html.Span("Official sources"),
                dcc.Textarea(id="control-sources", value=sources_value,
                             placeholder="One official URL per line", className="control-textarea small"),
            ]),
            html.Label(className="field-label", children=[
                html.Span("Assumptions"),
                dcc.Textarea(id="control-assumptions", value=assumptions_value,
                             placeholder="One assumption per line", className="control-textarea small"),
            ]),
            html.Label(className="field-label", children=[
                html.Span("Risks"),
                dcc.Textarea(id="control-risks", value=risks_value,
                             placeholder="One risk per line", className="control-textarea small"),
            ]),
            html.Label(className="field-label wide", children=[
                html.Span("Alternatives"),
                dcc.Textarea(id="control-alternatives", value=alternatives_value,
                             placeholder="Name | decision | reason", className="control-textarea small"),
            ]),
        ]),
        html.Div(className="control-actions", children=[
            html.Button("Write lakehouse starter", id="control-accelerator-btn", n_clicks=0, className="control-button"),
            html.Button("Save decision", id="control-save-decision-btn", n_clicks=0, className="control-button primary"),
            dcc.Checklist(
                id="control-force",
                options=[{"label": "overwrite existing starter files", "value": "force"}],
                value=[],
                className="control-checklist",
            ),
        ]),
        html.Div(id="control-action-status", className="control-status"),
    ])


def control_run_card(item):
    run = item["run"]
    readiness = item["readiness"]
    run_id = run.get("run_id", "run")
    req_path = item.get("requirements_path")
    decision_path = item.get("decision_path")
    requirements_file = req_path or os.path.join(run.get("root", ""), reqgate.FILENAME)
    decision_file = decision_path or os.path.join(run.get("root", ""), archdec.FILENAME)
    links = []
    if req_path:
        links.append(html.A("requirements.json", href=f"/runs/{run_id}/requirements.json",
                            target="_blank", className="report-link"))
    if decision_path:
        links.append(html.A("architecture_decision.json", href=f"/runs/{run_id}/architecture_decision.json",
                            target="_blank", className="report-link"))
    latest = readiness.get("latest_report") or {}
    if latest.get("id"):
        links.append(html.A("latest report", href=f"/runs/{run_id}/reports/{latest['id']}/report.html",
                            target="_blank", className="report-link"))
    commands = [
        f"python core/requirements.py check {requirements_file}",
        f"python core/minusctl.py decision template --run {run_id} --write",
        f"python core/architecture_decision.py set {decision_file} --architecture \"<selected architecture>\" --summary \"<why this choice>\"",
        f"python core/architecture_decision.py add-module {decision_file} <module-id>",
        f"python core/architecture_decision.py add-source {decision_file} \"<official doc URL>\"",
        f"python core/architecture_decision.py check {decision_file}",
        f"python core/synthesizer.py \"<requirements summary>\" --run {run_id} --requirements-file {requirements_file} --decision-file {decision_file}",
        f"python core/plan_gate.py verify --dir {run.get('terraform_dir')} --policy-mode production",
    ]
    return html.Div(className="control-card", children=[
        html.Div(className="control-main", children=[
            html.Div(run_id, className="run-title"),
            html.Div(run.get("request", "-"), className="control-request"),
            html.Div(className="run-meta", children=[
                html.Span(run.get("cloud", "-")),
                html.Span(run.get("blueprint", "-")),
                html.Span(readiness.get("status", "UNKNOWN")),
            ]),
        ]),
        html.Div(className="gate-grid", children=[
            _gate_status("requirements", item.get("requirements_ok"), bool(req_path)),
            _gate_status("decision", item.get("decision_ok"), bool(decision_path)),
            _gate_status("terraform", os.path.exists(os.path.join(run.get("terraform_dir", ""), "minus-generated.json")),
                         os.path.isdir(run.get("terraform_dir", ""))),
        ]),
        html.Details(className="command-details", children=[
            html.Summary("CLI commands"),
            html.Div(className="command-stack", children=[_command_line(command) for command in commands]),
        ]),
        html.Div(className="report-links", children=links or [
            html.Span("no control artifacts yet", className="report-missing")
        ]),
    ])


def control_plane_panel(selected_run_id=None):
    rows = run_inventory()
    selected = _row_for_run(rows, selected_run_id)
    if not rows:
        body = html.Div(className="empty sage", children=[
            html.Div("No run workspaces", className="empty-title"),
            html.Div('Run core/minusctl.py create "<request>".', className="empty-sub"),
        ])
    else:
        visible_rows = [selected] + [row for row in rows if row is not selected] if selected else rows
        body = html.Div(className="control-stack", children=[
            control_editor_panel(rows, selected_run_id=selected_run_id),
            html.Div(className="control-list", children=[control_run_card(row) for row in visible_rows[:4]]),
        ])
    return panel("Control plane", "requirements -> decision -> synthesis", body)


def readiness_panel(selected_run_id=None):
    rows = run_inventory()
    total_runs = len(run_store.list_runs()) if os.path.isdir(os.path.join(ROOT, "runs")) else 0
    selected = _row_for_run(rows, selected_run_id)
    if not rows:
        body = html.Div(className="empty sage", children=[
            html.Div("No run workspaces", className="empty-title"),
            html.Div('Run core/minusctl.py create "<request>" to create a requirements-first workspace.', className="empty-sub"),
        ])
    else:
        tabs = []
        visible = [selected] + [row for row in rows if row is not selected] if selected else rows
        for idx, row in enumerate(visible):
            run = row["run"]
            readiness = row["readiness"]
            label = ("Selected " if idx == 0 else "") + run.get("run_id", "run")
            tabs.append(dcc.Tab(
                label=label,
                value=run.get("run_id", str(idx)),
                className="run-tab",
                selected_className="run-tab selected",
                children=run_readiness_card(row),
            ))
        body = html.Div(className="runs", children=[
            dcc.Tabs(
                value=visible[0]["run"].get("run_id"),
                className="run-tabs",
                children=tabs,
            ),
            html.Div(f"Showing {len(rows)} selectable run(s). Older runs not shown: {max(total_runs - len(rows), 0)}. Use `python core/minusctl.py runs list` for full history.",
                     className="run-history-note"),
        ])
    return panel("Enterprise readiness", "run history", body)


_SEVERITY_TONE = {"HIGH": "terracotta", "MEDIUM": "sand", "LOW": "sage", "EXTERNAL": "muted"}


def finding_row(f):
    tone = C[_SEVERITY_TONE.get(f.get("severity", ""), "muted")]
    return html.Div(className="finding", style={"borderLeftColor": tone}, children=[
        html.Div(className="finding-top", children=[
            html.Span(f"{f.get('id', '')} · {f.get('resource') or '—'}", className="finding-id"),
            html.Span(f.get("severity", ""), className="finding-sev", style={"color": tone}),
        ]),
        html.Div(f.get("title", ""), className="finding-title"),
        html.Div(f.get("description", ""), className="finding-desc"),
    ])


def conformance_panel(readiness):
    """Reference-architecture conformance: six-layer coverage + Well-Architected gaps.

    Reads the report that minusctl._readiness already computed (readiness['conformance']),
    so it stays deterministic and consistent with the CLI / enterprise package.
    """
    conf = (readiness or {}).get("conformance")
    if not conf:
        return panel("Reference conformance", "six-layer analytics model",
                     html.Div(className="empty sage", children=[
                         html.Div("No plan analyzed", className="empty-title"),
                         html.Div("Run plan_gate plan to score against the reference architecture.",
                                  className="empty-sub"),
                     ]))
    score = conf.get("score", 0)
    tone = C["sage"] if score >= 90 else C["sand"] if score >= 60 else C["terracotta"]
    chips = []
    for name, info in (conf.get("layers") or {}).items():
        present = info.get("present")
        chips.append(html.Span(
            f"{name} {info.get('count', 0)}" if present else f"{name} —",
            style={
                "display": "inline-block", "padding": ".2rem .55rem", "marginRight": ".4rem",
                "marginBottom": ".4rem", "borderRadius": "20px", "fontSize": ".72rem",
                "fontFamily": MONO,
                "color": C["text"] if present else C["faint"],
                "border": f"1px solid {C['sage'] if present else C['line']}",
            }))
    findings = conf.get("findings", [])
    body = html.Div(children=[
        html.Div(style={"display": "flex", "alignItems": "baseline", "gap": ".6rem",
                        "marginBottom": ".6rem"}, children=[
            html.Strong(f"{score}/100", style={"color": tone, "fontFamily": MONO, "fontSize": "1.4rem"}),
            html.Span(conf.get("status", ""), style={"color": C["muted"], "fontFamily": MONO,
                                                     "fontSize": ".78rem"}),
        ]),
        html.Div(chips, style={"marginBottom": ".6rem"}),
        html.Div(className="findings", children=[
            finding_row({"id": f.get("id", ""), "severity": f.get("severity", ""),
                         "resource": f.get("reference", ""), "title": f.get("title", ""),
                         "description": f.get("detail", "")})
            for f in findings
        ]) if findings else html.Div("Conforms to the reference architecture + Well-Architected checks.",
                                     className="empty-sub"),
    ])
    return panel("Reference conformance", "six-layer analytics model · Well-Architected", body)


def optimization_panels(selected_run_id=None):
    """One distinct panel per finding category (Cost / Security / Observability)."""
    findings = collect_optimization_findings(run_id=selected_run_id)
    if not findings:
        return [panel("Optimization & findings", "scan of generated runs",
                      html.Div(className="empty sage", children=[
                          html.Div("No findings", className="empty-title"),
                          html.Div("Generated runs pass the security, cost, and observability scan.",
                                   className="empty-sub"),
                      ]))]
    grouped = {}
    for f in findings:
        grouped.setdefault(f.get("category", "Other"), []).append(f)
    eyebrows = {"Cost": "cost optimization", "Security": "security", "Observability": "observability"}
    panels = []
    for category in ["Cost", "Security", "Observability"] + [c for c in grouped if c not in ("Cost", "Security", "Observability")]:
        items = grouped.get(category)
        if not items:
            continue
        panels.append(panel(f"{category} findings", eyebrows.get(category, category.lower()),
                            html.Div(className="findings", children=[finding_row(f) for f in items])))
    return panels


def deployment_reports_panel(selected_run_id=None):
    reports = report_inventory(selected_run_id)
    if not reports:
        body = html.Div(className="empty sage", children=[
            html.Div("No deployment reports", className="empty-title"),
            html.Div("Run core/demo.py or plan_gate.py plan to generate report artifacts.", className="empty-sub"),
        ])
    else:
        body = html.Div(className="reports", children=[
            latest_report_summary(reports[0]),
            *[report_card(r) for r in reports[:6]],
        ])
    return panel("Deployment reports", "plan artifacts", body)


# ---------------------------------------------------------------------------
# Page — static shell renders instantly; data fills in via a callback (with a
# loading spinner) so a refresh never blocks on AWS round-trips.
# ---------------------------------------------------------------------------
def _cost_status(report):
    cost = (report or {}).get("cost") or {}
    if cost.get("ok"):
        try:
            total = float(cost.get("monthly_total_usd") or 0)
        except (TypeError, ValueError):
            total = 0
        value = f"${total:,.2f}/mo" if total else "priced"
        sub, tone = "AWS BCM estimate (on-demand list price)", "sage"
        budget = cost.get("monthly_budget_usd")
        if budget and total:
            util = total / float(budget) * 100
            sub = f"{util:.0f}% of the ${float(budget):,.0f}/mo budget guardrail"
            tone = "sage" if util <= 80 else "sand" if util <= 100 else "terracotta"
        return value, sub, tone
    if cost.get("bcm_pricing_calculator_required") or cost.get("ok") is False:
        return "BCM required", "cost unavailable until approved AWS BCM estimate", "sand"
    return "unknown", "cost evidence unavailable", "muted"


def _redact_account(account):
    value = str(account or "").strip()
    if not value:
        return "not connected"
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) >= 4:
        return "••••••••" + digits[-4:]
    return "connected"


def selected_run_banner(row, report):
    if not row:
        return html.Div(className="banner", children="No run selected.")
    run = row["run"]
    readiness = row.get("readiness") or {}
    report_id = (report or {}).get("short") or "no report"
    cost_value, cost_sub, cost_tone = _cost_status(report)
    return html.Div(className="selected-run-banner", children=[
        html.Div(className="selected-main", children=[
            html.Div(run.get("run_id", "run"), className="selected-title"),
            html.Div(run.get("request", "-"), className="selected-sub"),
        ]),
        html.Div(className="selected-chips", children=[
            html.Span(run.get("cloud", "-")),
            html.Span(readiness.get("status", "UNKNOWN")),
            html.Span(f"readiness {readiness.get('score', 0)}/100"),
            html.Span(f"plan {report_id}"),
            html.Span(cost_value, className=f"chip-{cost_tone}"),
        ]),
        html.Div(cost_sub, className="selected-cost-note"),
    ])


def architecture_panel(row, report):
    """The architecture itself, front and center on the overview: the plan-derived
    dataflow diagram (six-layer model) inline, with a jump to the interactive viewer.
    Nothing is fabricated — no report yet means an honest empty state, not a mockup."""
    run_id = ((row or {}).get("run") or {}).get("run_id", "")
    children = []
    if report and report.get("path") and run_id:
        short = report.get("short", "")
        for fname in ("dataflow.svg", "architecture.svg"):
            if os.path.exists(os.path.join(report["path"], fname)):
                children = [
                    html.Iframe(src=f"/runs/{run_id}/reports/{short}/{fname}", className="arch-embed"),
                    html.Div(className="report-links", children=[
                        html.A("Open interactive viewer (click-to-code, pan/zoom)",
                               href=f"/deployment-reports/{short}/architecture",
                               target="_blank", className="report-link"),
                    ]),
                ]
                break
    if not children:
        children = [html.Div(className="empty sage", children=[
            html.Div("No architecture yet", className="empty-title"),
            html.Div('Create a run and generate a plan — the diagram is derived from the '
                     'plan, so it appears with the first report.', className="empty-sub"),
        ])]
    return panel("Architecture — data flow", "derived from the plan · six-layer analytics model",
                 html.Div(children=children))


def _chart_empty(title, sub):
    return html.Div(className="empty sage", children=[
        html.Div(title, className="empty-title"),
        html.Div(sub, className="empty-sub"),
    ])


def monthly_spend_panel(months, connected):
    """Chart 1 — monthly spend columns, led by the MTD stat line (Cost Explorer)."""
    if not connected:
        body = _chart_empty("Not connected to AWS", "Run aws configure to load Cost Explorer data.")
    elif months and any(m["total"] for m in months):
        latest = months[-1]
        spend_value = f"${latest['total']:,.2f}" if latest["total"] < 100 else f"${latest['total']:,.0f}"
        if len(months) >= 2:
            delta = months[-1]["total"] - months[-2]["total"]
            spend_sub = f"{'up' if delta >= 0 else 'down'} ${abs(delta):,.2f} vs prior month"
        else:
            spend_sub = "trailing month"
        body = html.Div(children=[
            html.Div(className="spend-line", children=[html.Strong(spend_value), html.Span(spend_sub)]),
            dcc.Graph(figure=trend_line(months), config={"displayModeBar": False}),
        ])
    else:
        body = _chart_empty("No recorded spend", "Cost Explorer reports no spend in the trailing months.")
    return panel("Monthly spend", "cost explorer — trailing months", body)


def spend_service_panel(latest_month, connected):
    """Chart 2 — where the latest month's spend went, top service emphasized."""
    by_service = (latest_month or {}).get("by_service") or {}
    if not connected:
        body = _chart_empty("Not connected to AWS", "Run aws configure to load Cost Explorer data.")
    elif by_service:
        body = dcc.Graph(figure=spend_bar(latest_month), config={"displayModeBar": False})
    else:
        body = _chart_empty("No per-service spend", "The latest month has no recorded spend by service.")
    return panel("Spend by service", "latest month, cost explorer", body)


def anomaly_panel(anoms, connected):
    """Chart 4 — the anomaly ledger (Cost Anomaly Detection)."""
    if not connected:
        body = _chart_empty("Not connected to AWS", "Run aws configure to load anomaly data.")
    else:
        body = ledger(anoms)
    return panel("Spend anomalies", "cost anomaly detection — account level", body)


def build_dynamic(d, selected_run_id=None):
    """Build the data-dependent part of the page from one assembled snapshot."""
    months = d["months"]
    rows = run_inventory()
    selected_row = _row_for_run(rows, selected_run_id)
    selected_report = _selected_report(selected_row)
    readiness = (selected_row or {}).get("readiness") or {}
    counts = (selected_report or {}).get("counts") or {}
    cost_value, cost_sub, cost_tone = _cost_status(selected_report)
    service_count = len(_service_counts(selected_report))
    anoms = d["anomalies"]

    banner = None
    if not d["connected"]:
        banner = html.Div(className="banner", children=[
            "Not connected to AWS. Run ", html.Code("aws configure"),
            " to load live cost data, then refresh.",
        ])

    conf = readiness.get("conformance") or {}
    conf_score = conf.get("score")
    changes = (f"+{counts.get('create', 0)} ~{counts.get('update', 0)} -{counts.get('delete', 0)}"
               if counts else "—")
    changes_sub = f"{service_count} service(s) in plan" if counts else "no report yet"

    # Overview = the pipeline, not the wallet: run readiness, conformance to the
    # reference architecture, what the plan changes, the diagram, and the cost GATE.
    # Account-wide spend keeps one compact evidence panel instead of a page of $0 charts.
    overview = html.Div(className="tabpane", children=[
        selected_run_banner(selected_row, selected_report),
        html.Div(className="kpis", children=[
            kpi("Readiness", f"{readiness.get('score', 0)}/100", readiness.get("status", "UNKNOWN"),
                "sage" if readiness.get("score", 0) >= 90 else "sand"),
            kpi("Conformance", f"{conf_score}/100" if conf_score is not None else "—",
                conf.get("status", "no plan analyzed"),
                "sage" if (conf_score or 0) >= 90 else "sand"),
            kpi("Plan changes", changes, changes_sub, "text"),
            kpi("Cost evidence", cost_value, cost_sub, cost_tone),
        ]),
        html.Div(className="grid", children=[
            html.Div(className="col-main", children=[
                monthly_spend_panel(months, d["connected"]),
                spend_service_panel(months[-1] if months else None, d["connected"]),
                conformance_panel(readiness),
            ]),
            html.Div(className="col-side", children=[
                panel("Plan composition", f"selected plan · {service_count or 0} service(s)",
                      dcc.Graph(figure=plan_action_donut(selected_report),
                                config={"displayModeBar": False})
                      if any((counts or {}).values()) else
                      _chart_empty("No plan yet", "Actions (+/~/-) appear once a report is generated.")),
                anomaly_panel(anoms, d["connected"]),
            ]),
        ]),
    ])

    def _tab(label, value, children):
        return dcc.Tab(label=label, value=value, className="main-tab",
                       selected_className="main-tab selected",
                       children=html.Div(className="tabpane", children=children)
                       if not isinstance(children, html.Div) else children)

    tabs = dcc.Tabs(value=os.environ.get("MINUS_DASH_DEFAULT_TAB", "overview"),
                    className="main-tabs", children=[
        _tab("Overview", "overview", overview),
        _tab("Control", "control", [control_plane_panel(selected_run_id)]),
        _tab("Optimization", "optimization", optimization_panels(selected_run_id)),
        _tab("Reports", "reports", [architecture_panel(selected_row, selected_report),
                                    deployment_reports_panel(selected_run_id)]),
        _tab("Readiness", "readiness", [readiness_panel(selected_run_id)]),
    ])
    return [banner, tabs]


def app_shell():
    """Static frame served immediately; the callback below fills in the live data."""
    return html.Div(className="page", children=[
        html.Header(className="masthead", children=[
            html.Div(className="brand", children=[
                html.Span(className="brand-mark"),
                html.Div(children=[
                    html.Div("MinusOps", className="brand-name"),
                    html.Div("governed data-pipeline console", className="brand-tag"),
                ]),
            ]),
            html.Div(className="masthead-right", children=[
                html.Div(className="run-picker", children=[
                    html.Span("pipeline", className="acct-label"),
                    dcc.Dropdown(id="global-run-select", options=[], clearable=False,
                                 placeholder="select run", className="global-run-select"),
                ]),
                html.Div(className="acct", children=[
                    html.Span("account", className="acct-label"),
                    html.Span("connecting…", id="acct-value", className="acct-value"),
                ]),
                html.Span("loading…", id="refresh-time", className="refresh-time"),
                html.Button("↻ Refresh", id="refresh-btn", n_clicks=0, className="refresh"),
            ]),
        ]),
        dcc.Loading(
            type="default", color=C["terracotta"], parent_className="content-wrap",
            children=html.Div(id="content", className="content"),
        ),
    ])


# ---------------------------------------------------------------------------
# App shell (fonts + global CSS)
# ---------------------------------------------------------------------------
app = dash.Dash(__name__, title="MinusOps Console", suppress_callback_exceptions=True)
app.layout = app_shell


def _dashboard_token():
    return os.environ.get("MINUS_DASH_TOKEN") or os.environ.get("DASH_TOKEN")


def _is_loopback_host(host):
    host = (host or "").strip().lower()
    return host in {"", "localhost", "127.0.0.1", "::1"} or host.startswith("127.")


def _remote_bind_requires_token(host):
    return not _is_loopback_host(host) and not _dashboard_token()


def _valid_dashboard_token(value):
    token = _dashboard_token()
    return bool(token and value and hmac.compare_digest(str(value), str(token)))


def _request_authorized():
    token = _dashboard_token()
    if not token:
        return True
    from flask import request
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer ") and _valid_dashboard_token(auth[7:].strip()):
        return True
    if _valid_dashboard_token(request.args.get("token")):
        return True
    if _valid_dashboard_token(request.cookies.get("minus_dash_token")):
        return True
    return False


@app.server.before_request
def _enforce_dashboard_auth():
    if _request_authorized():
        return None
    from flask import Response
    return Response(
        "dashboard authentication required",
        401,
        {"WWW-Authenticate": 'Bearer realm="minusops-dashboard"'},
    )


@app.server.after_request
def _persist_dashboard_token(response):
    from flask import request
    supplied = request.args.get("token")
    if _valid_dashboard_token(supplied):
        response.set_cookie(
            "minus_dash_token",
            supplied,
            httponly=True,
            secure=request.is_secure,
            samesite="Strict",
        )
    return response


@app.server.route("/deployment-reports/<report_id>/<path:filename>")
def _serve_deployment_report(report_id, filename):
    from flask import abort, send_from_directory

    safe_id = report_id.replace("-", "").replace("_", "")
    if not safe_id.isalnum():
        abort(404)
    for root in report_roots():
        report_dir = os.path.abspath(os.path.join(root, report_id))
        root_abs = os.path.abspath(root)
        target = os.path.abspath(os.path.join(report_dir, filename))
        if (
            report_dir.startswith(root_abs)
            and target.startswith(report_dir)
            and os.path.exists(target)
            and os.path.isfile(target)
        ):
            return send_from_directory(report_dir, filename, as_attachment=False)
    abort(404)


_ARCH_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Architecture __TITLE__</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:#14110f;color:#fbf7f4;font-family:Inter,system-ui,sans-serif}
.wrap{display:flex;height:100vh}
.canvas{flex:1;position:relative;overflow:hidden;background:#14110f}
.canvas-inner{position:absolute;top:0;left:0;transform-origin:0 0;cursor:grab}
.canvas-inner.dragging{cursor:grabbing}
.canvas-inner svg{display:block;width:1280px;height:auto}
.zoom-controls{position:absolute;top:16px;right:16px;z-index:5;display:flex;flex-direction:column;gap:6px}
.zoom-controls button{width:32px;height:32px;border-radius:8px;border:1px solid rgba(217,93,57,.28);
 background:#1c1714;color:#fbf7f4;font:600 16px 'JetBrains Mono',monospace;cursor:pointer;line-height:1}
.zoom-controls button:hover{border-color:#d95d39;background:rgba(217,93,57,.14)}
.zoom-pct{font:500 10px 'JetBrains Mono',monospace;color:#b09c93;text-align:center;padding-top:2px}
.view-toggle{position:absolute;bottom:16px;left:16px;z-index:5;display:flex;gap:6px}
.view-toggle button{height:32px;padding:0 14px;border-radius:8px;border:1px solid rgba(217,93,57,.28);
 background:#1c1714;color:#b09c93;font:600 12px 'Outfit',sans-serif;cursor:pointer}
.view-toggle button.active{border-color:#d95d39;background:rgba(217,93,57,.14);color:#fbf7f4}
.panel{width:440px;flex:none;border-left:1px solid rgba(217,93,57,.18);background:#1c1714;padding:18px 20px;
 overflow:auto;scrollbar-width:none;-ms-overflow-style:none}
.panel::-webkit-scrollbar{display:none}
.panel h2{font-size:15px;margin:0 0 4px;font-family:'Outfit',sans-serif}
.hint{color:#b09c93;font-size:13px;line-height:1.5}
.addr{font-family:'JetBrains Mono',monospace;font-size:12px;color:#d4a373;word-break:break-all;margin-top:4px}
.badges{margin:12px 0}
.badge{display:inline-block;font:600 10px Inter,sans-serif;padding:2px 8px;border-radius:8px;margin:2px 3px 2px 0;color:#14110f}
.file{color:#8da189;font-family:'JetBrains Mono',monospace;font-size:11px;margin:12px 0 5px}
pre{background:#14110f;border:1px solid rgba(217,93,57,.18);border-radius:8px;padding:12px;overflow:auto;
 font-family:'JetBrains Mono',Consolas,monospace;font-size:11.5px;line-height:1.55;white-space:pre;color:#e8e2dc;
 scrollbar-width:none;-ms-overflow-style:none}
pre::-webkit-scrollbar{display:none}
.tc{color:#8a7f78;font-style:italic}.ts{color:#d4a373}.tk{color:#e8825f}.tn{color:#cb9a3e}.tb{color:#8da189}
.node{cursor:pointer}.node:hover .card{stroke-width:2.6}
</style></head><body>
<div class="wrap">
 <div class="canvas" id="canvas">
  <div class="canvas-inner" id="canvasInner">__VIEWS__</div>
  <div class="view-toggle" id="viewToggle">__TOGGLE__</div>
  <div class="zoom-controls">
   <button id="zoomIn" title="Zoom in">+</button>
   <button id="zoomReset" title="Fit to screen">⤢</button>
   <button id="zoomOut" title="Zoom out">−</button>
   <div class="zoom-pct" id="zoomPct">100%</div>
  </div>
 </div>
 <div class="panel" id="panel">
  <h2>Service inspector</h2>
  <div class="hint">Click any service box in the diagram to see the exact Terraform that provisions it, plus its security/cost findings. Scroll to zoom, drag to pan — useful once an architecture has many components.</div>
 </div>
</div>
<script>
const DATA = __DATA__;
const SEV = {HIGH:'#d95d39',MEDIUM:'#cb9a3e',LOW:'#8da189',EXTERNAL:'#b09c93'};
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function hcl(code){
 return esc(code).replace(
  /(#[^\\n]*|\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/)|("(?:[^"\\\\]|\\\\.)*")|\\b(resource|data|variable|output|module|locals|provider|terraform|for|in|if|else|dynamic|jsonencode|var|local|each|true|false|null)\\b|\\b(\\d+(?:\\.\\d+)?)\\b/g,
  function(m,c,s,k,n){
   if(c) return '<span class="tc">'+c+'</span>';
   if(s) return '<span class="ts">'+s+'</span>';
   if(k) return (k==='true'||k==='false'||k==='null')?'<span class="tb">'+k+'</span>':'<span class="tk">'+k+'</span>';
   if(n) return '<span class="tn">'+n+'</span>';
   return m;
  });
}
function show(addr){
 const file = DATA.addrFile[addr] || 'main.tf';
 const type = DATA.addrType[addr] || 'Service';
 const fnds = DATA.addrFindings[addr] || [];
 const code = DATA.sources[file] || 'Source not captured for this resource.';
 let badges = fnds.map(f=>'<span class="badge" style="background:'+(SEV[f.severity]||'#b09c93')+'">'+esc(f.id)+'</span>').join('');
 if(!badges) badges = '<span class="hint">No findings · passes scan</span>';
 document.getElementById('panel').innerHTML =
  '<h2>'+esc(type)+'</h2><div class="addr">'+esc(addr)+'</div>'+
  '<div class="badges">'+badges+'</div>'+
  '<div class="file">'+esc(file)+'</div><pre>'+hcl(code)+'</pre>';
}
document.querySelectorAll('.node').forEach(function(n){
 n.addEventListener('click',function(){
  document.querySelectorAll('.node .card').forEach(function(c){c.setAttribute('stroke-width','1.6')});
  const card=n.querySelector('.card'); if(card) card.setAttribute('stroke-width','3');
  show(n.getAttribute('data-address'));
 });
});

// Pan + zoom — large diagrams (many resources) get a tall canvas; this keeps it navigable
// instead of shrinking cards or clipping content.
(function(){
 const canvas = document.getElementById('canvas');
 const inner = document.getElementById('canvasInner');
 const pctLabel = document.getElementById('zoomPct');
 let scale = 1, tx = 20, ty = 20, dragging = false, moved = false, lastX = 0, lastY = 0;

 function apply(){
  inner.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
  pctLabel.textContent = Math.round(scale * 100) + '%';
 }
 function clampScale(s){ return Math.min(3, Math.max(0.15, s)); }
 function visibleSvg(){
  const views = inner.querySelectorAll('.diagram-view');
  for(const v of views){ if(v.style.display !== 'none'){ return v.querySelector('svg'); } }
  return inner.querySelector('svg');
 }
 function fitToScreen(){
  const svg = visibleSvg();
  if(!svg){ return; }
  const vb = svg.viewBox.baseVal;
  const w = vb && vb.width ? vb.width : 1280;
  const h = vb && vb.height ? vb.height : 760;
  const availW = canvas.clientWidth - 40;
  const availH = canvas.clientHeight - 40;
  scale = clampScale(Math.min(availW / w, availH / h, 1));
  tx = 20; ty = 20;
  apply();
 }
 window.addEventListener('resize', fitToScreen);
 fitToScreen();

 canvas.addEventListener('wheel', function(e){
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
  const prev = scale;
  scale = clampScale(scale * (e.deltaY < 0 ? 1.12 : 0.89));
  tx = cx - (cx - tx) * (scale / prev);
  ty = cy - (cy - ty) * (scale / prev);
  apply();
 }, {passive: false});

 canvas.addEventListener('mousedown', function(e){
  dragging = true; moved = false; lastX = e.clientX; lastY = e.clientY;
  inner.classList.add('dragging');
 });
 window.addEventListener('mousemove', function(e){
  if(!dragging){ return; }
  const dx = e.clientX - lastX, dy = e.clientY - lastY;
  if(Math.abs(dx) > 3 || Math.abs(dy) > 3){ moved = true; }
  tx += dx; ty += dy; lastX = e.clientX; lastY = e.clientY;
  apply();
 });
 window.addEventListener('mouseup', function(){
  dragging = false; inner.classList.remove('dragging');
 });

 document.getElementById('zoomIn').addEventListener('click', function(){ scale = clampScale(scale * 1.2); apply(); });
 document.getElementById('zoomOut').addEventListener('click', function(){ scale = clampScale(scale * 0.8); apply(); });
 document.getElementById('zoomReset').addEventListener('click', fitToScreen);

 // Topology / Data flow toggle (buttons exist only when dataflow.svg was generated).
 const toggles = document.querySelectorAll('#viewToggle button');
 const views = inner.querySelectorAll('.diagram-view');
 toggles.forEach(function(btn, i){
  btn.addEventListener('click', function(){
   toggles.forEach(function(b){ b.classList.remove('active'); });
   btn.classList.add('active');
   views.forEach(function(v, j){ v.style.display = (i === j) ? 'block' : 'none'; });
   fitToScreen();
  });
 });
})();
</script></body></html>"""


@app.server.route("/deployment-reports/<report_id>/architecture")
def _serve_architecture_page(report_id):
    from flask import abort, Response

    safe_id = report_id.replace("-", "").replace("_", "")
    if not safe_id.isalnum():
        abort(404)
    try:
        report_dir, manifest, plan = plan_inspector.load_report(report_id)
    except Exception:
        abort(404)
    svg_path = report_dir / "architecture.svg"
    if not svg_path.exists():
        abort(404)
    svg = svg_path.read_text(encoding="utf-8")
    df_path = report_dir / "dataflow.svg"
    df_svg = df_path.read_text(encoding="utf-8") if df_path.exists() else None
    if df_svg:
        views = (f'<div class="diagram-view">{df_svg}</div>'
                 f'<div class="diagram-view" style="display:none">{svg}</div>')
        toggle = ('<button class="active">Data flow</button>'
                  '<button>Topology</button>')
    else:
        views = f'<div class="diagram-view">{svg}</div>'
        toggle = ""

    # Embed the plan-bound source + per-resource file/type/findings for click-to-code.
    sources = {}
    snapshot = report_dir / "source_snapshot"
    if snapshot.exists():
        for f in snapshot.rglob("*"):
            if f.is_file() and f.suffix in (".tf", ".tfvars"):
                rel = f.relative_to(snapshot).as_posix()
                sources[rel] = f.read_text(encoding="utf-8", errors="replace")
    addr_file, addr_type = {}, {}
    for ch in plan.get("resource_changes", []):
        addr, rtype = ch.get("address"), ch.get("type", "")
        if addr:
            addr_file[addr] = plan_inspector.owner_file_for_address(addr, rtype)
            addr_type[addr] = rtype
    addr_findings = {}
    try:
        import optimize_analyzer
        fmap = {}
        for fnd in optimize_analyzer.scan_hcl_files(str(snapshot)) if snapshot.exists() else []:
            if fnd.get("resource"):
                fmap.setdefault(fnd["resource"], []).append({"id": fnd["id"], "severity": fnd["severity"]})
        for addr in addr_type:
            base = addr.split("[")[0]
            if base in fmap:
                addr_findings[addr] = fmap[base]
    except Exception:
        pass

    data = {"sources": sources, "addrFile": addr_file, "addrType": addr_type, "addrFindings": addr_findings}
    page = (_ARCH_PAGE
            .replace("__TITLE__", html_lib.escape(report_id))
            .replace("__VIEWS__", views)
            .replace("__TOGGLE__", toggle)
            .replace("__DATA__", json.dumps(data).replace("</", "<\\/")))
    return Response(page, mimetype="text/html")


@app.server.route("/deployment-reports/<report_id>/diff")
def _serve_report_diff(report_id):
    from flask import abort, Response

    safe_id = report_id.replace("-", "").replace("_", "")
    if not safe_id.isalnum():
        abort(404)
    try:
        status = plan_inspector.source_status(report_id)
        diff_lines = plan_inspector.diff_source(report_id)
    except Exception:
        abort(404)
    body = "\n".join(diff_lines)
    if status.get("reason") and body.strip() in ("", "source snapshot unavailable"):
        body = status.get("reason")
    return Response(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Source Diff {report_id}</title>
<style>
body{{background:#14110f;color:#fbf7f4;font-family:Inter,system-ui,sans-serif;margin:0;padding:28px}}
h1{{font-size:24px;margin:0 0 8px}}.sub{{color:#b09c93;font-family:Consolas,monospace;margin-bottom:18px}}
.badge{{display:inline-block;border:1px solid rgba(217,93,57,.28);border-radius:8px;padding:6px 10px;margin-bottom:18px}}
pre{{background:#1c1714;border:1px solid rgba(217,93,57,.18);border-radius:10px;padding:16px;overflow:auto;white-space:pre-wrap;line-height:1.45}}
</style></head><body>
<h1>Source Diff</h1>
<div class="sub">plan {report_id}</div>
<div class="badge">status: {status.get('status', 'UNKNOWN')}</div>
<pre>{html_lib.escape(body)}</pre>
</body></html>""", mimetype="text/html")


def _table_page(title, report_id, headers, rows):
    from flask import Response

    head = "".join(f"<th>{html_lib.escape(str(h))}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html_lib.escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    if not rows:
        body = f"<tr><td colspan=\"{len(headers)}\">No data</td></tr>"
    return Response(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html_lib.escape(title)} {report_id}</title>
<style>
body{{background:#14110f;color:#fbf7f4;font-family:Inter,system-ui,sans-serif;margin:0;padding:28px}}
h1{{font-size:24px;margin:0 0 8px}}.sub{{color:#b09c93;font-family:Consolas,monospace;margin-bottom:18px}}
table{{width:100%;border-collapse:collapse;background:#1c1714;border:1px solid rgba(217,93,57,.18);border-radius:10px;overflow:hidden}}
th,td{{text-align:left;border-bottom:1px solid rgba(255,255,255,.07);padding:9px 10px;font-size:13px;vertical-align:top}}
th{{color:#b09c93;text-transform:uppercase;font-size:11px;letter-spacing:.08em}}
td{{font-family:Consolas,monospace}}
</style></head><body>
<h1>{html_lib.escape(title)}</h1>
<div class="sub">plan {html_lib.escape(report_id)}</div>
<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
</body></html>""", mimetype="text/html")


@app.server.route("/deployment-reports/<report_id>/services")
def _serve_report_services(report_id):
    from flask import abort
    try:
        _, _, plan = plan_inspector.load_report(report_id)
        data = plan_inspector.services(plan)
        rows = [(svc, len(items), ", ".join(r["address"] for r in items)) for svc, items in data.items()]
    except Exception:
        abort(404)
    return _table_page("Services", report_id, ["Service", "Count", "Resources"], rows)


@app.server.route("/deployment-reports/<report_id>/resources")
def _serve_report_resources(report_id):
    from flask import abort
    try:
        _, _, plan = plan_inspector.load_report(report_id)
        rows = [
            (r["address"], r["type"], r["action"], plan_inspector.service_for_type(r["type"]), r["owner_file"])
            for r in plan_inspector.resource_rows(plan)
        ]
    except Exception:
        abort(404)
    return _table_page("Resources", report_id, ["Address", "Type", "Action", "Service", "File"], rows)


@app.server.route("/deployment-reports/<report_id>/roles")
def _serve_report_roles(report_id):
    from flask import abort
    try:
        _, _, plan = plan_inspector.load_report(report_id)
        data = plan_inspector.iam_roles(plan)
        rows = [(r["address"], r["name"], ", ".join(r["policy_attachments"])) for r in data["roles"]]
        rows.extend((p["address"], p["name"], "policy") for p in data["policies"])
    except Exception:
        abort(404)
    return _table_page("IAM Roles and Policies", report_id, ["Address", "Name", "Attachments"], rows)


@app.server.route("/deployment-reports/<report_id>/files")
def _serve_report_files(report_id):
    from flask import abort
    try:
        report_dir, manifest, _ = plan_inspector.load_report(report_id)
        rows = [
            (item.name, item.stat().st_size)
            for item in sorted(report_dir.iterdir())
            if item.name != "source_snapshot"
        ]
        rows.insert(0, ("Terraform directory", manifest.get("dir", "-")))
    except Exception:
        abort(404)
    return _table_page("Report Files", report_id, ["File", "Bytes"], rows)


@app.server.route("/runs/<run_id>/<filename>")
def _serve_run_file(run_id, filename):
    from flask import abort, send_file
    if filename not in {"enterprise-package.md", "enterprise-package.json", "requirements.json", "architecture_decision.json"}:
        abort(404)
    try:
        run = None
        for item in run_store.list_runs():
            if item.get("run_id") == run_id or item.get("run_id", "").startswith(run_id):
                run = item
                break
        if not run:
            abort(404)
        path = os.path.join(run["root"], filename)
        root = os.path.abspath(run["root"])
        resolved = os.path.abspath(path)
        if not resolved.startswith(root + os.sep) or not os.path.exists(resolved):
            abort(404)
        return send_file(resolved)
    except Exception:
        abort(404)


@app.server.route("/runs/<run_id>/reports/<report_id>/<filename>")
def _serve_run_report_file(run_id, report_id, filename):
    from flask import abort, send_file
    allowed = {
        "architecture.svg", "dataflow.svg", "report.html", "plan.html", "cost.html",
        "plan.pdf", "cost.pdf", "plan.json", "cost.json",
        "bcm-assumptions.json", "bcm-create-workload-estimate.json", "bcm-usage.json", "bcm-commands.json",
    }
    if filename not in allowed:
        abort(404)
    try:
        run = None
        for item in run_store.list_runs():
            if item.get("run_id") == run_id or item.get("run_id", "").startswith(run_id):
                run = item
                break
        if not run:
            abort(404)
        root = os.path.abspath(os.path.join(run["reports_dir"], report_id))
        resolved = os.path.abspath(os.path.join(root, filename))
        if not resolved.startswith(root + os.sep) or not os.path.exists(resolved):
            abort(404)
        return send_file(resolved)
    except Exception:
        abort(404)


@app.callback(
    Output("content", "children"),
    Output("acct-value", "children"),
    Output("refresh-time", "children"),
    Input("refresh-btn", "n_clicks"),
    Input("global-run-select", "value"),
)
def _render(_n_clicks, selected_run_id):
    # The Refresh button forces a fresh fetch; initial page load uses the cache if warm.
    force = ctx.triggered_id == "refresh-btn"
    d = assemble(force=force)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M UTC")
    return build_dynamic(d, selected_run_id), _redact_account(d["account"]), f"Refreshed {now}"


@app.callback(
    Output("global-run-select", "options"),
    Output("global-run-select", "value"),
    Input("refresh-btn", "n_clicks"),
)
def _run_selector(_n_clicks):
    rows = run_inventory()
    options = [
        {"label": row["run"].get("run_id", "run"), "value": row["run"].get("run_id")}
        for row in rows
    ]
    return options, (options[0]["value"] if options else None)


@app.callback(
    Output("control-action-status", "children"),
    Input("control-accelerator-btn", "n_clicks"),
    Input("control-save-decision-btn", "n_clicks"),
    State("control-run-select", "value"),
    State("control-architecture", "value"),
    State("control-summary", "value"),
    State("control-modules", "value"),
    State("control-sources", "value"),
    State("control-assumptions", "value"),
    State("control-risks", "value"),
    State("control-alternatives", "value"),
    State("control-force", "value"),
    prevent_initial_call=True,
)
def _control_action(_accelerator_clicks, _save_clicks, run_id, architecture, summary, modules_text,
                    sources_text, assumptions_text, risks_text, alternatives_text, force_values):
    run = _find_dashboard_run(run_id)
    if not run:
        return html.Div("Run not found.", className="status-bad")
    try:
        if ctx.triggered_id == "control-accelerator-btn":
            result = accelerators.write_lakehouse(run, force="force" in (force_values or []))
            return html.Div(className="status-good", children=[
                html.Strong("Lakehouse starter written."),
                html.Code(result["next"], className="command-line"),
            ])
        result = write_control_decision(
            run,
            selected_architecture=architecture or "",
            decision_summary=summary or "",
            modules_text=modules_text or "",
            sources_text=sources_text or "",
            assumptions_text=assumptions_text or "",
            risks_text=risks_text or "",
            alternatives_text=alternatives_text or "",
        )
    except Exception as exc:
        return html.Div(str(exc), className="status-bad")
    if result["ok"]:
        return html.Div(f"Decision complete: {result['path']}", className="status-good")
    return html.Div(className="status-warn", children=[
        html.Strong("Decision saved but incomplete."),
        html.Span(", ".join(result["missing"])),
    ])


@app.server.before_request
def _silence_internal_poll():
    """A stale renderer tab may poll the internal 'config-version' store, which has
    no callback here. Answer it quietly (204) instead of logging a 500 every few seconds."""
    from flask import request
    if request.path == "/_dash-update-component":
        body = request.get_json(silent=True) or {}
        if "config-version" in str(body.get("output", "")):
            return "", 204

app.index_string = """<!DOCTYPE html>
<html>
<head>
  {%metas%}<title>{%title%}</title>{%favicon%}{%css%}
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root{
      --bg:#14110f; --panel:rgba(40,33,30,.62); --line:rgba(217,93,57,.16);
      --terra:#d95d39; --sand:#d4a373; --sage:#8da189; --text:#fbf7f4; --muted:#b09c93; --faint:#6f635c;
    }
    *{box-sizing:border-box;margin:0;padding:0}
    html{scroll-behavior:smooth}
    body{
      background:var(--bg); color:var(--text); font-family:'Inter',sans-serif;
      background-image:
        radial-gradient(900px 500px at 12% -5%, rgba(217,93,57,.10), transparent 60%),
        radial-gradient(700px 500px at 100% 0%, rgba(212,163,115,.06), transparent 55%);
      height:100vh; overflow:hidden; -webkit-font-smoothing:antialiased;
    }
    .page{max-width:1320px;margin:0 auto;padding:1.5rem 2rem 0;height:100vh;
      display:flex;flex-direction:column}

    /* Masthead (fixed; never scrolls) */
    .masthead{display:flex;justify-content:space-between;align-items:center;flex:0 0 auto;
      padding-bottom:.9rem;border-bottom:1px solid var(--line);margin-bottom:.4rem}
    .refresh-time{font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--faint)}

    /* Top section tabs + fixed-height scroll panes (one screen per section) */
    .content-wrap{flex:1 1 auto;min-height:0}
    .main-tabs{flex:0 0 auto}
    .main-tabs .tab-container{display:flex;gap:.35rem;border-bottom:1px solid var(--line)!important;
      border-radius:0!important}
    .main-tab{font-family:'Outfit',sans-serif!important;font-weight:600!important;font-size:.92rem!important;
      color:var(--muted)!important;background:transparent!important;border:0!important;
      border-bottom:2px solid transparent!important;padding:.55rem 1.05rem!important;
      border-radius:8px 8px 0 0!important;cursor:pointer}
    .main-tab.selected{color:var(--text)!important;border-bottom:2px solid var(--terra)!important;
      background:rgba(217,93,57,.07)!important}
    .tabpane{height:calc(100vh - 150px);overflow-y:auto;overflow-x:hidden;
      padding:1.1rem .2rem 1.6rem;scrollbar-width:none;-ms-overflow-style:none}
    .tabpane::-webkit-scrollbar{display:none;width:0;height:0}
    .brand{display:flex;align-items:center;gap:.95rem}
    .brand-mark{width:34px;height:34px;border-radius:11px;
      background:conic-gradient(from 140deg, var(--terra), var(--sand), var(--terra));
      box-shadow:0 0 0 1px rgba(217,93,57,.35), 0 6px 22px rgba(217,93,57,.30)}
    .brand-name{font-family:'Outfit',sans-serif;font-weight:700;font-size:1.22rem;letter-spacing:-.01em}
    .brand-tag{font-size:.74rem;color:var(--muted);letter-spacing:.14em;text-transform:uppercase;margin-top:.12rem}
    .masthead-right{display:flex;align-items:center;gap:1.5rem}
    .run-picker{min-width:310px;display:flex;flex-direction:column;gap:.18rem}
    .global-run-select .Select-control{background:#14110f!important;border:1px solid var(--line)!important;
      border-radius:8px!important;min-height:34px!important}
    .global-run-select .Select-value,.global-run-select .Select-placeholder{line-height:32px!important}
    .global-run-select .Select-value-label,.global-run-select .Select-placeholder{color:var(--text)!important;
      font-family:'JetBrains Mono',monospace!important;font-size:.74rem!important}
    .global-run-select .Select-menu-outer{background:#1c1714!important;border:1px solid var(--line)!important;
      color:var(--text)!important}
    .acct{display:flex;flex-direction:column;align-items:flex-end;gap:.15rem}
    .acct-label{font-size:.64rem;letter-spacing:.18em;text-transform:uppercase;color:var(--faint)}
    .acct-value{font-family:'JetBrains Mono',monospace;font-size:.86rem;color:var(--sand)}
    .refresh{font-family:'JetBrains Mono',monospace;font-size:.82rem;color:var(--text);
      text-decoration:none;border:1px solid var(--line);padding:.5rem .9rem;border-radius:9px;
      background:rgba(217,93,57,.06);transition:all .18s ease}
    .refresh:hover{border-color:var(--terra);background:rgba(217,93,57,.14)}
    .refresh:focus-visible{outline:2px solid var(--terra);outline-offset:2px}

    .banner{background:rgba(217,93,57,.08);border:1px solid var(--line);border-left:3px solid var(--terra);
      border-radius:10px;padding:.85rem 1.1rem;margin-bottom:1.6rem;color:var(--muted);font-size:.9rem}
    .banner code{font-family:'JetBrains Mono',monospace;color:var(--sand);background:rgba(0,0,0,.25);
      padding:.1rem .4rem;border-radius:5px}
    .selected-run-banner{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:.6rem 1rem;
      align-items:center;border:1px solid var(--line);border-left:3px solid var(--sage);border-radius:10px;
      padding:.85rem 1rem;margin-bottom:1rem;background:rgba(141,161,137,.07)}
    .selected-main{min-width:0}
    .selected-title{font-family:'JetBrains Mono',monospace;font-size:.88rem;color:var(--text);overflow-wrap:anywhere}
    .selected-sub{font-size:.8rem;color:var(--muted);margin-top:.22rem;overflow-wrap:anywhere}
    .selected-chips{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:.4rem}
    .selected-chips span{font-family:'JetBrains Mono',monospace;font-size:.7rem;border:1px solid rgba(255,255,255,.09);
      border-radius:7px;padding:.22rem .42rem;color:var(--muted);background:rgba(0,0,0,.18)}
    .selected-chips .chip-sage{color:var(--sage);border-color:rgba(141,161,137,.4)}
    .selected-chips .chip-sand{color:var(--sand);border-color:rgba(212,163,115,.45)}
    .selected-chips .chip-terracotta{color:var(--terra);border-color:rgba(217,93,57,.5)}
    .selected-cost-note{grid-column:1/-1;color:var(--faint);font-size:.76rem;font-family:'JetBrains Mono',monospace}

    /* KPI strip */
    .arch-embed{width:100%;height:480px;border:0;border-radius:12px;background:var(--bg);display:block}
    .spend-line{display:flex;align-items:baseline;gap:.55rem;font-family:var(--mono,monospace);
      font-size:.86rem;color:var(--muted);margin-bottom:.7rem;flex-wrap:wrap}
    .spend-line strong{font-size:1.25rem;color:var(--text)}
    .kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:1.1rem;margin-bottom:1.6rem}
    .kpi{background:var(--panel);border:1px solid var(--line);border-radius:15px;padding:1.3rem 1.4rem;
      backdrop-filter:blur(10px)}
    .kpi-label{font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;color:var(--muted)}
    .kpi-value{font-family:'JetBrains Mono',monospace;font-weight:500;font-size:1.85rem;
      letter-spacing:-.02em;margin:.55rem 0 .25rem}
    .kpi-sub{font-size:.78rem;color:var(--faint)}

    /* Grid */
    .grid{display:grid;grid-template-columns:1.55fr 1fr;gap:1.1rem;align-items:start}
    .col-main{display:flex;flex-direction:column;gap:1.1rem}
    .col-side{display:flex;flex-direction:column;gap:1.1rem}
    .panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;
      padding:1.35rem 1.45rem;backdrop-filter:blur(10px)}
    .panel-head{margin-bottom:.9rem}
    .eyebrow{font-size:.66rem;letter-spacing:.2em;text-transform:uppercase;color:var(--terra)}
    .panel-title{font-family:'Outfit',sans-serif;font-weight:600;font-size:1.12rem;
      letter-spacing:-.01em;margin-top:.2rem}

    /* Anomaly ledger — the signature */
    .ledger{display:flex;flex-direction:column;gap:.7rem}
    .ledger-entry{background:rgba(0,0,0,.18);border:1px solid var(--line);border-left:3px solid var(--terra);
      border-radius:0 11px 11px 0;padding:.85rem 1rem}
    .ledger-top{display:flex;justify-content:space-between;align-items:baseline;gap:.5rem}
    .ledger-service{font-family:'Outfit',sans-serif;font-weight:600;font-size:1rem}
    .ledger-impact{font-family:'JetBrains Mono',monospace;font-size:1.02rem;font-weight:500}
    .ledger-meta{display:flex;align-items:center;gap:.5rem;margin-top:.3rem;
      font-family:'JetBrains Mono',monospace;font-size:.76rem;color:var(--muted)}
    .ledger-meta .dot{color:var(--faint)}
    .ledger-owner{margin-top:.5rem;font-size:.8rem;color:var(--sand);
      background:rgba(212,163,115,.08);padding:.32rem .55rem;border-radius:6px;display:inline-block}

    /* Optimization findings */
    .findings{display:flex;flex-direction:column;gap:.55rem}
    .finding{background:rgba(0,0,0,.18);border:1px solid var(--line);border-left:3px solid var(--terra);
      border-radius:0 10px 10px 0;padding:.7rem .9rem}
    .finding-top{display:flex;justify-content:space-between;align-items:baseline;gap:.5rem}
    .finding-id{font-family:'JetBrains Mono',monospace;font-size:.74rem;color:var(--sand)}
    .finding-sev{font-family:'JetBrains Mono',monospace;font-size:.7rem;font-weight:600;text-transform:uppercase}
    .finding-title{font-family:'Outfit',sans-serif;font-weight:600;font-size:.95rem;margin-top:.25rem}
    .finding-desc{font-size:.8rem;color:var(--muted);margin-top:.2rem;line-height:1.4}

    .empty{text-align:center;padding:2.4rem 1rem;border:1px dashed var(--line);border-radius:12px}
    .empty.sage{border-color:rgba(141,161,137,.3)}
    .empty-title{font-family:'Outfit',sans-serif;font-weight:600;font-size:1.05rem;color:var(--sage)}
    .empty-sub{font-size:.84rem;color:var(--muted);margin-top:.3rem}

    .reports{display:flex;flex-direction:column;gap:.75rem}
    .latest-report{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:.85rem 1rem;
      align-items:start;border:1px solid rgba(141,161,137,.26);border-radius:10px;
      padding:1rem;background:linear-gradient(135deg,rgba(141,161,137,.13),rgba(217,93,57,.07))}
    .latest-title{font-family:'Outfit',sans-serif;font-weight:650;font-size:1.06rem;overflow-wrap:anywhere}
    .latest-meta{display:flex;flex-wrap:wrap;gap:.42rem .75rem;margin-top:.35rem;
      color:var(--muted);font-family:'JetBrains Mono',monospace;font-size:.72rem}
    .latest-counts{display:flex;gap:.45rem;font-family:'JetBrains Mono',monospace;font-size:.82rem;color:var(--sage)}
    .latest-counts span{border:1px solid rgba(141,161,137,.32);border-radius:6px;
      padding:.18rem .42rem;background:rgba(141,161,137,.08)}
    .report-card{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:.85rem 1rem;
      align-items:start;background:rgba(0,0,0,.18);border:1px solid var(--line);
      border-radius:10px;padding:.95rem 1rem}
    .report-main{min-width:0}
    .report-title{font-family:'Outfit',sans-serif;font-weight:600;font-size:1rem;overflow-wrap:anywhere}
    .report-meta{display:flex;flex-wrap:wrap;gap:.42rem .7rem;margin-top:.35rem;
      font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--muted)}
    .report-status{border:1px solid var(--line);border-radius:6px;padding:.08rem .34rem;text-transform:uppercase}
    .report-status.current{color:var(--sage);border-color:rgba(141,161,137,.35)}
    .report-status.stale{color:var(--sand);border-color:rgba(212,163,115,.45)}
    .report-counts{display:flex;gap:.45rem;font-family:'JetBrains Mono',monospace;font-size:.82rem;color:var(--sand)}
    .report-counts span{border:1px solid var(--line);border-radius:6px;padding:.18rem .4rem;background:rgba(212,163,115,.06)}
    .report-links{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:.48rem}
    .report-link{font-family:'JetBrains Mono',monospace;font-size:.76rem;color:var(--text);
      text-decoration:none;border:1px solid var(--line);border-radius:7px;padding:.38rem .55rem;
      background:rgba(217,93,57,.06)}
    .report-link:hover{border-color:var(--terra);background:rgba(217,93,57,.14)}
    .report-link.disabled{pointer-events:none;color:var(--faint);background:rgba(255,255,255,.03)}
    .report-missing{font-size:.82rem;color:var(--faint)}
    .runs{display:flex;flex-direction:column;gap:.75rem}
    .run-tabs{display:flex;flex-direction:column;gap:.75rem}
    .run-tabs .tab-container{display:flex;flex-wrap:wrap;gap:.45rem;border:0!important}
    .run-tab{font-family:'JetBrains Mono',monospace!important;font-size:.72rem!important;
      color:var(--muted)!important;background:rgba(0,0,0,.18)!important;
      border:1px solid var(--line)!important;border-radius:7px!important;
      padding:.42rem .58rem!important;line-height:1.2!important}
    .run-tab.selected{color:var(--text)!important;border-color:rgba(217,93,57,.45)!important;
      background:rgba(217,93,57,.12)!important}
    .run-card{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:.8rem 1rem;
      align-items:start;background:rgba(0,0,0,.18);border:1px solid var(--line);
      border-radius:10px;padding:.95rem 1rem}
    .run-card.ready{border-color:rgba(141,161,137,.35)}
    .run-card.evidence{border-color:rgba(212,163,115,.35)}
    .run-card.blocked{border-color:rgba(217,93,57,.42)}
    .run-main{min-width:0}
    .run-title{font-family:'JetBrains Mono',monospace;font-weight:650;font-size:.86rem;overflow-wrap:anywhere}
    .run-meta{display:flex;flex-wrap:wrap;gap:.42rem .7rem;margin-top:.35rem;
      font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--muted)}
    .readiness-score{display:flex;gap:.45rem;align-items:baseline;font-family:'JetBrains Mono',monospace}
    .readiness-score span{font-size:.72rem;color:var(--muted);text-transform:uppercase}
    .readiness-score strong{font-size:1rem;color:var(--text)}
    .readiness-issue{grid-column:1/-1;display:flex;flex-direction:column;gap:.2rem;
      padding:.65rem .75rem;border:1px solid rgba(255,255,255,.07);border-radius:8px;
      background:rgba(255,255,255,.025)}
    .readiness-issue span{font-weight:600;color:var(--text)}
    .readiness-issue small{color:var(--muted);line-height:1.45}
    .run-history-note{font-size:.78rem;color:var(--faint);font-family:'JetBrains Mono',monospace;
      padding:.25rem .1rem;line-height:1.45}
    .control-stack{display:flex;flex-direction:column;gap:.9rem}
    .control-editor{border:1px solid var(--line);border-radius:10px;padding:1rem;background:rgba(0,0,0,.2)}
    .control-editor-head{display:flex;justify-content:space-between;gap:1rem;align-items:flex-end;margin-bottom:.9rem}
    .control-editor-title{font-family:'Outfit',sans-serif;font-weight:700;font-size:1rem}
    .control-editor-sub{font-size:.78rem;color:var(--muted);line-height:1.35;text-align:right}
    .control-editor-gates{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.45rem;margin-bottom:.9rem}
    .control-form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.75rem}
    .field-label{display:flex;flex-direction:column;gap:.35rem;font-size:.68rem;text-transform:uppercase;
      letter-spacing:.08em;color:var(--faint);font-family:'JetBrains Mono',monospace}
    .field-label.wide{grid-column:1/-1}
    .control-input,.control-textarea{width:100%;border:1px solid rgba(255,255,255,.1);border-radius:8px;
      background:#14110f;color:var(--text);font-family:'Inter',sans-serif;font-size:.84rem;
      padding:.62rem .7rem;outline:none}
    .control-textarea{min-height:96px;resize:vertical;line-height:1.42}
    .control-textarea.small{min-height:148px;font-family:'JetBrains Mono',monospace;font-size:.75rem}
    .control-textarea{scrollbar-width:none;-ms-overflow-style:none}
    .control-textarea::-webkit-scrollbar{display:none;width:0;height:0}
    .control-input:focus,.control-textarea:focus{border-color:var(--terra);box-shadow:0 0 0 2px rgba(217,93,57,.12)}
    .control-select .Select-control{background:#14110f;border:1px solid rgba(255,255,255,.1);border-radius:8px}
    .control-select .Select-value-label,.control-select .Select-placeholder{color:var(--text)!important}
    .control-actions{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;margin-top:.85rem}
    .control-button{font-family:'JetBrains Mono',monospace;font-size:.78rem;border:1px solid var(--line);
      border-radius:8px;padding:.55rem .75rem;background:rgba(217,93,57,.07);color:var(--text);cursor:pointer}
    .control-button.primary{border-color:rgba(141,161,137,.4);background:rgba(141,161,137,.12)}
    .control-button:hover{border-color:var(--terra)}
    .control-checklist{font-size:.76rem;color:var(--muted)}
    .control-status{margin-top:.7rem;font-size:.82rem;line-height:1.45}
    .control-status .command-line{margin-top:.45rem}
    .status-good,.status-warn,.status-bad{border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:.6rem .7rem}
    .status-good{border-color:rgba(141,161,137,.45);background:rgba(141,161,137,.08)}
    .status-warn{border-color:rgba(212,163,115,.5);background:rgba(212,163,115,.08)}
    .status-bad{border-color:rgba(217,93,57,.5);background:rgba(217,93,57,.08)}
    .control-list{display:flex;flex-direction:column;gap:.75rem}
    .control-card{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(260px,.8fr);
      gap:.8rem 1rem;align-items:start;background:rgba(0,0,0,.18);border:1px solid var(--line);
      border-radius:10px;padding:.95rem 1rem}
    .control-main{min-width:0}
    .control-request{font-size:.84rem;color:var(--muted);line-height:1.45;margin-top:.35rem;overflow-wrap:anywhere}
    .gate-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.45rem}
    .gate-status{border:1px solid var(--line);border-radius:8px;padding:.55rem .6rem;
      background:rgba(255,255,255,.025);font-family:'JetBrains Mono',monospace}
    .gate-status span{display:block;font-size:.68rem;color:var(--faint);text-transform:uppercase}
    .gate-status strong{display:block;margin-top:.18rem;font-size:.82rem;color:var(--text)}
    .gate-status.ok{border-color:rgba(141,161,137,.4);background:rgba(141,161,137,.08)}
    .gate-status.open{border-color:rgba(212,163,115,.42);background:rgba(212,163,115,.07)}
    .gate-status.missing{border-color:rgba(217,93,57,.42);background:rgba(217,93,57,.07)}
    .command-stack{grid-column:1/-1;display:flex;flex-direction:column;gap:.42rem}
    .command-line{display:block;white-space:normal;overflow-wrap:anywhere;border:1px solid rgba(255,255,255,.08);
      border-radius:7px;padding:.5rem .6rem;background:rgba(0,0,0,.22);color:var(--text);
      font-family:'JetBrains Mono',monospace;font-size:.75rem}
    .command-details{grid-column:1/-1;border:1px solid rgba(255,255,255,.08);border-radius:8px;
      padding:.55rem .65rem;background:rgba(0,0,0,.14)}
    .command-details summary{cursor:pointer;color:var(--sand);font-family:'JetBrains Mono',monospace;font-size:.74rem}
    .command-details .command-stack{margin-top:.55rem}

    .footer{display:flex;justify-content:space-between;align-items:center;margin-top:2rem;
      padding-top:1.3rem;border-top:1px solid var(--line);font-size:.76rem;color:var(--faint)}
    .footer-time{font-family:'JetBrains Mono',monospace}

    @media (max-width:920px){
      .kpis{grid-template-columns:repeat(2,1fr)}
      .grid{grid-template-columns:1fr}
      .masthead{align-items:flex-start;gap:.8rem;flex-direction:column}
      .masthead-right{width:100%;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
      .run-picker{min-width:min(100%,320px)}
      .selected-run-banner{grid-template-columns:1fr}
      .selected-chips{justify-content:flex-start}
      .report-card{grid-template-columns:1fr}
      .run-card{grid-template-columns:1fr}
      .control-form-grid{grid-template-columns:1fr}
      .control-editor-gates{grid-template-columns:repeat(2,minmax(0,1fr))}
      .control-editor-head{align-items:flex-start;flex-direction:column}
      .control-editor-sub{text-align:left}
      .control-card{grid-template-columns:1fr}
      .gate-grid{grid-template-columns:1fr}
      .latest-report{grid-template-columns:1fr}
      .report-counts{justify-content:flex-start}
      .latest-counts{justify-content:flex-start}
    }
    @media (prefers-reduced-motion:reduce){*{transition:none!important}}
  </style>
</head>
<body>{%app_entry%}<footer>{%config%}{%scripts%}{%renderer%}</footer></body>
</html>"""


def _port_in_use(host, port):
    """True if something is already listening. A TCP connect probe behaves the same
    on Windows/macOS/Linux (unlike bind(), whose SO_REUSEADDR semantics differ by OS)."""
    import socket
    probe = "127.0.0.1" if host in ("0.0.0.0", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((probe, port)) == 0


if __name__ == "__main__":
    # Safe default: bind to localhost only. Non-local binds require MINUS_DASH_TOKEN.
    host = os.environ.get("DASH_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("DASH_PORT", "8050"))
    except ValueError:
        port = 8050

    if _remote_bind_requires_token(host):
        print("[error] Refusing to bind dashboard to a non-local interface without auth.\n"
              "        Set MINUS_DASH_TOKEN to a strong random value, then open with:\n"
              f"        http://{host}:{port}/?token=$MINUS_DASH_TOKEN",
              file=sys.stderr)
        sys.exit(1)

    if _port_in_use(host, port):
        print(f"[error] Port {port} is already in use. Pick another, e.g.:\n"
              f"        DASH_PORT=8060 python app/dashboard_app.py", file=sys.stderr)
        sys.exit(1)

    auth_note = "token auth enabled" if _dashboard_token() else "localhost-only"
    print(f"\n  MinusOps Console  ->  http://{host}:{port}   ({auth_note}; Ctrl+C to stop)\n")
    # Werkzeug's built-in server behaves identically on Windows / macOS / Linux.
    # hot-reload off: some Dash renderers poll an internal endpoint that 500s without a callback.
    app.run(host=host, port=port, debug=False, dev_tools_hot_reload=False)
