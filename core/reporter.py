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

Usage:  python core/reporter.py --dir path/to/terraform   (any Terraform dir with a tfplan)
"""
import os
import sys
import json
import html
import hashlib
import argparse
import datetime
import subprocess
import base64
import pathlib
import secrets
import socket
import struct
import tempfile
import time
import urllib.request
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from providers.base import active_cloud  # noqa: E402
import plan_inspector  # noqa: E402
import bcm_pricing_calculator  # noqa: E402

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
    if any(k in t for k in ("s3_bucket", "s3_object", "glue_catalog", "dynamodb", "sqs_queue", "rds", "redshift", "athena")):
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
def _fit_text(value, limit=28):
    value = str(value)
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "."


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


def _collapse_components(rows):
    """
    Collapse a flat resource list into logical service components — a service plus its
    config resources (e.g. an S3 bucket with its versioning/lifecycle/encryption/PAB) become
    one node. Generalizes the clean per-service look to ANY plan, not just one blueprint.
    """
    groups = {}
    for r in rows:
        key = (_service_group(r["type"]), r["name"], _instance_key(r["address"]))
        groups.setdefault(key, []).append(r)
    comps = []
    for members in groups.values():
        primary = min(members, key=lambda m: len(m["type"]))   # base type (shortest) is the node
        actions = {m["action"] for m in members}
        comp = dict(primary)
        comp["action"] = next((a for a in ("delete", "update", "create", "no-op") if a in actions), "create")
        comp["config_count"] = len(members) - 1
        comps.append(comp)
    comps.sort(key=lambda c: c["address"])
    return comps


def _flow_edge(p1, p2, node_h, kind):
    return (p1[0] + 232, p1[1] + node_h // 2, p2[0], p2[1] + node_h // 2, kind)


def _pipeline_flow(rowmap, pos, node_h):
    """Real medallion data flow for the standard pipeline blueprint."""
    def find(pred):
        return next((a for a, r in rowmap.items() if pred(r)), None)
    bronze = find(lambda r: r["type"] == "aws_s3_bucket" and _instance_key(r["address"]) == "bronze")
    silver = find(lambda r: r["type"] == "aws_s3_bucket" and _instance_key(r["address"]) == "silver")
    gold = find(lambda r: r["type"] == "aws_s3_bucket" and _instance_key(r["address"]) == "gold")
    g1 = find(lambda r: r["type"] == "aws_glue_job" and r["name"] == "bronze_to_silver")
    g2 = find(lambda r: r["type"] == "aws_glue_job" and r["name"] == "silver_to_gold")
    athena = find(lambda r: r["type"] == "aws_athena_workgroup")
    sfn = find(lambda r: r["type"] == "aws_sfn_state_machine")
    edges = []
    for a, b in zip([bronze, g1, silver, g2, gold, athena], [g1, silver, g2, gold, athena, None]):
        if a in pos and b in pos:
            edges.append(_flow_edge(pos[a], pos[b], node_h, "data"))
    for g in (g1, g2):  # orchestration controls the Glue jobs (dashed)
        if sfn in pos and g in pos:
            edges.append(_flow_edge(pos[sfn], pos[g], node_h, "ctrl"))
    return edges


def _generic_flow(by_tier, pos, node_h, visible_tiers):
    """Fallback: connect the first node of consecutive non-empty tiers (anchored, no floaters)."""
    firsts = [by_tier[t][0]["address"] for t in visible_tiers if by_tier[t]]
    return [_flow_edge(pos[a], pos[b], node_h, "data")
            for a, b in zip(firsts, firsts[1:]) if a in pos and b in pos]


_SEV_ORDER = ("HIGH", "MEDIUM", "LOW", "EXTERNAL")
_SEV_COLOR = {"HIGH": "#d95d39", "MEDIUM": "#cb9a3e", "LOW": "#8da189", "EXTERNAL": "#b09c93"}
_LOCK = ('<rect x="0" y="5" width="13" height="9" rx="2" fill="none" stroke="#d4a373" stroke-width="1.3"/>'
         '<path d="M2.5,5 V3.2 a4,4 0 0 1 8,0 V5" fill="none" stroke="#d4a373" stroke-width="1.3"/>')

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
    tint = ACTION_TINT.get(action, "#b09c93")
    df = f' data-findings="{esc(",".join(f["id"] for f in findings))}"' if findings else ""
    out = [
        f'<g class="node" data-address="{esc(address)}" data-action="{esc(action)}"{df} transform="translate({x},{y})">',
        f'<rect class="card" width="{w}" height="{h}" rx="12" fill="#1c1714" stroke="{hue}" stroke-width="1.6"/>',
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
                   f'<rect width="{bw}" height="14" rx="7" fill="{_SEV_COLOR.get(top["severity"], "#b09c93")}"/>'
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
    color = "#8da189" if kind == "ctrl" else "#fbf7f4"
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
        "source": ("#d4a373", "Batch Source", "external files", "inbox"),
        "bronze": ("#d95d39", "S3 Bronze", "raw landing", "bucket"),
        "silver": ("#d95d39", "S3 Silver", "cleaned", "bucket"),
        "gold": ("#d95d39", "S3 Gold", "curated", "bucket"),
        "results": ("#d95d39", "S3 Results", "query output", "bucket"),
        "glue1": ("#e8825f", "Glue Job", "bronze to silver", "gears"),
        "glue2": ("#e8825f", "Glue Job", "silver to gold", "gears"),
        "athena": ("#8da189", "Athena", "query gold", "search"),
        "sfn": ("#8da189", "Step Functions", "starts & waits Glue", "workflow"),
        "catalog": ("#b09c93", "Glue Catalog", "table metadata", "book"),
        "kms": ("#b09c93", "KMS", "CMK encryption", "key"),
        "iam": ("#b09c93", "IAM", "scoped roles", "shield"),
        "cw": ("#cb9a3e", "CloudWatch", "failure alarm", "bell"),
        "budget": ("#cb9a3e", "Budget", "spend guardrail", "coin"),
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
        'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#fbf7f4"/></marker>'
        '<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">'
        '<path d="M40 0H0V40" fill="none" stroke="rgba(217,93,57,.06)" stroke-width="0.5"/></pattern>'
        '<style>'
        '.title{font:600 22px Outfit,system-ui,sans-serif;fill:#fbf7f4}'
        '.sub{font:500 12px "JetBrains Mono",ui-monospace,monospace;fill:#b09c93}'
        '.tier-h{font:600 12px Outfit,system-ui,sans-serif;fill:#fbf7f4;letter-spacing:.12em}'
        '.n-type{font:600 13px Inter,system-ui,sans-serif;fill:#fbf7f4}'
        '.n-name{font:400 11px "JetBrains Mono",ui-monospace,monospace;fill:#b09c93}'
        '.n-meta{font:500 9px "JetBrains Mono",ui-monospace,monospace;fill:#d4a373}'
        '.badge{font:600 9px Inter,system-ui,sans-serif;fill:#14110f}'
        '.legend{font:500 11px Inter,system-ui,sans-serif;fill:#b09c93}'
        '.p-l{font:600 9px Inter,system-ui,sans-serif;fill:#b09c93;letter-spacing:.06em}'
        '.p-v{font:600 13px Inter,system-ui,sans-serif;fill:#fbf7f4}'
        '.swim{fill:#1c1714;fill-opacity:.3;stroke:rgba(217,93,57,.14)}'
        '</style></defs>',
        '<g id="bg"><rect x="0" y="0" width="1280" height="760" fill="#14110f"/>'
        '<rect x="0" y="0" width="1280" height="760" fill="url(#grid)"/></g>',
        '<g id="titlebar"><rect x="0" y="0" width="1280" height="64" fill="#1c1714"/>'
        '<rect x="0" y="63" width="1280" height="1" fill="rgba(217,93,57,.18)"/>'
        f'<text class="title" x="24" y="34">{esc(template)}</text>'
        f'<text class="sub" x="24" y="52">{esc(cloud)} · plan {esc(short_hash)} · {esc(ts)}</text></g>',
        '<rect class="swim" x="24" y="92" width="1232" height="248" rx="12"/>'
        '<text class="tier-h" x="44" y="116">RUNTIME DATA FLOW</text>',
        '<rect class="swim" x="24" y="372" width="1232" height="248" rx="12"/>'
        '<text class="tier-h" x="44" y="396">ORCHESTRATION &amp; GOVERNANCE</text>',
    ]

    # edges first (under nodes)
    edges = ['<g id="edges">']
    chain = [k for k in ["source", "bronze", "glue1", "silver", "glue2", "gold"] if present(k)]
    for a, b in zip(chain, chain[1:]):
        edges.append(_ortho_edge(LAYOUT[a], LAYOUT[b], "data"))
    if present("gold") and present("athena"):
        edges.append(_ortho_edge(LAYOUT["gold"], LAYOUT["athena"], "data"))
    if present("athena") and present("results"):
        edges.append(_ortho_edge(LAYOUT["athena"], LAYOUT["results"], "data"))
    for i, g in enumerate(("glue1", "glue2")):  # orchestration controls the Glue jobs
        if present("sfn") and present(g):
            edges.append(_ortho_edge(LAYOUT["sfn"], LAYOUT[g], "ctrl", channel=364 - i * 8))
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
        parts.append(f'<rect x="{cx}" y="{cy}" width="{cw}" height="74" rx="10" fill="#1c1714" '
                     f'fill-opacity="0.6" stroke="rgba(217,93,57,.16)"/>'
                     f'<text class="p-l" x="{cx + 14}" y="{cy + 28}">{esc(label.upper())}</text>'
                     f'<text class="p-v" x="{cx + 14}" y="{cy + 52}">{esc(_fit_text(str(val), 22))}</text>')
        cx += cw + 8
    parts.append('</g>')

    # legend (same key set as the grid layout)
    parts.append('<g id="legend">'
                 '<line x1="24" y1="700" x2="58" y2="700" stroke="#fbf7f4" stroke-width="1.6" marker-end="url(#arrow)"/>'
                 '<text class="legend" x="64" y="704">data flow</text>'
                 '<line x1="146" y1="700" x2="180" y2="700" stroke="#8da189" stroke-width="1.6" stroke-dasharray="6 5" marker-end="url(#arrow)"/>'
                 '<text class="legend" x="186" y="704">control</text>'
                 '<g transform="translate(252,693)">' + _LOCK + '</g>'
                 '<text class="legend" x="274" y="704">encrypted (KMS)</text>'
                 '<rect x="392" y="693" width="32" height="13" rx="6" fill="#d95d39"/>'
                 '<text class="badge" x="408" y="703" text-anchor="middle">SEC</text>'
                 '<text class="legend" x="430" y="704">finding overlay</text>'
                 '<text class="legend" x="548" y="704">create=green · update=gold · delete=red</text>'
                 '<text class="legend" x="24" y="730">Governance controls apply across deployment and runtime; '
                 'they are intentionally not drawn as data movement.</text>'
                 '</g>')
    parts.append('</svg>')
    return "\n".join(parts)


def build_gate_flow_svg():
    """
    Deterministic process-flow diagram of the deploy gate (verify -> plan -> approve ->
    apply) with its decision gates and refusal paths. The gate logic is fixed, so this
    takes no inputs and always renders the same governed, self-contained SVG (MinusOps
    palette). Used in docs and the deploy report to explain the safety model.
    """
    # Semantic step types (process-flow convention) mapped to the MinusOps palette:
    # pill/start-end = sand, automated = terra-soft, manual/review = sage, decision = gold,
    # refuse/exception = terracotta. Decisions are diamonds with Yes/No branches; refusals
    # are dashed exception paths into a single REFUSED sink.
    AUTO, MANUAL, START, DEC, REFUSE = "#e8825f", "#8da189", "#d4a373", "#cb9a3e", "#d95d39"

    def pill(x, y, w, label, hue):
        return (f'<g><rect x="{x}" y="{y}" width="{w}" height="64" rx="32" fill="#1c1714" stroke="{hue}" '
                f'stroke-width="1.7"/><text class="pill" x="{x + w // 2}" y="{y + 39}" text-anchor="middle">{label}</text></g>')

    def stepbox(n, x, y, w, title, sub, hue):
        return (f'<g><rect x="{x}" y="{y}" width="{w}" height="70" rx="9" fill="#1c1714" stroke="{hue}" stroke-width="1.6"/>'
                f'<circle cx="{x}" cy="{y}" r="12" fill="#14110f" stroke="{hue}" stroke-width="1.5"/>'
                f'<text class="bd" x="{x}" y="{y + 4}" text-anchor="middle">{n}</text>'
                f'<text class="st" x="{x + 18}" y="{y + 31}">{title}</text>'
                f'<text class="ss" x="{x + 18}" y="{y + 50}">{sub}</text></g>')

    def diamond(cx, cy, label, sub):
        r = 42
        pts = f"{cx},{cy - r} {cx + r},{cy} {cx},{cy + r} {cx - r},{cy}"
        return (f'<g><polygon points="{pts}" fill="#1c1714" stroke="{DEC}" stroke-width="1.6"/>'
                f'<text class="dl" x="{cx}" y="{cy - r - 7}" text-anchor="middle">{label}</text>'
                f'<text class="dsub" x="{cx}" y="{cy + r + 15}" text-anchor="middle">{sub}</text></g>')

    def arrow(d, color="#b8a79e", dash=False):
        da = ' stroke-dasharray="6 5"' if dash else ''
        return f'<path d="{d}" stroke="{color}" stroke-width="1.6" fill="none" marker-end="url(#a)"{da}/>'

    def lbl(x, y, text, cls="al"):
        return f'<text class="{cls}" x="{x}" y="{y}" text-anchor="middle">{text}</text>'

    style = ("<style>"
             ".t{font:600 18px Outfit,system-ui,sans-serif;fill:#fbf7f4}"
             ".pill{font:600 12px Outfit,system-ui,sans-serif;fill:#fbf7f4}"
             ".st{font:600 12px Outfit,system-ui,sans-serif;fill:#fbf7f4}"
             ".ss{font:500 9px 'JetBrains Mono',ui-monospace,monospace;fill:#b09c93}"
             ".bd{font:600 10px Inter,system-ui,sans-serif;fill:#fbf7f4}"
             ".dl{font:600 10px Outfit,system-ui,sans-serif;fill:#cb9a3e}"
             ".dsub{font:500 8px 'JetBrains Mono',ui-monospace,monospace;fill:#d4a373}"
             ".al{font:600 9px Inter,system-ui,sans-serif;fill:#b8a79e}"
             ".lg{font:500 11px Inter,system-ui,sans-serif;fill:#b09c93}</style>")

    refuse = (f'<g><rect x="556" y="330" width="220" height="60" rx="9" fill="#1c1714" stroke="{REFUSE}" stroke-width="1.6"/>'
              f'<text class="st" x="574" y="358">REFUSED</text>'
              f'<text class="ss" x="574" y="376">audited · approval preserved</text></g>')

    legend = ('<g><rect x="24" y="424" width="14" height="14" rx="7" fill="none" stroke="#d4a373" stroke-width="1.6"/>'
              '<text class="lg" x="46" y="435">start/end</text>'
              '<rect x="150" y="424" width="14" height="14" rx="3" fill="none" stroke="#e8825f" stroke-width="1.6"/>'
              '<text class="lg" x="172" y="435">automated</text>'
              '<rect x="280" y="424" width="14" height="14" rx="3" fill="none" stroke="#8da189" stroke-width="1.6"/>'
              '<text class="lg" x="302" y="435">manual / review</text>'
              '<polygon points="445,424 459,431 445,438 431,431" fill="none" stroke="#cb9a3e" stroke-width="1.6"/>'
              '<text class="lg" x="466" y="435">decision</text>'
              '<line x1="560" y1="431" x2="592" y2="431" stroke="#b8a79e" stroke-width="1.6" marker-end="url(#a)"/>'
              '<text class="lg" x="598" y="435">Yes / pass</text>'
              '<line x1="690" y1="431" x2="722" y2="431" stroke="#d95d39" stroke-width="1.6" stroke-dasharray="6 5" marker-end="url(#a)"/>'
              '<text class="lg" x="728" y="435">No / refuse</text></g>')

    p = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1400 470" width="100%" role="img">',
         '<title>MinusOps deploy gate — process flow</title>',
         '<desc>verify, plan, approve, apply with decision gates and refusal paths; apply runs only the approved plan hash.</desc>',
         '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
         'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#fbf7f4"/></marker>'
         '<pattern id="g" width="40" height="40" patternUnits="userSpaceOnUse">'
         '<path d="M40 0H0V40" fill="none" stroke="rgba(217,93,57,.06)" stroke-width="0.5"/></pattern>' + style + '</defs>',
         '<rect width="1400" height="470" fill="#14110f"/><rect width="1400" height="470" fill="url(#g)"/>',
         '<rect width="1400" height="54" fill="#1c1714"/><rect y="53" width="1400" height="1" fill="rgba(217,93,57,.18)"/>',
         '<text class="t" x="24" y="33">Deploy Gate — process flow</text>',
         # happy path (left -> right)
         pill(24, 143, 124, "plan ready", START),
         stepbox(1, 178, 140, 148, "verify", "fmt·validate·scan", AUTO),
         stepbox(2, 356, 140, 148, "plan", "record plan-hash", AUTO),
         stepbox(3, 556, 140, 148, "approve", "RBAC · review", MANUAL),
         diamond(800, 175, "approve gate", "authorized·CURRENT·hash"),
         stepbox(4, 882, 140, 148, "apply", "exact tfplan", AUTO),
         diamond(1126, 175, "apply gate", "hash==approved·MFA"),
         pill(1212, 143, 128, "APPLIED", START),
         arrow("M148,175 H178"), arrow("M326,175 H356"), arrow("M504,175 H556"), arrow("M704,175 H758"),
         arrow("M842,175 H882"), arrow("M1030,175 H1084"), arrow("M1168,175 H1212"),
         lbl(862, 166, "Yes"), lbl(1190, 166, "Yes"),
         # exception paths -> REFUSED
         refuse,
         arrow("M800,217 V300 H666 V330", REFUSE, True), lbl(816, 280, "No"),
         arrow("M1126,217 V360 H776", REFUSE, True), lbl(1142, 300, "No"),
         legend,
         '</svg>']
    return "\n".join(p)


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
    rows = _collapse_components(rows)   # one node per service (+ its config), not a pile
    by_tier = {t: [r for r in rows if r["tier"] == t] for t in TIERS}
    visible_tiers = ["sources", "storage", "compute", "orchestration", "observability"]
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
    pos, rowmap = {}, {}
    for t in visible_tiers:
        y = 108
        for r in by_tier[t]:
            pos[r["address"]] = (TIER_X[t], y)
            rowmap[r["address"]] = r
            y += node_h + gap

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 {total_h}" width="100%" role="img">',
        f'<title>Architecture — {esc(template)}</title>',
        f'<desc>Auto-generated deploy architecture for {esc(template)} on {esc(cloud)} '
        '(architecture_svg_spec.md v2): tiered topology with real data-flow edges, encryption '
        'markers, and a per-resource overlay of security/cost/observability findings.</desc>',
        '<defs>'
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#fbf7f4"/></marker>'
        '<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">'
        '<path d="M40 0H0V40" fill="none" stroke="rgba(217,93,57,.06)" stroke-width="0.5"/></pattern>'
        '<style>'
        '.title{font:600 22px Outfit,system-ui,sans-serif;fill:#fbf7f4}'
        '.sub{font:500 12px "JetBrains Mono",ui-monospace,monospace;fill:#b09c93}'
        '.tier-h{font:600 13px Outfit,system-ui,sans-serif;fill:#fbf7f4;letter-spacing:.12em}'
        '.n-type{font:600 12px Inter,system-ui,sans-serif;fill:#fbf7f4}'
        '.n-name{font:400 11px "JetBrains Mono",ui-monospace,monospace;fill:#b09c93}'
        '.badge{font:600 9px Inter,system-ui,sans-serif;fill:#14110f}'
        '.legend{font:500 11px Inter,system-ui,sans-serif;fill:#b09c93}'
        '</style></defs>',
        f'<g id="bg"><rect x="0" y="0" width="1280" height="{total_h}" fill="#14110f"/>'
        f'<rect x="0" y="0" width="1280" height="{total_h}" fill="url(#grid)"/></g>',
        '<g id="titlebar"><rect x="0" y="0" width="1280" height="64" fill="#1c1714"/>'
        '<rect x="0" y="63" width="1280" height="1" fill="rgba(217,93,57,.18)"/>'
        f'<text class="title" x="24" y="34">{esc(template)}</text>'
        f'<text class="sub" x="24" y="52">{esc(cloud)} · plan {esc(short_hash)} · {esc(ts)}</text></g>',
    ]

    # edges — real flow anchored to node positions
    flow = (_pipeline_flow(rowmap, pos, node_h) if template == "aws-data-pipeline-standard"
            else _generic_flow(by_tier, pos, node_h, visible_tiers))
    edges = ['<g id="edges">']
    for x1, y1, x2, y2, kind in flow:
        mx = (x1 + x2) // 2
        color = "#8da189" if kind == "ctrl" else "#fbf7f4"
        dash = ' stroke-dasharray="6 5"' if kind == "ctrl" else ''
        edges.append(f'<path d="M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}" stroke="{color}" '
                     f'stroke-width="1.6" fill="none" marker-end="url(#arrow)" opacity="0.6"{dash}/>')
    edges.append('</g>')
    parts += edges

    # tier columns + nodes (with encryption markers and the governance finding overlay)
    for t in visible_tiers:
        x = TIER_X[t]
        parts.append(f'<g id="tier-{t}"><text class="tier-h" x="{x}" y="92">{t.upper()}</text>'
                     f'<rect x="{x}" y="100" width="232" height="2" fill="{TIER_HUE[t]}"/>')
        items = by_tier[t]
        y = 108
        for r in items:
            tint = ACTION_TINT.get(r["action"], "#b09c93")
            nf = node_findings(r["address"])
            locked = has_kms and (r["type"].startswith("aws_s3_") or "athena" in r["type"]
                                  or r["type"].startswith("aws_kms"))
            type_limit = 22 if (locked or nf) else 30
            df_attr = f' data-findings="{esc(",".join(f["id"] for f in nf))}"' if nf else ""
            node = [
                f'<g class="node" data-address="{esc(r["address"])}" data-action="{esc(r["action"])}"{df_attr} '
                f'transform="translate({x},{y})">',
                f'<rect class="card" width="232" height="{node_h}" rx="12" fill="#1c1714" '
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
                            f'<rect width="{bw}" height="14" rx="7" fill="{_SEV_COLOR.get(top["severity"], "#b09c93")}"/>'
                            f'<text class="badge" x="{bw // 2}" y="10" text-anchor="middle">{esc(label)}</text></g>')
            node.append('</g>')
            parts.append("".join(node))
            y += node_h + gap
        parts.append('</g>')

    # security band — chip border tinted when the resource has a finding
    sec = by_tier["security"]
    parts.append(f'<g id="band-security"><rect x="24" y="{632 + dy}" width="1224" height="56" rx="10" fill="none" '
                 'stroke="#b09c93" stroke-dasharray="4 4"/>'
                 f'<text class="tier-h" x="40" y="{654 + dy}">SECURITY &amp; IAM</text>')
    chip_cap = 6
    for i, r in enumerate(sec[:chip_cap]):
        cx = 220 + i * 168
        nf = node_findings(r["address"])
        stroke = _SEV_COLOR.get(nf[0]["severity"], "#b09c93") if nf else "#b09c93"
        df_attr = f' data-findings="{esc(",".join(f["id"] for f in nf))}"' if nf else ""
        parts.append(
            f'<g class="node" data-address="{esc(r["address"])}" data-action="{esc(r["action"])}"{df_attr}>'
            f'<rect x="{cx}" y="{646 + dy}" width="150" height="28" rx="8" fill="#1c1714" stroke="{stroke}" stroke-width="1"/>'
            f'<text class="n-name" x="{cx + 8}" y="{664 + dy}">{esc(_fit_text(r["name"], 18))}</text></g>')
    if len(sec) > chip_cap:
        parts.append(f'<text class="legend" x="1196" y="{664 + dy}">+{len(sec) - chip_cap}</text>')
    parts.append('</g>')

    # legend: tiers + flow + control + encryption + finding overlay + status
    parts.append(f'<g id="legend"><text class="legend" x="24" y="{712 + dy}">Tiers:</text>')
    lx = 70
    for t in visible_tiers:
        parts.append(f'<rect x="{lx}" y="{703 + dy}" width="12" height="12" rx="3" fill="{TIER_HUE[t]}"/>'
                     f'<text class="legend" x="{lx + 18}" y="{713 + dy}">{t.capitalize()}</text>')
        lx += 70 + len(t) * 6
    parts.append(
        f'<line x1="24" y1="{736 + dy}" x2="58" y2="{736 + dy}" stroke="#fbf7f4" stroke-width="1.6" marker-end="url(#arrow)"/>'
        f'<text class="legend" x="64" y="{740 + dy}">data flow</text>'
        f'<line x1="146" y1="{736 + dy}" x2="180" y2="{736 + dy}" stroke="#8da189" stroke-width="1.6" stroke-dasharray="6 5" marker-end="url(#arrow)"/>'
        f'<text class="legend" x="186" y="{740 + dy}">control</text>'
        f'<g transform="translate(252,{729 + dy})">' + _LOCK + '</g>'
        f'<text class="legend" x="274" y="{740 + dy}">encrypted (KMS)</text>'
        f'<rect x="392" y="{729 + dy}" width="32" height="13" rx="6" fill="#d95d39"/>'
        f'<text class="badge" x="408" y="{739 + dy}" text-anchor="middle">SEC</text>'
        f'<text class="legend" x="430" y="{740 + dy}">finding overlay</text>'
        f'<text class="legend" x="548" y="{740 + dy}">create=green · update=gold · delete=red</text>')
    parts.append('</g>')

    parts.append('</svg>')
    return "\n".join(parts)


def _v3_summary_cards(rows, findings):
    """Deterministic content for the three summary cards (Services / Security / Findings)."""
    services = [f"{label} ×{count}" for label, count in _service_summary(rows)][:6]
    pab = any(r["type"] == "aws_s3_bucket_public_access_block" for r in rows)
    sse = any("server_side_encryption" in r["type"] for r in rows)
    lifecycle = any("lifecycle" in r["type"] for r in rows)
    kms = any(r["type"].startswith("aws_kms_key") for r in rows)
    roles = sum(1 for r in rows if r["type"] == "aws_iam_role")
    controls = []
    if pab:
        controls.append("S3 public access blocked")
    if sse:
        controls.append("Server-side encryption")
    if lifecycle:
        controls.append("Lifecycle retention policies")
    if kms:
        controls.append("Customer-managed KMS key")
    if roles:
        controls.append(f"{roles} scoped IAM role(s)")
    if not controls:
        controls = ["No governance controls detected"]
    cats = {}
    for f in (findings or []):
        cats[f.get("category", "Other")] = cats.get(f.get("category", "Other"), 0) + 1
    finds = [f"{k}: {v}" for k, v in sorted(cats.items())] or ["No findings — passes scan"]
    return [("Services", SAND_C, services[:6]),
            ("Security & IAM", SAGE_C, controls[:6]),
            ("Findings", GOLD_C, finds[:6])]


# palette constants shared by v3 (the MinusOps warm dusk palette)
BG_C = "#14110f"; PANEL_C = "#1c1714"; PANEL2_C = "#221a16"; TEXT_C = "#fbf7f4"
MUTED_C = "#b09c93"; FAINT_C = "#6f635c"; TERRA_C = "#d95d39"; SAND_C = "#d4a373"
SAGE_C = "#8da189"; GOLD_C = "#cb9a3e"

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
# Tier-to-tier relationship verbs for the numbered flow narrative.
_V3_REL = {
    ("storage", "compute"): "read", ("compute", "orchestration"): "run",
    ("orchestration", "observability"): "watch", ("storage", "orchestration"): "orchestrate",
    ("compute", "observability"): "monitor", ("storage", "observability"): "monitor",
    ("sources", "storage"): "ingest", ("sources", "compute"): "ingest",
}


def _v3_role(r):
    """A short, deterministic role line for a node (falls back to zone key / action)."""
    role = _V3_ROLE.get(r["type"])
    if role:
        return role
    if r["type"] == "aws_s3_bucket":
        key = _instance_key(r["address"])
        return f"{key} zone" if key in ("bronze", "silver", "gold") else "object store"
    cfg = r.get("config_count", 0)
    return f"+{cfg} config" if cfg else r["action"]


def build_svg_v3(rows, template, cloud, short_hash, ts, findings=None, plan=None, region="us-east-1"):
    """Architecture diagram v3 (PROTOTYPE) — deterministic and plan-derived like build_svg,
    with a richer presentation adapted from the Cocoon diagram style but in the MinusOps
    warm palette: a dashed AWS Region containment box, three-line nodes (type / name / a
    real accent detail), labelled data-flow edges, only non-empty tiers evenly spaced, and
    Services / Security / Findings summary cards below the canvas.

    Not wired into the report; build_svg remains the contract renderer until this is approved.
    """
    def esc(s):
        return html.escape(str(s), quote=True)

    fmap = {}
    for f in (findings or []):
        if f.get("resource"):
            fmap.setdefault(f["resource"].split("[")[0], []).append(f)

    def nfind(addr):
        return fmap.get(addr.split("[")[0], [])

    original = list(rows)
    has_kms = any(r["type"].startswith("aws_kms_key") for r in rows)
    comps = _collapse_components(rows)
    order = ["sources", "storage", "compute", "orchestration", "observability"]
    by_tier = {t: [c for c in comps if c["tier"] == t] for t in order}
    sec = [c for c in comps if c["tier"] == "security"]
    tiers = [t for t in order if by_tier[t]]  # only non-empty tiers

    # geometry
    node_w, node_h, vgap = 212, 68, 18
    region_x, region_w, region_top = 40, 1200, 96
    region_side, head_h = 30, 56
    inner_w = region_w - 2 * region_side
    n = max(len(tiers), 1)
    col_gap = (inner_w - n * node_w) / (n - 1) if n > 1 else 0
    colx = {t: region_x + region_side + int(i * (node_w + col_gap)) for i, t in enumerate(tiers)}
    maxrows = max((len(by_tier[t]) for t in tiers), default=1)
    rows_top = region_top + head_h
    region_h = head_h + maxrows * (node_h + vgap) + 8
    region_bottom = region_top + region_h
    sec_y = region_bottom + 18
    sec_h = 60 if sec else 0
    canvas_bottom = (sec_y + sec_h if sec else region_bottom) + 20
    cards_y = canvas_bottom + 26
    card_h = 150
    legend_y = cards_y + card_h + 30
    total_h = legend_y + 22

    P = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 {total_h}" width="100%" role="img">',
        f'<title>Architecture v3 — {esc(template)}</title>',
        f'<desc>Deterministic plan-derived architecture (v3) for {esc(template)} on {esc(cloud)}.</desc>',
        '<defs>'
        '<marker id="av3" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{MUTED_C}"/></marker>'
        '<pattern id="g3" width="40" height="40" patternUnits="userSpaceOnUse">'
        '<path d="M40 0H0V40" fill="none" stroke="rgba(217,93,57,.05)" stroke-width="0.5"/></pattern>'
        '<style>'
        f'.t{{font:600 22px Outfit,system-ui,sans-serif;fill:{TEXT_C}}}'
        f'.s{{font:500 12px "JetBrains Mono",monospace;fill:{MUTED_C}}}'
        f'.rg{{font:600 12px "JetBrains Mono",monospace;fill:{SAND_C};letter-spacing:.08em}}'
        f'.ch{{font:600 12px Outfit,system-ui,sans-serif;letter-spacing:.14em}}'
        f'.nt{{font:600 13px Inter,system-ui,sans-serif;fill:{TEXT_C}}}'
        f'.nn{{font:400 11px "JetBrains Mono",monospace;fill:{MUTED_C}}}'
        f'.nd{{font:600 10px "JetBrains Mono",monospace}}'
        f'.el{{font:500 9px "JetBrains Mono",monospace;fill:{MUTED_C}}}'
        f'.ct{{font:600 13px Outfit,system-ui,sans-serif;fill:{TEXT_C}}}'
        f'.cb{{font:400 11px Inter,system-ui,sans-serif;fill:{MUTED_C}}}'
        f'.lg{{font:500 11px Inter,system-ui,sans-serif;fill:{MUTED_C}}}'
        f'.bd{{font:600 9px Inter,system-ui,sans-serif;fill:{BG_C}}}'
        '</style></defs>',
        f'<rect x="0" y="0" width="1280" height="{total_h}" fill="{BG_C}"/>',
        f'<rect x="0" y="0" width="1280" height="{total_h}" fill="url(#g3)"/>',
        # title bar
        f'<text class="t" x="24" y="34">{esc(template)}</text>'
        f'<text class="s" x="24" y="54">{esc(cloud)} · plan {esc(short_hash)} · {esc(ts)}</text>',
        # canvas panel
        f'<rect x="16" y="{region_top - 18}" width="1248" height="{canvas_bottom - (region_top - 18)}" '
        f'rx="16" fill="{PANEL_C}" stroke="rgba(217,93,57,.16)"/>',
        # region containment box
        f'<rect x="{region_x}" y="{region_top}" width="{region_w}" height="{region_h}" rx="14" '
        f'fill="rgba(217,93,57,.04)" stroke="{TERRA_C}" stroke-width="1.3" stroke-dasharray="8 4"/>',
        f'<text class="rg" x="{region_x + 20}" y="{region_top + 26}">AWS Region: {esc(region)}</text>',
    ]

    # column headers
    for t in tiers:
        x = colx[t]
        P.append(f'<text class="ch" x="{x}" y="{region_top + head_h - 6}" fill="{TIER_HUE[t]}">{t.upper()}</text>'
                 f'<rect x="{x}" y="{region_top + head_h + 2}" width="{node_w}" height="2" fill="{TIER_HUE[t]}"/>')

    # edges: numbered choreography between consecutive tiers, with a relationship verb
    ecx = rows_top + node_h // 2
    P.append('<g id="edges3">')
    for i, (a, b) in enumerate(zip(tiers, tiers[1:]), start=1):
        x1 = colx[a] + node_w
        x2 = colx[b]
        mx = (x1 + x2) // 2
        rel = _V3_REL.get((a, b), "flow")
        P.append(f'<path d="M{x1},{ecx} C{mx},{ecx} {mx},{ecx} {x2},{ecx}" stroke="{MUTED_C}" '
                 f'stroke-width="1.5" fill="none" marker-end="url(#av3)" opacity="0.75"/>')
        # numbered step badge on the line + the relationship verb beneath it
        P.append(f'<circle cx="{mx}" cy="{ecx}" r="11" fill="{TERRA_C}" stroke="{BG_C}" stroke-width="2"/>'
                 f'<text class="bd" x="{mx}" y="{ecx + 3}" text-anchor="middle" fill="{TEXT_C}">{i}</text>'
                 f'<text class="el" x="{mx}" y="{ecx + 26}" text-anchor="middle">{esc(rel)}</text>')
    P.append('</g>')

    # nodes
    for t in tiers:
        x = colx[t]
        y = rows_top
        for r in by_tier[t]:
            tint = ACTION_TINT.get(r["action"], MUTED_C)
            locked = has_kms and (r["type"].startswith("aws_s3_") or "athena" in r["type"]
                                  or r["type"].startswith("aws_kms"))
            detail, dcol = _v3_role(r), SAND_C
            nf = nfind(r["address"])
            df = f' data-findings="{esc(",".join(f["id"] for f in nf))}"' if nf else ""
            P.append(
                f'<g class="node" data-address="{esc(r["address"])}" data-action="{esc(r["action"])}"{df} '
                f'transform="translate({x},{y})">'
                f'<rect width="{node_w}" height="{node_h}" rx="11" fill="{PANEL2_C}" '
                f'stroke="{TIER_HUE[t]}" stroke-width="1.5"/>'
                f'<rect width="4" height="{node_h}" rx="2" fill="{tint}"/>'
                + _icon(_icon_for(r["type"]), TIER_HUE[t], 15, 15)
                + f'<text class="nt" x="44" y="26">{esc(_fit_text(_V3_NICE.get(r["type"], _humanize(r["type"])), 22))}</text>'
                f'<text class="nn" x="44" y="44">{esc(_fit_text(_node_label(r), 24))}</text>'
                f'<text class="nd" x="44" y="59" fill="{dcol}">{esc(_fit_text(detail, 24))}</text>')
            if locked:
                P.append(f'<g transform="translate({node_w - 22},9)">' + _LOCK + '</g>')
            if nf:
                top = min(nf, key=lambda f: _SEV_ORDER.index(f["severity"]) if f["severity"] in _SEV_ORDER else 9)
                lab = top["id"] + (f" +{len(nf) - 1}" if len(nf) > 1 else "")
                bw = 12 + len(lab) * 6
                bx = (node_w - 26 if locked else node_w - 8) - bw
                P.append(f'<g transform="translate({bx},{node_h - 20})">'
                         f'<rect width="{bw}" height="14" rx="7" fill="{_SEV_COLOR.get(top["severity"], MUTED_C)}"/>'
                         f'<text class="bd" x="{bw // 2}" y="10" text-anchor="middle">{esc(lab)}</text></g>')
            P.append('</g>')
            y += node_h + vgap

    # security band
    if sec:
        P.append(f'<rect x="{region_x}" y="{sec_y}" width="{region_w}" height="{sec_h}" rx="10" '
                 f'fill="none" stroke="{MUTED_C}" stroke-dasharray="4 4"/>'
                 f'<text class="ch" x="{region_x + 18}" y="{sec_y + 24}" fill="{MUTED_C}">SECURITY &amp; IAM</text>')
        cap = 6
        for i, r in enumerate(sec[:cap]):
            cx = region_x + 200 + i * 158
            nf = nfind(r["address"])
            st = _SEV_COLOR.get(nf[0]["severity"], MUTED_C) if nf else MUTED_C
            P.append(f'<g class="node" data-address="{esc(r["address"])}" data-action="{esc(r["action"])}">'
                     f'<rect x="{cx}" y="{sec_y + 16}" width="142" height="28" rx="8" fill="{PANEL2_C}" '
                     f'stroke="{st}" stroke-width="1"/>'
                     f'<text class="nn" x="{cx + 10}" y="{sec_y + 34}">{esc(_fit_text(_V3_NICE.get(r["type"], _humanize(r["type"])), 17))}</text></g>')
        if len(sec) > cap:
            P.append(f'<text class="lg" x="{region_x + region_w - 30}" y="{sec_y + 34}">+{len(sec) - cap}</text>')

    # summary cards
    cards = _v3_summary_cards(original, findings)
    cw = (1248 - 2 * 20) // 3
    for i, (title_, dot, bullets) in enumerate(cards):
        cx = 16 + i * (cw + 20)
        P.append(f'<rect x="{cx}" y="{cards_y}" width="{cw}" height="{card_h}" rx="12" '
                 f'fill="{PANEL_C}" stroke="rgba(217,93,57,.14)"/>'
                 f'<circle cx="{cx + 20}" cy="{cards_y + 26}" r="4" fill="{dot}"/>'
                 f'<text class="ct" x="{cx + 32}" y="{cards_y + 30}">{esc(title_)}</text>')
        by = cards_y + 56
        for b in bullets:
            P.append(f'<text class="cb" x="{cx + 20}" y="{by}">• {esc(_fit_text(b, 40))}</text>')
            by += 20

    # legend
    P.append(f'<line x1="24" y1="{legend_y}" x2="58" y2="{legend_y}" stroke="{MUTED_C}" stroke-width="1.5" '
             f'marker-end="url(#av3)"/><text class="lg" x="64" y="{legend_y + 4}">data flow</text>'
             f'<g transform="translate(150,{legend_y - 8})">' + _LOCK + '</g>'
             f'<text class="lg" x="172" y="{legend_y + 4}">encrypted (KMS)</text>'
             f'<rect x="292" y="{legend_y - 7}" width="12" height="12" rx="3" fill="{SAGE_C}"/>'
             f'<text class="lg" x="310" y="{legend_y + 4}">create</text>'
             f'<rect x="360" y="{legend_y - 7}" width="12" height="12" rx="3" fill="{MUTED_C}"/>'
             f'<text class="lg" x="378" y="{legend_y + 4}">no-op</text>'
             f'<rect x="430" y="{legend_y - 7}" width="12" height="12" rx="3" fill="{TERRA_C}"/>'
             f'<text class="lg" x="448" y="{legend_y + 4}">delete</text>'
             f'<text class="lg" x="520" y="{legend_y + 4}">dashed boundary = AWS Region containment</text>')

    P.append('</svg>')
    return "\n".join(P)


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
                       region="us-east-1", icons_dir=None):
    """Lake-house data-flow diagram (architecture_svg_spec.md v3), sharing the six-layer
    classifier with the conformance model (architecture_model). Deterministic and honest:
    stages on the spine, real transforms between them, catalog/governance in its own zone,
    results as side outputs, consumption reading curated, orchestration edges drawn only
    when the plan's references confirm the wiring, and a Security & Monitoring band.
    """
    import architecture_model as am

    def esc(s):
        return html.escape(str(s), quote=True)

    comps = _collapse_components(rows)
    for c in comps:
        c["role"] = am.classify_role(c["type"], _instance_key(c["address"]), c.get("name", ""))
    R = {}
    for c in comps:
        R.setdefault(c["role"], []).append(c)
    stages = sorted(R.get("stage", []), key=lambda c: (am.stage_rank(_instance_key(c["address"]), c.get("name", "")), c["address"]))
    xforms = list(R.get("transform", []))
    govern = R.get("catalog", [])
    consume = R.get("consume", [])
    side = R.get("store_other", [])
    orch = R.get("orchestrate", [])
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
    total_h = band_y + (120 if band else 20) + 30
    gov_top = 110
    n = max(len(spine), 1)
    slot = proc_w / (n + 0.2)
    cx = [int(proc_x + 40 + slot * (i + 0.4)) for i in range(n)]

    def nm(rt):
        return _V3_NICE.get(rt, _humanize(rt))

    def tnode(c, cxp, y, s, hue, sub=None):
        uid = re.sub(r"\W", "", c["address"])
        if sub is None:
            sub = _v3_role(c)
            if sub in ("no-op", "create", "update", "delete"):
                sub = "resource"
        return (f'<g class="node" data-address="{esc(c["address"])}" data-action="{esc(c.get("action", ""))}">'
                + _df_embed_icon(c["type"], uid, cxp - s // 2, y, s, hue, icons_dir)
                + f'<text x="{cxp}" y="{y + s + 15}" text-anchor="middle" style="font:600 12px Inter,sans-serif;fill:{TEXT_C}">{esc(_fit_text(nm(c["type"]), 18))}</text>'
                f'<text x="{cxp}" y="{y + s + 30}" text-anchor="middle" style="font:400 10px \'JetBrains Mono\',monospace;fill:{MUTED_C}">{esc(_fit_text(sub, 20))}</text></g>')

    def zone(x, y, w, h, label, col=None):
        col = col or "rgba(217,93,57,.5)"
        return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="rgba(217,93,57,.045)" '
                f'stroke="{col}" stroke-width="1.3" stroke-dasharray="7 4"/>'
                f'<text x="{x + 14}" y="{y + 20}" style="font:600 11px Outfit,sans-serif;fill:{TERRA_C};letter-spacing:.1em">{esc(label.upper())}</text>')

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
            P.append(tnode(c, gx0 + 22 + j * 130, gov_top + 18, 40, GOLD_C, sub="schema / catalog"))
        if len(govern) > 3:
            P.append(f'<text x="{proc_x + 300 + 420 - 14}" y="{gov_top + 52}" text-anchor="end" '
                     f'style="font:600 10px \'JetBrains Mono\',monospace;fill:{MUTED_C}">+{len(govern) - 3} more</text>')
        if used_xf:
            tx = cx[[k for k, (kind, _) in enumerate(spine) if kind == "xf"][0]]
            P.append(f'<path d="M{gx0 + 22},{gov_top + 92} C{gx0 + 22},{gov_top + 150} {tx},{spine_y - 60} {tx},{spine_y}" '
                     f'stroke="{SAND_C}" stroke-width="1.3" fill="none" stroke-dasharray="5 4" opacity="0.8"/>')

    for i in range(len(spine) - 1):
        x1, x2 = cx[i] + sz // 2 + 6, cx[i + 1] - sz // 2 - 6
        ey = spine_y + sz // 2
        if spine[i][0] == "stage" and spine[i + 1][0] == "stage":
            # Two storage stages with NO transform between them in the plan: an implied
            # solid arrow would fabricate a flow, so the gap is drawn faint and named.
            P.append(f'<line x1="{x1}" y1="{ey}" x2="{x2}" y2="{ey}" stroke="{MUTED_C}" '
                     f'stroke-width="1.2" stroke-dasharray="4 5" opacity="0.45" marker-end="url(#dfa)"/>')
            P.append(f'<text x="{(x1 + x2) // 2}" y="{ey - 8}" text-anchor="middle" '
                     f'style="font:600 9px \'JetBrains Mono\',monospace;fill:{GOLD_C}">no transform in plan</text>')
        else:
            P.append(f'<line x1="{x1}" y1="{ey}" x2="{x2}" y2="{ey}" stroke="{MUTED_C}" '
                     f'stroke-width="1.6" marker-end="url(#dfa)"/>')
    hue = {"stage": TERRA_C, "xf": SAGE_C}
    for i, (k, c) in enumerate(spine):
        P.append(tnode(c, cx[i], spine_y, sz, hue.get(k, TERRA_C)))

    if consume and spine:
        # Consumption reads the curated END OF STORAGE (last stage), not whatever
        # happens to sit last on the spine.
        last_stage = max((i for i, (k, _) in enumerate(spine) if k == "stage"), default=len(spine) - 1)
        ax = cons_x + cons_w // 2
        P.append(f'<line x1="{cx[last_stage] + sz // 2 + 6}" y1="{spine_y + sz // 2}" x2="{ax - 28}" y2="{spine_y + sz // 2}" stroke="{MUTED_C}" stroke-width="1.6" marker-end="url(#dfa)"/>')
        P.append(tnode(consume[0], ax, spine_y, sz, GOLD_C))
        if len(consume) > 1:
            P.append(f'<text x="{ax}" y="{spine_y + sz + 45}" text-anchor="middle" '
                     f'style="font:600 10px \'JetBrains Mono\',monospace;fill:{MUTED_C}">+{len(consume) - 1} more consumer(s)</text>')

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
        P.append(f'<line x1="{ox}" y1="{spine_y + sz + 34}" x2="{ox}" y2="{side_y - 6}" stroke="{MUTED_C}" stroke-width="1.2" stroke-dasharray="3 3" opacity="0.7"/>')
        P.append(tnode(sb, ox, side_y, 34, MUTED_C, sub="results / output"))

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
        P.append(tnode(oc, ox, orch_y, 46, SAGE_C, sub=osub))
        for k in xf_idx:
            P.append(f'<path d="M{ox},{orch_y} C{ox},{orch_y - 30} {cx[k]},{spine_y + sz + 50} {cx[k]},{spine_y + sz + 8}" '
                     f'stroke="{SAGE_C}" stroke-width="1.3" fill="none" stroke-dasharray="5 4" opacity="{("0.85" if wired else "0.35")}"/>')
        if wired:
            P.append(f'<text x="{ox}" y="{orch_y + 92}" text-anchor="middle" style="font:600 9px \'JetBrains Mono\',monospace;fill:{SAGE_C}">orchestrates</text>')
        else:
            P.append(f'<text x="{ox}" y="{orch_y + 92}" text-anchor="middle" style="font:600 9px \'JetBrains Mono\',monospace;fill:{GOLD_C}">not wired — placeholder definition</text>')

    if band:
        P.append(zone(24, band_y, W - 48, 120, "Security & Monitoring", col="rgba(176,156,147,.55)"))
        bg = {}
        for c in band:
            bg.setdefault(am._strip_provider(c["type"]).split("_")[0], []).append(c)
        bitems = [(g[0], len(g)) for g in bg.values()]
        bslot = (W - 160) / max(len(bitems), 1)
        for j, (c, cnt) in enumerate(bitems):
            x = int(100 + bslot * (j + 0.5))
            lab = nm(c["type"]) + (f" ×{cnt}" if cnt > 1 else "")
            P.append(f'<g class="node" data-address="{esc(c["address"])}">'
                     + _df_embed_icon(c["type"], re.sub(r"\W", "", c["address"]), x - 24, band_y + 28, 48, MUTED_C, icons_dir)
                     + f'<text x="{x}" y="{band_y + 94}" text-anchor="middle" style="font:600 12px Inter,sans-serif;fill:{TEXT_C}">{esc(_fit_text(lab, 18))}</text></g>')
    P.append('</svg>')
    return "\n".join(P)


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
        manifest["cost"] = cost if cost.get("ok") else {"ok": False}
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


def build_html(template, cloud, short_hash, ts, rows, counts, cost, svg, plan=None, manifest=None, tf_dir=None, git_sha=None):
    def esc(s):
        return html.escape(str(s))

    if cost.get("ok"):
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
    approval_html = _approval_status_html(manifest, tf_dir)
    artifacts_html = _artifact_index_html(short_hash)
    toc_html = _toc_html(sections)

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Plan Report - {esc(template)}</title><style>
@page{{size:A4;margin:0;background:#14110f}}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{min-height:100%;background:#14110f;color:#f5efe9;font-family:Inter,Segoe UI,Arial,sans-serif;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
body{{padding:0}}
.mono{{font-family:'JetBrains Mono',Consolas,ui-monospace,monospace;font-size:.8rem}}
h1{{font-size:1.65rem;font-weight:720;line-height:1.15}}h2{{font-size:1.02rem;margin:1rem 0 .55rem;color:#e8b58f}}h3{{font-size:.9rem;margin:.85rem 0 .3rem;color:#d4a373}}
.sub{{color:#b8a79e;font-family:Consolas,ui-monospace,monospace;font-size:.78rem;margin-top:.35rem}}
.page{{page-break-after:always;min-height:1122px;padding:42px 46px;background:#14110f}}.page:last-child{{page-break-after:auto}}
.header{{border-bottom:1px solid rgba(212,163,115,.26);padding-bottom:14px;margin-bottom:16px}}
.panel{{background:#1c1714;border:1px solid rgba(212,163,115,.22);border-radius:8px;padding:1rem;margin-top:.85rem}}
.section-no{{color:#d4a373;font-family:Consolas,ui-monospace,monospace;font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:.35rem}}
.architecture{{padding:.25rem;background:#14110f;border:none}}
svg{{width:100%;height:auto;display:block}}
table{{width:100%;border-collapse:collapse;margin-top:.5rem}}
th,td{{text-align:left;padding:.45rem .5rem;border-bottom:1px solid rgba(255,255,255,.07);font-size:.78rem;vertical-align:top}}
th{{color:#b8a79e;text-transform:uppercase;font-size:.64rem;letter-spacing:.06em}}
.badge{{padding:.12rem .5rem;border-radius:20px;font-size:.72rem;font-weight:600}}
.badge.create{{background:rgba(141,161,137,.18);color:#8da189}}
.badge.update{{background:rgba(203,154,62,.18);color:#cb9a3e}}
.badge.delete{{background:rgba(217,93,57,.18);color:#d95d39}}
.badge.no-op{{background:rgba(176,156,147,.15);color:#b09c93}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:.75rem}}
.kpi{{background:#181411;border:1px solid rgba(212,163,115,.18);border-radius:8px;padding:.8rem}}
.kl{{color:#b8a79e;font-size:.64rem;text-transform:uppercase;letter-spacing:.05em}}
.kv{{font-family:'JetBrains Mono',Consolas,ui-monospace,monospace;font-size:1.2rem;margin-top:.35rem}}
.counts span{{margin-right:1rem;font-family:Consolas,ui-monospace,monospace}}
.muted{{color:#b8a79e}}.small{{font-size:.74rem;margin-top:.55rem}}.flow{{line-height:1.55;color:#d8c8bf;margin-top:.45rem;font-size:.86rem}}
code{{font-family:'JetBrains Mono',Consolas,ui-monospace,monospace;color:#f5efe9;font-size:.78rem}}
footer{{margin-top:1.2rem;padding-top:.8rem;border-top:1px solid rgba(212,163,115,.18);color:#7d7068;font-size:.72rem}}
</style></head><body>
<section class="page cover">
<div class="header"><div class="section-no">Cover</div><h1>Terraform Plan Report</h1>
<div class="sub">{esc(template)} | {esc(cloud)} | plan {esc(short_hash)} | {esc(ts)}</div></div>
<div class="counts panel"><span style="color:#8da189">+{counts['create']} create</span>
<span style="color:#cb9a3e">~{counts['update']} update</span>
<span style="color:#d95d39">-{counts['delete']} delete</span>
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
<div class="panel"><p class="flow">Terraform plans <code>{counts['create']}</code> creates, <code>{counts['update']}</code> updates, and <code>{counts['delete']}</code> deletes for <code>{esc(template)}</code>. The selected design is a governed AWS batch analytics pipeline with S3 bronze, silver, and gold zones, Glue transformation jobs, Step Functions orchestration, Athena query access, KMS encryption, scoped IAM roles, CloudWatch monitoring, and an AWS Budget guardrail.</p></div>
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
</section>
<section class="page">
<div class="header"><div class="section-no">Section 7</div><h1>IAM, Security, and Governance</h1>
<div class="sub">IAM resources and controls reviewers should inspect before approval.</div></div>
<div class="panel">{security_html}</div>
<div class="panel">{iam_html}</div>
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
<div class="panel"><p class="flow"><code>terraform init</code> prepares providers. <code>python core/plan_gate.py verify --dir {esc(tf_dir or 'runs/&lt;run-id&gt;/terraform')} --policy-mode production</code> formats, validates, runs native SEC checks, and requires external scanner evidence. <code>python core/plan_gate.py plan --dir {esc(tf_dir or 'runs/&lt;run-id&gt;/terraform')}</code> generates <code>tfplan</code> and this report. Apply is intentionally absent from this report and remains gated.</p></div>
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
            color, vtxt = "#d95d39", f"+${v:,.2f}"
        else:
            color, vtxt = "#7fae7f", f"-${abs(v):,.2f}"
        ptxt = f"{p:+.1f}%" if p is not None else "-"
        rows += (f"<tr><td>{esc(r['service'])}</td>"
                 f"<td class=\"money\">{('$%.2f' % f) if f is not None else '—'}</td>"
                 f"<td class=\"money\">{('$%.2f' % a) if a is not None else '—'}</td>"
                 f"<td class=\"money\" style=\"color:{color}\">{vtxt}</td>"
                 f"<td class=\"money\" style=\"color:{color}\">{ptxt}</td></tr>")
    tot_v = at - ft
    tot_color = "#d95d39" if tot_v > 0 else "#7fae7f"
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


def build_cost_html(template, cloud, short_hash, ts, cost):
    def esc(s):
        return html.escape(str(s))

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

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
            return (f'<div style="background:#1c1714;border:1px solid rgba(212,163,115,.22);border-radius:8px;padding:12px">'
                    f'<span style="display:block;color:#b8a79e;font-size:10px;text-transform:uppercase;letter-spacing:.05em">{esc(label)}</span>'
                    f'<strong style="display:block;margin-top:5px;font-size:15px;color:#f5efe9">{esc(val)}</strong></div>')

        cards = ('<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0">'
                 + card("Monthly total", f"${total:,.2f}" if total is not None else "provided by BCM")
                 + card("Annual (x12)", f"${annual:,.2f}" if annual is not None else "-")
                 + card("Rate basis", rate_label) + card("Priced at", priced_at or "-") + "</div>")

        rows = ""
        for it in line_items:
            c, a = _f(it.get("cost")), _f(it.get("amount"))
            unit = it.get("unit") or ""
            usage_cell = f"{a:,.4g} {esc(unit)}" if a is not None else "-"
            # Effective $/unit = AWS's own cost ÷ AWS's own quantity — arithmetic on BCM's
            # response, never a hardcoded rate.
            rate = f"${c / a:,.4f}/{esc(unit or 'unit')}" if (c is not None and a) else "-"
            pct = f"{c / total * 100:.1f}%" if (c is not None and total) else "-"
            rows += (f"<tr><td>{esc(it.get('serviceCode') or it.get('service') or '-')}</td>"
                     f"<td>{esc(it.get('usageType') or '-')}</td><td>{esc(it.get('operation') or '-')}</td>"
                     f"<td class=\"money\">{usage_cell}</td>"
                     f"<td class=\"money\">{rate}</td>"
                     f"<td class=\"money\">{('$%.2f' % c) if c is not None else '-'}</td>"
                     f"<td class=\"money\">{pct}</td></tr>")
        if not rows:
            rows = "<tr><td colspan=\"7\">BCM returned no line items in the stored response.</td></tr>"
        # Unpriced plan services are shown, not hidden — absence of a price is NOT $0.
        for svc in cost.get("not_estimated_services") or []:
            rows += (f'<tr style="color:#b8a79e"><td>{esc(svc)}</td>'
                     '<td colspan="4">not estimated — no reviewed catalog usage line for this service</td>'
                     '<td class="money">unpriced</td><td class="money">-</td></tr>')

        drivers = ""
        for it in sorted(line_items, key=lambda i: _f(i.get("cost")) or 0, reverse=True)[:6]:
            c = _f(it.get("cost"))
            if c is None or not total:
                continue
            w = max(2, round(c / total * 100))
            drivers += (f'<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:12px">'
                        f'<span>{esc(it.get("serviceCode") or "-")}</span><span class="money">${c:,.2f} · {c / total * 100:.1f}%</span></div>'
                        f'<div style="background:#231d19;border-radius:5px;height:8px;margin-top:3px">'
                        f'<div style="width:{w}%;height:8px;border-radius:5px;background:#d95d39"></div></div></div>')

        assumptions = cost.get("assumptions") or {}
        assume_html = (_kv_table(assumptions.items()) if assumptions
                       else "<p class=\"note\">No derived assumptions recorded (usage supplied directly).</p>")

        # Budget check: the plan provisions its own guardrail (aws_budgets_budget) — hold
        # the AWS forecast against it BEFORE deploy, not after the first bill.
        budget_html = ""
        budget = cost.get("monthly_budget_usd")
        if budget and total is not None:
            util = total / budget * 100
            tone = "#8da189" if util <= 80 else "#cb9a3e" if util <= 100 else "#d95d39"
            verdict = ("within budget" if util <= 80 else
                       "approaching budget" if util <= 100 else "EXCEEDS BUDGET")
            budget_html = (
                "<h2>Budget check</h2>"
                f'<p class="note">Forecast <b>${total:,.2f}/mo</b> vs the plan\'s own budget guardrail '
                f'<b>${budget:,.2f}/mo</b> — <b style="color:{tone}">{util:.0f}% · {verdict}</b>. '
                "Both numbers are real: the forecast is AWS BCM's, the budget is the "
                "aws_budgets_budget this plan provisions.</p>"
                f'<div style="background:#231d19;border-radius:6px;height:10px;margin:8px 0 14px">'
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

        scenario_html = (
            "<h2>What-if scenarios (scale up / down, commitments)</h2>"
            "<p class=\"note\">Model changed usage or Savings Plans / Reserved Instances with a BCM "
            "bill scenario — AWS prices the scenario; nothing is computed locally:</p>"
            "<table><thead><tr><th>Scenario</th><th>Command</th></tr></thead><tbody>"
            "<tr><td>Scale usage up/down</td><td><code>python core/bcm_pricing_calculator.py scenario "
            "--report-dir &lt;this dir&gt; --usage-modifications usage-mods.json</code></td></tr>"
            "<tr><td>With commitments (SP/RI)</td><td><code>python core/bcm_pricing_calculator.py scenario "
            "--report-dir &lt;this dir&gt; --commitments commitments.json</code></td></tr>"
            "<tr><td>Different usage assumptions</td><td><code>python core/bcm_pricing_calculator.py prepare "
            "--report-dir &lt;this dir&gt; --derive --assume glue_runs_per_day=48 &amp;&amp; "
            "python core/bcm_pricing_calculator.py run --report-dir &lt;this dir&gt;</code></td></tr>"
            "</tbody></table>")

        variance_html = _build_variance_html(cost.get("variance"), esc)

        evidence = json.dumps(cost.get("estimate", cost), indent=2, sort_keys=True)
        body = (
            cards
            + variance_html
            + "<h2>Per-service cost breakdown</h2>"
            + "<table><thead><tr><th>Service</th><th>Usage type</th><th>Operation</th><th>Usage</th>"
            + "<th>Rate $/unit</th><th>Monthly</th><th>% of total</th></tr></thead>"
            + f"<tbody>{rows}</tbody></table>"
            + (f"<h2>Cost drivers</h2>{drivers}" if drivers else "")
            + budget_html
            + unit_econ
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


def build_plan_html(template, cloud, short_hash, ts, rows, counts):
    def esc(s):
        return html.escape(str(s))

    table_rows = "".join(
        f"<tr><td>{esc(r['address'])}</td><td>{esc(_humanize(r['type']))}</td><td>{esc(r['action'])}</td></tr>"
        for r in rows
    )
    body = (
        f"<p>Summary: +{counts['create']} create, ~{counts['update']} update, "
        f"-{counts['delete']} delete, {counts['no-op']} no-op.</p>"
        f"<table><thead><tr><th>Resource</th><th>Type</th><th>Action</th></tr></thead><tbody>{table_rows}</tbody></table>"
    )
    return _simple_report_html("Plan Report", template, cloud, short_hash, ts, body)


def _simple_report_html(title, template, cloud, short_hash, ts, body):
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{html.escape(title)} - {html.escape(template)}</title>
<style>
@page{{size:A4;margin:0;background:#14110f}}
*{{box-sizing:border-box}}
html,body{{min-height:100%;background:#14110f;color:#f5efe9;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
body{{font-family:Inter,Segoe UI,Arial,sans-serif;padding:34px 38px;line-height:1.42}}
h1{{font-size:27px;margin:0 0 4px 0;line-height:1.15}}h2{{font-size:17px;margin:24px 0 8px;color:#e8b58f}}
.sub{{color:#b8a79e;font-family:Consolas,ui-monospace,monospace;margin-bottom:22px;font-size:12px}}
table{{width:100%;border-collapse:collapse;margin-top:10px;background:#1c1714;border:1px solid rgba(212,163,115,.20);border-radius:8px;overflow:hidden}}
th,td{{text-align:left;border-bottom:1px solid rgba(255,255,255,.07);padding:8px;font-size:11.5px;vertical-align:top}}
th{{font-size:9.5px;text-transform:uppercase;color:#b8a79e;letter-spacing:.05em}}.money{{font-family:Consolas,ui-monospace,monospace;text-align:right;white-space:nowrap}}
.summary{{display:grid;grid-template-columns:1fr 1fr 2fr;gap:10px;margin:18px 0}}
.summary div{{background:#1c1714;border:1px solid rgba(212,163,115,.22);border-radius:8px;padding:12px}}
.summary span{{display:block;color:#b8a79e;font-size:10px;text-transform:uppercase;letter-spacing:.05em}}
.summary strong{{display:block;margin-top:5px;font-size:16px;color:#f5efe9}}
.total td{{font-weight:700;background:#231d19}}
.note{{margin-top:18px;color:#b8a79e;font-size:11.5px;background:#1c1714;border:1px solid rgba(212,163,115,.22);border-radius:8px;padding:12px}}
code{{font-family:Consolas,ui-monospace,monospace;font-size:10.5px;color:#f5efe9}}
pre{{background:#1c1714;padding:14px;border-radius:8px;white-space:pre-wrap;border:1px solid rgba(212,163,115,.22)}}
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


def render_png(input_path, png_path, window_size="1400,1000"):
    browser = find_browser()
    if not browser:
        return False, "no headless browser (Edge/Chrome) found"
    rc, _, err = run([browser, "--headless", "--disable-gpu", "--no-first-run",
                      f"--window-size={window_size}", f"--screenshot={png_path}", input_path],
                     timeout=40)
    if rc == 0 and os.path.exists(png_path):
        return True, browser
    return False, err or "screenshot failed"


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


def _cdp_print_pdf(browser, html_path, pdf_path):
    file_url = pathlib.Path(html_path).resolve().as_uri()
    port = _free_local_port()
    try:
        temp_ctx = tempfile.TemporaryDirectory(prefix="minus-report-browser-", ignore_cleanup_errors=True)
    except TypeError:
        temp_ctx = tempfile.TemporaryDirectory(prefix="minus-report-browser-")
    with temp_ctx as user_data_dir:
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
                result = call(
                    "Page.printToPDF",
                    {
                        "printBackground": True,
                        "preferCSSPageSize": True,
                        "displayHeaderFooter": False,
                        "marginTop": 0,
                        "marginBottom": 0,
                        "marginLeft": 0,
                        "marginRight": 0,
                    },
                )
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

    # BCM pricing: payloads are always prepared; the estimate itself is created
    # automatically when credentials allow (a free, deletable BCM pricing object —
    # human approval stays on APPLY, not on pricing). Reviewed usage is never clobbered.
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
    htmldoc = build_html(template, cloud, short, ts, rows, counts, cost, svg, data, None, dir_, git_commit())

    # v3 lake-house data-flow diagram (additive; shares the six-layer classifier with the
    # conformance model). Icons are opt-in via a local dir; default is on-palette glyphs.
    icons_dir = os.environ.get("MINUS_ARCH_ICONS_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "architecture-icons")
    if not os.path.isdir(icons_dir):
        icons_dir = None
    try:
        dataflow_svg = build_dataflow_svg(rows, template, cloud, short, ts, findings=findings,
                                          plan=data, region=region, icons_dir=icons_dir)
    except Exception:
        dataflow_svg = None

    with open(os.path.join(out, "architecture.svg"), "w", encoding="utf-8") as f:
        f.write(svg)
    if dataflow_svg:
        with open(os.path.join(out, "dataflow.svg"), "w", encoding="utf-8") as f:
            f.write(dataflow_svg)
    with open(os.path.join(out, "cost.json"), "w", encoding="utf-8") as f:
        json.dump(cost, f, indent=2)
    source_hashes = plan_inspector.write_source_snapshot(dir_, out)
    html_path = os.path.join(out, "plan.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(htmldoc)
    report_html_path = os.path.join(out, "report.html")
    with open(report_html_path, "w", encoding="utf-8") as f:
        f.write(htmldoc)

    pdf_path = os.path.join(out, "plan.pdf")
    pdf_ok, pdf_info = render_pdf(html_path, pdf_path)
    cost_html_path = os.path.join(out, "cost.html")
    with open(cost_html_path, "w", encoding="utf-8") as f:
        f.write(build_cost_html(template, cloud, short, ts, cost))
    cost_pdf_path = os.path.join(out, "cost.pdf")
    cost_pdf_ok, _ = render_pdf(cost_html_path, cost_pdf_path)

    files = [
        "plan.json", "architecture.svg", "cost.json",
        "bcm-assumptions.json", "bcm-create-workload-estimate.json", "bcm-usage.json", "bcm-commands.json",
        "plan.html", "cost.html", "report.html",
    ]
    if dataflow_svg:
        files.append("dataflow.svg")
    if pdf_ok:
        files.append("plan.pdf")
    if cost_pdf_ok:
        files.append("cost.pdf")

    manifest = {
        "plan_hash": h, "short": short, "template": template, "cloud": cloud,
        "generated_at": ts, "git_commit": git_commit(), "dir": dir_,
        "counts": counts, "resource_total": len(rows),
        "cost": cost if cost.get("ok") else {"ok": False},
        "pdf": pdf_ok,
        "cost_pdf": cost_pdf_ok,
        "files": files,
        "public_files": (["architecture.svg", "dataflow.svg"] if dataflow_svg else ["architecture.svg"]) + ["plan.pdf", "cost.pdf"],
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
