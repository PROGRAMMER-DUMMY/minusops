"""
reporter.py — versioned deploy report bundle, keyed by plan-hash.

After a `terraform plan -out=tfplan`, produces:

    .agents/reports/<plan_hash[:12]>/
      manifest.json      hash, timestamp, git commit, counts, cost, dir, cloud
      plan.json          raw `terraform show -json`
      architecture.svg   spec-conformant diagram (baseline; an agent may overwrite
                         with a richer one following docs/architecture_svg_spec.md)
      cost.json          per-run + monthly estimate
      report.html        terracotta report embedding the SVG + plan + cost
      report.pdf         rendered from the HTML via headless Edge/Chrome (HTML kept if absent)

The plan-hash is the version key: one plan -> one immutable report folder. git versions the
.tf; the plan-hash versions the report (manifest records the git commit linking them).

Usage:  python core/reporter.py --dir templates/aws/medallion-pipeline
"""
import os
import sys
import json
import html
import hashlib
import argparse
import datetime
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from providers.base import active_cloud  # noqa: E402

WORKSPACE = os.getcwd()
REPORTS = os.path.join(WORKSPACE, ".agents", "reports")
SPEC = "docs/architecture_svg_spec.md"
PLAN_FILE = "tfplan"

# --- tier map (mirrors docs/architecture_svg_spec.md §5) -------------------
TIERS = ["sources", "storage", "compute", "orchestration", "observability", "security"]
TIER_HUE = {"sources": "#d4a373", "storage": "#d95d39", "compute": "#e8825f",
            "orchestration": "#8da189", "observability": "#cb9a3e", "security": "#b09c93"}
TIER_X = {"sources": 24, "storage": 272, "compute": 520, "orchestration": 768, "observability": 1016}
ACTION_TINT = {"create": "#8da189", "update": "#cb9a3e", "delete": "#d95d39", "no-op": "#b09c93"}


def _tier_for(rtype):
    t = rtype.lower()
    if any(k in t for k in ("iam", "kms", "_policy", "_role", "public_access_block", "encryption")):
        return "security"
    if any(t.startswith(k) or k in t for k in ("cloudwatch_event", "s3_bucket_notification", "api_gateway", "sns_topic_subscription")):
        return "sources"
    if any(k in t for k in ("sfn_state_machine", "scheduler", "mwaa", "datapipeline", "stepfunction")):
        return "orchestration"
    if any(k in t for k in ("glue_job", "glue_crawler", "lambda", "emr", "ecs", "batch")):
        return "compute"
    if any(k in t for k in ("cloudwatch_metric_alarm", "cloudwatch_log_group", "sns", "budgets", "_ce_", "anomaly")):
        return "observability"
    if any(k in t for k in ("s3_bucket", "glue_catalog", "dynamodb", "sqs_queue", "rds", "redshift")):
        return "storage"
    return "compute"  # honest fallback (spec §5)


def _humanize(rtype):
    parts = rtype.split("_")
    out = []
    for p in parts:
        out.append("AWS" if p == "aws" else p.capitalize())
    return " ".join(out)


def run(args, capture=True, timeout=None):
    try:
        res = subprocess.run(args, text=True, capture_output=capture, timeout=timeout)
        return res.returncode, (res.stdout or ""), (res.stderr or "")
    except FileNotFoundError:
        return 127, "", f"not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"


def load_plan(dir_):
    rc, out, err = run(["terraform", f"-chdir={dir_}", "show", "-json", PLAN_FILE])
    if rc != 0:
        return None, err.strip() or "terraform show failed"
    try:
        return json.loads(out), ""
    except json.JSONDecodeError as e:
        return None, f"bad plan json: {e}"


def plan_hash(data):
    """Must match core/plan_gate.py._plan_hash."""
    payload = {"resource_changes": data.get("resource_changes", []),
               "output_changes": data.get("output_changes", {})}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def summarize(data):
    """-> (rows, counts). rows: dicts {address,type,name,module,tier,action}."""
    rows, counts = [], {"create": 0, "update": 0, "delete": 0, "no-op": 0}
    for rc in data.get("resource_changes", []):
        actions = rc.get("change", {}).get("actions", ["no-op"])
        action = ("delete" if "delete" in actions and "create" not in actions else
                  "create" if actions == ["create"] else
                  "update" if "update" in actions or set(actions) == {"create", "delete"} else
                  "no-op")
        rtype = rc.get("type", "unknown")
        rows.append({
            "address": rc.get("address", rtype),
            "type": rtype, "name": rc.get("name", ""),
            "module": rc.get("module_address", ""),
            "tier": _tier_for(rtype), "action": action,
        })
        counts[action] = counts.get(action, 0) + 1
    rows.sort(key=lambda r: r["address"])
    return rows, counts


