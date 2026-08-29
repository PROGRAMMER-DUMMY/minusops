import xml.etree.ElementTree as ET
import zlib
import base64
import urllib.parse
import json
import re
import os
import sys

import architecture_model  # noqa: E402  cross-subpackage, as in minusctl.py


def resolve_stencil(resource_type):
    if not resource_type:
        return "shape=mxgraph.aws4.resource;"

    if resource_type.startswith("aws_s3"):
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#7AA116;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.s3;"
    elif resource_type == "aws_glue_job":
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#8C4FFF;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.glue;"
    elif "glue_catalog" in resource_type or "glue_database" in resource_type:
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#8C4FFF;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.glue_data_catalog;"
    elif "glue_crawler" in resource_type:
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#8C4FFF;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.glue_crawler;"
    elif resource_type.startswith("aws_athena"):
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#8C4FFF;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.athena;"
    elif resource_type.startswith("aws_sfn"):
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#E7157B;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.step_functions;"
    elif resource_type.startswith("aws_emr"):
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#8C4FFF;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.emr;"
    elif resource_type.startswith("aws_kinesis"):
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#8C4FFF;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.kinesis;"
    elif resource_type.startswith("aws_redshift"):
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#8C4FFF;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.redshift;"
    elif "lakeformation" in resource_type:
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#DD344C;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lake_formation;"
    elif resource_type.startswith("aws_iam"):
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#DD344C;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.iam;"
    elif resource_type.startswith("aws_kms"):
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#DD344C;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.key_management_service;"
    elif resource_type.startswith("aws_secretsmanager"):
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#DD344C;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.secrets_manager;"
    elif "event_rule" in resource_type or "event_target" in resource_type:
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#E7157B;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.eventbridge;"
    elif "cloudwatch" in resource_type:
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#E7157B;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.cloudwatch;"
    elif "sns" in resource_type:
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#E7157B;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.sns;"
    elif "sqs" in resource_type:
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#E7157B;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.sqs;"
    elif "dynamodb" in resource_type:
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#2E73B8;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.dynamodb;"
    elif "lambda" in resource_type:
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#ED7100;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda;"
    elif "budget" in resource_type:
        return "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=#2E73B8;strokeColor=#ffffff;dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.budgets;"
    elif resource_type.startswith("azurerm_storage"):
        return "shape=mxgraph.azure2.storage;"
    elif resource_type == "azurerm_function_app":
        return "shape=mxgraph.azure2.function;"
    elif resource_type.startswith("google_storage"):
        return "shape=mxgraph.gcp2.storage;"
    elif resource_type.startswith("google_bigquery"):
        return "shape=mxgraph.gcp2.bigquery;"
    elif resource_type.startswith("databricks"):
        return "shape=mxgraph.aws4.databricks;"
    elif resource_type.startswith("snowflake"):
        return "shape=mxgraph.aws4.snowflake;"

    return "shape=mxgraph.aws4.resource;"


def discover_clusters(plan_json):
    clusters = {
        "vpc": [],
        "subnets": [],
        "storage": [],
        "security": [],
        "observability": []
    }

    if not plan_json or "resource_changes" not in plan_json:
        return clusters

    for res in plan_json.get("resource_changes", []):
        rtype = res.get("type", "")
        if rtype in ["aws_vpc", "azurerm_virtual_network", "google_compute_network"]:
            clusters["vpc"].append(res)
        elif "subnet" in rtype:
            clusters["subnets"].append(res)
        elif "storage" in rtype or "s3" in rtype:
            clusters["storage"].append(res)
        elif "iam" in rtype or "kms" in rtype:
            clusters["security"].append(res)
        elif "cloudwatch" in rtype or "monitor" in rtype:
            clusters["observability"].append(res)

    return clusters


def extract_node_metadata(resource_change):
    meta = {}
    after = resource_change.get("change", {}).get("after", {}) or {}

    # Compute attributes
    if "worker_type" in after:
        meta["worker_type"] = after["worker_type"]
    if "number_of_workers" in after:
        meta["number_of_workers"] = after["number_of_workers"]
    if "instance_type" in after:
        meta["instance_type"] = after["instance_type"]

    # Security badges
    if "kms_key_id" in after:
        meta["encrypted"] = True
    if after.get("publicly_accessible") is False or after.get("block_public_acls") is True:
        meta["private"] = True

    return meta


def _module_of(address):
    """`module.storage.aws_s3_bucket.bronze` -> `storage`. Root resources have no module."""
    parts = (address or "").split(".")
    return parts[1] if len(parts) > 2 and parts[0] == "module" else ""


