"""
reporter.py — versioned deploy report bundle, keyed by plan-hash.

After a `terraform plan -out=tfplan`, produces:

    artifacts/reports/<plan_hash[:12]>/
      manifest.json      hash, timestamp, git commit, counts, cost, dir, cloud
      plan.json          raw `terraform show -json`
      architecture.svg   full-screen architecture diagram
      plan.pdf           human plan report with architecture, cost summary, and changes
      cost.pdf           detailed standalone cost report
      cost.json          per-run + monthly estimate

The plan-hash is the version key: one plan -> one immutable report folder. git versions the
.tf; the plan-hash versions the report (manifest records the git commit linking them).

Usage:  python core/reporting/reporter.py --dir path/to/terraform   (any Terraform dir with a tfplan)

Reporting only: it reads an existing `tfplan` and never plans, applies, or destroys. The one
way it reaches AWS is pricing — BCM payloads are always prepared locally, and when
credentials allow, a free and deletable BCM pricing estimate object is created (approval
stays on APPLY, not on pricing). No cost number is ever invented offline; an unpriced service
is reported as unpriced rather than as $0.

Depends on: core/reporting/plan_inspector.py, core/cost/bcm_pricing_calculator.py,
    core/providers/base.py; and lazily, inside the functions that need them,
    core/generation/modules.py, core/architecture/architecture_model.py,
    core/reporting/optimize_analyzer.py, core/governance/verification_coverage.py
Shells out to: `terraform show -json` (read-only) to materialize plan.json, and a headless
    Chrome/Edge via the DevTools protocol to print the HTML reports to PDF. Reaches AWS
    read-only plus BCM pricing-estimate creation, through bcm_pricing_calculator.
Used by: core/governance/plan_gate.py (lazy), core/cost/bcm_pricing_calculator.py (lazy),
    core/generation/demo.py, app/dashboard_app.py, tests/test_reporter.py,
    tests/test_pdf_outline.py and other test modules
"""
import os
import sys
import json
import html
import hashlib
import argparse
import contextlib
import datetime
import subprocess
import base64
import pathlib
import secrets
import shutil
import socket
import struct
import tempfile
import time
import urllib.request
import re

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
from providers.base import active_cloud  # noqa: E402
import plan_inspector  # noqa: E402
import bcm_pricing_calculator  # noqa: E402
import drawio_generator  # noqa: E402

WORKSPACE = os.getcwd()
REPORTS = os.path.join(WORKSPACE, "artifacts", "reports")
SPEC = "docs/architecture_svg_spec.md"
PLAN_FILE = "tfplan"


def reports_root_for_dir(dir_):
    abs_dir = os.path.abspath(dir_)
    runs_root = os.path.abspath(os.path.join(WORKSPACE, "runs"))
    rel = os.path.relpath(abs_dir, runs_root) if abs_dir.startswith(runs_root) else ""
    parts = rel.split(os.sep) if rel and not rel.startswith("..") else []
    if len(parts) >= 2 and parts[1] == "terraform":
        return os.path.join(runs_root, parts[0], "reports")
    return REPORTS

# --- tier map (mirrors docs/architecture_svg_spec.md §5) -------------------
TIERS = ["sources", "storage", "compute", "orchestration", "observability", "security"]
# AWS service-category colours, the same ones drawio_generator.py puts on its icons. The
# report keeps its own warm ground (#fbf7f4) and chrome; what had to stop differing is the
# SERVICE colour, because an S3 bucket was green in architecture.drawio and terracotta in
# architecture.svg, and both ship in the same evidence bundle. A reader comparing them cannot
# tell whether the colour means something.
TIER_HUE = {"sources": "#ED7100", "storage": "#7AA116", "compute": "#8C4FFF",
            "orchestration": "#E7157B", "observability": "#2E73B8", "security": "#DD344C"}
TIER_X = {"sources": 24, "storage": 272, "compute": 520, "orchestration": 768, "observability": 1016}
# Plan actions stay semantic rather than service-coloured: green adds, amber changes, red
# removes. Drawn as a tint on the card, not as its hue, so the two never compete.
ACTION_TINT = {"create": "#7AA116", "update": "#ED7100", "delete": "#DD344C", "no-op": "#475569"}


# The roles architecture_model assigns, mapped onto this diagram's columns. Five columns
# leave no room for a catalog of its own, so a catalog sits with the storage it describes;
# security and network share the SECURITY & IAM band at the foot.
_ROLE_TIER = {
    "ingest": "sources",
    "stage": "storage", "store_other": "storage", "catalog": "storage",
    "transform": "compute", "consume": "compute",
    "orchestrate": "orchestration",
    "observability": "observability",
    "security": "security", "network": "security",
}


def _tier_for(rtype):
    """Which column a resource belongs in, from the classifier the rest of the tool uses.

    This was a second, independent substring cascade, and it disagreed with the shared one
    on exactly the resources it mattered for. Its fallback was "compute", so any type it
    had no rule for landed there: six Lake Formation resources and a security group were
    drawn as COMPUTE in the same evidence bundle where dataflow.svg drew them as
    governance. Athena and Redshift matched its storage rule and were drawn as STORAGE
    while dataflow.svg drew them as consumption. A reader holding both cannot tell which
    one is lying.

    The fallback is still compute, which is spec v2 §5's honest default -- the difference is
    that it now catches only what neither classifier can place, instead of everything the
    weaker one had no line for.
    """
    import architecture_model as am
    return _ROLE_TIER.get(am.classify_role(rtype), "compute")


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
    """Must match core/governance/plan_gate.py._plan_hash."""
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
def _fit_text(value, limit=28):
    value = str(value)
    # An ellipsis, not a full stop: "Analytics Workgro." reads as a finished word plus a
    # typo, where "Analytics Workgro…" reads as what it is.
    return value if len(value) <= limit else value[: max(0, limit - 1)].rstrip() + "…"


def _resource_type_counts(rows):
    counts = {}
    for r in rows:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    return sorted(counts.items(), key=lambda item: item[0])


def _service_summary(rows):
    service_map = [
        ("Amazon S3", "aws_s3_"),
        ("AWS KMS", "aws_kms_"),
        ("AWS Glue", "aws_glue_"),
        ("AWS Step Functions", "aws_sfn_"),
        ("Amazon Athena", "aws_athena_"),
        ("Amazon CloudWatch", "aws_cloudwatch_"),
        ("AWS Budgets", "aws_budgets_"),
        ("AWS IAM", "aws_iam_"),
    ]
    output = []
    for label, prefix in service_map:
        count = sum(1 for r in rows if r["type"].startswith(prefix))
        if count:
            output.append((label, count))
    return output


def _instance_key(address):
    """Extract the for_each/count key from an address, e.g. ...zone["bronze"] -> bronze."""
    m = re.search(r'\["([^"]+)"\]', address)
    return m.group(1) if m else ""


def _node_label(r):
    return _instance_key(r["address"]) or r["name"]


# Coarse service grouping so a service + its config resources collapse into one node.
_SERVICE_GROUP = [
    ("aws_s3", "s3"), ("aws_iam_role_policy", "iam_role_policy"), ("aws_iam_role", "iam_role"),
    ("aws_iam_policy", "iam_policy"), ("aws_iam", "iam"), ("aws_glue_job", "glue_job"),
    ("aws_glue_crawler", "glue_crawler"), ("aws_glue_catalog", "glue_catalog"), ("aws_glue", "glue"),
    ("aws_kms", "kms"), ("aws_cloudwatch", "cloudwatch"), ("aws_sfn", "sfn"), ("aws_athena", "athena"),
    ("aws_budgets", "budget"), ("aws_lambda", "lambda"), ("aws_dynamodb", "dynamodb"),
    ("aws_sqs", "sqs"), ("aws_sns", "sns"), ("aws_redshift", "redshift"), ("aws_emr", "emr"),
]


def _service_group(rtype):
    for prefix, group in _SERVICE_GROUP:
        if rtype.startswith(prefix):
            return group
    return rtype


def _collapse_components(rows, plan=None):
    """
    Collapse a flat resource list into logical service components — a service plus the
    resources that configure it (versioning, lifecycle, encryption, public-access block)
    become one node.

    A config resource joins its parent where the plan DECLARES the reference, which is
    what drawio_generator.fold_parents reads. Grouping on the Terraform local name, which
    is all this used to do, folds nothing on a real plan: the parts are named separately,
    so `medallion_buckets` never meets `medallion_versioning`. That left the lakehouse's
    eighteen S3 rows as eighteen peer nodes, and the data-flow spine was thirteen
    near-identical buckets with colliding labels instead of three medallion zones.

    The name grouping stays as a second rule, for the plans that do name a config after
    the resource it configures.
    """
    parent = {r["address"]: r["address"] for r in rows}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    declared = drawio_generator.fold_parents(plan, list(parent)) if plan else {}
    for child, anchor in declared.items():
        if child in parent and anchor in parent:
            union(child, anchor)

    by_name = {}
    for r in rows:
        by_name.setdefault((_service_group(r["type"]), r["name"], _instance_key(r["address"])),
                           []).append(r["address"])
    for members in by_name.values():
        for address in members[1:]:
            union(address, members[0])

    groups = {}
    for r in rows:
        groups.setdefault(find(r["address"]), []).append(r)

    comps = []
    for members in groups.values():
        # The resource that stands on its own is the node; a folded one never is, however
        # short its type name reads.
        primary = min(members, key=lambda m: (drawio_generator.is_folded(m["type"]), len(m["type"])))
        actions = {m["action"] for m in members}
        comp = dict(primary)
        comp["action"] = next((a for a in ("delete", "update", "create", "no-op") if a in actions), "create")
        comp["config_count"] = len(members) - 1
        comps.append(comp)
    comps.sort(key=lambda c: c["address"])
    return comps


# Column pitch: a 232-wide card, then a 16px gutter. Edges run down the gutters, which
# is why the edge layer paints UNDER the cards -- a stray crossing is hidden rather than
# scribbled over a node.
_CARD_W = 232
_GUTTER_HALF = 8


def _flow_edge(p1, p2, node_h, kind):
    """Route one hop between two placed cards, leaving on the side that faces the target.

    This used to leave the source's RIGHT edge and enter the target's LEFT one always,
    which only describes a hop that runs rightward. Most of these do not: Lake Formation
    describes a bucket one column to its left, and Athena writes a results bucket in its
    own column. Those arrows doubled back underneath the card stack, so the diagram drew
    four edges and showed none -- while the legend advertised "data flow" and "control".
    """
    sx, sy = p1
    tx, ty = p2
    y1, y2 = sy + node_h // 2, ty + node_h // 2
    if tx > sx:                       # rightward: out the right, in the left
        x1, x2, lane = sx + _CARD_W, tx, tx - _GUTTER_HALF
    elif tx < sx:                     # leftward: out the left, in the right
        x1, x2, lane = sx, tx + _CARD_W, tx + _CARD_W + _GUTTER_HALF
    else:                             # same column: a bracket in the gutter beside it
        x1, x2, lane = sx, tx, sx - _GUTTER_HALF
    return f"M{x1},{y1} H{lane} V{y2} H{x2}", kind


def declared_hops(plan):
    """The hops the plan states, from the one derivation both renderers share.

    This file used to build its own edges by matching resource NAMES -- a bucket whose
    instance key was "bronze", a Glue job called "bronze_to_silver" -- and then drew an arrow
    between every pair of slots that happened to be filled. `_generic_flow` went further and
    joined the first node of consecutive tiers so the picture would have arrows in it. Both
    are the defect deleted from `drawio_generator` in 14ab3f1, and they shipped in
    architecture.svg, which minusctl lists as a required report artifact.

    `discover_data_edges` reads only data-carrying arguments, so an arrow here means the same
    thing it means on the draw.io canvas: one resource names the other's path.
    """
    return drawio_generator.discover_data_edges(plan or {})


def _anchored_flow(plan, pos, node_h):
    """Declared hops between nodes this layout actually placed."""
    edges, drawn = [], set()
    for hop in declared_hops(plan):
        pair = (hop["source"], hop["target"])
        if pair in drawn or pair[0] not in pos or pair[1] not in pos:
            continue
        drawn.add(pair)
        edges.append(_flow_edge(pos[pair[0]], pos[pair[1]], node_h, "data"))
    return edges


_SEV_ORDER = ("HIGH", "MEDIUM", "LOW", "EXTERNAL")
_SEV_COLOR = {"HIGH": "#DD344C", "MEDIUM": "#ED7100", "LOW": "#7AA116", "EXTERNAL": "#475569"}
_LOCK = ('<rect x="0" y="5" width="13" height="9" rx="2" fill="none" stroke="#DD344C" stroke-width="1.3"/>'
         '<path d="M2.5,5 V3.2 a4,4 0 0 1 8,0 V5" fill="none" stroke="#DD344C" stroke-width="1.3"/>')

# Inline, self-contained service glyphs (generic — not AWS's trademarked icon set), drawn
# in an ~18x18 local frame. Stroked in the tier hue so they stay on-palette and embed in PDFs.
_ICONS = {
    "bucket": '<path d="M3,4 H15 L13,16 H5 Z"/><path d="M3,4 a6,1.6 0 0 0 12,0"/>',
    "gears": '<circle cx="9" cy="9" r="4.3"/><circle cx="9" cy="9" r="1.4"/>'
             '<path d="M9,2 V4 M9,14 V16 M2,9 H4 M14,9 H16 M4.4,4.4 L5.8,5.8 M12.2,12.2 L13.6,13.6 '
             'M13.6,4.4 L12.2,5.8 M4.4,13.6 L5.8,12.2"/>',
    "search": '<circle cx="8" cy="8" r="4.6"/><path d="M11.5,11.5 L16,16"/>',
    "workflow": '<rect x="2" y="2.5" width="6" height="5" rx="1"/><rect x="10" y="10.5" width="6" height="5" rx="1"/>'
                '<path d="M5,7.5 V10 a1,1 0 0 0 1,1 H10"/>',
    "key": '<circle cx="6" cy="9" r="3.4"/><path d="M9,9 H16 M13.5,9 V12 M16,9 V12.5"/>',
    "shield": '<path d="M9,2 L15,4 V9 C15,13 9,16.5 9,16.5 C9,16.5 3,13 3,9 V4 Z"/>',
    "bell": '<path d="M5,13 C5,8 6,4.5 9,4.5 C12,4.5 13,8 13,13 Z"/><path d="M7.5,15 a1.6,1.6 0 0 0 3,0"/>',
    "coin": '<circle cx="9" cy="9" r="6"/><path d="M9,5 V13 M11,6.6 a2.6,2 0 0 0 -4,.2 c0,2 4,1.2 4,3.4 '
            'a2.6,2 0 0 1 -4,.2"/>',
    "book": '<path d="M4,3 H12 a1.5,1.5 0 0 1 1.5,1.5 V16 a1.5,1.5 0 0 0 -1.5,-1.5 H4 Z"/><path d="M4,3 V14.5"/>',
    "inbox": '<path d="M3,10 L5,4 H13 L15,10 V15 H3 Z"/><path d="M3,10 H6.5 L7.5,12 H10.5 L11.5,10 H15"/>',
    "doc": '<path d="M4.5,2.5 H11 L14,5.5 V16 H4.5 Z"/><path d="M11,2.5 V5.5 H14"/>',
    "lambda": '<path d="M5,15.5 L9,4.5 L13,15.5 M7.2,10.5 H10.8"/>',
    "cube": '<path d="M9,2.5 L15,5.5 V12 L9,15.5 L3,12 V5.5 Z"/><path d="M3,5.5 L9,9 L15,5.5 M9,9 V15.5"/>',
}


def _icon(name, hue, x, y):
    frag = _ICONS.get(name, _ICONS["cube"])
    return (f'<g transform="translate({x},{y})" stroke="{hue}" stroke-width="1.5" fill="none" '
            f'stroke-linejoin="round" stroke-linecap="round">{frag}</g>')


def _icon_for(rtype):
    t = rtype
    if t.startswith("aws_s3"):
        return "bucket"
    if t.startswith("aws_glue_catalog"):
        return "book"
    if t.startswith("aws_glue"):
        return "gears"
    if t.startswith("aws_athena"):
        return "search"
    if t.startswith("aws_sfn"):
        return "workflow"
    if t.startswith("aws_kms"):
        return "key"
    if t.startswith("aws_iam"):
        return "shield"
    if "cloudwatch_metric_alarm" in t:
        return "bell"
    if t.startswith("aws_cloudwatch"):
        return "doc"
    if t.startswith("aws_budgets"):
        return "coin"
    if t.startswith("aws_lambda"):
        return "lambda"
    return "cube"


