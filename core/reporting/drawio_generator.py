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
        return "shape=mxgraph.aws4.s3;fillColor=#E7157B;"
    elif resource_type == "aws_glue_job":
        return "shape=mxgraph.aws4.glue;fillColor=#8C4FFF;"
    elif resource_type.startswith("aws_athena"):
        return "shape=mxgraph.aws4.athena;fillColor=#8C4FFF;"
    elif resource_type.startswith("aws_sfn"):
        return "shape=mxgraph.aws4.step_functions;fillColor=#E7157B;"
    elif resource_type.startswith("aws_emr"):
        return "shape=mxgraph.aws4.emr;fillColor=#8C4FFF;"
    elif resource_type.startswith("aws_kinesis"):
        return "shape=mxgraph.aws4.kinesis;fillColor=#8C4FFF;"
    elif resource_type.startswith("aws_redshift"):
        return "shape=mxgraph.aws4.redshift;fillColor=#8C4FFF;"
    elif resource_type.startswith("aws_iam"):
        return "shape=mxgraph.aws4.iam;"
    elif resource_type.startswith("aws_kms"):
        # 'mxgraph.aws4.key' is not a stencil name; it fell through to an empty white box.
        return "shape=mxgraph.aws4.key_management_service;fillColor=#DD344C;"
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


def discover_flow_edges(plan_json):
    """Edges the plan actually declares, traced through module input references.

    This used to chain resources by their ADJACENCY IN THE PLAN FILE, with the comment
    "simple mock extraction for now". Every consumer treated the result as a traced flow --
    the canvas drew arrows, and generate_flow_ledger gave each hop a protocol, a latency
    budget and a list of safeguards. So a ten-resource plan grew nine confident hops that no
    reference supported, and a gold bucket "flowed" into a KMS key.

    A plan with no `configuration` block yields NO edges. There is nothing to trace, and an
    invented chain is worse than an empty one on a surface an auditor reads.
    """
    if not plan_json or "resource_changes" not in plan_json:
        return []

    dependencies = architecture_model.module_dependencies(plan_json)
    if not dependencies:
        return []

    # One representative resource per module, so an edge can be drawn between two real
    # addresses. First managed resource wins, in plan order.
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
                edges.append({"source": representative[producer],
                              "target": representative[consumer], "hop": hop})
    return edges

# diagrams.net decodes a #R payload as: base64 -> inflateRaw -> decodeURIComponent.
# The URI-encode step is not decoration. Without it, any percent sign in the diagram makes
# decodeURIComponent throw URIError and the canvas renders nothing -- a "99% uptime" label,
# a Terraform interpolation, a percent-encoded bucket name. Plain ASCII diagrams survive by
# luck, because decodeURIComponent is the identity on text containing no escapes, which is
# why a self-consistent Python round-trip test never caught it.
_URI_SAFE = "!~*'()"


def encode_drawio_url(xml_text):
    """A 1-click https://app.diagrams.net/#R... link. Offline, standard library only."""
    quoted = urllib.parse.quote(xml_text, safe=_URI_SAFE)     # == encodeURIComponent
    compressor = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=-15)
    compressed = compressor.compress(quoted.encode('utf-8')) + compressor.flush()
    # STANDARD alphabet, not urlsafe. diagrams.net decodes the fragment with atob(),
    # which rejects '-' and '_' outright -- a real three-resource plan contains both,
    # so a urlsafe payload threw InvalidCharacterError and rendered nothing. The '+'
    # and '/' it produces are legal in a URL fragment and are what draw.io expects.
    encoded = base64.b64encode(compressed).decode('utf-8').rstrip('=')
    return f"https://app.diagrams.net/#R{encoded}"


def decode_drawio_url(url):
    """Inverse of encode_drawio_url, following the same steps diagrams.net does.

    Exists so the round-trip can be asserted against the real decoder rather than against
    our own encoder, which is what AC-03 was actually asking for.
    """
    payload = url.split("#R", 1)[1] if "#R" in url else url
    payload += "=" * (-len(payload) % 4)                       # padding was stripped
    inflated = zlib.decompress(base64.b64decode(payload), -15).decode('utf-8')
    return urllib.parse.unquote(inflated)

# Per-hop transport facts, keyed on the CONSUMING resource. Every hop previously reported
# HTTPS / 100ms / TLS 1.2+ regardless of what it was, which violates the zero-hardcoding
# invariant and makes FR-06.1's protocol and latency columns decoration. These are declared
# characteristics of the service, not measurements of this pipeline -- `latency` is a
# budget, and the ledger labels it as one.
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
    """Longest-prefix match on the target resource type inside its address."""
    best = None
    for prefix, facts in _TRANSPORT:
        if prefix in address and (best is None or len(prefix) > len(best[0])):
            best = (prefix, facts)
    return best[1] if best else _TRANSPORT_DEFAULT


def generate_flow_ledger(hops):
    """The step execution ledger (FR-06.1), one row per discovered hop."""
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
    """The same ledger as a Markdown table, for PR comments and reports (FR-06.2)."""
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