# --- baseline SVG (conforms to docs/architecture_svg_spec.md) --------------
def build_svg(rows, template, cloud, short_hash, ts):
    def esc(s):
        return html.escape(str(s), quote=True)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 760" width="100%" role="img">',
        f'<title>Architecture — {esc(template)}</title>',
        '<desc>Auto-generated deploy architecture (baseline, conforms to architecture_svg_spec.md v1).</desc>',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#fbf7f4"/></marker>'
        '<style>.t{font:600 22px system-ui,sans-serif;fill:#fbf7f4}.s{font:500 12px ui-monospace,monospace;fill:#b09c93}'
        '.h{font:600 13px system-ui,sans-serif;fill:#fbf7f4;letter-spacing:.1em}'
        '.nt{font:600 12px system-ui,sans-serif;fill:#fbf7f4}.nn{font:400 10px ui-monospace,monospace;fill:#b09c93}'
        '.lg{font:500 11px system-ui,sans-serif;fill:#b09c93}</style></defs>',
        '<g id="bg"><rect width="1280" height="760" fill="#14110f"/></g>',
        '<g id="titlebar"><rect width="1280" height="64" fill="#1c1714"/>'
        '<rect y="63" width="1280" height="1" fill="rgba(217,93,57,.18)"/>'
        f'<text class="t" x="24" y="34">{esc(template)}</text>'
        f'<text class="s" x="24" y="52">{esc(cloud)} · plan {esc(short_hash)} · {esc(ts)}</text></g>',
    ]

    by_tier = {t: [r for r in rows if r["tier"] == t] for t in TIERS}
    # edges: light flow between consecutive non-empty columns
    cols = [t for t in ["sources", "storage", "compute", "orchestration", "observability"] if by_tier[t]]
    edges = ['<g id="edges">']
    for a, b in zip(cols, cols[1:]):
        x1, x2 = TIER_X[a] + 232, TIER_X[b]
        edges.append(f'<path d="M{x1},360 C{(x1 + x2) / 2},360 {(x1 + x2) / 2},360 {x2},360" '
                     'stroke="#fbf7f4" stroke-width="1.5" fill="none" marker-end="url(#arrow)" opacity="0.5"/>')
    edges.append('</g>')
    parts += edges

    # tier columns
    for t in ["sources", "storage", "compute", "orchestration", "observability"]:
        x = TIER_X[t]
        parts.append(f'<g id="tier-{t}"><text class="h" x="{x}" y="92">{t.upper()}</text>'
                     f'<rect x="{x}" y="100" width="232" height="2" fill="{TIER_HUE[t]}"/>')
        items = by_tier[t]
        ch, gap = (60, 14) if len(items) <= 8 else (44, 8)
        y = 108
        for r in items:
            tint = ACTION_TINT[r["action"]]
            parts.append(
                f'<g class="node" data-address="{esc(r["address"])}" data-action="{r["action"]}" '
                f'transform="translate({x},{y})">'
                f'<rect width="232" height="{ch}" rx="12" fill="#1c1714" stroke="{TIER_HUE[t]}" stroke-width="1.5"/>'
                f'<rect width="4" height="{ch}" rx="2" fill="{tint}"/>'
                f'<circle cx="26" cy="{ch // 2}" r="9" fill="{TIER_HUE[t]}"/>'
                f'<text class="nt" x="44" y="{ch // 2 - 3}">{esc(_humanize(r["type"]))}</text>'
                f'<text class="nn" x="44" y="{ch // 2 + 12}">{esc(r["name"])}</text></g>')
            y += ch + gap
        parts.append('</g>')

    # security band
    sec = by_tier["security"]
    parts.append('<g id="band-security"><rect x="24" y="632" width="1224" height="56" rx="10" fill="none" '
                 'stroke="#b09c93" stroke-dasharray="4 4"/><text class="h" x="40" y="652">SECURITY &amp; IAM</text>')
    cx = 200
    for r in sec[:14]:
        parts.append(f'<g class="node" data-address="{esc(r["address"])}"><rect x="{cx}" y="646" width="74" height="28" '
                     f'rx="8" fill="#1c1714" stroke="#b09c93" stroke-width="1"/>'
                     f'<text class="nn" x="{cx + 8}" y="664">{esc(r["name"][:9])}</text></g>')
        cx += 82
    parts.append('</g>')

    # legend
    parts.append('<g id="legend"><text class="lg" x="24" y="712">Tiers:</text>')
    lx = 70
    for t in ["sources", "storage", "compute", "orchestration", "observability"]:
        parts.append(f'<rect x="{lx}" y="703" width="12" height="12" rx="3" fill="{TIER_HUE[t]}"/>'
                     f'<text class="lg" x="{lx + 18}" y="713">{t.capitalize()}</text>')
        lx += 70 + len(t) * 6
    parts.append('<text class="lg" x="24" y="740">Status: green=create · gold=update · terracotta=delete</text></g>')
    parts.append('</svg>')
    return "\n".join(parts)