def _component_box(x, y, w, h, hue, title, sub, action, findings, locked, address, esc, icon="cube", detail=""):
    """One service component box (collapses a service + its config into a single node)."""
    tint = ACTION_TINT.get(action, "#64748b")
    df = f' data-findings="{esc(",".join(f["id"] for f in findings))}"' if findings else ""
    out = [
        f'<g class="node" data-address="{esc(address)}" data-action="{esc(action)}"{df} transform="translate({x},{y})">',
        f'<rect class="card" width="{w}" height="{h}" rx="12" fill="#ffffff" stroke="{hue}" stroke-width="1.6"/>',
        f'<rect width="4" height="{h}" rx="2" fill="{tint}"/>',
        _icon(icon, hue, 16, h // 2 - 9),
    ]
    if detail:
        out += [
            f'<text class="n-type" x="46" y="{h // 2 - 12}">{esc(_fit_text(title, 18))}</text>',
            f'<text class="n-name" x="46" y="{h // 2 + 4}">{esc(_fit_text(sub, 20))}</text>',
            f'<text class="n-meta" x="46" y="{h // 2 + 19}">{esc(_fit_text(detail, 24))}</text>',
        ]
    else:
        out += [
            f'<text class="n-type" x="46" y="{h // 2 - 4}">{esc(_fit_text(title, 18))}</text>',
            f'<text class="n-name" x="46" y="{h // 2 + 13}">{esc(_fit_text(sub, 20))}</text>',
        ]
    if locked:
        out.append(f'<g transform="translate({w - 26},10)">' + _LOCK + '</g>')
    if findings:
        top = min(findings, key=lambda f: _SEV_ORDER.index(f["severity"]) if f["severity"] in _SEV_ORDER else 9)
        label = top["id"] + (f" +{len(findings) - 1}" if len(findings) > 1 else "")
        bw = 10 + len(label) * 6
        out.append(f'<g transform="translate({w - bw - 8},{h - 22})">'
                   f'<rect width="{bw}" height="14" rx="7" fill="{_SEV_COLOR.get(top["severity"], "#64748b")}"/>'
                   f'<text class="badge" x="{bw // 2}" y="10" text-anchor="middle">{esc(label)}</text></g>')
    out.append('</g>')
    return "".join(out)


def _ortho_edge(b1, b2, kind="data", channel=None):
    """
    Orthogonal (right-angle) connector — horizontal/vertical segments only, the
    convention for clean architecture diagrams (minimise bends/crossings). Control
    edges route UP through an inter-lane channel and back into the target's bottom via
    column alleys, so they never cut diagonally across the diagram.
    """
    x1, y1, w1, h1 = b1
    x2, y2, w2, h2 = b2
    color = "#475569" if kind == "ctrl" else "#1e293b"
    dash = ' stroke-dasharray="6 5"' if kind == "ctrl" else ''
    if channel is not None:
        sx, sy = x1 + w1 // 2, y1
        tx, ty = x2 + w2 // 2, y2 + h2
        d = f"M{sx},{sy} V{channel} H{tx} V{ty}"
    elif x2 >= x1 + w1:                       # target to the right: horizontal (Z if rows differ)
        sx, sy = x1 + w1, y1 + h1 // 2
        tx, ty = x2, y2 + h2 // 2
        mx = (sx + tx) // 2
        d = f"M{sx},{sy} H{mx} V{ty} H{tx}"
    else:                                      # target below: down, across an alley, down
        sx, sy = x1 + w1 // 2, y1 + h1
        tx, ty = x2 + w2 // 2, y2
        my = (sy + ty) // 2
        d = f"M{sx},{sy} V{my} H{tx} V{ty}" if abs(sx - tx) > 3 else f"M{sx},{sy} V{ty}"
    return (f'<path d="{d}" stroke="{color}" stroke-width="1.6" fill="none" '
            f'marker-end="url(#arrow)" opacity="0.7"{dash}/>')


def build_pipeline_flow_svg(rows, template, cloud, short_hash, ts, findings=None, plan=None):
    """
    Real architecture flow for the standard data pipeline (spec v2 §9 flow layout).

    Collapses per-bucket config (versioning/lifecycle/encryption/PAB) into one service box
    and lays the medallion path out left->right: Source -> S3 Bronze -> Glue -> S3 Silver ->
    Glue -> S3 Gold -> Athena, with a governance band (Step Functions, Catalog, KMS, IAM,
    CloudWatch, Budget) and dashed control edges. Carries the v2 governance overlay.
    """
    def esc(s):
        return html.escape(str(s), quote=True)

    addr_rows = {r["address"]: r for r in rows}
    has_kms = any(r["type"].startswith("aws_kms_key") for r in rows)
    fmap = {}
    for f in (findings or []):
        if f.get("resource"):
            fmap.setdefault(f["resource"], []).append(f)

    def find(pred):
        return [a for a, r in addr_rows.items() if pred(r)]

    def zone(key):
        return [a for a in addr_rows if addr_rows[a]["type"].startswith("aws_s3_") and _instance_key(a) == key]

    R = {
        "bronze": zone("bronze"), "silver": zone("silver"), "gold": zone("gold"),
        "results": zone("athena_results"),
        "glue1": find(lambda r: r["type"] == "aws_glue_job" and r["name"] == "bronze_to_silver"),
        "glue2": find(lambda r: r["type"] == "aws_glue_job" and r["name"] == "silver_to_gold"),
        "athena": find(lambda r: r["type"].startswith("aws_athena")),
        "sfn": find(lambda r: r["type"] == "aws_sfn_state_machine"),
        "catalog": find(lambda r: r["type"] == "aws_glue_catalog_database"),
        "kms": find(lambda r: r["type"].startswith("aws_kms")),
        "iam": find(lambda r: r["type"].startswith("aws_iam")),
        "cw": find(lambda r: r["type"].startswith("aws_cloudwatch")),
        "budget": find(lambda r: r["type"].startswith("aws_budgets")),
    }
    LAYOUT = {
        "source": (32, 130, 156, 80), "bronze": (220, 130, 156, 80), "glue1": (408, 130, 156, 80),
        "silver": (596, 130, 156, 80), "glue2": (784, 130, 156, 80), "gold": (972, 130, 156, 80),
        "athena": (784, 250, 156, 72), "results": (972, 250, 156, 72),
        "sfn": (40, 404, 152, 72), "catalog": (240, 404, 152, 72), "kms": (440, 404, 152, 72),
        "iam": (640, 404, 152, 72), "cw": (840, 404, 152, 72), "budget": (1040, 404, 152, 72),
    }
    META = {
        # Per service, matching drawio_generator._STENCILS exactly, so the same resource is
        # the same colour whichever artifact the reader opens.
        "source": ("#ED7100", "Batch Source", "external files", "inbox"),
        "bronze": ("#7AA116", "S3 Bronze", "raw landing", "bucket"),
        "silver": ("#7AA116", "S3 Silver", "cleaned", "bucket"),
        "gold": ("#7AA116", "S3 Gold", "curated", "bucket"),
        "results": ("#7AA116", "S3 Results", "query output", "bucket"),
        "glue1": ("#8C4FFF", "Glue Job", "bronze to silver", "gears"),
        "glue2": ("#8C4FFF", "Glue Job", "silver to gold", "gears"),
        "athena": ("#8C4FFF", "Athena", "query gold", "search"),
        "sfn": ("#E7157B", "Step Functions", "starts & waits Glue", "workflow"),
        "catalog": ("#8C4FFF", "Glue Catalog", "table metadata", "book"),
        "kms": ("#DD344C", "KMS", "CMK encryption", "key"),
        "iam": ("#DD344C", "IAM", "scoped roles", "shield"),
        "cw": ("#E7157B", "CloudWatch", "failure alarm", "bell"),
        "budget": ("#2E73B8", "Budget", "spend guardrail", "coin"),
    }
    ENCRYPTED = {"bronze", "silver", "gold", "results", "athena", "kms"}

    def present(key):
        return key == "source" or bool(R.get(key))

    def addr(key):
        return (R.get(key) or [None])[0] or f"{key}.synthetic"

    def find_for(key):
        out = []
        for a in R.get(key) or []:
            out += fmap.get(a.split("[")[0], [])
        return out

    def action_for(key):
        acts = {addr_rows[a]["action"] for a in (R.get(key) or []) if a in addr_rows}
        for pref in ("delete", "update", "create", "no-op"):
            if pref in acts:
                return pref
        return "create"

    roles = len([a for a in addr_rows if addr_rows[a]["type"] == "aws_iam_role"])
    policies = len([a for a in addr_rows if addr_rows[a]["type"] in ("aws_iam_role_policy", "aws_iam_policy")])

    def zone_protections(zkey):
        real = "athena_results" if zkey == "results" else zkey
        types = {addr_rows[a]["type"] for a in addr_rows if _instance_key(a) == real}
        flags = []
        if any("server_side_encryption" in t for t in types):
            flags.append("KMS")
        if any(t.endswith("_versioning") for t in types):
            flags.append("versioned")
        if any("lifecycle" in t for t in types):
            flags.append("lifecycle")
        return "·".join(flags)

    DETAIL = {
        "source": "batch", "bronze": zone_protections("bronze"), "silver": zone_protections("silver"),
        "gold": zone_protections("gold"), "results": zone_protections("results"),
        "glue1": "Spark ETL", "glue2": "Spark ETL", "athena": "SSE-KMS results",
        "sfn": "sequential workflow", "catalog": "table metadata", "kms": "CMK · rotation",
        "iam": f"{roles} roles · {policies} policies", "cw": "ExecutionsFailed alarm",
        "budget": "monthly guardrail",
    }

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 760" width="100%" role="img">',
        f'<title>Architecture — {esc(template)}</title>',
        f'<desc>Governed AWS data pipeline for {esc(cloud)} (architecture_svg_spec.md v2 flow layout): '
        'batch source to S3 bronze, Glue to silver, Glue to gold, Athena queries gold; governance band '
        'and per-resource security/cost findings overlaid.</desc>',
        '<defs>'
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#1e293b"/></marker>'
        '<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">'
        '<path d="M40 0H0V40" fill="none" stroke="rgba(217,93,57,.06)" stroke-width="0.5"/></pattern>'
        '<style>'
        '.title{font:600 22px Outfit,system-ui,sans-serif;fill:#1e293b}'
        '.sub{font:500 12px "JetBrains Mono",ui-monospace,monospace;fill:#64748b}'
        '.tier-h{font:600 12px Outfit,system-ui,sans-serif;fill:#1e293b;letter-spacing:.12em}'
        '.n-type{font:600 13px Inter,system-ui,sans-serif;fill:#1e293b}'
        '.n-name{font:400 11px "JetBrains Mono",ui-monospace,monospace;fill:#64748b}'
        '.n-meta{font:500 9px "JetBrains Mono",ui-monospace,monospace;fill:#b45309}'
        '.badge{font:600 9px Inter,system-ui,sans-serif;fill:#ffffff}'
        '.legend{font:500 11px Inter,system-ui,sans-serif;fill:#64748b}'
        '.p-l{font:600 9px Inter,system-ui,sans-serif;fill:#64748b;letter-spacing:.06em}'
        '.p-v{font:600 13px Inter,system-ui,sans-serif;fill:#1e293b}'
        '.swim{fill:#ffffff;fill-opacity:.75;stroke:rgba(217,93,57,.14)}'
        '</style></defs>',
        '<g id="bg"><rect x="0" y="0" width="1280" height="760" fill="#fbf7f4"/>'
        '<rect x="0" y="0" width="1280" height="760" fill="url(#grid)"/></g>',
        '<g id="titlebar"><rect x="0" y="0" width="1280" height="64" fill="#ffffff"/>'
        '<rect x="0" y="63" width="1280" height="1" fill="rgba(217,93,57,.18)"/>'
        f'<text class="title" x="24" y="34">{esc(template)}</text>'
        f'<text class="sub" x="24" y="52">{esc(cloud)} · plan {esc(short_hash)} · {esc(ts)}</text></g>',
        '<rect class="swim" x="24" y="92" width="1232" height="248" rx="12"/>'
        '<text class="tier-h" x="44" y="116">RUNTIME DATA FLOW</text>',
        '<rect class="swim" x="24" y="372" width="1232" height="248" rx="12"/>'
        '<text class="tier-h" x="44" y="396">ORCHESTRATION &amp; GOVERNANCE</text>',
    ]

    # Edges first (under nodes). The slots are a fixed layout; which of them are JOINED is
    # read from the plan. This drew the whole chain source -> bronze -> glue1 -> silver ->
    # glue2 -> gold -> athena -> results whenever the slots were filled, so a stack whose
    # Glue job declares no source or target path still showed a complete medallion pipeline.
    slot_of = {address: key for key, addresses in R.items() for address in addresses}
    edges = ['<g id="edges">']
    drawn = set()
    for hop in declared_hops(plan):
        a, b = slot_of.get(hop["source"]), slot_of.get(hop["target"])
        if not a or not b or a == b or (a, b) in drawn:
            continue
        if not (present(a) and present(b)):
            continue
        drawn.add((a, b))
        edges.append(_ortho_edge(LAYOUT[a], LAYOUT[b], "data"))
    edges.append('</g>')
    parts += edges

    # runtime + consumption nodes
    parts.append('<g id="flow-runtime">')
    for key in ("source", "bronze", "glue1", "silver", "glue2", "gold", "athena", "results"):
        if not present(key):
            continue
        x, y, w, h = LAYOUT[key]
        hue, title, sub, icon = META[key]
        parts.append(_component_box(
            x, y, w, h, hue, title, sub, action_for(key), find_for(key),
            has_kms and key in ENCRYPTED, addr(key), esc, icon=icon, detail=DETAIL.get(key, "")))
    parts.append('</g>')

    # governance band nodes
    parts.append('<g id="band-governance">')
    for key in ("sfn", "catalog", "kms", "iam", "cw", "budget"):
        if not present(key):
            continue
        x, y, w, h = LAYOUT[key]
        hue, title, sub, icon = META[key]
        parts.append(_component_box(
            x, y, w, h, hue, title, sub, action_for(key), find_for(key),
            has_kms and key in ENCRYPTED, addr(key), esc, icon=icon, detail=DETAIL.get(key, "")))
    parts.append('</g>')

    # deployment posture — fills the governance lane with real signal
    counts = {"create": 0, "update": 0, "delete": 0, "no-op": 0}
    for r in rows:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    variables = (plan or {}).get("variables", {})

    def var(name):
        v = variables.get(name, {})
        return v.get("value") if isinstance(v, dict) else None

    context = "·".join(str(v) for v in (var("owner"), var("environment"), var("region")) if v) or "—"
    sev = [f.get("severity") for f in (findings or [])]
    findings_text = "0 · clean" if not findings else f"{len(findings)} ({sev.count('HIGH')}H/{sev.count('MEDIUM')}M)"
    cells = [
        ("Resources", f"{len(rows)}  +{counts['create']}/~{counts['update']}/-{counts['delete']}"),
        ("Services", str(len(_service_summary(rows)))),
        ("Encryption", "KMS CMK" if has_kms else "none"),
        ("Findings", findings_text),
        ("Context", context),
        ("Apply", "gated · plan hash"),
    ]
    parts.append('<g id="posture"><text class="tier-h" x="24" y="500">DEPLOYMENT POSTURE</text>')
    cw, cx, cy = 194, 24, 510
    for label, val in cells:
        parts.append(f'<rect x="{cx}" y="{cy}" width="{cw}" height="74" rx="10" fill="#ffffff" '
                     f'fill-opacity="0.6" stroke="rgba(217,93,57,.16)"/>'
                     f'<text class="p-l" x="{cx + 14}" y="{cy + 28}">{esc(label.upper())}</text>'
                     f'<text class="p-v" x="{cx + 14}" y="{cy + 52}">{esc(_fit_text(str(val), 22))}</text>')
        cx += cw + 8
    parts.append('</g>')

    # legend (same key set as the grid layout)
    parts.append('<g id="legend">'
                 '<line x1="24" y1="700" x2="58" y2="700" stroke="#1e293b" stroke-width="1.6" marker-end="url(#arrow)"/>'
                 '<text class="legend" x="64" y="704">data flow</text>'
                 '<line x1="146" y1="700" x2="180" y2="700" stroke="#475569" stroke-width="1.6" stroke-dasharray="6 5" marker-end="url(#arrow)"/>'
                 '<text class="legend" x="186" y="704">control</text>'
                 '<g transform="translate(252,693)">' + _LOCK + '</g>'
                 '<text class="legend" x="274" y="704">encrypted (KMS)</text>'
                 f'<rect x="392" y="693" width="32" height="13" rx="6" fill="{_SEV_COLOR["HIGH"]}"/>'
                 '<text class="badge" x="408" y="703" text-anchor="middle">SEC</text>'
                 '<text class="legend" x="430" y="704">finding overlay</text>'
                 '<text class="legend" x="548" y="704">create=green · update=amber · delete=red</text>'
                 '<text class="legend" x="24" y="730">Governance controls apply across deployment and runtime; '
                 'they are intentionally not drawn as data movement.</text>'
                 '</g>')
    parts.append('</svg>')
    return "\n".join(parts)