def _create_mxgraph_xml():
    mxGraphModel = ET.Element('mxGraphModel', {
        'dx': '1000', 'dy': '1000', 'grid': '1', 'gridSize': '10',
        'guides': '1', 'tooltips': '1', 'connect': '1', 'arrows': '1',
        'fold': '1', 'page': '1', 'pageScale': '1', 'pageWidth': '827',
        'pageHeight': '1169', 'math': '0', 'shadow': '0',
        # Parchment, so the embedded canvas sits on the page instead of punching a white
        # slab through it. It travels with the .drawio file, so a diagram opened standalone
        # in diagrams.net carries the same surface as the console it came from.
        'background': '#f6f3f1',
    })
    root = ET.SubElement(mxGraphModel, 'root')
    ET.SubElement(root, 'mxCell', {'id': '0'})
    ET.SubElement(root, 'mxCell', {'id': '1', 'parent': '0'})
    return mxGraphModel, root

# The canvas is laid out by canonical layer -- one column per layer, in the order data
# actually moves through the reference architecture. The previous layout put every resource
# at x=100 and stepped y by 150, so a ten-resource plan was an 80x1500 thread: fitted into
# the viewer, the labels were sub-pixel and the architecture was unreadable. Classification
# comes from architecture_model, the same six-layer model that drives conformance, rather
# than a second table here that could disagree with it.
_LAYER_ORDER = list(architecture_model.CANONICAL_LAYERS) + ["other"]
_COLUMN_WIDTH = 280
_ROW_HEIGHT = 150

# Labels sit UNDER the stencil and wrap inside the column. Centred on an 80px shape with no
# wrapping, a 45-character address ran straight through its neighbours in both directions.
_LABEL_STYLE = ("verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;"
                "whiteSpace=wrap;fontSize=10;fontColor=#4e4d4d;")


def node_label(address):
    """`module.storage.aws_s3_bucket.bronze` -> `aws_s3_bucket / bronze`.

    The module prefix is what the column already says, so it is the part worth dropping. The
    full address is kept on the cell as a tooltip -- shortening a label is presentation, and
    losing the address would make the canvas unciteable against the plan.
    """
    parts = (address or "").split(".")
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
    for index, layer in enumerate([l for l in _LAYER_ORDER if l in columns]):
        x = 60 + index * _COLUMN_WIDTH
        headers.append((layer, x))
        for row, address in enumerate(columns[layer]):
            positions[address] = (x, 90 + row * _ROW_HEIGHT)
    return positions, headers


def generate_drawio_from_plan(plan_json, title="Architecture Blueprint"):
    mxGraphModel, root = _create_mxgraph_xml()
    
    clusters = discover_clusters(plan_json)
    edges = discover_flow_edges(plan_json)
    
    # Process resources
    resources = [r for r in (plan_json.get("resource_changes", []) if plan_json else [])
                 if r.get("mode") == "managed"]
    positions, headers = layout_positions(resources)
    node_map = {}

    for layer, x in headers:
        header = ET.SubElement(root, 'mxCell', {
            'id': f"layer_{layer}",
            'value': layer.upper(),
            'style': ('text;html=1;align=left;verticalAlign=middle;fontSize=13;'
                      'fontColor=#797776;letterSpacing=1;'),
            'vertex': '1',
            'parent': '1'
        })
        ET.SubElement(header, 'mxGeometry', {
            'x': str(x), 'y': '30', 'width': str(_COLUMN_WIDTH - 40), 'height': '24',
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
            'width': '80',
            'height': '80',
            'as': 'geometry'
        })
        
    # Process edges
    for i, edge in enumerate(edges):
        src_id = node_map.get(edge["source"])
        tgt_id = node_map.get(edge["target"])
        if src_id and tgt_id:
            edge_id = f"edge_{i}"
            cell = ET.SubElement(root, 'mxCell', {
                'id': edge_id,
                'value': f"[{edge['hop']}]",
                'style': 'edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;',
                'edge': '1',
                'parent': '1',
                'source': src_id,
                'target': tgt_id
            })
            geom = ET.SubElement(cell, 'mxGeometry', {'relative': '1', 'as': 'geometry'})
            
    xml_str = ET.tostring(mxGraphModel, encoding='utf-8', xml_declaration=False).decode('utf-8')
    url = encode_drawio_url(xml_str)
    ledger = generate_flow_ledger(edges)
    
    return {
        "xml": xml_str,
        "url": url,
        "ledger": ledger,
        "ledger_markdown": generate_flow_ledger_markdown(edges),
        # No "svg" key. It used to return a literal empty <svg></svg> as a deliverable and
        # `minusctl diagram --format svg` offered it. reporter.py already renders a real
        # architecture.svg; a second, empty one presented as a diagram is worse than none.
    }

def parse_graph(xml_text):
    """Read a diagram back into {"nodes": {id: address}, "edges": {id: {source, target}}}.

    The INPUT side of FR-05.1. What is deliberately absent from the result is as important
    as what is in it:

    - No geometry. Dragging a box to tidy the layout is not an architecture change, and a
      diff that noticed it would raise an unbypassable review for a cosmetic edit -- which
      teaches an operator to click through the gate.
    - No layer headers. Those are captions this module draws; deleting one edits the
      picture, never the infrastructure.
    - Nodes are keyed by their `tooltip`, which carries the full Terraform address. Falling
      back to `value` loses that: an edge between two shortened labels cannot be mapped back
      to a resource.

    Never raises. A diagram the editor returns malformed is a diff of nothing, which shows
    the operator no pending changes -- the safe direction. A crash here would take down the
    view that is supposed to be governing the edit.
    """
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