# --- cost (reuse budget_calculator defaults; honest about assumptions) -----
def estimate_cost():
    try:
        import budget_calculator as bc
        b = bc.calculate_detailed_budget("GLUE", 4, 6, 24, 200.0, 150, 15.0)
        f = b["billing_forecast_usd"]
        runs_monthly = b["parameters"]["monthly_runs"]
        per_run = round(f["primary_compute_cost"] / runs_monthly, 4) if runs_monthly else 0.0
        return {"ok": True, "assumptions": b["parameters"], "monthly": f,
                "per_run_compute_usd": per_run}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- HTML report -----------------------------------------------------------
def build_html(template, cloud, short_hash, ts, rows, counts, cost, svg):
    def esc(s):
        return html.escape(str(s))

    rowhtml = "".join(
        f'<tr><td class="mono">{esc(r["address"])}</td><td>{esc(_humanize(r["type"]))}</td>'
        f'<td><span class="badge {r["action"]}">{r["action"]}</span></td></tr>' for r in rows) \
        or '<tr><td colspan="3" class="muted">No resource changes in this plan.</td></tr>'

    if cost.get("ok"):
        m = cost["monthly"]
        costhtml = (
            f'<div class="kpis"><div class="kpi"><div class="kl">Per run (compute)</div>'
            f'<div class="kv">${cost["per_run_compute_usd"]:.4f}</div></div>'
            f'<div class="kpi"><div class="kl">Monthly total</div>'
            f'<div class="kv">${m["monthly_grand_total"]:.2f}</div></div>'
            f'<div class="kpi"><div class="kl">Compute / mo</div>'
            f'<div class="kv">${m["primary_compute_cost"]:.2f}</div></div>'
            f'<div class="kpi"><div class="kl">Storage / mo</div>'
            f'<div class="kv">${m["s3_storage"]:.2f}</div></div></div>'
            f'<p class="muted small">Estimate using default assumptions '
            f'({esc(cost["assumptions"]["daily_runs"])} runs/day, '
            f'{esc(cost["assumptions"]["job_duration_minutes"])} min/run); live pricing where available.</p>')
    else:
        costhtml = f'<p class="muted">Cost estimate unavailable: {esc(cost.get("error", ""))}</p>'

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Deploy Report — {esc(template)}</title><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#14110f;color:#fbf7f4;font-family:Inter,system-ui,sans-serif;padding:2.4rem}}
.mono{{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:.82rem}}
h1{{font-size:1.6rem;font-weight:700}}h2{{font-size:1.1rem;margin:1.6rem 0 .7rem;color:#e8825f}}
.sub{{color:#b09c93;font-family:ui-monospace,monospace;font-size:.85rem;margin-top:.3rem}}
.panel{{background:rgba(40,33,30,.55);border:1px solid rgba(217,93,57,.18);border-radius:14px;padding:1.2rem;margin-top:1rem}}
svg{{width:100%;height:auto;border-radius:12px}}
table{{width:100%;border-collapse:collapse;margin-top:.5rem}}
th,td{{text-align:left;padding:.5rem .6rem;border-bottom:1px solid rgba(255,255,255,.06);font-size:.85rem}}
th{{color:#b09c93;text-transform:uppercase;font-size:.7rem;letter-spacing:.08em}}
.badge{{padding:.12rem .5rem;border-radius:20px;font-size:.72rem;font-weight:600}}
.badge.create{{background:rgba(141,161,137,.18);color:#8da189}}
.badge.update{{background:rgba(203,154,62,.18);color:#cb9a3e}}
.badge.delete{{background:rgba(217,93,57,.18);color:#d95d39}}
.badge.no-op{{background:rgba(176,156,147,.15);color:#b09c93}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem}}
.kpi{{background:rgba(0,0,0,.2);border:1px solid rgba(217,93,57,.15);border-radius:12px;padding:1rem}}
.kl{{color:#b09c93;font-size:.7rem;text-transform:uppercase;letter-spacing:.06em}}
.kv{{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:1.4rem;margin-top:.4rem}}
.counts span{{margin-right:1.2rem;font-family:ui-monospace,monospace}}
.muted{{color:#b09c93}}.small{{font-size:.78rem;margin-top:.6rem}}
footer{{margin-top:2rem;padding-top:1rem;border-top:1px solid rgba(217,93,57,.18);color:#6f635c;font-size:.78rem}}
</style></head><body>
<h1>Deploy Report — {esc(template)}</h1>
<div class="sub">{esc(cloud)} · plan {esc(short_hash)} · {esc(ts)}</div>
<div class="counts panel"><span style="color:#8da189">+{counts['create']} create</span>
<span style="color:#cb9a3e">~{counts['update']} update</span>
<span style="color:#d95d39">-{counts['delete']} delete</span>
<span class="muted">{counts['no-op']} no-op</span></div>
<h2>Architecture</h2><div class="panel">{svg}</div>
<h2>Estimated cost per run</h2><div class="panel">{costhtml}</div>
<h2>Planned changes</h2><div class="panel"><table>
<thead><tr><th>Resource</th><th>Type</th><th>Action</th></tr></thead><tbody>{rowhtml}</tbody></table></div>
<footer>Generated by MinusOps reporter · architecture conforms to {esc(SPEC)} · report keyed by plan-hash {esc(short_hash)}</footer>
</body></html>"""


def find_browser():
    cands = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "microsoft-edge", "google-chrome", "chromium",
    ]
    for c in cands:
        if os.path.isabs(c) and os.path.exists(c):
            return c
        if not os.path.isabs(c):
            rc, _, _ = run([c, "--version"], timeout=4)
            if rc == 0:
                return c
    return None


def render_pdf(html_path, pdf_path):
    browser = find_browser()
    if not browser:
        return False, "no headless browser (Edge/Chrome) found"
    rc, _, err = run([browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                      f"--print-to-pdf={pdf_path}", html_path], timeout=40)
    if rc == 0 and os.path.exists(pdf_path):
        return True, browser
    return False, err or "render failed"


def git_commit():
    rc, out, _ = run(["git", "rev-parse", "--short", "HEAD"])
    return out.strip() if rc == 0 else None


def generate(dir_):
    data, err = load_plan(dir_)
    if data is None:
        print(f"[reporter] {err} — run `terraform plan -out=tfplan` first.", file=sys.stderr)
        return None
    h = plan_hash(data)
    short = h[:12]
    cloud = active_cloud()
    template = os.path.basename(dir_.rstrip("/\\"))
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rows, counts = summarize(data)
    cost = estimate_cost()
    svg = build_svg(rows, template, cloud, short, ts)
    htmldoc = build_html(template, cloud, short, ts, rows, counts, cost, svg)

    out = os.path.join(REPORTS, short)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)
    with open(os.path.join(out, "architecture.svg"), "w", encoding="utf-8") as f:
        f.write(svg)
    with open(os.path.join(out, "cost.json"), "w", encoding="utf-8") as f:
        json.dump(cost, f, indent=2)
    html_path = os.path.join(out, "report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(htmldoc)

    pdf_path = os.path.join(out, "report.pdf")
    pdf_ok, pdf_info = render_pdf(html_path, pdf_path)

    manifest = {
        "plan_hash": h, "short": short, "template": template, "cloud": cloud,
        "generated_at": ts, "git_commit": git_commit(), "dir": dir_,
        "counts": counts, "resource_total": len(rows),
        "cost": cost if cost.get("ok") else {"ok": False},
        "pdf": pdf_ok, "files": ["plan.json", "architecture.svg", "cost.json", "report.html"]
                       + (["report.pdf"] if pdf_ok else []),
    }
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # append to the version index
    os.makedirs(REPORTS, exist_ok=True)
    idx = os.path.join(REPORTS, "INDEX.md")
    if not os.path.exists(idx):
        with open(idx, "w", encoding="utf-8") as f:
            f.write("# Deploy Reports (newest first)\n\n| plan-hash | template | when | +/~/- | commit |\n| :-- | :-- | :-- | :-- | :-- |\n")
    line = (f"| `{short}` | {template} | {ts} | "
            f"+{counts['create']}/~{counts['update']}/-{counts['delete']} | {manifest['git_commit'] or '-'} |\n")
    existing = open(idx, encoding="utf-8").read().splitlines(keepends=True)
    head, tail = existing[:5], existing[5:]
    with open(idx, "w", encoding="utf-8") as f:
        f.writelines(head + [line] + tail)

    print(f"[reporter] report -> {os.path.relpath(out, WORKSPACE)}  "
          f"(+{counts['create']}/~{counts['update']}/-{counts['delete']}, "
          f"PDF: {'yes via ' + os.path.basename(pdf_info) if pdf_ok else 'no — ' + str(pdf_info)})")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Versioned deploy report (plan + cost + architecture)")
    ap.add_argument("--dir", default="templates/aws/medallion-pipeline", help="Terraform directory with a tfplan")
    args = ap.parse_args()
    sys.exit(0 if generate(args.dir) else 1)