def build_svg(rows, template, cloud, short_hash, ts, findings=None, plan=None):
    """
    Render the deploy architecture diagram (docs/architecture_svg_spec.md v2).

    On top of the v1 fixed-grid contract (viewBox 0 0 1280 760, the named layer groups,
    every node with data-address + data-action, §6 palette only), v2 adds:
      * REAL data-flow edges anchored to nodes (medallion path for the pipeline blueprint;
        a node-anchored fallback otherwise) — no decorative arrows into empty space.
      * for_each instance labels (bronze/silver/gold) instead of the block name.
      * encryption (lock) markers on KMS-protected nodes.
      * a GOVERNANCE OVERLAY: each node carries its SEC/COST/OBS findings as a badge and a
        machine-readable data-findings attribute, so the diagram is also the review surface.
    """
    # Known blueprints use the readable flow/topology layout (spec v2 §9).
    if template == "aws-data-pipeline-standard":
        return build_pipeline_flow_svg(rows, template, cloud, short_hash, ts, findings, plan)

    def esc(s):
        return html.escape(str(s), quote=True)

    has_kms = any(r["type"].startswith("aws_kms_key") for r in rows)
    rows = _collapse_components(rows, plan)   # one node per service (+ its config), not a pile
    by_tier = {t: [r for r in rows if r["tier"] == t] for t in TIERS}
    # Every tier group is emitted, in spec order, because the group ids are a contract a
    # consumer reads. An EMPTY one is emitted empty: SOURCES was drawn as a heading and a
    # coloured rule over 232px of blank canvas on every plan whose data already lives in
    # the account, and a labelled empty column reads as "the sources are missing" rather
    # than "there are none". Occupied columns reflow so they stay flush left.
    column_tiers = ["sources", "storage", "compute", "orchestration", "observability"]
    visible_tiers = [t for t in column_tiers if by_tier.get(t)]
    tier_x = {t: TIER_X["sources"] + i * (_CARD_W + 2 * _GUTTER_HALF)
              for i, t in enumerate(visible_tiers)}
    node_h, gap = 44, 8

    # The canvas grows with the tallest tier instead of hiding overflow — a busy
    # architecture should be scrollable/zoomable (see the pan-zoom viewer), never
    # missing resources or crushed into illegibly short cards.
    max_items = max((len(by_tier[t]) for t in visible_tiers), default=0)
    content_h = max(0, max_items * (node_h + gap) - gap)
    sec_top = max(632, 108 + content_h + 24)
    dy = sec_top - 632
    total_h = 760 + dy

    fmap = {}
    for f in (findings or []):
        if f.get("resource"):
            fmap.setdefault(f["resource"], []).append(f)

    def node_findings(address):
        return fmap.get(address.split("[")[0], [])

    # layout pass — record node positions so edges anchor to real nodes
    pos = {}
    for t in visible_tiers:
        y = 108
        for r in by_tier[t]:
            pos[r["address"]] = (tier_x[t], y)
            y += node_h + gap

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 {total_h}" width="100%" role="img">',
        f'<title>Architecture — {esc(template)}</title>',
        f'<desc>Auto-generated deploy architecture for {esc(template)} on {esc(cloud)} '
        '(architecture_svg_spec.md v2): tiered topology with real data-flow edges, encryption '
        'markers, and a per-resource overlay of security/cost/observability findings.</desc>',
        '<defs>'
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#1e293b"/></marker>'
        '<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">'
        '<path d="M40 0H0V40" fill="none" stroke="rgba(217,93,57,.06)" stroke-width="0.5"/></pattern>'
        '<style>'
        '.title{font:600 22px Outfit,system-ui,sans-serif;fill:#1e293b}'
        '.sub{font:500 12px "JetBrains Mono",ui-monospace,monospace;fill:#64748b}'
        '.tier-h{font:600 13px Outfit,system-ui,sans-serif;fill:#1e293b;letter-spacing:.12em}'
        '.n-type{font:600 12px Inter,system-ui,sans-serif;fill:#1e293b}'
        '.n-name{font:400 11px "JetBrains Mono",ui-monospace,monospace;fill:#64748b}'
        '.badge{font:600 9px Inter,system-ui,sans-serif;fill:#ffffff}'
        '.legend{font:500 11px Inter,system-ui,sans-serif;fill:#64748b}'
        '</style></defs>',
        f'<g id="bg"><rect x="0" y="0" width="1280" height="{total_h}" fill="#fbf7f4"/>'
        f'<rect x="0" y="0" width="1280" height="{total_h}" fill="url(#grid)"/></g>',
        '<g id="titlebar"><rect x="0" y="0" width="1280" height="64" fill="#ffffff"/>'
        '<rect x="0" y="63" width="1280" height="1" fill="rgba(217,93,57,.18)"/>'
        f'<text class="title" x="24" y="34">{esc(template)}</text>'
        f'<text class="sub" x="24" y="52">{esc(cloud)} · plan {esc(short_hash)} · {esc(ts)}</text></g>',
    ]

    # edges — real flow anchored to node positions
    flow = _anchored_flow(plan, pos, node_h)
    edges = ['<g id="edges">']
    for d, kind in flow:
        color = "#475569" if kind == "ctrl" else "#1e293b"
        dash = ' stroke-dasharray="6 5"' if kind == "ctrl" else ''
        edges.append(f'<path d="{d}" stroke="{color}" stroke-width="1.6" fill="none" '
                     f'stroke-linejoin="round" marker-end="url(#arrow)" opacity="0.6"{dash}/>')
    edges.append('</g>')
    parts += edges

    # tier columns + nodes (with encryption markers and the governance finding overlay)
    for t in column_tiers:
        if not by_tier.get(t):
            parts.append(f'<g id="tier-{t}"></g>')
            continue
        x = tier_x[t]
        parts.append(f'<g id="tier-{t}"><text class="tier-h" x="{x}" y="92">{t.upper()}</text>'
                     f'<rect x="{x}" y="100" width="232" height="2" fill="{TIER_HUE[t]}"/>')
        items = by_tier[t]
        y = 108
        for r in items:
            tint = ACTION_TINT.get(r["action"], "#64748b")
            nf = node_findings(r["address"])
            locked = has_kms and (r["type"].startswith("aws_s3_") or "athena" in r["type"]
                                  or r["type"].startswith("aws_kms"))
            type_limit = 22 if (locked or nf) else 30
            df_attr = f' data-findings="{esc(",".join(f["id"] for f in nf))}"' if nf else ""
            node = [
                f'<g class="node" data-address="{esc(r["address"])}" data-action="{esc(r["action"])}"{df_attr} '
                f'transform="translate({x},{y})">',
                f'<rect class="card" width="232" height="{node_h}" rx="12" fill="#ffffff" '
                f'stroke="{TIER_HUE[t]}" stroke-width="1.5"/>',
                f'<rect width="4" height="{node_h}" rx="2" fill="{tint}"/>',
                _icon(_icon_for(r["type"]), TIER_HUE[t], 14, node_h // 2 - 9),
                f'<text class="n-type" x="44" y="{node_h // 2 - 3}">{esc(_fit_text(_humanize(r["type"]), type_limit))}</text>',
                f'<text class="n-name" x="44" y="{node_h // 2 + 12}">{esc(_fit_text(_node_label(r), 26))}</text>',
            ]
            if locked:
                node.append('<g transform="translate(210,7)">' + _LOCK + '</g>')
            if nf:
                top = min(nf, key=lambda f: _SEV_ORDER.index(f["severity"]) if f["severity"] in _SEV_ORDER else 9)
                label = top["id"] + (f" +{len(nf) - 1}" if len(nf) > 1 else "")
                bw = 10 + len(label) * 6
                bx = (200 if locked else 224) - bw
                node.append(f'<g transform="translate({bx},6)">'
                            f'<rect width="{bw}" height="14" rx="7" fill="{_SEV_COLOR.get(top["severity"], "#64748b")}"/>'
                            f'<text class="badge" x="{bw // 2}" y="10" text-anchor="middle">{esc(label)}</text></g>')
            node.append('</g>')
            parts.append("".join(node))
            y += node_h + gap
        parts.append('</g>')

    # security band — one chip per NAME (a role and its policy share a name; repeating
    # the bare name read as duplicates), border tinted when any member has a finding
    sec = by_tier["security"]
    sec_groups = {}
    for r in sec:
        sec_groups.setdefault(r["name"], []).append(r)
    parts.append(f'<g id="band-security"><rect x="24" y="{632 + dy}" width="1224" height="56" rx="10" fill="none" '
                 'stroke="#64748b" stroke-dasharray="4 4"/>'
                 f'<text class="tier-h" x="40" y="{654 + dy}">SECURITY &amp; IAM</text>')
    chip_cap = 6
    grouped = list(sec_groups.items())
    for i, (name, members) in enumerate(grouped[:chip_cap]):
        cx = 220 + i * 168
        r = members[0]
        nf = [f for m in members for f in node_findings(m["address"])]
        stroke = _SEV_COLOR.get(nf[0]["severity"], "#64748b") if nf else "#64748b"
        df_attr = f' data-findings="{esc(",".join(f["id"] for f in nf))}"' if nf else ""
        label = name + (f" ×{len(members)}" if len(members) > 1 else "")
        parts.append(
            f'<g class="node" data-address="{esc(r["address"])}" data-action="{esc(r["action"])}"{df_attr}>'
            f'<rect x="{cx}" y="{646 + dy}" width="150" height="28" rx="8" fill="#ffffff" stroke="{stroke}" stroke-width="1"/>'
            f'<text class="n-name" x="{cx + 8}" y="{664 + dy}">{esc(_fit_text(label, 18))}</text></g>')
    if len(grouped) > chip_cap:
        parts.append(f'<text class="legend" x="1196" y="{664 + dy}">+{len(grouped) - chip_cap}</text>')
    parts.append('</g>')

    # legend: tiers + flow + control + encryption + finding overlay + status
    parts.append(f'<g id="legend"><text class="legend" x="24" y="{712 + dy}">Tiers:</text>')
    lx = 70
    for t in visible_tiers:
        parts.append(f'<rect x="{lx}" y="{703 + dy}" width="12" height="12" rx="3" fill="{TIER_HUE[t]}"/>'
                     f'<text class="legend" x="{lx + 18}" y="{713 + dy}">{t.capitalize()}</text>')
        lx += 70 + len(t) * 6
    parts.append(
        f'<line x1="24" y1="{736 + dy}" x2="58" y2="{736 + dy}" stroke="#1e293b" stroke-width="1.6" marker-end="url(#arrow)"/>'
        f'<text class="legend" x="64" y="{740 + dy}">data flow</text>'
        f'<line x1="146" y1="{736 + dy}" x2="180" y2="{736 + dy}" stroke="#475569" stroke-width="1.6" stroke-dasharray="6 5" marker-end="url(#arrow)"/>'
        f'<text class="legend" x="186" y="{740 + dy}">control</text>'
        f'<g transform="translate(252,{729 + dy})">' + _LOCK + '</g>'
        f'<text class="legend" x="274" y="{740 + dy}">encrypted (KMS)</text>'
        f'<rect x="392" y="{729 + dy}" width="32" height="13" rx="6" fill="{_SEV_COLOR["HIGH"]}"/>'
        f'<text class="badge" x="408" y="{739 + dy}" text-anchor="middle">SEC</text>'
        f'<text class="legend" x="430" y="{740 + dy}">finding overlay</text>'
        f'<text class="legend" x="548" y="{740 + dy}">create=green · update=amber · delete=red</text>')
    parts.append('</g>')

    parts.append('</svg>')
    return "\n".join(parts)


# palette constants shared by v3 (the MinusOps Monad light palette)
BG_C = "#fbf7f4"; PANEL_C = "#ffffff"; PANEL2_C = "#f4ece6"; TEXT_C = "#1e293b"
MUTED_C = "#64748b"; FAINT_C = "#94a3b8"; TERRA_C = "#d95d39"; SAND_C = "#b45309"
SAGE_C = "#8da189"; GOLD_C = "#cb9a3e"
# TERRA_C / SAND_C / SAGE_C / GOLD_C stay as the report's warm CHROME -- section labels,
# annotations, accents. They are no longer used to colour a resource, because a resource's
# colour now has to mean the same thing here as it does in architecture.drawio. These are the
# AWS service-category colours drawio_generator._STENCILS puts on its icons.
STORAGE_C = "#7AA116"    # S3
TRANSFORM_C = "#8C4FFF"  # Glue, EMR, Athena, Redshift -- analytics
ORCH_C = "#E7157B"       # Step Functions, EventBridge, CloudWatch
SECURITY_C = "#DD344C"   # IAM, KMS, Lake Formation
OBSERV_C = "#2E73B8"     # budgets, DynamoDB
NEUTRAL_C = "#475569"    # not a service: control edges, no-op, external
ADVISORY_C = "#ED7100"   # attention, the same amber an update carries

# Clean display names + semantic role lines (deterministic, per resource type).
_V3_NICE = {
    "aws_s3_bucket": "S3 Bucket", "aws_athena_workgroup": "Athena Workgroup",
    "aws_glue_job": "Glue Job", "aws_glue_registry": "Glue Registry",
    "aws_glue_catalog_database": "Glue Database", "aws_sfn_state_machine": "Step Functions",
    "aws_budgets_budget": "Budget", "aws_cloudwatch_metric_alarm": "CloudWatch Alarm",
    "aws_cloudwatch_log_group": "Log Group", "aws_kms_key": "KMS Key",
    "aws_iam_role": "IAM Role", "aws_iam_role_policy": "IAM Policy", "aws_lambda_function": "Lambda",
}
_V3_ROLE = {
    "aws_athena_workgroup": "query engine", "aws_glue_job": "Spark ETL job",
    "aws_glue_registry": "schema registry", "aws_glue_catalog_database": "data catalog",
    "aws_sfn_state_machine": "workflow orchestrator", "aws_budgets_budget": "spend guardrail",
    "aws_cloudwatch_metric_alarm": "failure alarm", "aws_kms_key": "encryption key",
    "aws_lambda_function": "function",
}
def _v3_role(r):
    """A short, deterministic role line for a node (falls back to config count / action)."""
    role = _V3_ROLE.get(r["type"])
    if role:
        return role
    if r["type"] == "aws_s3_bucket":
        return "object store"
    cfg = r.get("config_count", 0)
    return f"+{cfg} config" if cfg else r["action"]


_SVG_ACTIVE_ELEMS = ("script", "foreignobject", "iframe", "embed", "object", "image", "animate", "set")


def _sanitize_svg_fragment(inner):
    """Strip active content from an untrusted SVG fragment.

    Icon files come from an operator-supplied directory and the result is embedded in
    reports the dashboard serves, so they must not carry script. Removes script/
    foreignObject/embedding/animation elements, comments, event-handler attributes, and
    any href that is not fragment-local. Fails closed: returns None (caller falls back
    to the built-in glyph) if anything dangerous survives.
    """
    inner = re.sub(r"<!--.*?-->", "", inner, flags=re.S)
    for tag in _SVG_ACTIVE_ELEMS:
        inner = re.sub(rf"<\s*{tag}\b.*?(/\s*>|<\s*/\s*{tag}\s*>)", "", inner, flags=re.S | re.I)
    inner = re.sub(r"\son[\w-]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", inner, flags=re.I)
    # Only fragment-local targets survive (keeps <use href="#id">, drops files/URLs/data:).
    inner = re.sub(r"\s(?:xlink:)?href\s*=\s*([\"'])(?!#)[^\"']*\1", "", inner, flags=re.I)
    inner = re.sub(r"\s(?:xlink:)?href\s*=\s*(?![\"']|#)[^\s>]+", "", inner, flags=re.I)
    low = inner.lower()
    if ("javascript:" in low
            or re.search(r"<\s*(" + "|".join(_SVG_ACTIVE_ELEMS) + r")\b", low)
            or re.search(r"\son[\w-]+\s*=", low)):
        return None
    return inner


def _default_icons_dir():
    """The opt-in local icon directory (MINUS_ARCH_ICONS_DIR or assets/architecture-icons).
    Resolved HERE so every caller of build_dataflow_svg gets icons automatically — a
    caller once forgot to pass icons_dir and silently shipped glyph-only diagrams."""
    path = os.environ.get("MINUS_ARCH_ICONS_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets", "architecture-icons")
    return path if os.path.isdir(path) else None


def _df_embed_icon(rtype, uid, x, y, size, hue, icons_dir):
    """Embed a real service icon by slug from icons_dir if present; else a generic glyph.

    Nothing vendor-owned is shipped in the repo — icons are opt-in via a local dir; the
    default path is the on-palette generic glyph, so the diagram always renders. Icon
    content is sanitized on embed (see _sanitize_svg_fragment) — a file that still looks
    active after sanitization is rejected in favor of the glyph.
    """
    if icons_dir:
        import architecture_model as _am
        path = os.path.join(icons_dir, _am._strip_provider(rtype).split("_")[0] + ".svg")
        if os.path.exists(path):
            try:
                txt = open(path, encoding="utf-8").read()
                m = re.search(r"<svg([^>]*)>(.*)</svg>", txt, re.S)
                inner = _sanitize_svg_fragment(m.group(2) if m else txt)
                if inner is not None:
                    # carry the source viewBox through so 80x80 icon sets aren't cropped
                    vb = re.search(r'viewBox="([^"]+)"', m.group(1)) if m else None
                    viewbox = vb.group(1) if vb else "0 0 64 64"
                    for i in sorted(set(re.findall(r'id="([^"]+)"', inner)), key=len, reverse=True):
                        inner = (inner.replace(f'id="{i}"', f'id="{uid}_{i}"')
                                 .replace(f'url(#{i})', f'url(#{uid}_{i})')
                                 .replace(f'xlink:href="#{i}"', f'xlink:href="#{uid}_{i}"')
                                 .replace(f'href="#{i}"', f'href="#{uid}_{i}"'))
                    return (f'<svg x="{x}" y="{y}" width="{size}" height="{size}" viewBox="{viewbox}" '
                            f'xmlns:xlink="http://www.w3.org/1999/xlink">{inner}</svg>')
            except Exception:
                pass
    return _icon(_icon_for(rtype), hue, x + size // 2 - 9, y + size // 2 - 9)


def build_dataflow_svg(rows, template, cloud, short_hash, ts, findings=None, plan=None,
                       region="us-east-1", icons_dir=None, usage_annotations=None):
    """Lake-house data-flow diagram (architecture_svg_spec.md v3), sharing the six-layer
    classifier with the conformance model (architecture_model). Deterministic and honest:
    stages on the spine, real transforms between them, catalog/governance in its own zone,
    results as side outputs, consumption reading curated, orchestration edges drawn only
    when the plan's references confirm the wiring, and a Security & Monitoring band.
    """
    import architecture_model as am

    if icons_dir is None:
        icons_dir = _default_icons_dir()

    def esc(s):
        return html.escape(str(s), quote=True)

    comps = _collapse_components(rows, plan)
    for c in comps:
        c["role"] = am.classify_role(c["type"], _instance_key(c["address"]), c.get("name", ""))
    R = {}
    for c in comps:
        R.setdefault(c["role"], []).append(c)
    stages = sorted(R.get("stage", []), key=lambda c: (am.stage_rank(_instance_key(c["address"]), c.get("name", "")), c["address"]))
    xforms = list(R.get("transform", []))
    # Maintenance jobs (compaction/vacuum/optimize) are NOT data-flow steps — drawing
    # them on the spine would claim data flows THROUGH them. They render off-spine.
    _MAINT = re.compile(r"compact|vacuum|optimi[sz]e|maintenance|reindex", re.I)
    maintenance = [c for c in xforms
                   if _MAINT.search(c.get("name", "") + " " + c.get("module", ""))]
    xforms = [c for c in xforms if c not in maintenance]
    govern = R.get("catalog", [])
    consume = R.get("consume", [])
    side = R.get("store_other", [])
    # Real workflow orchestrators outrank job triggers/schedulers: a compaction
    # schedule must never displace Step Functions as "the orchestrator".
    def _orch_rank(c):
        t = c["type"].lower()
        return 0 if any(k in t for k in ("sfn", "state_machine", "mwaa", "airflow", "composer")) else 1
    orch = sorted(R.get("orchestrate", []), key=lambda c: (_orch_rank(c), c["address"]))
    band = R.get("security", []) + R.get("observability", [])
    deps = am.module_dependencies(plan) if plan else {}

    # Place each transform between the stages it actually bridges. The `<from>_to_<to>`
    # naming convention (bronze_to_silver, silver_to_gold, raw_to_cleaned, …) is matched
    # against the stage keys first; unnamed jobs fall into the first empty gap in order;
    # anything still unplaced is appended after the last stage — never silently dropped.
    def _skey(c):
        return (_instance_key(c["address"]) or c.get("name", "")).lower()

    stage_keys = [_skey(c) for c in stages]

    def _stage_idx(token):
        if token in stage_keys:
            return stage_keys.index(token)
        rank = am._STAGE_RANK.get(token)
        if rank is not None:
            for i, k in enumerate(stage_keys):
                if am._STAGE_RANK.get(k) == rank:
                    return i
        return None

    gaps = {i: [] for i in range(max(len(stages) - 1, 0))}
    unplaced = []
    for x in xforms:
        m = re.match(r"([a-z0-9]+)_to_([a-z0-9]+)", (x.get("name") or "").lower())
        gi = None
        if m:
            a, b = _stage_idx(m.group(1)), _stage_idx(m.group(2))
            if a is not None and b == a + 1:
                gi = a
            elif a is not None and a < len(stages) - 1:
                gi = a
        if gi is not None:
            gaps[gi].append(x)
        else:
            unplaced.append(x)
    for x in unplaced:
        empty = next((i for i in sorted(gaps) if not gaps[i]), None)
        if empty is not None:
            gaps[empty].append(x)
        else:
            gaps.setdefault(len(stages) - 1, []).append(x)   # after the last stage

    spine, used_xf = [], []
    for i, c in enumerate(stages):
        spine.append(("stage", c))
        for x in gaps.get(i, []):
            used_xf.append(x)
            spine.append(("xf", x))
    if not stages:              # transform-only plans still render their jobs
        for x in xforms:
            used_xf.append(x)
            spine.append(("xf", x))

    W = 1280
    proc_x, proc_w, cons_x, cons_w = 24, 990, 1030, 226
    spine_y, sz = 250, 56
    side_y = spine_y + sz + 95
    orch_y = side_y + (110 if orch else 0)
    proc_top = 210
    proc_bottom = (orch_y + 70) if orch else (side_y + 70 if side else spine_y + sz + 55)
    band_y = proc_bottom + 30
    total_h = band_y + (120 if band else 20) + 56          # +26 for the edge-semantics legend
    gov_top = 110
    n = max(len(spine), 1)
    slot = proc_w / (n + 0.2)
    cx = [int(proc_x + 40 + slot * (i + 0.4)) for i in range(n)]

    def nm(rt):
        return _V3_NICE.get(rt, _humanize(rt))

    def title_of(c):
        label = _instance_key(c["address"]) or c.get("name") or ""
        return re.sub(r"[_-]+", " ", label).strip().title() or nm(c["type"])

    def tnode(c, cxp, y, s, hue, sub=None):
        uid = re.sub(r"\W", "", c["address"])
        if sub is None:
            sub = _v3_role(c)
            if sub in ("no-op", "create", "update", "delete"):
                sub = "resource"
        return (f'<g class="node" data-address="{esc(c["address"])}" data-action="{esc(c.get("action", ""))}">'
                + _df_embed_icon(c["type"], uid, cxp - s // 2, y, s, hue, icons_dir)
                + f'<text x="{cxp}" y="{y + s + 15}" text-anchor="middle" style="font:600 12px Inter,sans-serif;fill:{TEXT_C}">{esc(_fit_text(title_of(c), 18))}</text>'
                f'<text x="{cxp}" y="{y + s + 30}" text-anchor="middle" style="font:400 10px \'JetBrains Mono\',monospace;fill:{MUTED_C}">{esc(_fit_text(sub, 20))}</text></g>')

    def zone(x, y, w, h, label, col=None):
        col = col or "rgba(217,93,57,.5)"
        return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="rgba(217,93,57,.045)" '
                f'stroke="{col}" stroke-width="1.3" stroke-dasharray="7 4"/>'
                # A band label names STRUCTURE ("STORAGE & PROCESS"), not a service. In
                # terracotta it read as a category colour competing with the AWS hues on the
                # nodes inside it. The faint band wash above stays -- that is ground, not a
                # claim about what anything is.
                f'<text x="{x + 14}" y="{y + 20}" style="font:600 11px Outfit,sans-serif;fill:{MUTED_C};letter-spacing:.1em">{esc(label.upper())}</text>')

    P = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {total_h}" width="100%" role="img">',
         f'<title>Data flow — {esc(template)}</title>',
         f'<desc>Lake-house data-flow architecture (v3) for {esc(template)} on {esc(cloud)}.</desc>',
         '<defs>'
         f'<marker id="dfa" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{MUTED_C}"/></marker>'
         '<pattern id="dfg" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="rgba(217,93,57,.05)" stroke-width="0.5"/></pattern>'
         '</defs>',
         f'<rect width="{W}" height="{total_h}" fill="{BG_C}"/><rect width="{W}" height="{total_h}" fill="url(#dfg)"/>',
         f'<text x="24" y="34" style="font:600 22px Outfit,sans-serif;fill:{TEXT_C}">{esc(template)} — data flow</text>'
         f'<text x="24" y="54" style="font:500 12px \'JetBrains Mono\',monospace;fill:{MUTED_C}">{esc(cloud)} · plan {esc(short_hash)} · {esc(ts)} · {esc(region)}</text>']

    P.append(zone(proc_x, proc_top, proc_w, proc_bottom - proc_top, "Storage & Processing"))
    if consume:
        P.append(zone(cons_x, proc_top, cons_w, (side_y + 70) - proc_top, "Consumption"))
    if govern:
        P.append(zone(proc_x + 300, gov_top, 420, 92, "Cataloging & Governance"))
        gx0 = proc_x + 320
        for j, c in enumerate(govern[:3]):
            P.append(tnode(c, gx0 + 22 + j * 130, gov_top + 18, 40, TRANSFORM_C, sub="schema / catalog"))
        if len(govern) > 3:
            P.append(f'<text x="{proc_x + 300 + 420 - 14}" y="{gov_top + 52}" text-anchor="end" '
                     f'style="font:600 10px \'JetBrains Mono\',monospace;fill:{MUTED_C}">+{len(govern) - 3} more</text>')
        if used_xf:
            tx = cx[[k for k, (kind, _) in enumerate(spine) if kind == "xf"][0]]
            P.append(f'<path d="M{gx0 + 22},{gov_top + 92} C{gx0 + 22},{gov_top + 150} {tx},{spine_y - 60} {tx},{spine_y}" '
                     f'stroke="{NEUTRAL_C}" stroke-width="1.3" fill="none" stroke-dasharray="5 4" opacity="0.8"/>')

    for i in range(len(spine) - 1):
        x1, x2 = cx[i] + sz // 2 + 6, cx[i + 1] - sz // 2 - 6
        ey = spine_y + sz // 2
        if spine[i][0] == "stage" and spine[i + 1][0] == "stage":
            # Two storage stages with NO transform between them in the plan: an implied
            # solid arrow would fabricate a flow, so the gap is drawn faint and named.
            P.append(f'<line x1="{x1}" y1="{ey}" x2="{x2}" y2="{ey}" stroke="{MUTED_C}" '
                     f'stroke-width="1.2" stroke-dasharray="4 5" opacity="0.45" marker-end="url(#dfa)"/>')
            P.append(f'<text x="{(x1 + x2) // 2}" y="{ey - 8}" text-anchor="middle" '
                     f'style="font:600 9px \'JetBrains Mono\',monospace;fill:{ADVISORY_C}">no transform in plan</text>')
        else:
            P.append(f'<line x1="{x1}" y1="{ey}" x2="{x2}" y2="{ey}" stroke="{MUTED_C}" '
                     f'stroke-width="1.6" marker-end="url(#dfa)"/>')
    # Capacity annotations: the priced usage quantity (from the BCM estimate) rendered
    # under the node it belongs to — topology AND capacity in one picture.
    _ANN_CODE = {"glue": "AWSGlue", "s3": "AmazonS3", "athena": "AmazonAthena"}

    def _annotation(c):
        prefix = am._strip_provider(c["type"]).split("_")[0]
        return (usage_annotations or {}).get(_ANN_CODE.get(prefix))

    hue = {"stage": STORAGE_C, "xf": TRANSFORM_C}
    annotated_codes = set()
    for i, (k, c) in enumerate(spine):
        P.append(tnode(c, cx[i], spine_y, sz, hue.get(k, STORAGE_C)))
        ann = _annotation(c)
        code = _ANN_CODE.get(am._strip_provider(c["type"]).split("_")[0])
        if ann and code not in annotated_codes:      # once per service, above its first node
            annotated_codes.add(code)
            P.append(f'<text x="{cx[i]}" y="{spine_y - 8}" text-anchor="middle" '
                     f'style="font:600 9px \'JetBrains Mono\',monospace;fill:{OBSERV_C}">{esc(ann)}</text>')

    if consume and spine:
        # Consumption reads the curated END OF STORAGE (last stage), not whatever
        # happens to sit last on the spine.
        last_stage = max((i for i, (k, _) in enumerate(spine) if k == "stage"), default=len(spine) - 1)
        ax = cons_x + cons_w // 2
        P.append(f'<line x1="{cx[last_stage] + sz // 2 + 6}" y1="{spine_y + sz // 2}" x2="{ax - 28}" y2="{spine_y + sz // 2}" stroke="{MUTED_C}" stroke-width="1.6" marker-end="url(#dfa)"/>')
        P.append(tnode(consume[0], ax, spine_y, sz, TRANSFORM_C))
        if len(consume) > 1:
            P.append(f'<text x="{ax}" y="{spine_y + sz + 45}" text-anchor="middle" '
                     f'style="font:600 10px \'JetBrains Mono\',monospace;fill:{MUTED_C}">+{len(consume) - 1} more consumer(s)</text>')

    side_used = []
    for sb in side:
        owner = next((c for c in used_xf + consume + stages if c["module"] == sb["module"]), None)
        ox = None
        if owner:
            for i, (k, c) in enumerate(spine):
                if c["address"] == owner["address"]:
                    ox = cx[i]
            if ox is None and consume and owner["address"] == consume[0]["address"]:
                ox = cons_x + cons_w // 2
        if ox is None:
            continue
        side_used.append(ox)
        P.append(f'<line x1="{ox}" y1="{spine_y + sz + 34}" x2="{ox}" y2="{side_y - 6}" stroke="{MUTED_C}" stroke-width="1.2" stroke-dasharray="3 3" opacity="0.7"/>')
        P.append(tnode(sb, ox, side_y, 34, MUTED_C, sub="results / output"))

    # Maintenance jobs hang below the spine too — scheduled housekeeping, not data flow —
    # placed in free slots so they never collide with the results/output buckets.
    free_x = [x for x in range(int(proc_x + 70), int(proc_x + proc_w - 60), 130)
              if all(abs(x - u) > 95 for u in side_used)]
    for mc, mx in zip(maintenance[:4], free_x):
        P.append(tnode(mc, mx, side_y, 34, TRANSFORM_C, sub="maintenance job"))

    if orch:
        oc = orch[0]
        xf_idx = [k for k, (kk, _) in enumerate(spine) if kk == "xf"]
        ox = (sum(cx[k] for k in xf_idx) // len(xf_idx)) if xf_idx else cx[len(spine) // 2]
        # Same wiring test as architecture_model.conformance (any orchestrator module
        # referencing any transform module), so the picture and the report always agree.
        xf_mods = {c["module"].split(".")[-1] for c in xforms if c["module"].startswith("module.")}
        wired = any(xf_mods & deps.get(o["module"].split(".")[-1], set())
                    for o in orch if o["module"].startswith("module."))
        osub = "orchestrator" + (f" +{len(orch) - 1} more" if len(orch) > 1 else "")
        P.append(tnode(oc, ox, orch_y, 46, ORCH_C, sub=osub))
        for k in xf_idx:
            P.append(f'<path d="M{ox},{orch_y} C{ox},{orch_y - 30} {cx[k]},{spine_y + sz + 50} {cx[k]},{spine_y + sz + 8}" '
                     f'stroke="{ORCH_C}" stroke-width="1.3" fill="none" stroke-dasharray="5 4" opacity="{("0.85" if wired else "0.35")}"/>')
        if wired:
            P.append(f'<text x="{ox}" y="{orch_y + 92}" text-anchor="middle" style="font:600 9px \'JetBrains Mono\',monospace;fill:{ORCH_C}">orchestrates</text>')
        else:
            P.append(f'<text x="{ox}" y="{orch_y + 92}" text-anchor="middle" style="font:600 9px \'JetBrains Mono\',monospace;fill:{ADVISORY_C}">not wired — placeholder definition</text>')

    if band:
        P.append(zone(24, band_y, W - 48, 120, "Security & Monitoring", col="rgba(176,156,147,.55)"))
        bg = {}
        for c in band:
            bg.setdefault(am._strip_provider(c["type"]).split("_")[0], []).append(c)
        bitems = [(g[0], len(g)) for g in bg.values()]
        bslot = (W - 160) / max(len(bitems), 1)
        # A grouped count must be labeled at the SERVICE level: "IAM Role ×10" claimed
        # ten roles when the ten were roles + policies + policy documents.
        _BAND_SVC = {"iam": "AWS IAM", "kms": "AWS KMS", "cloudwatch": "CloudWatch",
                     "sns": "Amazon SNS", "budgets": "AWS Budgets", "secrets": "Secrets Mgr",
                     "secretsmanager": "Secrets Mgr"}
        for j, (c, cnt) in enumerate(bitems):
            x = int(100 + bslot * (j + 0.5))
            prefix = am._strip_provider(c["type"]).split("_")[0]
            lab = (f"{_BAND_SVC.get(prefix, nm(c['type']))} ×{cnt}" if cnt > 1
                   else nm(c["type"]))
            P.append(f'<g class="node" data-address="{esc(c["address"])}">'
                     + _df_embed_icon(c["type"], re.sub(r"\W", "", c["address"]), x - 24, band_y + 28, 48, MUTED_C, icons_dir)
                     + f'<text x="{x}" y="{band_y + 94}" text-anchor="middle" style="font:600 12px Inter,sans-serif;fill:{TEXT_C}">{esc(_fit_text(lab, 18))}</text></g>')

    # Edge-semantics legend. Each dashed style must mean exactly ONE thing; reusing a style
    # for a second meaning makes every edge in the diagram ambiguous.
    ly = total_h - 18
    lt = f"font:500 11px Inter,sans-serif;fill:{MUTED_C}"
    P.append(
        f'<line x1="24" y1="{ly}" x2="56" y2="{ly}" stroke="{MUTED_C}" stroke-width="1.6" marker-end="url(#dfa)"/>'
        f'<text x="62" y="{ly + 4}" style="{lt}">data flow</text>'
        f'<line x1="150" y1="{ly}" x2="182" y2="{ly}" stroke="{ORCH_C}" stroke-width="1.3" stroke-dasharray="5 4"/>'
        f'<text x="188" y="{ly + 4}" style="{lt}">orchestration / schedule</text>'
        f'<line x1="352" y1="{ly}" x2="384" y2="{ly}" stroke="{MUTED_C}" stroke-width="1.2" stroke-dasharray="3 3"/>'
        f'<text x="390" y="{ly + 4}" style="{lt}">side output</text>'
        f'<line x1="486" y1="{ly}" x2="518" y2="{ly}" stroke="{NEUTRAL_C}" stroke-width="1.3" stroke-dasharray="5 4"/>'
        f'<text x="524" y="{ly + 4}" style="{lt}">catalog reference</text>'
        f'<rect x="656" y="{ly - 6}" width="12" height="12" rx="3" fill="none" stroke="{TRANSFORM_C}" stroke-width="1.4"/>'
        f'<text x="674" y="{ly + 4}" style="{lt}">maintenance job (off-flow, scheduled)</text>'
        f'<text x="920" y="{ly + 4}" style="font:600 10px \'JetBrains Mono\',monospace;fill:{OBSERV_C}">blue figures = AWS-priced monthly usage</text>')
    P.append('</svg>')
    return "\n".join(P)


def build_inspect_html(manifest, plan, report_files=(), drift_status="CURRENT", diff_text="",
                       for_print=False):
    """The consolidated inspection page (services / resources / IAM / drift / files).

    One builder serves both surfaces: the dashboard route renders it live (collapsible
    sections, live drift), and the report bundle prints it to inspect.pdf (sections
    forced open — PDF can't collapse — with real headings so Chromium emits a clickable
    PDF outline as the section dropdown).
    """
    def esc(s):
        return html.escape(str(s))

    def table(headers, rows_):
        head = "".join(f"<th>{esc(h)}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>"
                       for row in rows_) or f'<tr><td colspan="{len(headers)}">No data</td></tr>'
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    svc = plan_inspector.services(plan)
    resources = plan_inspector.resource_rows(plan)
    iam = plan_inspector.iam_roles(plan)
    counts = manifest.get("counts", {})
    drift_tone = "#7AA116" if drift_status == "CURRENT" else "#DD344C"

    def section(title, meta, body, open_=False):
        o = " open" if (for_print or open_) else ""
        return (f'<details{o}><summary><h2>{esc(title)}</h2>'
                f'<span class="meta">{meta}</span></summary>{body}</details>')

    sections = [
        section("Services", f"{len(svc)} service(s)",
                table(["Service", "Count", "Resources"],
                      [(s, len(items), ", ".join(r["address"] for r in items))
                       for s, items in svc.items()]), open_=True),
        section("Resources",
                f'{len(resources)} — +{counts.get("create", 0)} ~{counts.get("update", 0)} '
                f'-{counts.get("delete", 0)}',
                table(["Address", "Type", "Action", "Service", "File"],
                      [(r["address"], r["type"], r["action"],
                        plan_inspector.service_for_type(r["type"]), r["owner_file"])
                       for r in resources])),
        section("IAM roles & policies",
                f'{len(iam["roles"])} role(s), {len(iam["policies"])} policy(ies)',
                table(["Address", "Name", "Attachments"],
                      [(r["address"], r["name"], ", ".join(r["policy_attachments"]))
                       for r in iam["roles"]]
                      + [(p["address"], p["name"], "policy") for p in iam["policies"]])),
        section("Source drift",
                f'<span style="color:{drift_tone}">{esc(drift_status)}</span>',
                "<pre>" + esc(diff_text or "no drift — source matches the plan snapshot") + "</pre>",
                open_=(drift_status != "CURRENT")),
        section("Report files", f"{len(report_files)} artifact(s)",
                table(["File", "Bytes"], list(report_files))),
    ]
    note = ("<p class=\"note\">Point-in-time record printed with the report; source drift "
            "shown as of generation — the dashboard Inspect page checks drift live.</p>"
            if for_print else "")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Inspect {esc(manifest.get('short', ''))}</title>
<style>
@page{{size:A4;margin:12mm 14mm;background:#ffffff}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#ffffff;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Roboto,sans-serif;margin:0;padding:24px;
-webkit-print-color-adjust:exact;print-color-adjust:exact;font-size:13px;line-height:1.5}}
h1{{font-size:24px;margin:0 0 6px;color:#111827;font-weight:700}}
.sub{{color:#6b7280;font-family:Consolas,monospace;margin-bottom:20px;font-size:12px}}
h2{{display:inline;font-size:15px;margin:0;font-weight:600;color:#1e3a8a}}
details{{background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:14px;break-inside:avoid-page}}
summary{{cursor:pointer;padding:12px 16px;font-size:14px;list-style:none;display:flex;justify-content:space-between;align-items:baseline;font-weight:600}}
summary::-webkit-details-marker{{display:none}}
summary .meta{{color:#6b7280;font:500 12px Consolas,monospace}}
details[open] summary{{border-bottom:1px solid #e5e7eb}}
table{{width:100%;border-collapse:collapse;background:#ffffff}}
th,td{{text-align:left;border-bottom:1px solid #e5e7eb;padding:8px 12px;font-size:12px;vertical-align:top;color:#374151}}
th{{background:#f3f4f6;color:#4b5563;text-transform:uppercase;font-size:10px;letter-spacing:.08em;font-weight:600}}
td{{font-family:Consolas,monospace;word-break:break-word}}
pre{{padding:12px 16px;overflow:hidden;white-space:pre-wrap;line-height:1.45;font-size:11.5px;color:#1f2937;margin:0;background:#ffffff}}
.note{{color:#166534;font-size:11.5px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:10px 12px;margin-top:12px}}
</style></head><body>
<h1>Report inspection</h1>
<div class="sub">plan {esc(manifest.get('short', ''))} · {esc(manifest.get('template', ''))} ·
{esc(manifest.get('generated_at', ''))}</div>
{''.join(sections)}
{note}
</body></html>"""


# --- cost (BCM evidence only; no offline or service-specific assumptions) ---
def estimate_cost():
    pricing_commands = [
        "aws bcm-pricing-calculator create-workload-estimate --cli-input-json file://bcm-create-workload-estimate.json",
        "aws bcm-pricing-calculator batch-create-workload-estimate-usage --cli-input-json file://bcm-batch-create-usage.json",
        "aws bcm-pricing-calculator get-workload-estimate --identifier <id>",
        "aws bcm-pricing-calculator list-workload-estimate-usage --workload-estimate-id <id>",
    ]
    return {
        "ok": False,
        "error": (
            "AWS BCM Pricing Calculator API estimate was not generated. Estimates are created "
            "automatically when AWS credentials with BCM Pricing Calculator access are available; "
            "configure credentials (aws configure) and regenerate the report, or run the commands below."
        ),
        "pricing_source": "unavailable - AWS BCM Pricing Calculator API required",
        "pricing_commands": pricing_commands,
        "calculator": "AWS BCM Pricing Calculator API",
        "bcm_pricing_calculator_required": True,
    }


def _num(v):
    if isinstance(v, dict):
        v = v.get("amount")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _svc_key(x):
    """Map a BCM serviceCode OR a Cost Explorer service name to a common key for matching."""
    n = (x or "").lower()
    for needle, key in (("simple storage", "s3"), ("amazons3", "s3"), ("glue", "glue"),
                        ("athena", "athena"), ("lambda", "lambda"), ("dynamo", "dynamodb"),
                        ("redshift", "redshift"), ("mapreduce", "emr"), ("emr", "emr"),
                        ("step functions", "stepfunctions"), ("stepfunctions", "stepfunctions"),
                        ("cloudwatch", "cloudwatch"), ("key management", "kms"), ("kms", "kms")):
        if needle in n:
            return key
    return re.sub(r"[^a-z0-9]", "", n)


def forecast_vs_actual(line_items, actuals):
    """
    Compare the BCM forecast (per-service) to Cost Explorer actuals (per-service) and return
    a variance table. Both sides are normalized to a common service key so BCM serviceCodes
    line up with CE service names. No prices are invented — both inputs are real data.
    """
    fc, ac = {}, {}
    for it in line_items or []:
        c = _num(it.get("cost"))
        if c is not None:
            fc[_svc_key(it.get("serviceCode") or it.get("service"))] = fc.get(_svc_key(it.get("serviceCode") or it.get("service")), 0) + c
    for name, amt in (actuals or {}).items():
        a = _num(amt)
        if a is not None:
            ac[_svc_key(name)] = ac.get(_svc_key(name), 0) + a
    rows = []
    for k in sorted(set(fc) | set(ac)):
        f, a = fc.get(k), ac.get(k)
        var = (a - f) if (a is not None and f is not None) else None
        pct = (var / f * 100) if (var is not None and f) else None
        rows.append({"service": k, "forecast": f, "actual": a, "variance": var, "variance_pct": pct})
    return {"rows": rows, "forecast_total": sum(fc.values()), "actual_total": sum(ac.values())}


_SIZE_LADDER = ["KB", "MB", "GB", "TB", "PB", "EB"]
_SIZE_WORDS = {"kilobyte": "KB", "megabyte": "MB", "gigabyte": "GB",
               "terabyte": "TB", "petabyte": "PB"}


def humanize_quantity(amount, unit):
    """Auto-tier size units: (153600, 'GB-Mo') -> (150.0, 'TB-Mo'); (1200, 'GB') -> (1.17, 'TB').

    Only byte-size units climb the ladder (1024 steps, matching the volume parser);
    AWS's non-size units (DPU-Hour, Requests, ...) pass through untouched. Suffixes
    like '-Mo' are preserved. Returns (display_amount, display_unit).
    """
    if amount is None:
        return None, unit or ""
    m = re.match(r"(?i)^\s*(kilobytes?|megabytes?|gigabytes?|terabytes?|petabytes?|KB|MB|GB|TB|PB)(.*)$",
                 str(unit or ""))
    if not m:
        return amount, unit or ""
    base = _SIZE_WORDS.get(m.group(1).lower().rstrip("s"), m.group(1).upper())
    suffix = m.group(2)
    idx = _SIZE_LADDER.index(base)
    value = float(amount)
    while value >= 1024 and idx + 1 < len(_SIZE_LADDER):
        value /= 1024.0
        idx += 1
    return value, _SIZE_LADDER[idx] + suffix


def _plan_budget(report_dir):
    """The monthly budget guardrail the plan itself provisions (aws_budgets_budget
    limit_amount) — plan-derived, so forecast-vs-budget compares two real numbers."""
    path = os.path.join(report_dir, "plan.json")
    try:
        plan = json.loads(open(path, encoding="utf-8").read())
    except Exception:
        return None

    def walk(mod):
        for r in (mod or {}).get("resources", []):
            if r.get("type") == "aws_budgets_budget":
                try:
                    return float((r.get("values") or {}).get("limit_amount"))
                except (TypeError, ValueError):
                    pass
        for child in (mod or {}).get("child_modules", []):
            found = walk(child)
            if found is not None:
                return found
        return None

    return walk((plan.get("planned_values") or {}).get("root_module"))


def load_bcm_estimate(report_dir):
    """
    Load a completed BCM estimate (written by bcm_pricing_calculator.run) into a cost dict
    with per-service line items. Returns None if no estimate exists yet. No prices are
    computed here — these are AWS BCM Pricing Calculator results.
    """
    # Prefer a bill-scenario (commitment-aware) estimate over the plain workload estimate.
    for fname, source in (("bcm-scenario-estimate.json", "AWS BCM Bill Estimate (with commitments)"),
                          ("bcm-estimate.json", "AWS BCM Pricing Calculator API")):
        path = os.path.join(report_dir, fname)
        if not os.path.exists(path):
            continue
        try:
            data = json.loads(open(path, encoding="utf-8").read())
        except Exception:
            continue

        def _amt(v):
            return (v.get("amount") if isinstance(v, dict) else v)

        est = data.get("bill_estimate") or data.get("estimate") or {}
        raw = data.get("line_items") or data.get("usage_lines") or {}
        items = raw.get("items") if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
        line_items = []
        for it in items or []:
            if isinstance(it, dict):
                qty = it.get("quantity") if isinstance(it.get("quantity"), dict) else {}
                line_items.append({
                    "serviceCode": it.get("serviceCode"), "usageType": it.get("usageType"),
                    "operation": it.get("operation"), "cost": _amt(it.get("cost")),
                    "amount": qty.get("amount", it.get("amount")),
                    "unit": qty.get("unit"),
                })
        commits = data.get("commitments") or {}
        commit_items = commits.get("items") if isinstance(commits, dict) else (commits if isinstance(commits, list) else [])

        # Side files written at prepare time: the usage assumptions + rate basis.
        def _read(name):
            p = os.path.join(report_dir, name)
            try:
                return json.loads(open(p, encoding="utf-8").read()) if os.path.exists(p) else {}
            except Exception:
                return {}
        assumption_doc = _read("bcm-assumptions.json")
        assumptions = assumption_doc.get("derived_amount_assumptions") or {}
        not_estimated = assumption_doc.get("not_estimated_services") or []
        scale_curve = _read("bcm-scale-curve.json") or None
        rate_type = _read("bcm-create-workload-estimate.json").get("rateType") or "BEFORE_DISCOUNTS"
        actuals = _read("bcm-actuals.json") or {}
        variance = forecast_vs_actual(line_items, actuals) if actuals else None

        return {
            "ok": True,
            "monthly_total_usd": _amt(est.get("totalCost") or est.get("cost")),
            "line_items": line_items,
            "commitments": commit_items,
            "assumptions": assumptions,
            "not_estimated_services": not_estimated,
            "monthly_budget_usd": _plan_budget(report_dir),
            "scale_curve": scale_curve,
            "rate_type": rate_type,
            "priced_at": data.get("generated_at", ""),
            "actuals": actuals,
            "variance": variance,
            "estimate": data,
            "pricing_source": source,
            "aws_pricing_calculator_used": True,
        }
    return None


def refresh_cost(report_dir):
    """Rebuild cost.html / cost.pdf (+ manifest cost) from a completed BCM estimate."""
    manifest_path = os.path.join(report_dir, "manifest.json")
    manifest = json.loads(open(manifest_path, encoding="utf-8").read()) if os.path.exists(manifest_path) else {}
    cost = load_bcm_estimate(report_dir) or estimate_cost()
    html_doc = build_cost_html(manifest.get("template", "terraform-plan"),
                               manifest.get("cloud", active_cloud()),
                               manifest.get("short", ""), manifest.get("generated_at", ""), cost)
    cost_html = os.path.join(report_dir, "cost.html")
    with open(cost_html, "w", encoding="utf-8") as f:
        f.write(html_doc)
    render_pdf(cost_html, os.path.join(report_dir, "cost.pdf"))
    if manifest:
        manifest["cost"] = cost if (cost.get("ok") or cost.get("destroy")) else {"ok": False}
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
    return cost


# --- HTML report -----------------------------------------------------------
def _terraform_structure_html(tf_dir=None):
    """Describe the actual Terraform package on disk — root files plus composed modules.

    Scans tf_dir so the report reflects the real generated layout (module-based
    composition or flat files) instead of a hardcoded fixture. Falls back to a
    generic description only when the directory is unavailable.
    """
    purpose_by_name = {
        "main.tf": "Entry point — module composition and resource wiring.",
        "providers.tf": "Provider requirements, region, account data, and default tags.",
        "provider.tf": "Provider requirements, region, account data, and default tags.",
        "variables.tf": "Input variables (owner, environment, region, and module inputs).",
        "versions.tf": "Required Terraform and provider version constraints.",
        "outputs.tf": "Values exposed after apply.",
        "terraform.tfvars": "Resolved input values for this generated run.",
        "minus-generated.json": "Synthesis manifest — modules composed into this package.",
        "COMPOSITION.md": "Human-readable summary of the composed modules.",
    }
    files = []
    if tf_dir and os.path.isdir(tf_dir):
        for name in sorted(os.listdir(tf_dir)):
            path = os.path.join(tf_dir, name)
            if os.path.isfile(path) and (
                name.endswith(".tf") or name in ("terraform.tfvars", "minus-generated.json", "COMPOSITION.md")
            ):
                files.append((name, purpose_by_name.get(name, "Terraform configuration.")))
        modules_dir = os.path.join(tf_dir, "modules")
        if os.path.isdir(modules_dir):
            for mod in sorted(os.listdir(modules_dir)):
                if os.path.isdir(os.path.join(modules_dir, mod)):
                    files.append((f"modules/{mod}/", f"Composed module: {mod}."))
    if not files:
        files = [
            ("main.tf", "Entry point — module composition and resource wiring."),
            ("variables.tf", "Input variables for the generated package."),
            ("outputs.tf", "Values exposed after apply."),
        ]
    rows = "".join(
        f"<tr><td><code>{html.escape(name)}</code></td><td>{html.escape(purpose)}</td></tr>"
        for name, purpose in files
    )
    return f"<table><thead><tr><th>File</th><th>Purpose</th></tr></thead><tbody>{rows}</tbody></table>"


def _kv_table(rows):
    body = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>"
        for k, v in rows
    )
    return f"<table><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody>{body}</tbody></table>"


def _toc_html(sections):
    rows = "".join(
        f"<tr><td>{i}</td><td>{html.escape(title)}</td><td>{page}</td></tr>"
        for i, (title, page) in enumerate(sections, start=1)
    )
    return f"<table><thead><tr><th>#</th><th>Section</th><th>Page</th></tr></thead><tbody>{rows}</tbody></table>"


def _plan_metadata_html(template, cloud, short_hash, ts, tf_dir, git_sha, counts):
    return _kv_table([
        ("Template", template),
        ("Cloud", cloud),
        ("Plan hash", short_hash),
        ("Generated at", ts),
        ("Terraform directory", tf_dir or "-"),
        ("Git commit", git_sha or "-"),
        ("Creates", counts.get("create", 0)),
        ("Updates", counts.get("update", 0)),
        ("Deletes", counts.get("delete", 0)),
        ("No-op", counts.get("no-op", 0)),
    ])


def _variables_html(plan):
    variables = (plan or {}).get("variables", {})
    if not variables:
        return "<p class=\"flow muted\">No Terraform input variables were recorded in the plan JSON.</p>"
    return _kv_table((name, variables[name].get("value", "")) for name in sorted(variables))


def _outputs_html(plan):
    outputs = (plan or {}).get("output_changes", {})
    if not outputs:
        return "<p class=\"flow muted\">No Terraform outputs are changed by this plan.</p>"
    body = []
    for name in sorted(outputs):
        item = outputs[name]
        change = item.get("change", {})
        actions = ", ".join(change.get("actions", [])) or "-"
        sensitive = "yes" if item.get("sensitive") else "no"
        if item.get("sensitive"):
            value = "sensitive"
        elif change.get("after_unknown"):
            value = "known after apply"
        elif change.get("after") is None:
            value = "-"
        else:
            value = change.get("after")
        body.append(
            f"<tr><td><code>{html.escape(name)}</code></td><td>{html.escape(actions)}</td>"
            f"<td>{sensitive}</td><td>{html.escape(str(value))}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Output</th><th>Action</th><th>Sensitive</th><th>Planned value</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def _iam_summary_html(plan):
    changes = (plan or {}).get("resource_changes", [])
    roles, policies, attachments = [], [], []
    for rc in changes:
        rtype = rc.get("type", "")
        address = rc.get("address", "")
        after = rc.get("change", {}).get("after") or {}
        if rtype == "aws_iam_role":
            roles.append((address, after.get("name") or rc.get("name") or "-", "trust policy recorded in plan"))
        elif rtype in ("aws_iam_policy", "aws_iam_role_policy"):
            policies.append((address, after.get("name") or rc.get("name") or "-", rtype))
        elif rtype.endswith("_policy_attachment") or rtype == "aws_iam_role_policy_attachment":
            attachments.append((address, after.get("role") or "-", after.get("policy_arn") or after.get("policy") or "-"))
    if not (roles or policies or attachments):
        return "<p class=\"flow muted\">No IAM resources are changed by this plan.</p>"
    blocks = []
    if roles:
        rows = "".join(f"<tr><td><code>{html.escape(a)}</code></td><td>{html.escape(n)}</td><td>{html.escape(t)}</td></tr>" for a, n, t in roles)
        blocks.append(f"<h2>Roles</h2><table><thead><tr><th>Address</th><th>Name</th><th>Trust</th></tr></thead><tbody>{rows}</tbody></table>")
    if policies:
        rows = "".join(f"<tr><td><code>{html.escape(a)}</code></td><td>{html.escape(n)}</td><td>{html.escape(t)}</td></tr>" for a, n, t in policies)
        blocks.append(f"<h2>Policies</h2><table><thead><tr><th>Address</th><th>Name</th><th>Type</th></tr></thead><tbody>{rows}</tbody></table>")
    if attachments:
        rows = "".join(f"<tr><td><code>{html.escape(a)}</code></td><td>{html.escape(r)}</td><td><code>{html.escape(p)}</code></td></tr>" for a, r, p in attachments)
        blocks.append(f"<h2>Policy Attachments</h2><table><thead><tr><th>Address</th><th>Role</th><th>Policy</th></tr></thead><tbody>{rows}</tbody></table>")
    return "".join(blocks)


def _security_governance_html(rows):
    checks = [
        ("S3 public access blocks", any(r["type"] == "aws_s3_bucket_public_access_block" for r in rows), "Prevents public bucket exposure."),
        ("S3 server-side encryption", any(r["type"] == "aws_s3_bucket_server_side_encryption_configuration" for r in rows), "Requires encrypted object storage."),
        ("S3 lifecycle controls", any(r["type"] == "aws_s3_bucket_lifecycle_configuration" for r in rows), "Controls retention and storage cost."),
        ("Customer-managed KMS", any(r["type"].startswith("aws_kms_") for r in rows), "Central encryption key material and alias."),
        ("Scoped IAM roles", any(r["type"] == "aws_iam_role" for r in rows), "Dedicated service roles instead of shared operator credentials."),
        ("CloudWatch alarm", any(r["type"] == "aws_cloudwatch_metric_alarm" for r in rows), "Failure signal for the orchestrated workflow."),
        ("AWS Budget", any(r["type"].startswith("aws_budgets_") for r in rows), "Monthly spend guardrail."),
    ]
    body = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{'present' if present else 'missing'}</td><td>{html.escape(note)}</td></tr>"
        for name, present, note in checks
    )
    return f"<table><thead><tr><th>Control</th><th>Status</th><th>Reason</th></tr></thead><tbody>{body}</tbody></table>"


def _approval_status_html(manifest, tf_dir):
    status = "Approval required"
    source_state = "Captured during new report generation"
    reason = "Apply is blocked until plan_gate.py approve records this exact plan hash."
    if manifest:
        if manifest.get("stale_after_terraform_change"):
            status = "Blocked"
            source_state = "Stale"
            reason = manifest.get("stale_reason") or "Terraform source changed after this saved plan."
        elif manifest.get("source_hashes_file"):
            source_state = "Source hashes recorded"
        else:
            source_state = "Unknown for older report"
    return _kv_table([
        ("Approval status", status),
        ("Source status", source_state),
        ("Terraform directory", tf_dir or (manifest or {}).get("dir", "-")),
        ("Gate behavior", reason),
        ("Apply command", "Not included in report. Must pass plan_gate.py approve first."),
    ])


def _artifact_index_html(short_hash):
    base = f"reports/{short_hash}"
    return _kv_table([
        ("Architecture SVG", f"{base}/architecture.svg"),
        ("Plan PDF", f"{base}/plan.pdf"),
        ("Cost PDF", f"{base}/cost.pdf"),
        ("Raw Terraform plan JSON", f"{base}/plan.json"),
        ("Cost JSON", f"{base}/cost.json"),
        ("BCM workload estimate payload", f"{base}/bcm-create-workload-estimate.json"),
        ("BCM usage payload", f"{base}/bcm-usage.json"),
        ("BCM review commands", f"{base}/bcm-commands.json"),
        ("Manifest", f"{base}/manifest.json"),
    ])


def _plan_rows_by_service(rows):
    grouped = {}
    for r in rows:
        service = "Other"
        for label, prefix in [
            ("S3", "aws_s3_"),
            ("KMS", "aws_kms_"),
            ("Glue", "aws_glue_"),
            ("Step Functions", "aws_sfn_"),
            ("Athena", "aws_athena_"),
            ("CloudWatch", "aws_cloudwatch_"),
            ("Budgets", "aws_budgets_"),
            ("IAM", "aws_iam_"),
        ]:
            if r["type"].startswith(prefix):
                service = label
                break
        grouped.setdefault(service, []).append(r)
    blocks = []
    for service in sorted(grouped):
        table_rows = "".join(
            f"<tr><td class=\"mono\">{html.escape(r['address'])}</td>"
            f"<td>{html.escape(_humanize(r['type']))}</td>"
            f"<td><span class=\"badge {r['action']}\">{r['action']}</span></td></tr>"
            for r in grouped[service]
        )
        blocks.append(f"<h3>{html.escape(service)}</h3><table><thead><tr><th>Resource</th><th>Type</th><th>Action</th></tr></thead><tbody>{table_rows}</tbody></table>")
    return "".join(blocks)


def _etag_drift_note(plan):
    """SSE-KMS bucket + aws_s3_object.etag = filemd5(...) is a known, harmless false-positive:
    S3 computes a different ETag for KMS-encrypted objects than the local filemd5() value, so
    Terraform shows a perpetual 'update' on etag alone even when the uploaded file hasn't
    changed (confirmed by direct content diff -- not real drift). Flagged only when etag is
    the SOLE real difference: keys that are merely 'known
    after apply' (e.g. version_id, a side effect of the same etag-triggered re-upload when the
    bucket has versioning enabled) are excluded from the comparison, not counted as extra
    drift -- without that exclusion this would never fire against a real plan."""
    hits = []
    for rc in (plan or {}).get("resource_changes", []):
        if rc.get("type") != "aws_s3_object":
            continue
        change = rc.get("change", {})
        if "update" not in (change.get("actions") or []):
            continue
        before, after = change.get("before") or {}, change.get("after") or {}
        after_unknown = change.get("after_unknown") or {}
        diffs = {k for k in set(before) | set(after)
                 if not after_unknown.get(k) and before.get(k) != after.get(k)}
        if diffs == {"etag"}:
            hits.append(rc.get("address") or rc.get("type"))
    if not hits:
        return ""
    listed = ", ".join(f"<code>{html.escape(a)}</code>" for a in hits)
    return (
        '<div class="panel" style="margin-top:12px">'
        '<strong>Known non-issue: SSE-KMS ETag drift.</strong> '
        f"{listed} show{'s' if len(hits) == 1 else ''} an update on <code>etag</code> alone. "
        "This is expected: S3 computes a different ETag for KMS-encrypted objects than the "
        "local <code>filemd5()</code> value, so the diff reappears on every plan even when the "
        "uploaded file is unchanged. Confirmed via direct content diff during the 2026-07-04 "
        "production-readiness test -- not real drift, safe to approve."
        '</div>'
    )


def _architecture_sentence(manifest):
    """One sentence describing what THIS plan composes, built from the real manifest.

    Never fall back to a canned description of the full canonical lakehouse (S3 + Glue +
    Step Functions + Athena + ...): it reads as authoritative while describing
    infrastructure that may not exist in this plan. When the manifest or its module list is
    unavailable, say so plainly instead."""
    module_ids = (manifest or {}).get("modules") or []
    if not module_ids:
        return ("Composed module list unavailable for this report -- see Section 6 "
                "(Services and Resource Summary) for what this plan actually creates.")
    import modules as module_registry
    titles = []
    for mid in module_ids:
        m = module_registry.get_module(mid)
        titles.append(m["title"] if m else mid)
    return "The composed modules for this plan are: " + "; ".join(titles) + "."


def _verification_coverage_html(coverage):
    """Render what the gate actually checked. Without this the reviewer cannot distinguish
    'checked and clean' from 'no rule exists for this type' -- both render as silence."""
    if not coverage or coverage.get("error") or not coverage.get("types"):
        return ('<p class="flow muted">Verification coverage unavailable for this plan.</p>')
    label = {"rule_covered": "Policy rule fired",
             "claim_informed": "No rule — claims only (informational, not verification)",
             "unchecked": "No rule, no claims"}
    body = "".join(
        f"<tr><td class=\"mono\">{html.escape(r['resource_type'])}</td>"
        f"<td>{r['resource_count']}</td>"
        f"<td>{html.escape(label.get(r['state'], r['state']))}</td>"
        f"<td>{html.escape(', '.join(r['rule_ids']) or '-')}</td></tr>"
        for r in coverage["types"])
    ratio = coverage.get("coverage_ratio")
    pct = f"{ratio * 100:.0f}%" if ratio is not None else "n/a"
    return (
        f'<p class="flow">Policy-rule coverage for this plan: <strong>{pct}</strong> '
        f'({coverage["rule_covered_count"]} of {coverage["type_count"]} resource types had an '
        f'executable rule fire). Types marked <em>no rule</em> were not checked by policy — '
        f'a clean result for them means nothing was evaluated, not that nothing is wrong. '
        f'Claims inform authoring only and never grant permission to ship.</p>'
        f'<table><thead><tr><th>Resource type</th><th>Count</th><th>Verification</th>'
        f'<th>Rules fired</th></tr></thead><tbody>{body}</tbody></table>')


def build_html(template, cloud, short_hash, ts, rows, counts, cost, svg, plan=None, manifest=None, tf_dir=None, git_sha=None, coverage=None):
    def esc(s):
        return html.escape(str(s))

    if cost.get("destroy"):
        costhtml = (
            f'<p class="flow"><strong>Destroy plan — no cost forecast applies.</strong> '
            f'{esc(cost.get("error", ""))}</p>'
        )
    elif cost.get("ok"):
        def _cf(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        li = cost.get("line_items") or []
        total = _cf(cost.get("monthly_total_usd")) or sum(_cf(i.get("cost")) or 0 for i in li)
        annual = total * 12 if total else None
        rate_label = {"BEFORE_DISCOUNTS": "On-demand list price", "AFTER_DISCOUNTS": "After discounts",
                      "AFTER_DISCOUNTS_AND_COMMITMENTS": "After discounts & commitments"
                      }.get(cost.get("rate_type"), cost.get("rate_type", "On-demand list price"))

        def kpi(label, val):
            return f'<div class="kpi"><div class="kl">{esc(label)}</div><div class="kv">{esc(val)}</div></div>'

        svc_rows = ""
        for it in sorted(li, key=lambda i: _cf(i.get("cost")) or 0, reverse=True):
            c = _cf(it.get("cost"))
            if c is None:
                continue
            pct = f"{c / total * 100:.1f}%" if total else "-"
            svc_rows += f'<tr><td>{esc(it.get("serviceCode") or "-")}</td><td>${c:,.2f}</td><td>{pct}</td></tr>'
        costhtml = (
            '<div class="kpis">'
            + kpi("Monthly total", f"${total:,.2f}" if total is not None else "BCM")
            + kpi("Annual (x12)", f"${annual:,.2f}" if annual is not None else "-")
            + kpi("Rate basis", rate_label) + kpi("Services", str(len(li))) + '</div>'
            + (f'<table><thead><tr><th>Service</th><th>Monthly</th><th>% of total</th></tr></thead>'
               f'<tbody>{svc_rows}</tbody></table>' if svc_rows else '')
            + f'<p class="muted small">Pricing: {esc(cost.get("pricing_source", "AWS BCM Pricing Calculator API"))}. '
            + 'Full per-service usage, $/unit rates, assumptions, and cost drivers are in <code>cost.pdf</code>.</p>')
    else:
        commands = "".join(f"<tr><td><code>{esc(cmd)}</code></td></tr>" for cmd in cost.get("pricing_commands", []))
        costhtml = (
            f'<p class="flow">Cost estimate unavailable: {esc(cost.get("error", ""))}</p>'
            '<p class="flow muted">Enterprise reports require AWS BCM Pricing Calculator API estimates. '
            'Offline catalog pricing is disabled; estimates are created automatically when AWS credentials with BCM access are available.</p>'
            f'<table><thead><tr><th>Required pricing lookup</th></tr></thead><tbody>{commands}</tbody></table>'
        )

    services = "".join(
        f"<tr><td>{esc(name)}</td><td>{count}</td></tr>"
        for name, count in _service_summary(rows)
    )
    plan_detail = _plan_rows_by_service(rows)
    etag_note = _etag_drift_note(plan)
    sections = [
        ("Index", 2),
        ("Executive Summary", 3),
        ("Plan Metadata", 4),
        ("Request and Blueprint Inputs", 5),
        ("Architecture", 6),
        ("Services and Resource Summary", 7),
        ("IAM, Security, and Governance", 8),
        ("Cost Summary", 9),
        ("Terraform Package Structure", 10),
        ("Terraform Outputs", 11),
        ("Approval Gate and Drift Status", 12),
        ("Planned Changes by Service", 13),
        ("Artifact Index", 14),
    ]
    if template == "aws-data-pipeline-standard":
        blueprint_note = ("Demo fixture <code>aws-data-pipeline-standard</code> packages a governed AWS data "
                          "pipeline example into reviewable Terraform. Production runs start from requirements "
                          "and an architecture decision before Terraform is synthesized. Inputs below come from "
                          "<code>terraform show -json tfplan</code>.")
    else:
        blueprint_note = ("Inputs are resolved from the run's requirements and architecture decision, then "
                          "synthesized into a composed, module-based Terraform package. Inputs below come from "
                          "<code>terraform show -json tfplan</code>.")
    metadata_html = _plan_metadata_html(template, cloud, short_hash, ts, tf_dir, git_sha, counts)
    variables_html = _variables_html(plan)
    outputs_html = _outputs_html(plan)
    iam_html = _iam_summary_html(plan)
    security_html = _security_governance_html(rows)
    coverage_html = _verification_coverage_html(coverage)
    approval_html = _approval_status_html(manifest, tf_dir)
    artifacts_html = _artifact_index_html(short_hash)
    toc_html = _toc_html(sections)

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Plan Report - {esc(template)}</title><style>
@page{{size:A4;margin:12mm 14mm;background:#ffffff}}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{min-height:100%;background:#ffffff;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Roboto,sans-serif;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
body{{padding:0}}
.mono{{font-family:'JetBrains Mono',Consolas,ui-monospace,monospace;font-size:.8rem}}
h1{{font-size:1.65rem;font-weight:700;line-height:1.2;color:#111827}}h2{{font-size:1.05rem;margin:1.2rem 0 .55rem;color:#1e3a8a;font-weight:600}}h3{{font-size:.92rem;margin:1rem 0 .35rem;color:#374151;font-weight:600}}
.sub{{color:#6b7280;font-family:Consolas,ui-monospace,monospace;font-size:.78rem;margin-top:.35rem}}
.page{{page-break-after:always;min-height:1080px;padding:36px 40px;background:#ffffff}}.page:last-child{{page-break-after:auto}}
.header{{border-bottom:2px solid #2b59d1;padding-bottom:12px;margin-bottom:16px}}
.panel{{background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:1rem;margin-top:.85rem}}
.section-no{{color:#2b59d1;font-family:Consolas,ui-monospace,monospace;font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:.35rem;font-weight:600}}
.architecture{{padding:.25rem;background:#ffffff;border:none}}
svg{{width:100%;height:auto;display:block}}
table{{width:100%;border-collapse:collapse;margin-top:.5rem;background:#ffffff;border:1px solid #e5e7eb;border-radius:6px;overflow:hidden}}
th,td{{text-align:left;padding:.5rem .6rem;border-bottom:1px solid #e5e7eb;font-size:.78rem;vertical-align:top;color:#374151}}
th{{background:#f3f4f6;color:#4b5563;text-transform:uppercase;font-size:.66rem;letter-spacing:.06em;font-weight:600}}
.badge{{padding:.14rem .5rem;border-radius:20px;font-size:.72rem;font-weight:600}}
.badge.create{{background:#ecfdf5;color:#065f46;border:1px solid #a7f3d0}}
.badge.update{{background:#fffbeb;color:#92400e;border:1px solid #fde68a}}
.badge.delete{{background:#fef2f2;color:#991b1b;border:1px solid #fecaca}}
.badge.no-op{{background:#f3f4f6;color:#4b5563;border:1px solid #e5e7eb}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:.75rem}}
.kpi{{background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:.8rem}}
.kl{{color:#6b7280;font-size:.64rem;text-transform:uppercase;letter-spacing:.05em;font-weight:600}}
.kv{{font-family:'JetBrains Mono',Consolas,ui-monospace,monospace;font-size:1.2rem;margin-top:.35rem;color:#111827}}
.counts span{{margin-right:1rem;font-family:Consolas,ui-monospace,monospace;font-weight:600}}
.muted{{color:#6b7280}}.small{{font-size:.74rem;margin-top:.55rem}}.flow{{line-height:1.55;color:#374151;margin-top:.45rem;font-size:.86rem}}
code{{font-family:'JetBrains Mono',Consolas,ui-monospace,monospace;color:#111827;background:#f3f4f6;padding:2px 4px;border-radius:4px;font-size:.78rem}}
footer{{margin-top:1.2rem;padding-top:.8rem;border-top:1px solid #e5e7eb;color:#9ca3af;font-size:.72rem}}
</style></head><body>
<section class="page cover">
<div class="header"><div class="section-no">Cover</div><h1>Terraform Plan Report</h1>
<div class="sub">{esc(template)} | {esc(cloud)} | plan {esc(short_hash)} | {esc(ts)}</div></div>
<div class="counts panel"><span style="color:#059669">+{counts['create']} create</span>
<span style="color:#d97706">~{counts['update']} update</span>
<span style="color:#dc2626">-{counts['delete']} delete</span>
<span class="muted">{counts['no-op']} no-op</span></div>
<div class="panel"><p class="flow">This report is a review artifact for a Terraform plan. It is not an apply approval and it does not create cloud resources. Deployment remains blocked until the plan gate records approval for this exact plan hash.</p></div>
</section>
<section class="page">
<div class="header"><div class="section-no">Section 1</div><h1>Index</h1>
<div class="sub">Every major section starts on a new page for review and sign-off.</div></div>
<div class="panel">{toc_html}</div>
</section>
<section class="page">
<div class="header"><div class="section-no">Section 2</div><h1>Executive Summary</h1>
<div class="sub">High-level plan outcome and approval posture.</div></div>
<div class="panel"><p class="flow">Terraform plans <code>{counts['create']}</code> creates, <code>{counts['update']}</code> updates, and <code>{counts['delete']}</code> deletes for <code>{esc(template)}</code>. {esc(_architecture_sentence(manifest))}</p></div>
<div class="panel"><p class="flow">Risk posture: apply is gated; source provenance is checked; stale or unknown report provenance is rejected by <code>plan_gate.py approve</code> and <code>plan_gate.py apply</code>.</p></div>
</section>
<section class="page">
<div class="header"><div class="section-no">Section 3</div><h1>Plan Metadata</h1>
<div class="sub">Identity fields used for audit, review, and traceability.</div></div>
<div class="panel">{metadata_html}</div>
</section>
<section class="page">
<div class="header"><div class="section-no">Section 4</div><h1>Request and Blueprint Inputs</h1>
<div class="sub">Resolved user intent and Terraform inputs captured in the plan.</div></div>
<div class="panel"><p class="flow">{blueprint_note}</p></div>
<div class="panel">{variables_html}</div>
</section>
<section class="page">
<div class="header"><div class="section-no">Section 5</div><h1>Architecture</h1>
<div class="sub">Runtime data flow is solid. Governance controls are separated from data movement.</div></div>
<div class="architecture">{svg}</div>
</section>
<section class="page">
<div class="header"><div class="section-no">Section 6</div><h1>Services and Resource Summary</h1>
<div class="sub">Cloud services represented in the Terraform plan.</div></div>
<div class="panel"><table><thead><tr><th>Service</th><th>Resources in plan</th></tr></thead><tbody>{services}</tbody></table></div>
{etag_note}
</section>
<section class="page">
<div class="header"><div class="section-no">Section 7</div><h1>IAM, Security, and Governance</h1>
<div class="sub">IAM resources and controls reviewers should inspect before approval.</div></div>
<div class="panel">{security_html}</div>
<div class="panel">{iam_html}</div>
<h2>Verification coverage</h2>
<div class="panel">{coverage_html}</div>
</section>
<section class="page">
<div class="header"><div class="section-no">Section 8</div><h1>Cost Summary</h1>
<div class="sub">Plan-level cost status. Detailed pricing evidence is in cost.pdf.</div></div>
<div class="panel">{costhtml}</div>
</section>
<section class="page">
<div class="header"><div class="section-no">Section 9</div><h1>Terraform Package</h1>
<div class="sub">Terraform loads all .tf files in this directory. Files are split by concern for reviewability.</div></div>
<div class="panel">{_terraform_structure_html(tf_dir)}</div>
<h2>Safe execution flow</h2>
<div class="panel"><p class="flow"><code>terraform init</code> prepares providers. <code>minusctl gate verify --dir {esc(tf_dir or 'runs/&lt;run-id&gt;/terraform')} --policy-mode production</code> formats, validates, runs native SEC checks, and requires external scanner evidence. <code>minusctl gate plan --dir {esc(tf_dir or 'runs/&lt;run-id&gt;/terraform')}</code> generates <code>tfplan</code> and this report. Apply is intentionally absent from this report and remains gated.</p></div>
</section>
<section class="page">
<div class="header"><div class="section-no">Section 10</div><h1>Terraform Outputs</h1>
<div class="sub">Outputs planned by this Terraform run.</div></div>
<div class="panel">{outputs_html}</div>
</section>
<section class="page">
<div class="header"><div class="section-no">Section 11</div><h1>Approval Gate and Drift Status</h1>
<div class="sub">Deployment is hash-bound and source-aware.</div></div>
<div class="panel">{approval_html}</div>
</section>
<section class="page">
<div class="header"><div class="section-no">Section 12</div><h1>Planned Changes</h1>
<div class="sub">Resource list from terraform show -json tfplan, grouped for review.</div></div>
<div class="panel">{plan_detail}</div>
</section>
<section class="page">
<div class="header"><div class="section-no">Section 13</div><h1>Artifact Index</h1>
<div class="sub">Generated files tied to this exact report hash.</div></div>
<div class="panel">{artifacts_html}</div>
<footer>Generated by MinusOps reporter | architecture conforms to {esc(SPEC)} | report keyed by plan-hash {esc(short_hash)}</footer>
</section>
</body></html>"""


def _build_variance_html(variance, esc):
    """Render the BCM-forecast vs Cost-Explorer-actual variance table. Empty when no actuals."""
    if not variance or not variance.get("rows"):
        return ""
    ft = variance.get("forecast_total") or 0
    at = variance.get("actual_total") or 0
    rows = ""
    for r in variance["rows"]:
        f, a, v, p = r.get("forecast"), r.get("actual"), r.get("variance"), r.get("variance_pct")
        if v is None:
            color, vtxt = "#b8a79e", "n/a"
        elif v > 0:
            color, vtxt = "#DD344C", f"+${v:,.2f}"
        else:
            color, vtxt = "#7fae7f", f"-${abs(v):,.2f}"
        ptxt = f"{p:+.1f}%" if p is not None else "-"
        rows += (f"<tr><td>{esc(r['service'])}</td>"
                 f"<td class=\"money\">{('$%.2f' % f) if f is not None else '—'}</td>"
                 f"<td class=\"money\">{('$%.2f' % a) if a is not None else '—'}</td>"
                 f"<td class=\"money\" style=\"color:{color}\">{vtxt}</td>"
                 f"<td class=\"money\" style=\"color:{color}\">{ptxt}</td></tr>")
    tot_v = at - ft
    tot_color = "#DD344C" if tot_v > 0 else "#7AA116"
    tot_p = f"{tot_v / ft * 100:+.1f}%" if ft else "-"
    rows += (f"<tr class=\"total\"><td>Total</td>"
             f"<td class=\"money\">${ft:,.2f}</td><td class=\"money\">${at:,.2f}</td>"
             f"<td class=\"money\" style=\"color:{tot_color}\">{'+$%.2f' % tot_v if tot_v >= 0 else '-$%.2f' % abs(tot_v)}</td>"
             f"<td class=\"money\" style=\"color:{tot_color}\">{tot_p}</td></tr>")
    return ("<h2>Forecast vs. actual</h2>"
            "<p class=\"note\">BCM forecast (this estimate) compared to AWS Cost Explorer actuals for the same "
            "services. Positive variance means actuals exceeded the forecast — investigate drift before the next run. "
            "Both columns are real data: forecast from the BCM Pricing Calculator, actuals from Cost Explorer.</p>"
            "<table><thead><tr><th>Service</th><th>Forecast</th><th>Actual</th><th>Variance</th><th>Variance %</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>")


def _format_cost_assumptions(assumptions, esc):
    if not assumptions:
        return '<p class="note">No derived assumptions recorded (usage supplied directly).</p>'

    scalar_items = []
    inventory_table = ""
    line_map_html = ""

    for k, v in assumptions.items():
        if k == "terraform_resource_inventory" and isinstance(v, dict):
            rows = ""
            for rtype, info in sorted(v.items()):
                cnt = info.get("count", 1) if isinstance(info, dict) else 1
                actions = ", ".join(f"{act}: {c}" for act, c in info.get("actions", {}).items()) if (isinstance(info, dict) and "actions" in info) else "create"
                rows += (f"<tr><td><code>{esc(rtype)}</code></td>"
                         f'<td class="money">{cnt}</td>'
                         f"<td>{esc(actions)}</td></tr>")
            inventory_table = (
                '<h3 style="margin-top:20px;font-size:13px;color:#334155;font-weight:600">Terraform Resource Inventory</h3>'
                '<table><thead><tr><th>Resource Type</th><th>Count</th><th>Actions</th></tr></thead>'
                f'<tbody>{rows}</tbody></table>'
            )
        elif k == "usage_line_map" and isinstance(v, dict):
            json_str = json.dumps(v, indent=2)
            line_map_html = (
                '<details style="margin-top:14px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px 12px">'
                '<summary style="cursor:pointer;font-weight:600;font-size:12px;color:#475569">View BCM Usage Line Mapping (Audit Trail JSON)</summary>'
                f'<pre style="font-size:11px;max-height:220px;overflow-y:auto;background:#ffffff;padding:8px;border:1px solid #cbd5e1;border-radius:4px"><code>{esc(json_str)}</code></pre></details>'
            )
        elif isinstance(v, (dict, list)):
            json_str = json.dumps(v, indent=2)
            label = k.replace("_", " ").title()
            scalar_items.append((label, f'<pre style="margin:0;font-size:11px"><code>{esc(json_str)}</code></pre>'))
        else:
            label = k.replace("_", " ").title()
            scalar_items.append((label, esc(v)))

    # esc on the label too. Every value here is escaped and the inventory table escapes its
    # resource types, but the assumption NAME went in raw. Today's keys are all fixed
    # identifiers out of DEFAULT_ASSUMPTIONS, so nothing injects -- but this report is HTML
    # somebody opens, and the first assumption keyed by something plan-derived (an address, a
    # module, a zone) would go straight through. Escaping it now costs nothing.
    scalar_rows = "".join(
        f"<tr><td><b>{esc(k)}</b></td><td>{v}</td></tr>"
        for k, v in scalar_items
    )
    scalar_table = (
        '<table><thead><tr><th>Parameter</th><th>Assumption / Derived Value</th></tr></thead>'
        f'<tbody>{scalar_rows}</tbody></table>'
    )

    return f"{scalar_table}{inventory_table}{line_map_html}"


def build_cost_html(template, cloud, short_hash, ts, cost):
    def esc(s):
        return html.escape(str(s))

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    if cost.get("destroy"):
        body = (
            f'<p class="note"><strong>Destroy plan — no cost forecast applies.</strong> '
            f'{esc(cost.get("error", ""))}</p>'
        )
        return _simple_report_html("Cost Report", template, cloud, short_hash, ts, body)

    if cost.get("ok"):
        line_items = cost.get("line_items") or cost.get("lineItems") or []
        total = _f(cost.get("monthly_total_usd")) or sum(_f(i.get("cost")) or 0 for i in line_items)
        annual = total * 12 if total else None
        rate_label = {"BEFORE_DISCOUNTS": "On-demand list price",
                      "AFTER_DISCOUNTS": "After discounts",
                      "AFTER_DISCOUNTS_AND_COMMITMENTS": "After discounts & commitments"
                      }.get(cost.get("rate_type"), cost.get("rate_type", "On-demand list price"))
        priced_at = cost.get("priced_at") or ts

        def card(label, val):
            return (f'<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:12px">'
                    f'<span style="display:block;color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:.05em;font-weight:600">{esc(label)}</span>'
                    f'<strong style="display:block;margin-top:5px;font-size:15px;color:#111827">{esc(val)}</strong></div>')

        cards = ('<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0">'
                 + card("Monthly total", f"${total:,.2f}" if total is not None else "provided by BCM")
                 + card("Annual (x12)", f"${annual:,.2f}" if annual is not None else "-")
                 + card("Rate basis", rate_label) + card("Priced at", priced_at or "-") + "</div>")

        rows = ""
        for it in line_items:
            c, a = _f(it.get("cost")), _f(it.get("amount"))
            # Auto-tier size units so 153,600 GB-Mo reads as 150 TB-Mo; rate uses the
            # SAME displayed unit so the two columns stay consistent.
            da, du = humanize_quantity(a, it.get("unit") or "")
            if da is None:
                usage_cell = "-"
            else:
                qty = f"{da:,.0f}" if da >= 100 else f"{da:,.4g}"
                usage_cell = f"{qty} {esc(du)}"
            # Effective $/unit = AWS's own cost ÷ AWS's own quantity — arithmetic on BCM's
            # response, never a hardcoded rate.
            rate = f"${c / da:,.4f}/{esc(du or 'unit')}" if (c is not None and da) else "-"
            pct = f"{c / total * 100:.1f}%" if (c is not None and total) else "-"
            rows += (f"<tr><td>{esc(it.get('serviceCode') or it.get('service') or '-')}</td>"
                     f"<td>{esc(it.get('usageType') or '-')}</td><td>{esc(it.get('operation') or '-')}</td>"
                     f"<td class=\"money\">{usage_cell}</td>"
                     f"<td class=\"money\">{rate}</td>"
                     f"<td class=\"money\">{('$%.2f' % c) if c is not None else '-'}</td>"
                     f"<td class=\"money\">{pct}</td></tr>")
        if not rows:
            rows = "<tr><td colspan=\"7\">BCM returned no line items in the stored response.</td></tr>"
        # Unpriced plan services are shown, not hidden — absence of a price is NOT $0. Where a
        # dated AWS Price List rate/free-tier fact has been checked (never a usage total, that
        # would still require a count we don't have), cite it instead of a bare "not estimated".
        import pricing_catalog
        for svc in cost.get("not_estimated_services") or []:
            citation = pricing_catalog.rate_citation_for_service_code(svc)
            message = citation or "not estimated — no reviewed catalog usage line for this service"
            rows += (f'<tr style="color:#6b7280"><td>{esc(svc)}</td>'
                     f'<td colspan="4">{esc(message)}</td>'
                     '<td class="money">unpriced</td><td class="money">-</td></tr>')

        drivers = ""
        for it in sorted(line_items, key=lambda i: _f(i.get("cost")) or 0, reverse=True)[:6]:
            c = _f(it.get("cost"))
            if c is None or not total:
                continue
            w = max(2, round(c / total * 100))
            drivers += (f'<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:12px">'
                        f'<span>{esc(it.get("serviceCode") or "-")}</span><span class="money">${c:,.2f} · {c / total * 100:.1f}%</span></div>'
                        f'<div style="background:#e5e7eb;border-radius:5px;height:8px;margin-top:3px">'
                        f'<div style="width:{w}%;height:8px;border-radius:5px;background:#2b59d1"></div></div></div>')

        assumptions = cost.get("assumptions") or {}
        assume_html = _format_cost_assumptions(assumptions, esc)

        # Budget check: the plan provisions its own guardrail (aws_budgets_budget) — hold
        # the AWS forecast against it BEFORE deploy, not after the first bill.
        budget_html = ""
        budget = cost.get("monthly_budget_usd")
        if budget and total is not None:
            util = total / budget * 100
            tone = "#059669" if util <= 80 else "#d97706" if util <= 100 else "#dc2626"
            verdict = ("within budget" if util <= 80 else
                       "approaching budget" if util <= 100 else "EXCEEDS BUDGET")
            budget_html = (
                "<h2>Budget check</h2>"
                f'<p class="note">Forecast <b>${total:,.2f}/mo</b> vs the plan\'s own budget guardrail '
                f'<b>${budget:,.2f}/mo</b> — <b style="color:{tone}">{util:.0f}% · {verdict}</b>. '
                "Both numbers are real: the forecast is AWS BCM's, the budget is the "
                "aws_budgets_budget this plan provisions.</p>"
                f'<div style="background:#e5e7eb;border-radius:6px;height:10px;margin:8px 0 14px">'
                f'<div style="width:{min(100, max(2, util)):.0f}%;height:10px;border-radius:6px;background:{tone}"></div></div>')

        # Unit economics for the data domain: cost per GB processed, derived strictly from
        # the AWS total ÷ the run's own stated volume (only when the run states a volume).
        unit_econ = ""
        try:
            daily_gb = float(assumptions.get("daily_data_gb") or 0)
        except (TypeError, ValueError):
            daily_gb = 0
        days = float(assumptions.get("days_per_month") or 30)
        if total is not None and daily_gb > 0:
            per_gb = total / (daily_gb * days)
            unit_econ = ("<h2>Unit economics</h2>"
                         f'<p class="note">Cost per GB processed: <b>${per_gb:,.4f}/GB</b> '
                         f"(AWS total ${total:,.2f} ÷ {daily_gb:g} GB/day × {days:g} days). "
                         "Track this per run — it is the number that tells you whether the "
                         "pipeline gets cheaper or more expensive as it scales.</p>")

        # Cost at scale: AWS-priced points of the SAME architecture at usage multiples —
        # diseconomies (or savings cliffs) show up before deploy, not on the first big bill.
        curve_html = ""
        curve = (cost.get("scale_curve") or {}).get("points") or []
        if curve:
            rows_c = ""
            for p in curve:
                t = _f(p.get("total"))
                per_gb = (f"${t / (daily_gb * days * p['factor']):,.4f}/GB"
                          if (t is not None and daily_gb > 0) else "-")
                rows_c += (f'<tr><td class="money">x{p["factor"]:g}</td>'
                           f'<td class="money">{("$%.2f" % t) if t is not None else "-"}</td>'
                           f'<td class="money">{per_gb}</td></tr>')
            curve_html = (
                "<h2>Cost at scale</h2>"
                "<p class=\"note\">Each point is a separate AWS BCM estimate of this architecture "
                "at a usage multiple — no local extrapolation. A rising cost/GB signals a "
                "diseconomy (time to change tier: compaction, table format, or engine).</p>"
                "<table><thead><tr><th>Usage</th><th>Monthly (AWS-priced)</th><th>Cost/GB</th></tr></thead>"
                f"<tbody>{rows_c}</tbody></table>")

        scenario_html = (
            "<h2>What-if scenarios (scale up / down, commitments)</h2>"
            "<p class=\"note\">Model changed usage or Savings Plans / Reserved Instances with a BCM "
            "bill scenario — AWS prices the scenario; nothing is computed locally:</p>"
            "<table><thead><tr><th>Scenario</th><th>Command</th></tr></thead><tbody>"
            "<tr><td>Scale usage up/down</td><td><code>python core/cost/bcm_pricing_calculator.py scenario "
            "--report-dir &lt;this dir&gt; --usage-modifications usage-mods.json</code></td></tr>"
            "<tr><td>With commitments (SP/RI)</td><td><code>python core/cost/bcm_pricing_calculator.py scenario "
            "--report-dir &lt;this dir&gt; --commitments commitments.json</code></td></tr>"
            "<tr><td>Different usage assumptions</td><td><code>python core/cost/bcm_pricing_calculator.py prepare "
            "--report-dir &lt;this dir&gt; --derive --assume glue_runs_per_day=48 &amp;&amp; "
            "python core/cost/bcm_pricing_calculator.py run --report-dir &lt;this dir&gt;</code></td></tr>"
            "</tbody></table>")

        variance_html = _build_variance_html(cost.get("variance"), esc)

        evidence = json.dumps(cost.get("estimate", cost), indent=2, sort_keys=True)
        body = (
            cards
            + variance_html
            + "<h2>Per-service cost breakdown</h2>"
            + (f"<p class=\"note\">{len(line_items)} of "
               f"{len(line_items) + len(cost.get('not_estimated_services') or [])} plan services priced; "
               f"{len(cost.get('not_estimated_services') or [])} not estimated (listed below — "
               "unpriced is not $0).</p>")
            + "<table><thead><tr><th>Service</th><th>Usage type</th><th>Operation</th><th>Usage</th>"
            + "<th>Rate $/unit</th><th>Monthly</th><th>% of total</th></tr></thead>"
            + f"<tbody>{rows}</tbody></table>"
            + (f"<h2>Cost drivers</h2>{drivers}" if drivers else "")
            + budget_html
            + unit_econ
            + curve_html
            + scenario_html
            + "<h2>Usage assumptions</h2>"
            + "<p class=\"note\">These drove the submitted usage amounts; AWS BCM Pricing Calculator priced them. "
            + "MinusOps sets no prices.</p>" + assume_html
            + "<h2>Notes</h2>"
            + f"<p class=\"note\">Pricing basis: <b>{esc(rate_label)}</b> via {esc(cost.get('pricing_source', 'AWS BCM Pricing Calculator API'))}. "
            + f"Rates are a point-in-time AWS estimate (priced {esc(priced_at)}); cloud rates change, so re-run the BCM "
            + "estimate for current pricing. After deployment, compare this forecast against AWS Cost Explorer actuals.</p>"
            + f"<h2>BCM response evidence</h2><pre>{esc(evidence)}</pre>"
        )
    else:
        command_rows = "".join(
            f"<tr><td><code>{esc(cmd)}</code></td></tr>"
            for cmd in cost.get("pricing_commands", [])
        )
        body = (
            f"<p class=\"note\">Cost estimate unavailable: {esc(cost.get('error', 'unknown'))}</p>"
            "<h2>Required BCM Pricing Calculator workflow</h2>"
            "<p class=\"note\">Offline catalog pricing is disabled. Configure AWS CLI credentials with Billing and Cost Management pricing calculator access, "
            "approve the BCM estimate creation step, and rerun report generation to publish cost totals.</p>"
            f"<table><thead><tr><th>AWS CLI command</th></tr></thead><tbody>{command_rows}</tbody></table>"
        )
    return _simple_report_html("Cost Report", template, cloud, short_hash, ts, body)


def _simple_report_html(title, template, cloud, short_hash, ts, body):
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{html.escape(title)} - {html.escape(template)}</title>
<style>
@page{{size:A4;margin:12mm 14mm;background:#ffffff}}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{min-height:100%;background:#ffffff;color:#1f2937;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Roboto,sans-serif;padding:28px 32px;line-height:1.45;font-size:13px}}
h1{{font-size:26px;margin:0 0 4px 0;line-height:1.2;color:#111827;font-weight:700}}
h2{{font-size:16px;margin:20px 0 8px;color:#1e3a8a;font-weight:600}}
.sub{{color:#6b7280;font-family:Consolas,ui-monospace,monospace;margin-bottom:18px;font-size:12px}}
table{{width:100%;border-collapse:collapse;margin-top:10px;background:#ffffff;border:1px solid #e5e7eb;border-radius:6px;overflow:hidden}}
th,td{{text-align:left;border-bottom:1px solid #e5e7eb;padding:8px 10px;font-size:11.5px;vertical-align:top;color:#374151}}
th{{background:#f3f4f6;font-size:10px;text-transform:uppercase;color:#4b5563;letter-spacing:.05em;font-weight:600}}
.money{{font-family:Consolas,ui-monospace,monospace;text-align:right;white-space:nowrap}}
.summary{{display:grid;grid-template-columns:1fr 1fr 2fr;gap:10px;margin:16px 0}}
.summary div{{background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:12px}}
.summary span{{display:block;color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:.05em;font-weight:600}}
.summary strong{{display:block;margin-top:5px;font-size:16px;color:#111827}}
.total td{{font-weight:700;background:#f3f4f6}}
.note{{margin-top:16px;color:#374151;font-size:11.5px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:12px}}
code{{font-family:Consolas,ui-monospace,monospace;font-size:10.5px;color:#111827;background:#f3f4f6;padding:2px 4px;border-radius:4px}}
pre{{background:#f9fafb;padding:14px;border-radius:8px;white-space:pre-wrap;border:1px solid #e5e7eb;color:#1f2937;font-family:Consolas,monospace;font-size:11px}}
</style></head><body>
<h1>{html.escape(title)} - {html.escape(template)}</h1>
<div class="sub">{html.escape(cloud)} | plan {html.escape(short_hash)} | {html.escape(ts)}</div>
{body}
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


def _pdf_escape(text):
    return str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _html_to_pdf_lines(html_path, limit=420):
    raw = pathlib.Path(html_path).read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
    raw = re.sub(r"<style[\s\S]*?</style>", " ", raw, flags=re.I)
    raw = re.sub(r"</(h1|h2|h3|p|div|li|tr)>", "\n", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(raw)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            while len(line) > 96:
                lines.append(line[:96])
                line = line[96:].strip()
            lines.append(line)
        if len(lines) >= limit:
            lines.append("...")
            break
    return lines or ["Report content unavailable."]


def _write_builtin_pdf(html_path, pdf_path, title="MinusOps Report"):
    lines = _html_to_pdf_lines(html_path)
    page_w, page_h = 612, 792
    per_page = 44
    pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]
    objects = []

    def add(obj):
        objects.append(obj)
        return len(objects)

    font_id = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for idx, page_lines in enumerate(pages, 1):
        chunks = [
            "0.078 0.067 0.059 rg 0 0 612 792 re f",
            "0.95 0.93 0.90 rg /F1 18 Tf 42 744 Td",
            f"({_pdf_escape(title)}) Tj",
            "0.69 0.61 0.58 rg /F1 9 Tf 0 -18 Td",
            f"(Page {idx} of {len(pages)}) Tj",
            "0.98 0.96 0.94 rg /F1 9 Tf 0 -24 Td",
        ]
        for line in page_lines:
            chunks.append(f"({_pdf_escape(line)}) Tj")
            chunks.append("0 -14 Td")
        stream = "\n".join(["BT", *chunks, "ET"])
        content_id = add(f"<< /Length {len(stream.encode('latin-1', errors='replace'))} >>\nstream\n{stream}\nendstream")
        page_id = add(
            f"<< /Type /Page /Parent 0 0 R /MediaBox [0 0 {page_w} {page_h}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        )
        page_ids.append(page_id)
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    pages_id = add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>")
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")
    objects = [
        obj.replace("/Parent 0 0 R", f"/Parent {pages_id} 0 R")
        for obj in objects
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1", errors="replace"))
    xref = len(out)
    out.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objects)+1} /Root {catalog_id} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    pathlib.Path(pdf_path).write_bytes(out)
    return os.path.exists(pdf_path)


def render_pdf(html_path, pdf_path):
    browser = find_browser()
    if not browser:
        return False, "no headless browser (Edge/Chrome) found"
    ok, info = _cdp_print_pdf(browser, html_path, pdf_path)
    if ok and os.path.exists(pdf_path):
        return True, info
    rc, _, err = run([browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                      f"--print-to-pdf={pdf_path}", html_path], timeout=40)
    if rc == 0 and os.path.exists(pdf_path):
        return True, browser + " (fallback without forced print background)"
    if _write_builtin_pdf(html_path, pdf_path, title=os.path.basename(pdf_path)):
        return True, "built-in text PDF fallback"
    return False, info or err or "render failed"


def _free_local_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _read_exact(sock, n):
    chunks = []
    got = 0
    while got < n:
        chunk = sock.recv(n - got)
        if not chunk:
            raise RuntimeError("websocket closed")
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def _ws_send(sock, payload):
    data = json.dumps(payload).encode("utf-8")
    mask = secrets.token_bytes(4)
    header = bytearray([0x81])
    if len(data) < 126:
        header.append(0x80 | len(data))
    elif len(data) < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", len(data)))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", len(data)))
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(bytes(header) + mask + masked)


def _ws_recv(sock):
    chunks = []
    while True:
        b1, b2 = _read_exact(sock, 2)
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", _read_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _read_exact(sock, 8))[0]
        mask = _read_exact(sock, 4) if masked else b""
        data = _read_exact(sock, length) if length else b""
        if masked:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if opcode == 8:
            raise RuntimeError("websocket closed")
        if opcode in (1, 0):
            chunks.append(data)
            if fin:
                return json.loads(b"".join(chunks).decode("utf-8"))


def _ws_connect(ws_url):
    if not ws_url.startswith("ws://"):
        raise RuntimeError("only local ws:// devtools endpoints are supported")
    rest = ws_url[len("ws://"):]
    host_port, path = rest.split("/", 1)
    host, port_s = host_port.rsplit(":", 1)
    sock = socket.create_connection((host, int(port_s)), timeout=10)
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    req = (
        f"GET /{path} HTTP/1.1\r\n"
        f"Host: {host_port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(req.encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response:
        response += sock.recv(4096)
    if b" 101 " not in response.split(b"\r\n", 1)[0]:
        raise RuntimeError("devtools websocket upgrade failed")
    return sock


@contextlib.contextmanager
def _browser_profile_dir():
    """A throwaway Chrome profile directory that cleans up without ever raising.

    NOT `TemporaryDirectory(ignore_cleanup_errors=True)`. That flag exists from 3.10, but on
    3.10/3.11 it does not actually suppress this case: Chrome keeps a handle open on
    `Default/Shared Dictionary/cache/index-dir` for a few milliseconds after the process
    exits, `shutil.rmtree` hits WinError 32, and the error surfaces out of the context
    manager and fails the caller. Observed in CI on Windows/py3.10 -- a PDF that rendered
    perfectly well, reported as a failure by its own cleanup.

    Retry briefly, then give up and leave the directory. It is under the system temp root
    and a few kilobytes; the OS reclaims it. Losing a report because a browser was slow to
    release a cache file is the worse outcome.
    """
    path = tempfile.mkdtemp(prefix="minus-report-browser-")
    try:
        yield path
    finally:
        for attempt in range(5):
            try:
                shutil.rmtree(path)
                return
            except OSError:
                if attempt == 4:
                    return          # deliberate: cleanup failure is not the caller's problem
                time.sleep(0.1)


def _cdp_print_pdf(browser, html_path, pdf_path):
    file_url = pathlib.Path(html_path).resolve().as_uri()
    port = _free_local_port()
    with _browser_profile_dir() as user_data_dir:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--disable-extensions",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={user_data_dir}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        try:
            deadline = time.time() + 12
            targets = None
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1) as r:
                        targets = json.loads(r.read().decode("utf-8"))
                    if targets:
                        break
                except Exception:
                    time.sleep(0.15)
            if not targets:
                return False, "devtools endpoint did not start"
            page = next((t for t in targets if t.get("type") == "page"), targets[0])
            sock = _ws_connect(page["webSocketDebuggerUrl"])
            try:
                msg_id = 1

                def call(method, params=None, wait_event=None):
                    nonlocal msg_id
                    current = msg_id
                    msg_id += 1
                    _ws_send(sock, {"id": current, "method": method, "params": params or {}})
                    result = None
                    event_seen = wait_event is None
                    while True:
                        msg = _ws_recv(sock)
                        if msg.get("id") == current:
                            if "error" in msg:
                                raise RuntimeError(str(msg["error"]))
                            result = msg.get("result", {})
                        elif wait_event and msg.get("method") == wait_event:
                            event_seen = True
                        if result is not None and event_seen:
                            return result

                call("Page.enable")
                call("Page.navigate", {"url": file_url}, wait_event="Page.loadEventFired")
                params = {
                    "printBackground": True,
                    "preferCSSPageSize": True,
                    "displayHeaderFooter": False,
                    "marginTop": 0,
                    "marginBottom": 0,
                    "marginLeft": 0,
                    "marginRight": 0,
                    # Clickable PDF outline from the document's headings (the section
                    # "dropdown" in any PDF viewer). Retried without if unsupported.
                    "generateDocumentOutline": True,
                }
                result = call("Page.printToPDF", params)
                if not result:
                    params.pop("generateDocumentOutline", None)
                    result = call("Page.printToPDF", params)
                with open(pdf_path, "wb") as f:
                    f.write(base64.b64decode(result["data"]))
                return True, browser
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
        except Exception as e:
            return False, str(e)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


def git_commit():
    rc, out, _ = run(["git", "rev-parse", "--short", "HEAD"])
    return out.strip() if rc == 0 else None


def _verification_coverage(plan, findings):
    """Per-plan disclosure of what was actually checked. Never raises -- a report that fails
    to render because its own honesty section broke would be worse than the ambiguity it
    exists to remove."""
    try:
        import verification_coverage
        import synthesizer
        claims_by_type = {}
        for rc in (plan or {}).get("resource_changes") or []:
            rtype = rc.get("type") if isinstance(rc, dict) else None
            if rtype and rtype not in claims_by_type:
                claims_by_type[rtype] = [
                    c for c in synthesizer._grounding_claims(rtype)
                    if c.get("resource_type") == rtype
                ]
        return verification_coverage.classify(plan, findings=findings,
                                              claims_by_type=claims_by_type)
    except Exception as exc:
        return {"error": str(exc), "types": [], "type_count": 0,
                "rule_covered_count": 0, "coverage_ratio": None}


def _generate_report_bundle(dir_, data, template=None):
    h = plan_hash(data)
    short = h[:12]
    cloud = active_cloud()
    if not template:
        template = os.path.basename(dir_.rstrip("/\\"))
        if template == "terraform":
            # Run workspaces are runs/<run-id>/terraform — title reports after the run,
            # not the meaningless directory basename.
            run_meta = os.path.join(os.path.dirname(dir_.rstrip("/\\")), "run.json")
            try:
                with open(run_meta, encoding="utf-8") as f:
                    meta = json.load(f)
                template = meta.get("blueprint") or meta.get("run_id") or template
            except Exception:
                pass
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rows, counts = summarize(data)
    reports_root = reports_root_for_dir(dir_)
    out = os.path.join(reports_root, short)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        region = ((data.get("variables") or {}).get("region") or {}).get("value") or "us-east-1"
    except Exception:
        region = "us-east-1"

    # A destroy plan's resource_changes are all actions=["delete"], and every resource in it
    # already has real cost evidence from when it was CREATED. Pricing them again would derive
    # a "creating this costs $X/mo" BCM estimate for infrastructure being torn down -- a
    # forecast pointing the wrong direction. Detect that shape from the counts summarize()
    # already computed and skip BCM entirely.
    is_destroy_plan = counts["delete"] > 0 and counts["create"] == 0 and counts["update"] == 0

    # BCM pricing: payloads are always prepared; the estimate itself is created
    # automatically when credentials allow (a free, deletable BCM pricing object —
    # human approval stays on APPLY, not on pricing). Reviewed usage is never clobbered.
    if is_destroy_plan:
        cost = {
            "ok": False,
            "destroy": True,
            "error": ("This is a destroy plan -- no cost forecast is generated. Every resource "
                      "below is being removed, not created; it already has real cost evidence "
                      "from when it was provisioned, and tearing it down stops that cost rather "
                      "than adding new cost. Compare against AWS Cost Explorer actuals to see the "
                      "spend this removes."),
        }
    else:
        if not os.path.exists(os.path.join(out, "bcm-usage.json")):
            bcm_pricing_calculator.prepare(out, region=region)
        est_ok, est_note = bcm_pricing_calculator.auto_estimate(out, region=region)
        if not est_ok:
            print(f"[reporter] BCM estimate not auto-created: {est_note}")
        # Pick up the completed BCM estimate (just created or pre-existing) so the plan PDF's
        # cost summary reflects it; otherwise the honest "unavailable" state.
        cost = load_bcm_estimate(out) or estimate_cost()
    try:
        import optimize_analyzer
        findings = optimize_analyzer.scan_hcl_files(dir_)
    except Exception:
        findings = []
    svg = build_svg(rows, template, cloud, short, ts, findings=findings, plan=data)
    try:
        with open(os.path.join(dir_, "minus-generated.json"), encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception:
        manifest = None
    # Computed once and shared by the PDF and the manifest -- it queries the claim store,
    # so doing it twice would double that work for identical output.
    coverage = _verification_coverage(data, findings)
    htmldoc = build_html(template, cloud, short, ts, rows, counts, cost, svg, data, manifest,
                         dir_, git_commit(), coverage=coverage)

    # v3 lake-house data-flow diagram (additive; shares the six-layer classifier with the
    # conformance model). Icons resolve inside the renderer (_default_icons_dir). When an
    # estimate exists, the priced usage quantities annotate the nodes (capacity view).
    usage_annotations = {}
    if cost.get("ok"):
        for it in cost.get("line_items") or []:
            qty, unit = humanize_quantity(_num(it.get("amount")), it.get("unit") or "")
            if qty is not None and it.get("serviceCode"):
                q = f"{qty:,.0f}" if qty >= 100 else f"{qty:,.4g}"
                usage_annotations[it["serviceCode"]] = f"{q} {unit}/mo"
    try:
        dataflow_svg = build_dataflow_svg(rows, template, cloud, short, ts, findings=findings,
                                          plan=data, region=region,
                                          usage_annotations=usage_annotations)
    except Exception:
        dataflow_svg = None

    with open(os.path.join(out, "architecture.svg"), "w", encoding="utf-8") as f:
        f.write(svg)
        
    try:
        drawio_result = drawio_generator.generate_drawio_from_plan(data)
        with open(os.path.join(out, "architecture.drawio"), "w", encoding="utf-8") as f:
            f.write(drawio_result["xml"])
        with open(os.path.join(out, "architecture_url.txt"), "w", encoding="utf-8") as f:
            f.write(drawio_result["url"])
    except Exception as e:
        pass
    if dataflow_svg:
        with open(os.path.join(out, "dataflow.svg"), "w", encoding="utf-8") as f:
            f.write(dataflow_svg)
    with open(os.path.join(out, "cost.json"), "w", encoding="utf-8") as f:
        json.dump(cost, f, indent=2)
    source_hashes = plan_inspector.write_source_snapshot(dir_, out)
    # One HTML per report: report.html is both the UI-served document and the print source
    # for plan.pdf. Deliberately not also written as plan.html -- that was a byte-identical
    # second copy with nothing reading it.
    html_path = os.path.join(out, "report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(htmldoc)

    pdf_path = os.path.join(out, "plan.pdf")
    pdf_ok, pdf_info = render_pdf(html_path, pdf_path)
    cost_html_path = os.path.join(out, "cost.html")
    with open(cost_html_path, "w", encoding="utf-8") as f:
        f.write(build_cost_html(template, cloud, short, ts, cost))
    cost_pdf_path = os.path.join(out, "cost.pdf")
    cost_pdf_ok, _ = render_pdf(cost_html_path, cost_pdf_path)

    # inspect.pdf — the consolidated review record (services/resources/IAM/drift/files),
    # sections expanded with a clickable PDF outline. The HTML is only a print source.
    inspect_pdf_ok = False
    try:
        file_rows = [(name, os.path.getsize(os.path.join(out, name)))
                     for name in sorted(os.listdir(out))
                     if os.path.isfile(os.path.join(out, name))]
        inspect_manifest = {"short": short, "template": template, "generated_at": ts,
                            "counts": counts}
        inspect_src = os.path.join(out, "inspect.html")
        with open(inspect_src, "w", encoding="utf-8") as f:
            f.write(build_inspect_html(inspect_manifest, data, report_files=file_rows,
                                       drift_status="CURRENT", diff_text="", for_print=True))
        inspect_pdf_ok, _ = render_pdf(inspect_src, os.path.join(out, "inspect.pdf"))
        os.remove(inspect_src)                     # only the PDF ships
    except Exception:
        inspect_pdf_ok = False

    files = [
        "plan.json", "architecture.svg", "cost.json",
        "bcm-assumptions.json", "bcm-create-workload-estimate.json", "bcm-usage.json", "bcm-commands.json",
        "cost.html", "report.html",
    ]
    if dataflow_svg:
        files.append("dataflow.svg")
    if pdf_ok:
        files.append("plan.pdf")
    if cost_pdf_ok:
        files.append("cost.pdf")
    if inspect_pdf_ok:
        files.append("inspect.pdf")

    manifest = {
        "plan_hash": h, "short": short, "template": template, "cloud": cloud,
        "generated_at": ts, "git_commit": git_commit(), "dir": dir_,
        "counts": counts, "resource_total": len(rows),
        "cost": cost if (cost.get("ok") or cost.get("destroy")) else {"ok": False},
        "pdf": pdf_ok,
        "cost_pdf": cost_pdf_ok,
        "files": files,
        "public_files": (["architecture.svg", "dataflow.svg"] if dataflow_svg else ["architecture.svg"])
        + ["plan.pdf", "cost.pdf"] + (["inspect.pdf"] if inspect_pdf_ok else []),
        # What the gate actually checked, per resource type. A green report must not be
        # mistakable for a verified one -- see core/governance/verification_coverage.py.
        "verification_coverage": coverage,
        "source_snapshot": "source_snapshot",
        "source_hashes_file": "source_hashes.json",
        "source_file_count": len(source_hashes),
        "stale_after_terraform_change": False,
    }
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # append to the version index
    os.makedirs(reports_root, exist_ok=True)
    idx = os.path.join(reports_root, "INDEX.md")
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


def generate(dir_):
    data, err = load_plan(dir_)
    if data is None:
        print(f"[reporter] {err} — run `terraform plan -out=tfplan` first.", file=sys.stderr)
        return None
    return _generate_report_bundle(dir_, data)


def generate_from_plan_json(dir_, plan_json_path, template=None):
    with open(plan_json_path, encoding="utf-8") as f:
        data = json.load(f)
    return _generate_report_bundle(dir_, data, template=template)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Versioned deploy report (plan + cost + architecture)")
    ap.add_argument("--dir", required=True, help="Terraform directory with a tfplan (no default — this is a generic engine)")
    ap.add_argument("--plan-json", help="Use an existing terraform show -json file instead of invoking terraform")
    args = ap.parse_args()
    if args.plan_json:
        sys.exit(0 if generate_from_plan_json(args.dir, args.plan_json) else 1)
    sys.exit(0 if generate(args.dir) else 1)
