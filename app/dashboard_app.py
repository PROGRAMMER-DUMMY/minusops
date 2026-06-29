"""
AWS Cost Ledger — a live FinOps operator console (Plotly Dash).

Reads the real account through finops_agent (Cost Explorer, Cost Anomaly Detection,
CloudTrail, resource tags). No mock data. Degrades to honest empty states when AWS
credentials are not configured.

Cross-platform — runs the same on Windows, macOS, and Linux (pure Python + the
werkzeug dev server; no OS-specific calls).

Run:
    pip install -r requirements.txt          # (pip3 / python3 on macOS & Linux)
    python app/dashboard_app.py          # then open http://127.0.0.1:8050

Optional environment overrides:
    DASH_PORT=8060   # use a different port if 8050 is taken
    DASH_HOST=0.0.0.0  # expose on the LAN (default is localhost-only, which is safer)
"""
import os
import sys
import html as html_lib
import json
import time
import datetime
from concurrent.futures import ThreadPoolExecutor

# Talk to the active cloud only through the provider abstraction (core/ package).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "core")
sys.path.insert(0, SCRIPTS)
from providers.base import get_provider, active_cloud  # noqa: E402
import plan_inspector  # noqa: E402
import runs as run_store  # noqa: E402
import minusctl  # noqa: E402

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


def report_inventory():
    """Return generated deployment reports, preferring product artifacts over agent internals."""
    reports = {}
    for root in report_roots():
        if not os.path.isdir(root):
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
                "template": manifest.get("template", "unknown"),
                "generated_at": manifest.get("generated_at", "unknown"),
                "counts": manifest.get("counts", {}),
                "files": files,
                "source": "run" if "\\runs\\" in root or "/runs/" in root else ("artifacts" if "artifacts" in root else "agent-runtime"),
                "status": status,
            }
    return sorted(reports.values(), key=lambda r: r["generated_at"], reverse=True)