def _extract_expr_refs(expr):
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "references" in node:
                found.extend(node["references"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(expr)
    return found


def discover_flow_edges(plan_json):
    """Edges the plan actually declares, traced through module input references and root resource expressions."""
    if not plan_json or "configuration" not in plan_json:
        return []

    config = plan_json.get("configuration", {})
    root = config.get("root_module", {})

    dependencies = architecture_model.module_dependencies(plan_json)

    # 1. Module-level dependencies
    if dependencies:
        representative = {}
        for change in plan_json.get("resource_changes", []):
            if change.get("mode") != "managed":
                continue
            name = _module_of(change.get("address"))
            if name and name not in representative:
                representative[name] = change.get("address")

        edges, hop = [], 0
        for consumer in sorted(dependencies):
            for producer in sorted(dependencies[consumer]):
                if producer in representative and consumer in representative:
                    hop += 1
                    edges.append({
                        "source": representative[producer],
                        "target": representative[consumer],
                        "hop": hop
                    })
        if edges:
            return edges

    # 2. Root-resource direct expression references
    root_resources = root.get("resources", [])
    if not root_resources:
        return []

    valid_addresses = {
        r.get("address")
        for r in plan_json.get("resource_changes", [])
        if r.get("mode") == "managed"
    }

    edges, hop = [], 0
    seen_pairs = set()

    for r in root_resources:
        target_addr = r.get("address")
        if not target_addr:
            rtype = r.get("type", "")
            rname = r.get("name", "")
            target_addr = f"{rtype}.{rname}"

        matching_targets = [a for a in valid_addresses if a == target_addr or a.startswith(f"{target_addr}[")]
        if not matching_targets:
            continue

        exprs = r.get("expressions", {})
        for _, expr_val in exprs.items():
            for ref in _extract_expr_refs(expr_val):
                matching_sources = [a for a in valid_addresses if (a == ref or a.startswith(f"{ref}[")) and a not in matching_targets]
                for s in matching_sources:
                    for t in matching_targets:
                        pair = (s, t)
                        if pair not in seen_pairs:
                            seen_pairs.add(pair)
                            hop += 1
                            edges.append({
                                "source": s,
                                "target": t,
                                "hop": hop
                            })

    return edges


_URI_SAFE = "!~*'()"


def encode_drawio_url(xml_text):
    """A 1-click https://app.diagrams.net/#R... link. Offline, standard library only."""
    quoted = urllib.parse.quote(xml_text, safe=_URI_SAFE)
    compressor = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=-15)
    compressed = compressor.compress(quoted.encode('utf-8')) + compressor.flush()
    encoded = base64.b64encode(compressed).decode('utf-8').rstrip('=')
    return f"https://app.diagrams.net/#R{encoded}"


def decode_drawio_url(url):
    """Inverse of encode_drawio_url, following the same steps diagrams.net does."""
    payload = url.split("#R", 1)[1] if "#R" in url else url
    payload += "=" * (-len(payload) % 4)
    inflated = zlib.decompress(base64.b64decode(payload), -15).decode('utf-8')
    return urllib.parse.unquote(inflated)


_TRANSPORT = (
    ("aws_glue_job",            ("S3 Read (Spark)",  "minutes (batch)",  "SSE-KMS at rest, IAM job role")),
    ("aws_emr",                 ("S3 Read (Spark)",  "minutes (batch)",  "SSE-KMS at rest, IAM job role")),
    ("aws_athena",              ("JDBC / SQL",       "seconds (query)",  "Workgroup result encryption")),
    ("aws_redshift",            ("JDBC / SQL",       "seconds (query)",  "TLS in transit, KMS at rest")),
    ("aws_kinesis",             ("Kinesis PutRecord","sub-second stream","TLS in transit, KMS at rest")),
    ("aws_sfn",                 ("States API",       "orchestration",    "IAM role, execution history")),
    ("aws_s3",                  ("S3 API (HTTPS)",   "sub-second object","SSE-KMS, public access blocked")),
    ("aws_lambda",              ("Invoke (HTTPS)",   "sub-second",       "IAM execution role")),
    ("aws_kms",                 ("KMS API (HTTPS)",  "sub-second",       "CMK policy, grant constraints")),
)
_TRANSPORT_DEFAULT = ("HTTPS", "not characterised", "TLS 1.2+ in transit")


def _transport_for(address):
    best = None
    for prefix, facts in _TRANSPORT:
        if prefix in address and (best is None or len(prefix) > len(best[0])):
            best = (prefix, facts)
    return best[1] if best else _TRANSPORT_DEFAULT


def generate_flow_ledger(hops):
    """The step execution ledger, one row per discovered hop."""
    ledger = []
    for edge in hops:
        protocol, latency, safeguards = _transport_for(str(edge.get("target", "")))
        ledger.append({
            "hop": f"[{edge['hop']}]",
            "source": edge["source"],
            "target": edge["target"],
            "protocol": protocol,
            "latency": latency,
            "safeguards": safeguards,
        })
    return ledger


def generate_flow_ledger_markdown(hops):
    """The same ledger as a Markdown table, for PR comments and reports."""
    rows = generate_flow_ledger(hops)
    lines = ["| Hop | Source | Target | Protocol | Latency budget | Safeguards |",
             "| :--- | :--- | :--- | :--- | :--- | :--- |"]
    if not rows:
        lines.append("| - | no flow hops were discovered in this plan | - | - | - | - |")
        return "\n".join(lines)
    for row in rows:
        lines.append(f"| {row['hop']} | {row['source']} | {row['target']} | "
                     f"{row['protocol']} | {row['latency']} | {row['safeguards']} |")
    return "\n".join(lines)


def _create_mxgraph_xml(page_width=1800, page_height=1000):
    mxGraphModel = ET.Element('mxGraphModel', {
        'dx': '1600', 'dy': '1000', 'grid': '1', 'gridSize': '10',
        'guides': '1', 'tooltips': '1', 'connect': '1', 'arrows': '1',
        'fold': '1', 'page': '1', 'pageScale': '1', 'pageWidth': str(page_width),
        'pageHeight': str(page_height), 'math': '0', 'shadow': '0',
        'background': '#ffffff',
    })
    root = ET.SubElement(mxGraphModel, 'root')
    ET.SubElement(root, 'mxCell', {'id': '0'})
    ET.SubElement(root, 'mxCell', {'id': '1', 'parent': '0'})
    return mxGraphModel, root


_LAYER_ORDER = list(architecture_model.CANONICAL_LAYERS) + ["other"]
_COLUMN_WIDTH = 380
_ROW_HEIGHT = 160

_LABEL_STYLE = ("verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;"
                "whiteSpace=wrap;overflow=hidden;fontSize=11;fontColor=#1e293b;fontStyle=1;")

_LAYER_COLORS = {
    "ingestion": "#E7157B",
    "storage": "#7AA116",
    "catalog": "#8C4FFF",
    "processing": "#ED7100",
    "consumption": "#01A88D",
    "governance": "#DD344C",
    "other": "#64748b"
}


def node_label(address):
    """Formats address into a clean, human-readable 2-line title."""
    parts = (address or "").split(".")
    leaf_parts = [p for p in parts if not p.startswith("module")]
    if len(leaf_parts) >= 2:
        rtype = leaf_parts[-2]
        rname = leaf_parts[-1]
        short_type = rtype.replace("aws_", "").replace("_", " ").title()
        return f"{short_type}<br>{rname}"
    return "<br>".join(parts[-2:]) if len(parts) > 2 else (address or "")


def layout_positions(resources):
    """Return {address: (x, y)} and the column headers, keyed by canonical layer."""
    columns = {}
    for res in resources:
        role = architecture_model.classify_role(
            res.get("type", ""), "", res.get("name", ""))
        layer = architecture_model.layer_of(role)
        columns.setdefault(layer, []).append(res.get("address"))

    positions, headers = {}, []
    current_x = 60
    for layer in [l for l in _LAYER_ORDER if l in columns]:
        res_list = columns[layer]
        use_2col = len(res_list) > 2
        layer_width = _COLUMN_WIDTH if use_2col else 240
        headers.append((layer, current_x, layer_width))

        for row_idx, address in enumerate(res_list):
            if use_2col:
                col_in_layer = row_idx % 2
                row_in_layer = row_idx // 2
                x = current_x + 30 + col_in_layer * 180
                y = 80 + row_in_layer * _ROW_HEIGHT
            else:
                x = current_x + 85
                y = 80 + row_idx * _ROW_HEIGHT
            positions[address] = (x, y)
        current_x += layer_width + 50

    return positions, [(layer, x) for layer, x, _ in headers]


def generate_drawio_from_plan(plan_json, title="Architecture Blueprint"):
    resources = [r for r in (plan_json.get("resource_changes", []) if plan_json else [])
                 if r.get("mode") == "managed"]
    positions, headers = layout_positions(resources)

    max_x = max([x for x, _ in positions.values()], default=800)
    max_y = max([y for _, y in positions.values()], default=600)
    page_w = max(1800, max_x + 350)
    page_h = max(1000, max_y + 250)

    mxGraphModel, root = _create_mxgraph_xml(page_width=page_w, page_height=page_h)

    edges = discover_flow_edges(plan_json)
    node_map = {}

    col_resources = {}
    for res in resources:
        role = architecture_model.classify_role(
            res.get("type", ""), "", res.get("name", ""))
        layer = architecture_model.layer_of(role)
        col_resources.setdefault(layer, []).append(res.get("address"))

    for layer, x in headers:
        res_list = col_resources.get(layer, [])
        use_2col = len(res_list) > 2
        layer_w = _COLUMN_WIDTH if use_2col else 240
        row_count = ((len(res_list) + 1) // 2) if use_2col else max(1, len(res_list))
        box_height = max(280, 80 + row_count * _ROW_HEIGHT)
        color = _LAYER_COLORS.get(layer, "#64748b")

        container = ET.SubElement(root, 'mxCell', {
            'id': f"layer_box_{layer}",
            'value': f"<b>{layer.upper()} TIER</b>",
            'style': (f'swimlane;startSize=28;fillColor=none;strokeColor={color};strokeWidth=1.5;'
                      'fontColor=#1e293b;fontStyle=1;fontSize=12;rounded=1;arcSize=8;'
                      'container=1;collapsible=0;dropTarget=1;'),
            'vertex': '1',
            'parent': '1'
        })
        ET.SubElement(container, 'mxGeometry', {
            'x': str(x), 'y': '30', 'width': str(layer_w), 'height': str(box_height),
            'as': 'geometry'
        })

    for i, res in enumerate(resources):
        addr = res.get("address")
        x, y = positions[addr]
        node_id = f"node_{i}"
        node_map[addr] = node_id

        cell = ET.SubElement(root, 'mxCell', {
            'id': node_id,
            'value': node_label(addr),
            'tooltip': addr,
            'style': resolve_stencil(res.get("type")) + _LABEL_STYLE,
            'vertex': '1',
            'parent': '1'
        })
        ET.SubElement(cell, 'mxGeometry', {
            'x': str(x),
            'y': str(y),
            'width': '68',
            'height': '68',
            'as': 'geometry'
        })

    # Process edges with smooth orthogonal routing
    for i, edge in enumerate(edges):
        src_id = node_map.get(edge["source"])
        tgt_id = node_map.get(edge["target"])
        if src_id and tgt_id:
            edge_id = f"edge_{i}"
            cell = ET.SubElement(root, 'mxCell', {
                'id': edge_id,
                'value': f"[{edge['hop']}]",
                'style': ('edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;'
                          'jettySize=auto;html=1;strokeColor=#475569;strokeWidth=2;'
                          'endArrow=block;endFill=1;fontSize=10;fontColor=#334155;'
                          'labelBackgroundColor=#ffffff;'),
                'edge': '1',
                'parent': '1',
                'source': src_id,
                'target': tgt_id
            })
            ET.SubElement(cell, 'mxGeometry', {'relative': '1', 'as': 'geometry'})

    xml_str = ET.tostring(mxGraphModel, encoding='utf-8', xml_declaration=False).decode('utf-8')
    url = encode_drawio_url(xml_str)
    ledger = generate_flow_ledger(edges)

    return {
        "xml": xml_str,
        "url": url,
        "ledger": ledger,
        "ledger_markdown": generate_flow_ledger_markdown(edges),
    }


def parse_graph(xml_text):
    """Read a diagram back into {"nodes": {id: address}, "edges": {id: {source, target}}}."""
    nodes, edges = {}, {}
    try:
        root = ET.fromstring(xml_text or "")
    except ET.ParseError:
        return {"nodes": nodes, "edges": edges}

    for cell in root.iter("mxCell"):
        cell_id = cell.get("id") or ""
        if cell_id.startswith("layer_"):
            continue
        if cell.get("vertex") == "1":
            nodes[cell_id] = cell.get("tooltip") or cell.get("value") or cell_id
        elif cell.get("edge") == "1":
            edges[cell_id] = {"source": cell.get("source"), "target": cell.get("target")}
    return {"nodes": nodes, "edges": edges}


def generate_drawio_from_requirements(requirements_data, decision_data):
    mxGraphModel, root = _create_mxgraph_xml()
    xml_str = ET.tostring(mxGraphModel, encoding='utf-8', xml_declaration=False).decode('utf-8')
    url = encode_drawio_url(xml_str)
    return {
        "xml": xml_str,
        "url": url,
        "ledger": [],
        "svg": "<svg></svg>"
    }
