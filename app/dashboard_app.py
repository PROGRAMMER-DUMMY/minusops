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

This process binds a network port, so it is the one component in the repo with an inbound
attack surface. Two rules hold it closed, and both are enforced in code, not in docs:
`__main__` refuses to start when DASH_HOST is not a loopback address unless
MINUS_DASH_TOKEN is set, and a `before_request` hook rejects every request that does not
present that token (Bearer header, `?token=`, or the cookie it sets) once one is
configured — `DASH_TOKEN` is accepted as an alias. With no token set the server is reachable only from localhost. Loosening
either check exposes live AWS account and cost data to the LAN.

Depends on: providers.base, plan_inspector, reporter, runs, minusctl, requirements,
    architecture_decision, accelerators — all imported flat via the core/ sys.path shim
    set up below, not as `core.*`
Shells out to: nothing directly; AWS is reached only through providers.base.get_provider(),
    and minusctl/plan_inspector run the CLIs on its behalf
Used by: tests/test_dashboard.py; otherwise a leaf, run as a script
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
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(SCRIPTS, _sub))
sys.path.insert(0, SCRIPTS)
from providers.base import get_provider  # noqa: E402
import plan_inspector  # noqa: E402
import reporter as report_builder  # noqa: E402
import runs as run_store  # noqa: E402
import minusctl  # noqa: E402
import requirements as reqgate  # noqa: E402
import architecture_decision as archdec  # noqa: E402
import accelerators  # noqa: E402

import dash  # noqa: E402
from dash import dcc, html, Input, Output, State, ctx  # noqa: E402
import plotly.graph_objects as go  # noqa: E402

# ---------------------------------------------------------------------------
# Design tokens — Monad (DESIGN.md): an editorial tech journal on warm parchment.
#
# Two adaptations of the brief, both deliberate:
#
# 1. DENSITY. DESIGN.md describes a marketing site -- 80px hero, 40px card padding,
#    64px section gaps. This is an operator console where a screen of KPIs, tables and
#    charts has to be read at a glance. The token LANGUAGE is kept exactly (parchment
#    canvas, 1px Ash hairlines, pill containers, serif-400 headings, mono everything
#    else); the SCALE steps down one rung -- 24px card padding, 32px section gaps, and
#    a type scale that tops out at 32px because a console has no hero.
#
# 2. CHART COLOR. The brief has no data-viz guidance and states its pastel palette is
#    decorative-only. Measured on parchment that is exactly right: Coral and Crimson
#    sit at deltaE 8.7 in NORMAL vision and every pastel is under 3:1 contrast, so they
#    are unusable as data. Magnitude charts therefore carry no chroma at all -- ink for
#    the value being emphasised, ash for the rest, which is what an engineering journal
#    would print. The one place colour encodes data is the plan-action ramp, and that is
#    ordinal (create < update < replace < delete by destructiveness), so it is a single
#    hue stepped by lightness rather than four competing hues.
#
# Role keys are unchanged from the previous palette so every call site keeps working;
# only the values moved.
# ---------------------------------------------------------------------------
C = {
    "bg":         "#f6f3f1",   # Parchment  -- page canvas, never pure white
    "bg_elev":    "#cfdaf5",   # Periwinkle Mist -- the one coloured surface
    "panel":      "transparent",
    "line":       "#cecac8",   # Ash -- every border and divider, 1px, always
    "terracotta": "#2b59d1",   # Lake Blue -- THE single primary action, never scattered
    "terra_soft": "#5a7fdd",
    "sand":       "#8a6516",   # attention  (4.81:1 on parchment)
    "sage":       "#2f6b4f",   # good       (5.70:1)
    "text":       "#242424",   # Off-Black  (14.05:1)
    "muted":      "#4e4d4d",   # Graphite   (7.63:1)
    "faint":      "#797776",   # Smoke      (4.03:1 -- meta only, never actionable copy)
    "critical":   "#8f2d18",   # (7.44:1)
}

# Plan actions ordered by destructiveness. Monotonic lightness (0.601 -> 0.078), so the
# ramp reads as severity even in greyscale or without colour vision; every segment is
# direct-labelled, and the two darkest carry parchment text rather than ink.
ACTION_RAMP = {
    "no-op":   "#cecac8",
    "create":  "#e3c98f",
    "update":  "#dc9358",
    "replace": "#c05a2c",
    "delete":  "#8f2d18",
}

# Untitled Serif and ABC Diatype Mono are licensed faces. DESIGN.md names the
# substitutes: "any editorial serif with similar stroke contrast" and "JetBrains Mono,
# IBM Plex Mono, or Space Mono". Instrument Serif ships weight 400 only, which enforces
# the brief's hardest rule for free -- headings CANNOT go bold.
DISPLAY = "'Instrument Serif', Georgia, 'Times New Roman', serif"
BODY = "'JetBrains Mono', ui-monospace, Menlo, Consolas, monospace"
MONO = "'JetBrains Mono', ui-monospace, Menlo, Consolas, monospace"


# ---------------------------------------------------------------------------
# Data assembly (live AWS, via providers.base.get_provider())
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
    """Spend by service -- horizontal bars with EMPHASIS: the top spender is set in ink,
    the rest in ash. No chroma: DESIGN.md reserves Lake Blue for the single primary action,
    and its pastels measure under 3:1 on parchment, so an engineering journal's answer --
    weight, not hue -- is the correct one here."""
    items = sorted((month or {}).get("by_service", {}).items(), key=lambda r: r[1])[-8:]
    fig = go.Figure()
    if items:
        labels = [s.replace("Amazon", "").replace("AWS", "").strip() for s, _ in items]
        vals = [v for _, v in items]
        hi = max(vals)
        colors = [C["text"] if v == hi else C["line"] for v in vals]
        fig.add_bar(
            x=vals, y=labels, orientation="h", width=0.5,
            marker=dict(color=colors),
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
        bargap=0.45,
    )
    fig.update_layout(**lay)
    try:
        fig.update_layout(barcornerradius=4)   # 4px rounded data-end
    except Exception:
        pass                                    # older plotly: square ends, still correct
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
            marker=dict(color=C["text"]),
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


