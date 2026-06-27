"""
AWS Cost Ledger — a live FinOps operator console (Plotly Dash).

Reads the real account through finops_agent (Cost Explorer, Cost Anomaly Detection,
CloudTrail, resource tags). No mock data. Degrades to honest empty states when AWS
credentials are not configured.

Cross-platform — runs the same on Windows, macOS, and Linux (pure Python + the
werkzeug dev server; no OS-specific calls).

Run:
    pip install -r requirements.txt          # (pip3 / python3 on macOS & Linux)
    python .agents/dashboard_app.py          # then open http://127.0.0.1:8050

Optional environment overrides:
    DASH_PORT=8060   # use a different port if 8050 is taken
    DASH_HOST=0.0.0.0  # expose on the LAN (default is localhost-only, which is safer)
"""
import os
import sys
import time
import datetime
from concurrent.futures import ThreadPoolExecutor

# Talk to the active cloud only through the provider abstraction (core/ package).
SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core")
sys.path.insert(0, SCRIPTS)
from providers.base import get_provider, active_cloud  # noqa: E402

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


def assemble(force=False):
    now = time.time()
    if not force and _CACHE["data"] is not None and (now - _CACHE["ts"]) < _TTL:
        return _CACHE["data"]
    data = _fetch()
    _CACHE["data"] = data
    _CACHE["ts"] = now
    return data


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

    return [
        banner,
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
    ]


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
                html.Button("↻ Refresh", id="refresh-btn", n_clicks=0, className="refresh"),
            ]),
        ]),
        dcc.Loading(
            type="default", color=C["terracotta"],
            children=html.Div(id="content", className="content"),
        ),
        html.Footer(className="footer", children=[
            html.Span("Source: AWS Cost Explorer · Cost Anomaly Detection · CloudTrail"),
            html.Span("loading…", id="refresh-time", className="footer-time"),
        ]),
    ])


# ---------------------------------------------------------------------------
# App shell (fonts + global CSS)
# ---------------------------------------------------------------------------
app = dash.Dash(__name__, title="AWS Cost Ledger")
app.layout = app_shell


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
      min-height:100vh; -webkit-font-smoothing:antialiased;
    }
    .page{max-width:1200px;margin:0 auto;padding:2.4rem 2rem 3rem}

    /* Masthead */
    .masthead{display:flex;justify-content:space-between;align-items:center;
      padding-bottom:1.6rem;border-bottom:1px solid var(--line);margin-bottom:1.8rem}
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

    .empty{text-align:center;padding:2.4rem 1rem;border:1px dashed var(--line);border-radius:12px}
    .empty.sage{border-color:rgba(141,161,137,.3)}
    .empty-title{font-family:'Outfit',sans-serif;font-weight:600;font-size:1.05rem;color:var(--sage)}
    .empty-sub{font-size:.84rem;color:var(--muted);margin-top:.3rem}

    .footer{display:flex;justify-content:space-between;align-items:center;margin-top:2rem;
      padding-top:1.3rem;border-top:1px solid var(--line);font-size:.76rem;color:var(--faint)}
    .footer-time{font-family:'JetBrains Mono',monospace}

    @media (max-width:920px){
      .kpis{grid-template-columns:repeat(2,1fr)}
      .grid{grid-template-columns:1fr}
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
              f"        DASH_PORT=8060 python .agents/dashboard_app.py", file=sys.stderr)
        sys.exit(1)

    print(f"\n  AWS Cost Ledger  ->  http://{host}:{port}   (Ctrl+C to stop)\n")
    # Werkzeug's built-in server behaves identically on Windows / macOS / Linux.
    # hot-reload off: some Dash renderers poll an internal endpoint that 500s without a callback.
    app.run(host=host, port=port, debug=False, dev_tools_hot_reload=False)