def collect_optimization_findings(limit=3):
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
    for run in runs_list[:limit]:
        tf_dir = run.get("terraform_dir")
        if not tf_dir or not os.path.isdir(tf_dir):
            continue
        try:
            for finding in optimize_analyzer.scan_hcl_files(tf_dir):
                findings.append({**finding, "run_id": run.get("run_id")})
        except Exception:
            continue
    return findings


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
        package_md = os.path.join(root, "enterprise-package.md")
        package_json = os.path.join(root, "enterprise-package.json")
        rows.append({
            "run": item,
            "readiness": readiness,
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


def spend_bar(month):
    """Horizontal bars — service composition of the latest month's spend."""
    items = sorted((month or {}).get("by_service", {}).items(), key=lambda r: r[1])[-8:]
    fig = go.Figure()
    if items:
        labels = [s.replace("Amazon", "").replace("AWS", "").strip() for s, _ in items]
        vals = [v for _, v in items]
        hi = max(vals)
        # warm gradient: bigger spend → hotter terracotta
        colors = [C["terracotta"] if v == hi else C["sand"] for v in vals]
        fig.add_bar(
            x=vals, y=labels, orientation="h", marker=dict(color=colors),
            text=[f"${v:,.0f}" for v in vals], textposition="outside",
            textfont=dict(family=MONO, color=C["text"], size=12),
            hovertemplate="%{y}: $%{x:,.2f}<extra></extra>",
        )
    lay = _base_layout(max(180, 38 * max(len(items), 1)))
    lay.update(
        xaxis=dict(visible=False),
        yaxis=dict(tickfont=dict(family=MONO, color=C["muted"], size=12), automargin=True),
        bargap=0.35,
    )
    fig.update_layout(**lay)
    return fig


def trend_line(months):
    """Monthly burn — total spend trend."""
    fig = go.Figure()
    if months:
        x = [m["month"] for m in months]
        y = [m["total"] for m in months]
        rising = len(y) >= 2 and y[-1] >= y[-2]
        accent = C["terracotta"] if rising else C["sage"]
        fig.add_scatter(
            x=x, y=y, mode="lines+markers",
            line=dict(color=accent, width=2.5, shape="spline"),
            fill="tozeroy", fillcolor="rgba(217, 93, 57, 0.10)",
            marker=dict(size=7, color=accent, line=dict(color=C["bg"], width=2)),
            hovertemplate="%{x}: $%{y:,.2f}<extra></extra>",
        )
    lay = _base_layout(190)
    lay.update(
        xaxis=dict(tickfont=dict(family=MONO, color=C["faint"], size=11),
                   showgrid=False, showline=False),
        yaxis=dict(tickprefix="$", tickfont=dict(family=MONO, color=C["faint"], size=11),
                   gridcolor=C["line"], zeroline=False),
    )
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
    if not item.get("package_md"):
        links.append(html.Span("run: python core/minusctl.py package", className="report-missing"))
    first_issue = (blockers or warnings or [{}])[0]
    return html.Div(className=f"run-card {tone}", children=[
        html.Div(className="run-main", children=[
            html.Div(run.get("run_id", "unknown"), className="run-title"),
            html.Div(className="run-meta", children=[
                html.Span(run.get("blueprint", "-")),
                html.Span(run.get("cloud", "-")),
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


def readiness_panel():
    rows = run_inventory()
    total_runs = len(run_store.list_runs()) if os.path.isdir(os.path.join(ROOT, "runs")) else 0
    if not rows:
        body = html.Div(className="empty sage", children=[
            html.Div("No run workspaces", className="empty-title"),
            html.Div("Run core/minusctl.py create ... --generate to create a governed workspace.", className="empty-sub"),
        ])
    else:
        tabs = []
        for idx, row in enumerate(rows):
            run = row["run"]
            readiness = row["readiness"]
            label = ("Latest " if idx == 0 else "") + run.get("run_id", "run").split("-aws-data-pipeline-standard")[0]
            tabs.append(dcc.Tab(
                label=label,
                value=run.get("run_id", str(idx)),
                className="run-tab",
                selected_className="run-tab selected",
                children=run_readiness_card(row),
            ))
        body = html.Div(className="runs", children=[
            dcc.Tabs(
                value=rows[0]["run"].get("run_id"),
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


def optimization_panels():
    """One distinct panel per finding category (Cost / Security / Observability)."""
    findings = collect_optimization_findings()
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


def deployment_reports_panel():
    reports = report_inventory()
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
def build_dynamic(d):
    """Build the data-dependent part of the page from one assembled snapshot."""
    months = d["months"]
    if months:
        curr = months[-1]["total"]
        prev = months[-2]["total"] if len(months) >= 2 else 0
        delta_pct = ((curr - prev) / prev * 100) if prev else 0
        this_month_val = f"${curr:,.2f}"
        vs_val = f"{delta_pct:+.1f}%"
        vs_tone = "terracotta" if delta_pct >= 0 else "sage"
        vs_sub = f"{'up' if delta_pct >= 0 else 'down'} from {months[-2]['month']}" if prev else "no prior month"
        tracked = str(len(months[-1]["by_service"]))
    else:
        this_month_val, vs_val, vs_tone, vs_sub, tracked = "—", "—", "muted", "awaiting data", "—"

    anoms = d["anomalies"]
    crit = sum(1 for a in anoms if a["severity"] == "CRITICAL")
    anom_val = str(len(anoms))
    anom_tone = "terracotta" if crit else ("sage" if not anoms else "sand")
    anom_sub = f"{crit} critical" if crit else ("all clear" if not anoms else "review")

    banner = None
    if not d["connected"]:
        banner = html.Div(className="banner", children=[
            "Not connected to AWS. Run ", html.Code("aws configure"),
            " to load live cost data, then refresh.",
        ])

    overview = html.Div(className="tabpane", children=[
        html.Div(className="kpis", children=[
            kpi("This month", this_month_val, "unblended, month-to-date", "text"),
            kpi("Vs last month", vs_val, vs_sub, vs_tone),
            kpi("Anomalies", anom_val, anom_sub, anom_tone),
            kpi("Tracked services", tracked, "with spend this month", "sand"),
        ]),
        html.Div(className="grid", children=[
            html.Div(className="col-main", children=[
                panel("Spend by service", "this month",
                      dcc.Graph(figure=spend_bar(months[-1] if months else None),
                                config={"displayModeBar": False})),
                panel("Monthly burn", "trailing months",
                      dcc.Graph(figure=trend_line(months),
                                config={"displayModeBar": False})),
            ]),
            html.Div(className="col-side", children=[
                panel("Anomaly ledger", "cost spike -> root cause", ledger(anoms)),
            ]),
        ]),
    ])

    def _tab(label, value, children):
        return dcc.Tab(label=label, value=value, className="main-tab",
                       selected_className="main-tab selected",
                       children=html.Div(className="tabpane", children=children)
                       if not isinstance(children, html.Div) else children)

    tabs = dcc.Tabs(value="overview", className="main-tabs", children=[
        _tab("Overview", "overview", overview),
        _tab("Optimization", "optimization", optimization_panels()),
        _tab("Reports", "reports", [deployment_reports_panel()]),
        _tab("Readiness", "readiness", [readiness_panel()]),
    ])
    return [banner, tabs]


def app_shell():
    """Static frame served immediately; the callback below fills in the live data."""
    return html.Div(className="page", children=[
        html.Header(className="masthead", children=[
            html.Div(className="brand", children=[
                html.Span(className="brand-mark"),
                html.Div(children=[
                    html.Div("AWS Cost Ledger", className="brand-name"),
                    html.Div("live FinOps console", className="brand-tag"),
                ]),
            ]),
            html.Div(className="masthead-right", children=[
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
app = dash.Dash(__name__, title="AWS Cost Ledger")
app.layout = app_shell


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
.canvas{flex:1;overflow:auto;padding:20px;scrollbar-width:none;-ms-overflow-style:none}
.canvas::-webkit-scrollbar{display:none}
.canvas svg{width:100%;height:auto}
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
 <div class="canvas">__SVG__</div>
 <div class="panel" id="panel">
  <h2>Service inspector</h2>
  <div class="hint">Click any service box in the diagram to see the exact Terraform that provisions it, plus its security/cost findings.</div>
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

    # Embed the plan-bound source + per-resource file/type/findings for click-to-code.
    sources = {}
    snapshot = report_dir / "source_snapshot"
    if snapshot.exists():
        for f in snapshot.rglob("*"):
            if f.is_file() and f.suffix in (".tf", ".tfvars"):
                sources[f.name] = f.read_text(encoding="utf-8", errors="replace")
    addr_file, addr_type = {}, {}
    for ch in plan.get("resource_changes", []):
        addr, rtype = ch.get("address"), ch.get("type", "")
        if addr:
            addr_file[addr] = plan_inspector.owner_file_for_type(rtype)
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
            .replace("__SVG__", svg)
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
    if filename not in {"enterprise-package.md", "enterprise-package.json"}:
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
        "architecture.svg", "report.html", "plan.html", "cost.html",
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
)
def _render(_n_clicks):
    # The Refresh button forces a fresh fetch; initial page load uses the cache if warm.
    force = ctx.triggered_id == "refresh-btn"
    d = assemble(force=force)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M UTC")
    return build_dynamic(d), (d["account"] or "not connected"), f"Refreshed {now}"


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

    /* KPI strip */
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

    .footer{display:flex;justify-content:space-between;align-items:center;margin-top:2rem;
      padding-top:1.3rem;border-top:1px solid var(--line);font-size:.76rem;color:var(--faint)}
    .footer-time{font-family:'JetBrains Mono',monospace}

    @media (max-width:920px){
      .kpis{grid-template-columns:repeat(2,1fr)}
      .grid{grid-template-columns:1fr}
      .report-card{grid-template-columns:1fr}
      .run-card{grid-template-columns:1fr}
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
    # Safe default: bind to localhost only. Set DASH_HOST=0.0.0.0 to expose on the LAN.
    host = os.environ.get("DASH_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("DASH_PORT", "8050"))
    except ValueError:
        port = 8050

    if _port_in_use(host, port):
        print(f"[error] Port {port} is already in use. Pick another, e.g.:\n"
              f"        DASH_PORT=8060 python app/dashboard_app.py", file=sys.stderr)
        sys.exit(1)

    print(f"\n  AWS Cost Ledger  ->  http://{host}:{port}   (Ctrl+C to stop)\n")
    # Werkzeug's built-in server behaves identically on Windows / macOS / Linux.
    # hot-reload off: some Dash renderers poll an internal endpoint that 500s without a callback.
    app.run(host=host, port=port, debug=False, dev_tools_hot_reload=False)