def plan_action_bar(report):
    """Plan composition as one horizontal stacked bar.

    Was a donut. A donut of a single action type renders as a plain ring that encodes
    nothing -- which is exactly what this plan produces most of the time, since a first
    apply is all creates. A stacked bar degrades honestly: one action fills the width and
    reads as "all of it", four actions read as proportion.

    The ramp is ordinal (create < update < replace < delete by destructiveness), so a
    reader scanning left to right sees severity increase. Every segment is direct-labelled,
    so identity never rests on colour alone.
    """
    counts = (report or {}).get("counts") or {}
    order = [a for a in ("no-op", "create", "update", "replace", "delete") if counts.get(a, 0)]
    fig = go.Figure()
    for action in order:
        value = counts[action]
        fig.add_bar(
            x=[value], y=["plan"], orientation="h", name=action,
            marker=dict(color=ACTION_RAMP[action],
                        line=dict(color=C["bg"], width=2)),   # 2px surface gap between fills
            text=[f"{action} {value}"], textposition="inside", insidetextanchor="middle",
            # The two darkest steps cannot carry ink; parchment on them is 7.4:1.
            textfont=dict(family=MONO, size=12,
                          color=C["bg"] if action in ("replace", "delete") else C["text"]),
            hovertemplate=f"{action}: %{{x}}<extra></extra>",
        )
    lay = _base_layout(120)
    lay.update(
        barmode="stack", bargap=0.6,
        margin=dict(l=8, r=8, t=28, b=8),
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        uniformtext=dict(mode="hide", minsize=10),
    )
    fig.update_layout(**lay)
    return fig


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
def kpi(label, value, sub=None, tone="text"):
    """A stat tile. The NUMBER is always ink; status rides a small rule beneath it.

    Colouring the numeral itself put an ochre 72/100 and a green $1.00/mo on a page whose
    whole discipline is one accent -- and green on a cost figure reads as "good cost",
    which is not the claim being made (the claim is "this figure has evidence behind it").
    DESIGN.md: never introduce additional accent colours for UI elements.
    """
    return html.Div(className="kpi", children=[
        html.Div(label, className="kpi-label"),
        html.Div(value, className="kpi-value"),
        html.Div(sub or "", className="kpi-sub",
                 style={"borderTopColor": C[tone]} if tone != "text" else None),
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
    tone = {"CRITICAL": C["critical"], "HIGH": C["sand"], "MODERATE": C["sage"]}[a["severity"]]
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
    # Services / resources / IAM / drift / files ship as ONE artifact: inspect.pdf
    # (sections expanded, clickable PDF outline). Older reports without the PDF fall
    # back to the live consolidated page.
    if "inspect.pdf" in files:
        links.append(report_link(short, "inspect.pdf", "Inspect PDF"))
    else:
        links.append(html.A("Inspect", href=f"/deployment-reports/{short}/inspect",
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
        links.append(html.Span("run: python core/reporting/minusctl.py package", className="report-missing"))
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
                           validation_text="", rollback_text="", failure_modes_text="",
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
    for field, text in (("sources", sources_text), ("assumptions", assumptions_text),
                        ("risks", risks_text), ("validation", validation_text),
                        ("rollback", rollback_text), ("failure_modes", failure_modes_text)):
        values = _split_control_lines(text)
        if values:
            if field == "failure_modes":
                unknown = [v for v in values if v not in archdec.FAILURE_MODES]
                if unknown:
                    raise ValueError("unknown failure mode(s): " + ", ".join(unknown)
                                     + " (valid: " + ", ".join(sorted(archdec.FAILURE_MODES)) + ")")
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
    # Decision versioning: every overwrite snapshots the prior record, so "why did the
    # architecture change" is answerable from the run itself.
    if os.path.exists(decision_path):
        history_dir = os.path.join(run["root"], "decisions")
        os.makedirs(history_dir, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        try:
            import shutil
            shutil.copy2(decision_path, os.path.join(history_dir, f"architecture_decision.{stamp}.json"))
        except OSError:
            pass
    archdec.save(decision_path, data, decided_by=decided_by)
    ok, missing = archdec.validate(data)
    versions = 0
    history_dir = os.path.join(run["root"], "decisions")
    if os.path.isdir(history_dir):
        versions = len([n for n in os.listdir(history_dir) if n.endswith(".json")])
    return {"path": decision_path, "record": data, "ok": ok, "missing": missing,
            "prior_versions": versions}


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
    validation_value = "\n".join(decision.get("validation") or [])
    rollback_value = "\n".join(decision.get("rollback") or [])
    failure_modes_value = "\n".join(decision.get("failure_modes") or [])
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
            html.Label(className="field-label", children=[
                html.Span("Validation"),
                dcc.Textarea(id="control-validation", value=validation_value,
                             placeholder="One check per line that proves this design correct",
                             className="control-textarea small"),
            ]),
            html.Label(className="field-label", children=[
                html.Span("Rollback"),
                dcc.Textarea(id="control-rollback", value=rollback_value,
                             placeholder="One step per line for undoing this design",
                             className="control-textarea small"),
            ]),
            html.Label(className="field-label", children=[
                html.Span("Failure modes"),
                dcc.Textarea(id="control-failure-modes", value=failure_modes_value,
                             placeholder="FM-01 .. FM-05, one per line (optional)",
                             className="control-textarea small"),
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
        f"python core/architecture/requirements.py check {requirements_file}",
        f"python core/reporting/minusctl.py decision template --run {run_id} --write",
        f"python core/architecture/architecture_decision.py set {decision_file} --architecture \"<selected architecture>\" --summary \"<why this choice>\"",
        f"python core/architecture/architecture_decision.py add-module {decision_file} <module-id>",
        f"python core/architecture/architecture_decision.py add-source {decision_file} \"<official doc URL>\"",
        f"python core/architecture/architecture_decision.py check {decision_file}",
        f"python core/generation/synthesizer.py \"<requirements summary>\" --run {run_id} --requirements-file {requirements_file} --decision-file {decision_file}",
        f"python core/governance/plan_gate.py verify --dir {run.get('terraform_dir')} --policy-mode production",
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
            html.Div('Run core/reporting/minusctl.py create "<request>".', className="empty-sub"),
        ])
    else:
        visible_rows = [selected] + [row for row in rows if row is not selected] if selected else rows
        body = html.Div(className="control-stack", children=[
            control_editor_panel(rows, selected_run_id=selected_run_id),
            html.Div(className="control-list", children=[control_run_card(row) for row in visible_rows[:4]]),
        ])
    return panel("Control plane", "requirements -> decision -> synthesis", body)


def _run_trend_table(rows):
    """Cross-run trend: readiness, conformance, and unit economics per run — the numbers
    that show whether the pipeline practice is getting better or worse over time."""
    if not rows:
        return None
    trs = [html.Tr([html.Th(h) for h in
                    ("Run", "Readiness", "Conformance", "Tier", "Forecast $/mo", "Cost/GB")])]
    for row in rows:
        r = row.get("readiness") or {}
        conf = r.get("conformance") or {}
        cost = (r.get("latest_report") or {}).get("cost") or {}
        total = cost.get("monthly_total_usd")
        per_gb = "—"
        try:
            a = cost.get("assumptions") or {}
            dg = float(a.get("daily_data_gb") or 0)
            days = float(a.get("days_per_month") or 30)
            if total and dg > 0:
                per_gb = f"${float(total) / (dg * days):,.4f}"
        except (TypeError, ValueError):
            pass
        trs.append(html.Tr([
            html.Td(row["run"].get("run_id", "—")),
            html.Td(f"{r.get('score', 0)}/100 {r.get('status', '')}"),
            html.Td(f"{conf.get('score', '—')}/100" if conf else "—"),
            html.Td((conf.get("volume_tier") or "—").upper() if conf else "—"),
            html.Td(f"${float(total):,.2f}" if total else "—"),
            html.Td(per_gb),
        ]))
    return html.Table(className="trend-table", children=trs)


def readiness_panel(selected_run_id=None):
    rows = run_inventory()
    total_runs = len(run_store.list_runs()) if os.path.isdir(os.path.join(ROOT, "runs")) else 0
    selected = _row_for_run(rows, selected_run_id)
    if not rows:
        body = html.Div(className="empty sage", children=[
            html.Div("No run workspaces", className="empty-title"),
            html.Div('Run core/reporting/minusctl.py create "<request>" to create a requirements-first workspace.', className="empty-sub"),
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
            _run_trend_table(rows),
            dcc.Tabs(
                value=visible[0]["run"].get("run_id"),
                className="run-tabs",
                children=tabs,
            ),
            html.Div(f"Showing {len(rows)} selectable run(s). Older runs not shown: {max(total_runs - len(rows), 0)}. Use `python core/reporting/minusctl.py runs list` for full history.",
                     className="run-history-note"),
        ])
    return panel("Enterprise readiness", "run history", body)


_SEVERITY_TONE = {"HIGH": "critical", "MEDIUM": "sand", "LOW": "sage", "EXTERNAL": "faint"}


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
    tone = C["sage"] if score >= 90 else C["sand"] if score >= 60 else C["critical"]
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


def _scale_curve_table(cost):
    """The AWS-priced scale curve, rendered as RESULTS (not a command) when it exists."""
    points = ((cost or {}).get("scale_curve") or {}).get("points") or []
    if not points:
        return None
    a = (cost or {}).get("assumptions") or {}
    try:
        dg = float(a.get("daily_data_gb") or 0)
        days = float(a.get("days_per_month") or 30)
    except (TypeError, ValueError):
        dg, days = 0, 30
    trs = [html.Tr([html.Th(h) for h in ("Usage", "Monthly (AWS-priced)", "Cost/GB")])]
    for p in points:
        try:
            total = float(p.get("total"))
        except (TypeError, ValueError):
            continue
        per_gb = f"${total / (dg * days * p['factor']):,.4f}" if dg > 0 else "—"
        trs.append(html.Tr([html.Td(f"×{p['factor']:g}"),
                            html.Td(f"${total:,.2f}"), html.Td(per_gb)]))
    return html.Table(className="trend-table", children=trs)


def scenario_shortcuts_panel(selected_run_id=None):
    """What-if scenarios for the selected run: results first (scale curve, variance),
    one-click buttons for the zero-input AWS-priced actions, and commands only where
    the operator must author input (commitments JSON, custom assumptions)."""
    rows = run_inventory()
    row = _row_for_run(rows, selected_run_id)
    report = _selected_report(row)
    cost = (report or {}).get("cost") or {}
    rd = report.get("path") if report else None
    children = []

    curve = _scale_curve_table(cost)
    if curve:
        children += [html.Div("Cost at scale — each point is a separate AWS BCM estimate:",
                              className="empty-sub", style={"marginBottom": ".4rem"}), curve]
    variance = cost.get("variance") or {}
    if variance.get("actual_total") is not None:
        children.append(html.Div(
            f"Forecast ${variance.get('forecast_total', 0):,.2f} vs actual "
            f"${variance.get('actual_total', 0):,.2f} — see the cost report for per-service variance.",
            className="empty-sub", style={"margin": ".6rem 0"}))

    if rd and cost.get("ok"):
        children.append(html.Div(className="control-actions", children=[
            html.Button("Price at ×1/×5/×10 usage (AWS)", id="whatif-scale-btn",
                        n_clicks=0, className="control-button"),
            html.Button("Pull Cost Explorer actuals", id="whatif-actuals-btn",
                        n_clicks=0, className="control-button"),
        ]))
        children.append(dcc.Loading(type="default", color=C["text"],
                                    children=html.Div(id="whatif-status")))
    elif not rd:
        children.append(_chart_empty("No report yet",
                                     "Generate a plan first — scenarios price a specific plan."))
    else:
        children.append(_chart_empty("No estimate yet",
                                     "Scenarios need the base BCM estimate (created automatically "
                                     "when AWS credentials are available)."))

    target = f'"{rd}"' if rd else "<report-dir>"
    children.append(html.Details(className="command-details", children=[
        html.Summary("Operator-authored scenarios (run in terminal)"),
        html.Div(className="command-stack", children=[
            html.Div(children=[
                html.Div(label, className="empty-sub", style={"margin": ".5rem 0 .2rem"}),
                _command_line(cmd),
            ]) for label, cmd in [
                ("Model Savings Plans / RI commitments (needs your commitments JSON)",
                 f"python core/cost/bcm_pricing_calculator.py scenario --report-dir {target} --commitments commitments.json"),
                ("Re-price with different usage assumptions",
                 f"python core/cost/bcm_pricing_calculator.py prepare --report-dir {target} --derive "
                 f"--assume glue_runs_per_day=48 && python core/cost/bcm_pricing_calculator.py run --report-dir {target}"),
            ]
        ]),
    ]))
    return panel("What-if scenarios & evidence", "scale, commitments, actuals — all AWS-priced",
                 html.Div(children=children))


def deployment_reports_panel(selected_run_id=None):
    reports = report_inventory(selected_run_id)
    if not reports:
        body = html.Div(className="empty sage", children=[
            html.Div("No deployment reports", className="empty-title"),
            html.Div("Run core/generation/demo.py or plan_gate.py plan to generate report artifacts.", className="empty-sub"),
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
                      dcc.Graph(figure=plan_action_bar(selected_report),
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

    # Empty or invalid values must never blank the page (an unset-vs-empty env var once
    # rendered tab bars with no selected tab) — anything unknown falls back to overview.
    default_tab = (os.environ.get("MINUS_DASH_DEFAULT_TAB") or "overview").strip().lower()
    if default_tab not in ("overview", "control", "optimization", "reports", "readiness"):
        default_tab = "overview"
    tabs = dcc.Tabs(value=default_tab,
                    className="main-tabs", children=[
        _tab("Overview", "overview", overview),
        _tab("Control", "control", [control_plane_panel(selected_run_id)]),
        _tab("Optimization", "optimization", optimization_panels(selected_run_id)
             + [scenario_shortcuts_panel(selected_run_id)]),
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
            type="default", color=C["text"], parent_className="content-wrap",
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
    """Authorize a request against MINUS_DASH_TOKEN (Bearer header, ?token=, or cookie).

    Returning True when no token is configured is deliberate, not a missing check: the
    only way to reach that branch is a loopback bind, because `__main__` refuses to start
    on a non-loopback host with no token set. Making this fail closed instead would break
    the ordinary `python app/dashboard_app.py` case; the guard that matters lives at bind
    time. Compare with hmac.compare_digest, never `==`.
    """
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


@app.server.route("/deployment-reports/<report_id>/inspect")
def _serve_report_inspect(report_id):
    """Live consolidated review page — same builder as the printed inspect.pdf, but with
    collapsible sections and LIVE source-drift status (the PDF records generation time)."""
    from flask import abort, Response
    try:
        report_dir, manifest, plan = plan_inspector.load_report(report_id)
        drift = plan_inspector.source_status(report_id)
        diff_lines = plan_inspector.diff_source(report_id)
        files = [(i.name, i.stat().st_size) for i in sorted(report_dir.iterdir())
                 if i.name != "source_snapshot"]
    except Exception:
        abort(404)
    return Response(report_builder.build_inspect_html(
        manifest, plan, report_files=files,
        drift_status=drift.get("status", "UNKNOWN"),
        diff_text="\n".join(diff_lines) or drift.get("reason", ""),
        for_print=False), mimetype="text/html")


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
        rows.insert(0, ("Terraform directory", os.path.normpath(manifest.get("dir", "-"))))
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
        "architecture.svg", "dataflow.svg", "report.html", "cost.html",
        "plan.pdf", "cost.pdf", "inspect.pdf", "plan.json", "cost.json",
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
    State("control-validation", "value"),
    State("control-rollback", "value"),
    State("control-failure-modes", "value"),
    State("control-alternatives", "value"),
    State("control-force", "value"),
    prevent_initial_call=True,
)
def _control_action(_accelerator_clicks, _save_clicks, run_id, architecture, summary, modules_text,
                    sources_text, assumptions_text, risks_text, validation_text, rollback_text,
                    failure_modes_text, alternatives_text, force_values):
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
            validation_text=validation_text or "",
            rollback_text=rollback_text or "",
            failure_modes_text=failure_modes_text or "",
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


@app.callback(
    Output("whatif-status", "children"),
    Input("whatif-scale-btn", "n_clicks"),
    Input("whatif-actuals-btn", "n_clicks"),
    State("global-run-select", "value"),
    prevent_initial_call=True,
)
def _whatif_action(_scale_clicks, _actuals_clicks, run_id):
    """One-click what-ifs. Both are safe by construction: the scale curve creates
    temporary AWS pricing estimates (deleted after reading) and the actuals pull is
    read-only Cost Explorer. AWS produces every number; results land in the report."""
    row = _row_for_run(run_inventory(), run_id)
    report = _selected_report(row)
    if not (report and report.get("path")):
        return html.Div("No report for this run yet.", className="status-warn")
    import bcm_pricing_calculator as bcm
    try:
        if ctx.triggered_id == "whatif-scale-btn":
            res = bcm.scale_curve(report["path"])
            pts = " · ".join(f"×{p['factor']:g} = ${float(p['total']):,.2f}/mo"
                             for p in res["points"])
            return html.Div(className="status-good", children=[
                html.Strong("AWS priced the curve: "), html.Span(pts),
                html.Span("  — hit Refresh to render the table."),
            ])
        res = bcm.fetch_actuals(report["path"])
        return html.Div(
            f"Actuals pulled for {res['month']} ({len(res['actuals'])} service(s)) — "
            "hit Refresh for the variance view.", className="status-good")
    except Exception as exc:
        return html.Div(str(exc), className="status-bad")


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
  <link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    /* ------------------------------------------------------------------
       Monad — editorial tech journal on warm parchment (DESIGN.md).

       The system in one line: parchment canvas, 1px Ash hairlines, pill
       containers, Untitled Serif at 400 for anything that announces, mono for
       everything that instructs. No shadows anywhere — elevation is surface
       colour and a hairline, never a blur.
       ------------------------------------------------------------------ */
    :root{
      --parchment:#f6f3f1; --periwinkle:#cfdaf5; --lake:#2b59d1;
      --ink:#242424; --graphite:#4e4d4d; --smoke:#797776; --ash:#cecac8;
      --good:#2f6b4f; --warn:#8a6516; --crit:#8f2d18;
      --serif:'Instrument Serif',Georgia,'Times New Roman',serif;
      --mono:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
      /* Console density: the brief's scale stepped down one rung. */
      --pad-card:24px; --gap-section:32px; --r-card:24px; --r-pill:100px; --r-tag:9999px;
    }
    *{box-sizing:border-box;margin:0;padding:0}
    html{scroll-behavior:smooth}
    body{
      background:var(--parchment); color:var(--ink);
      font-family:var(--mono); font-size:14px; line-height:1.35; letter-spacing:-0.28px;
      height:100vh; overflow:hidden; -webkit-font-smoothing:antialiased;
    }
    ::selection{background:var(--periwinkle);color:var(--ink)}
    a{color:inherit}
    :focus-visible{outline:2px solid var(--lake);outline-offset:2px;border-radius:4px}

    .page{max-width:1432px;margin:0 auto;padding:20px 32px 0;height:100vh;
      display:flex;flex-direction:column}

    /* Masthead — the wordmark sits in serif, the only place it appears at rest. */
    .masthead{display:flex;justify-content:space-between;align-items:center;flex:0 0 auto;
      padding-bottom:16px;border-bottom:1px solid var(--ash);margin-bottom:4px}
    .refresh-time{font-size:12px;color:var(--smoke);letter-spacing:-0.4px}
    .brand{display:flex;align-items:center;gap:14px}
    /* The dot mark: a pill, like every other container in this system. */
    .brand-mark{width:12px;height:12px;border-radius:var(--r-tag);background:var(--lake);flex:0 0 auto}
    .brand-name{font-family:var(--serif);font-weight:400;font-size:28px;letter-spacing:-0.56px;
      line-height:1.1}
    .brand-tag{font-size:12px;color:var(--smoke);letter-spacing:0.08em;text-transform:uppercase;
      margin-top:2px}
    .masthead-right{display:flex;align-items:center;gap:24px}
    .acct{display:flex;flex-direction:column;align-items:flex-end;gap:2px}
    .acct-label{font-size:12px;letter-spacing:0.1em;text-transform:uppercase;color:var(--smoke)}
    .acct-value{font-size:14px;color:var(--ink)}

    /* Ghost pill — tertiary action, per the brief. */
    .refresh{font-size:12px;text-transform:uppercase;letter-spacing:0.06em;color:var(--ink);
      text-decoration:none;border:1px solid var(--ink);padding:10px 20px;border-radius:var(--r-pill);
      background:transparent;transition:background .15s ease,color .15s ease;white-space:nowrap}
    .refresh:hover{background:var(--ink);color:var(--parchment)}

    /* Run picker */
    .run-picker{min-width:320px;display:flex;flex-direction:column;gap:4px}
    .global-run-select .Select-control{background:var(--parchment)!important;
      border:1px solid var(--ash)!important;border-radius:var(--r-pill)!important;min-height:38px!important;
      padding:0 6px!important}
    .global-run-select .Select-value,.global-run-select .Select-placeholder{line-height:36px!important}
    .global-run-select .Select-value-label,.global-run-select .Select-placeholder{color:var(--ink)!important;
      font-family:var(--mono)!important;font-size:13px!important}
    .global-run-select .Select-menu-outer{background:var(--parchment)!important;
      border:1px solid var(--ash)!important;color:var(--ink)!important;border-radius:16px!important;
      overflow:hidden!important;box-shadow:none!important}
    .global-run-select .Select-option{background:var(--parchment)!important;color:var(--graphite)!important;
      font-size:13px!important}
    .global-run-select .Select-option.is-focused{background:var(--periwinkle)!important;color:var(--ink)!important}
    .global-run-select .Select-arrow{border-color:var(--graphite) transparent transparent!important}

    /* Section tabs — hairline rail, ink underline on the active one. */
    .content-wrap{flex:1 1 auto;min-height:0}
    .main-tabs{flex:0 0 auto}
    .main-tabs .tab-container{display:flex;gap:4px;border-bottom:1px solid var(--ash)!important;
      border-radius:0!important}
    .main-tab{font-family:var(--mono)!important;font-weight:400!important;font-size:13px!important;
      text-transform:uppercase!important;letter-spacing:0.06em!important;
      color:var(--smoke)!important;background:transparent!important;border:0!important;
      border-bottom:1px solid transparent!important;padding:12px 18px!important;
      border-radius:0!important;cursor:pointer;transition:color .15s ease}
    .main-tab:hover{color:var(--graphite)!important}
    .main-tab.selected{color:var(--ink)!important;font-weight:500!important;
      border-bottom:1px solid var(--ink)!important;background:transparent!important}
    .tabpane{height:calc(100vh - 150px);overflow-y:auto;overflow-x:hidden;
      padding:24px 2px 40px;scrollbar-width:none;-ms-overflow-style:none}
    .tabpane::-webkit-scrollbar{display:none;width:0;height:0}

    /* Notices */
    .banner{background:transparent;border:1px solid var(--ash);border-radius:var(--r-card);
      padding:16px 20px;margin-bottom:var(--gap-section);color:var(--graphite);font-size:14px}
    .banner code{background:var(--periwinkle);color:var(--ink);padding:2px 8px;border-radius:var(--r-tag);
      font-size:13px}
    .selected-run-banner{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 16px;
      align-items:center;border:1px solid var(--ash);border-radius:var(--r-card);
      padding:16px 20px;margin-bottom:16px;background:var(--periwinkle)}
    .selected-main{min-width:0}
    .selected-title{font-size:14px;color:var(--ink);overflow-wrap:anywhere}
    .selected-sub{font-size:13px;color:var(--graphite);margin-top:4px;overflow-wrap:anywhere}
    .selected-chips{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:6px}
    .selected-chips span{font-size:12px;border:1px solid rgba(36,36,36,.28);
      border-radius:var(--r-tag);padding:4px 12px;color:var(--ink);background:transparent;
      text-transform:uppercase;letter-spacing:0.04em}
    .selected-chips .chip-sage{color:var(--good);border-color:rgba(47,107,79,.45)}
    .selected-chips .chip-sand{color:var(--warn);border-color:rgba(138,101,22,.45)}
    .selected-chips .chip-terracotta{color:var(--lake);border-color:rgba(43,89,209,.45)}
    .selected-cost-note{grid-column:1/-1;color:var(--graphite);font-size:12px}

    /* KPI strip — the numbers are the page's headline, so they take the serif. */
    .kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:var(--gap-section)}
    .kpi{background:transparent;border:1px solid var(--ash);border-radius:var(--r-card);
      padding:var(--pad-card)}
    .kpi-label{font-size:12px;letter-spacing:0.1em;text-transform:uppercase;color:var(--smoke)}
    .kpi-value{font-family:var(--serif);font-weight:400;font-size:40px;letter-spacing:-0.8px;
      line-height:1.1;margin:12px 0 6px;color:var(--ink)}
    .kpi-sub{font-size:13px;color:var(--graphite);padding-top:10px;
      border-top:1px solid transparent}
    .control-actions label{display:inline-flex;align-items:center;gap:8px;font-size:13px;
      color:var(--graphite)}
    .control-actions input[type=checkbox]{accent-color:var(--lake);width:15px;height:15px;
      margin:0}

    /* Layout */
    .grid{display:grid;grid-template-columns:1.55fr 1fr;gap:16px;align-items:start}
    .col-main{display:flex;flex-direction:column;gap:16px}
    .col-side{display:flex;flex-direction:column;gap:16px}
    .panel{background:transparent;border:1px solid var(--ash);border-radius:var(--r-card);
      padding:var(--pad-card)}
    .panel-head{margin-bottom:20px}
    .eyebrow{font-size:12px;letter-spacing:0.12em;text-transform:uppercase;color:var(--smoke)}
    .panel-title{font-family:var(--serif);font-weight:400;font-size:24px;letter-spacing:-0.48px;
      line-height:1.2;margin-top:6px;color:var(--ink)}

    .arch-embed{width:100%;height:480px;border:1px solid var(--ash);border-radius:var(--r-card);
      background:var(--parchment);display:block}
    .trend-table{width:100%;border-collapse:collapse;margin-bottom:16px;font-size:13px}
    .trend-table th{text-align:left;color:var(--smoke);font-size:12px;text-transform:uppercase;
      letter-spacing:0.1em;padding:8px 10px;border-bottom:1px solid var(--ash);font-weight:400}
    .trend-table td{padding:10px;color:var(--graphite);border-bottom:1px solid var(--ash)}
    .trend-table tr:last-child td{border-bottom:0}
    .spend-line{display:flex;align-items:baseline;gap:10px;font-size:14px;color:var(--graphite);
      margin-bottom:12px;flex-wrap:wrap}
    .spend-line strong{font-family:var(--serif);font-weight:400;font-size:32px;letter-spacing:-0.64px;
      color:var(--ink)}

    /* Anomaly ledger — the signature. Each entry is a pipeline node: a hairline pill
       with the impact set in serif, so the ledger reads as a column of findings in a
       journal rather than a list of alert rows. */
    .ledger{display:flex;flex-direction:column;gap:12px}
    .ledger-entry{background:transparent;border:1px solid var(--ash);
      border-left:3px solid var(--ash);border-radius:var(--r-card);padding:20px}
    .ledger-top{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
    .ledger-service{font-family:var(--serif);font-weight:400;font-size:24px;letter-spacing:-0.48px;
      color:var(--ink)}
    .ledger-impact{font-family:var(--serif);font-weight:400;font-size:24px;letter-spacing:-0.48px}
    .ledger-meta{display:flex;align-items:center;gap:8px;margin-top:8px;font-size:12px;
      color:var(--smoke);text-transform:uppercase;letter-spacing:0.06em}
    .ledger-meta .dot{color:var(--ash)}
    .ledger-owner{margin-top:12px;font-size:12px;color:var(--ink);border:1px solid var(--ash);
      padding:4px 12px;border-radius:var(--r-tag);display:inline-block}

    /* Findings */
    .findings{display:flex;flex-direction:column;gap:12px}
    .finding{background:transparent;border:1px solid var(--ash);
      border-left:3px solid var(--ash);border-radius:var(--r-card);padding:20px}
    .finding-top{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
    .finding-id{font-size:12px;color:var(--smoke);letter-spacing:0.06em}
    .finding-sev{font-size:12px;font-weight:500;text-transform:uppercase;letter-spacing:0.08em;
      border:1px solid currentColor;border-radius:var(--r-tag);padding:3px 12px}
    .finding-title{font-family:var(--serif);font-weight:400;font-size:20px;letter-spacing:-0.4px;
      margin-top:10px;color:var(--ink)}
    .finding-desc{font-size:13px;color:var(--graphite);margin-top:6px;line-height:1.45}

    .empty{text-align:center;padding:40px 20px;border:1px solid var(--ash);border-radius:var(--r-card)}
    .empty.sage{border-color:var(--ash)}
    .empty-title{font-family:var(--serif);font-weight:400;font-size:24px;letter-spacing:-0.48px;
      color:var(--ink)}
    .empty-sub{font-size:13px;color:var(--graphite);margin-top:8px}

    /* Reports */
    .reports{display:flex;flex-direction:column;gap:12px}
    .latest-report{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px 16px;
      align-items:start;border:1px solid var(--ash);border-radius:var(--r-card);
      padding:var(--pad-card);background:var(--periwinkle)}
    .latest-title{font-family:var(--serif);font-weight:400;font-size:24px;letter-spacing:-0.48px;
      overflow-wrap:anywhere;color:var(--ink)}
    .latest-meta{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:8px;color:var(--graphite);
      font-size:12px}
    .latest-counts{display:flex;gap:6px;font-size:12px}
    .latest-counts span{border:1px solid rgba(36,36,36,.25);border-radius:var(--r-tag);
      padding:4px 12px;color:var(--ink)}
    .report-card{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px 16px;
      align-items:start;background:transparent;border:1px solid var(--ash);
      border-radius:var(--r-card);padding:var(--pad-card)}
    .report-main{min-width:0}
    .report-title{font-family:var(--serif);font-weight:400;font-size:20px;letter-spacing:-0.4px;
      overflow-wrap:anywhere;color:var(--ink)}
    .report-meta{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:8px;font-size:12px;
      color:var(--smoke)}
    .report-status{border:1px solid var(--ash);border-radius:var(--r-tag);padding:3px 10px;
      text-transform:uppercase;letter-spacing:0.06em}
    .report-status.current{color:var(--good);border-color:rgba(47,107,79,.45)}
    .report-status.stale{color:var(--warn);border-color:rgba(138,101,22,.45)}
    .report-counts{display:flex;gap:6px;font-size:12px}
    .report-counts span{border:1px solid var(--ash);border-radius:var(--r-tag);padding:4px 12px;
      color:var(--graphite)}
    .report-links{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:8px}
    .report-link{font-size:12px;color:var(--ink);text-decoration:none;text-transform:uppercase;
      letter-spacing:0.06em;border:1px solid var(--ink);border-radius:var(--r-pill);
      padding:8px 16px;background:transparent;transition:background .15s ease,color .15s ease}
    .report-link:hover{background:var(--ink);color:var(--parchment)}
    .report-link.disabled{pointer-events:none;color:var(--smoke);border-color:var(--ash)}
    .report-missing{font-size:13px;color:var(--smoke)}

    /* Runs */
    .runs{display:flex;flex-direction:column;gap:12px}
    .run-tabs{display:flex;flex-direction:column;gap:12px}
    .run-tabs .tab-container{display:flex;flex-wrap:wrap;gap:8px;border:0!important}
    .run-tab{font-family:var(--mono)!important;font-size:12px!important;
      color:var(--graphite)!important;background:transparent!important;
      border:1px solid var(--ash)!important;border-radius:var(--r-tag)!important;
      padding:8px 16px!important;line-height:1.2!important;text-transform:uppercase!important;
      letter-spacing:0.05em!important}
    .run-tab.selected{color:var(--parchment)!important;border-color:var(--ink)!important;
      background:var(--ink)!important}
    .run-card{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px 16px;
      align-items:start;background:transparent;border:1px solid var(--ash);
      border-radius:var(--r-card);padding:var(--pad-card)}
    /* State reads from the left edge — a 3px rule, the one place weight is allowed. */
    .run-card.ready{border-left:3px solid var(--good)}
    .run-card.evidence{border-left:3px solid var(--warn)}
    .run-card.blocked{border-left:3px solid var(--crit)}
    .run-main{min-width:0}
    .run-title{font-size:14px;font-weight:500;overflow-wrap:anywhere;color:var(--ink)}
    .run-meta{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:8px;font-size:12px;
      color:var(--smoke)}
    .readiness-score{display:flex;gap:8px;align-items:baseline}
    .readiness-score span{font-size:12px;color:var(--smoke);text-transform:uppercase;
      letter-spacing:0.08em}
    .readiness-score strong{font-family:var(--serif);font-weight:400;font-size:32px;
      letter-spacing:-0.64px;color:var(--ink)}
    .readiness-issue{grid-column:1/-1;display:flex;flex-direction:column;gap:4px;
      padding:16px;border:1px solid var(--ash);border-radius:16px;background:transparent}
    .readiness-issue span{font-weight:500;color:var(--ink)}
    .readiness-issue small{color:var(--graphite);line-height:1.45;font-size:13px}
    .run-history-note{font-size:13px;color:var(--smoke);padding:4px 2px;line-height:1.45}

    /* Control plane */
    .control-stack{display:flex;flex-direction:column;gap:16px}
    .control-editor{border:1px solid var(--ash);border-radius:var(--r-card);
      padding:var(--pad-card);background:transparent}
    .control-editor-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-end;
      margin-bottom:20px}
    .control-editor-title{font-family:var(--serif);font-weight:400;font-size:24px;
      letter-spacing:-0.48px;color:var(--ink)}
    .control-editor-sub{font-size:13px;color:var(--graphite);line-height:1.45;text-align:right}
    .control-editor-gates{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;
      margin-bottom:20px}
    .control-form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
    .field-label{display:flex;flex-direction:column;gap:8px;font-size:12px;text-transform:uppercase;
      letter-spacing:0.08em;color:var(--smoke)}
    .field-label.wide{grid-column:1/-1}
    .control-input,.control-textarea{width:100%;border:1px solid var(--ash);border-radius:16px;
      background:var(--parchment);color:var(--ink);font-family:var(--mono);font-size:13px;
      padding:12px 16px;outline:none;letter-spacing:-0.26px}
    .control-textarea{min-height:96px;resize:vertical;line-height:1.5}
    .control-textarea.small{min-height:148px;font-size:12px}
    .control-textarea{scrollbar-width:none;-ms-overflow-style:none}
    .control-textarea::-webkit-scrollbar{display:none;width:0;height:0}
    .control-input:focus,.control-textarea:focus{border-color:var(--lake);
      box-shadow:0 0 0 2px rgba(43,89,209,.15)}
    .control-select .Select-control{background:var(--parchment);border:1px solid var(--ash);
      border-radius:var(--r-pill)}
    .control-select .Select-value-label,.control-select .Select-placeholder{color:var(--ink)!important}
    .control-actions{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-top:20px}
    /* Ghost pill by default; exactly one Lake Blue action per screen (.primary). */
    .control-button{font-family:var(--mono);font-size:12px;text-transform:uppercase;
      letter-spacing:0.06em;border:1px solid var(--ink);border-radius:var(--r-pill);
      padding:12px 24px;background:transparent;color:var(--ink);cursor:pointer;
      transition:background .15s ease,color .15s ease}
    .control-button:hover{background:var(--ink);color:var(--parchment)}
    .control-button.primary{border-color:var(--lake);background:var(--lake);color:#fff}
    .control-button.primary:hover{background:#1f47ad;border-color:#1f47ad;color:#fff}
    .control-checklist{font-size:13px;color:var(--graphite)}
    .control-status{margin-top:16px;font-size:13px;line-height:1.5}
    .control-status .command-line{margin-top:8px}
    .status-good,.status-warn,.status-bad{border:1px solid var(--ash);border-radius:16px;
      padding:12px 16px;background:transparent}
    .status-good{border-left:3px solid var(--good)}
    .status-warn{border-left:3px solid var(--warn)}
    .status-bad{border-left:3px solid var(--crit)}
    .control-list{display:flex;flex-direction:column;gap:12px}
    .control-card{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(260px,.8fr);
      gap:12px 16px;align-items:start;background:transparent;border:1px solid var(--ash);
      border-radius:var(--r-card);padding:var(--pad-card)}
    .control-main{min-width:0}
    .control-request{font-size:13px;color:var(--graphite);line-height:1.5;margin-top:8px;
      overflow-wrap:anywhere}

    /* Gate status — pipeline nodes, straight from the brief's diagram language. */
    .gate-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
    .gate-status{border:1px solid var(--ash);border-radius:var(--r-tag);padding:10px 16px;
      background:transparent}
    .gate-status span{display:block;font-size:12px;color:var(--smoke);text-transform:uppercase;
      letter-spacing:0.08em}
    .gate-status strong{display:block;margin-top:4px;font-size:13px;font-weight:500;color:var(--ink)}
    .gate-status.ok{border-color:var(--good)}
    .gate-status.open{border-color:var(--warn)}
    .gate-status.missing{border-color:var(--crit)}

    .command-stack{grid-column:1/-1;display:flex;flex-direction:column;gap:8px}
    .command-line{display:block;white-space:normal;overflow-wrap:anywhere;
      border:1px solid var(--ash);border-radius:16px;padding:12px 16px;background:var(--parchment);
      color:var(--ink);font-size:12px;line-height:1.5}
    .command-details{grid-column:1/-1;border:1px solid var(--ash);border-radius:16px;
      padding:12px 16px;background:transparent}
    .command-details summary{cursor:pointer;color:var(--graphite);font-size:12px;
      text-transform:uppercase;letter-spacing:0.06em}
    .command-details .command-stack{margin-top:12px}

    .footer{display:flex;justify-content:space-between;align-items:center;margin-top:var(--gap-section);
      padding-top:20px;border-top:1px solid var(--ash);font-size:12px;color:var(--smoke)}
    .footer-time{font-size:12px}

    @media (max-width:920px){
      .page{padding:16px 20px 0}
      .kpis{grid-template-columns:repeat(2,1fr)}
      .grid{grid-template-columns:1fr}
      .masthead{align-items:flex-start;gap:16px;flex-direction:column}
      .masthead-right{width:100%;justify-content:space-between;gap:16px;flex-wrap:wrap}
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
      .kpi-value{font-size:32px}
    }
    @media (prefers-reduced-motion:reduce){*{transition:none!important;scroll-behavior:auto}}
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
