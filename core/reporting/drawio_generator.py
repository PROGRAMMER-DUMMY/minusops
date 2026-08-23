import xml.etree.ElementTree as ET
import zlib
import base64
import urllib.parse
import json
import re
import os
import sys

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
        return "shape=mxgraph.aws4.key;"
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

def discover_flow_edges(plan_json):
    edges = []
    
    if not plan_json or "resource_changes" not in plan_json:
        return edges
        
    resources = plan_json.get("resource_changes", [])
    
    # Simple mock extraction for now, in a real scenario we'd trace depends_on or references
    for i in range(len(resources) - 1):
        if resources[i].get("mode") == "managed" and resources[i+1].get("mode") == "managed":
            edges.append({
                "source": resources[i].get("address"),
                "target": resources[i+1].get("address"),
                "hop": i + 1
            })
            
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
    encoded = base64.urlsafe_b64encode(compressed).decode('utf-8').rstrip('=')
    return f"https://app.diagrams.net/#R{encoded}"


def decode_drawio_url(url):
    """Inverse of encode_drawio_url, following the same steps diagrams.net does.

    Exists so the round-trip can be asserted against the real decoder rather than against
    our own encoder, which is what AC-03 was actually asking for.
    """
    payload = url.split("#R", 1)[1] if "#R" in url else url
    payload += "=" * (-len(payload) % 4)                       # padding was stripped
    inflated = zlib.decompress(base64.urlsafe_b64decode(payload), -15).decode('utf-8')
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
        'pageHeight': '1169', 'math': '0', 'shadow': '0'
    })
    root = ET.SubElement(mxGraphModel, 'root')
    ET.SubElement(root, 'mxCell', {'id': '0'})
    ET.SubElement(root, 'mxCell', {'id': '1', 'parent': '0'})
    return mxGraphModel, root

def generate_drawio_from_plan(plan_json, title="Architecture Blueprint"):
    mxGraphModel, root = _create_mxgraph_xml()
    
    clusters = discover_clusters(plan_json)
    edges = discover_flow_edges(plan_json)
    
    # Process resources
    y_offset = 50
    resources = plan_json.get("resource_changes", []) if plan_json else []
    node_map = {}
    
    for i, res in enumerate(resources):
        if res.get("mode") != "managed":
            continue
            
        addr = res.get("address")
        rtype = res.get("type")
        stencil = resolve_stencil(rtype)
        
        node_id = f"node_{i}"
        node_map[addr] = node_id
        
        cell = ET.SubElement(root, 'mxCell', {
            'id': node_id,
            'value': addr,
            'style': stencil,
            'vertex': '1',
            'parent': '1'
        })
        ET.SubElement(cell, 'mxGeometry', {
            'x': '100',
            'y': str(y_offset),
            'width': '80',
            'height': '80',
            'as': 'geometry'
        })
        y_offset += 150
        
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
