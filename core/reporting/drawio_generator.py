"""Draw.io architecture diagrams rendered from a Terraform plan.

Nodes are managed resources classified into the canonical analytics layers. Edges are the
data movement a plan declares through resource arguments; Terraform dependency references
are not data flow and are not drawn.

Depends on: core/architecture/architecture_model.py
Shells out to: nothing
Used by: core/cli/commands/diagram.py, app/console_app.py, tests/test_drawio_generator.py
"""
import base64
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zlib

import architecture_model  # noqa: E402  cross-subpackage, as in minusctl.py


_ICON_STYLE = (
    "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor={fill};strokeColor=#ffffff;"
    "dashed=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{icon};"
)
_GENERIC_STENCIL = "shape=mxgraph.aws4.resource;"

_STENCILS = (
    ("aws_glue_job", "glue", "#8C4FFF"),
    ("glue_crawler", "glue_crawler", "#8C4FFF"),
    ("glue_catalog", "glue_data_catalog", "#8C4FFF"),
    ("glue_database", "glue_data_catalog", "#8C4FFF"),
    ("aws_s3", "s3", "#7AA116"),
    ("aws_athena", "athena", "#8C4FFF"),
    ("aws_sfn", "step_functions", "#E7157B"),
    ("aws_emr", "emr", "#8C4FFF"),
    ("aws_kinesis", "kinesis", "#8C4FFF"),
    ("aws_redshift", "redshift", "#8C4FFF"),
    ("lakeformation", "lake_formation", "#DD344C"),
    ("aws_iam", "iam", "#DD344C"),
    ("aws_kms", "key_management_service", "#DD344C"),
    ("aws_secretsmanager", "secrets_manager", "#DD344C"),
    ("event_rule", "eventbridge", "#E7157B"),
    ("event_target", "eventbridge", "#E7157B"),
    ("cloudwatch", "cloudwatch", "#E7157B"),
    ("sns", "sns", "#E7157B"),
    ("sqs", "sqs", "#E7157B"),
    ("dynamodb", "dynamodb", "#2E73B8"),
    ("lambda", "lambda", "#ED7100"),
    ("budget", "budgets", "#2E73B8"),
)

_PARTNER_STENCILS = (
    ("databricks", "shape=mxgraph.aws4.databricks;"),
    ("snowflake", "shape=mxgraph.aws4.snowflake;"),
)


def resolve_stencil(resource_type):
    """Return the draw.io style string for a Terraform resource type."""
    rtype = resource_type or ""
    for needle, icon, fill in _STENCILS:
        if needle in rtype:
            return _ICON_STYLE.format(fill=fill, icon=icon)
    for needle, style in _PARTNER_STENCILS:
        if rtype.startswith(needle):
            return style
    return _GENERIC_STENCIL


_FOLD_BADGES = {
    "aws_s3_bucket_server_side_encryption_configuration": "encrypted",
    "aws_s3_bucket_public_access_block": "private",
    "aws_s3_bucket_versioning": "versioned",
    "aws_s3_bucket_lifecycle_configuration": "lifecycle",
    "aws_s3_bucket_replication_configuration": "replicated",
    "aws_s3_bucket_object_lock_configuration": "object lock",
}

_FOLD_SILENT = (
    "aws_s3_bucket_policy",
    "aws_kms_alias",
    "aws_iam_role_policy",
    "aws_iam_role_policy_attachment",
)


def is_folded(resource_type):
    """True when a resource configures another resource rather than standing on its own."""
    rtype = resource_type or ""
    return rtype in _FOLD_BADGES or rtype in _FOLD_SILENT


def extract_node_metadata(resource_change):
    """Capacity and posture facts a plan states outright for one resource."""
    meta = {}
    after = (resource_change.get("change") or {}).get("after") or {}

    for key in ("worker_type", "number_of_workers", "instance_type"):
        if after.get(key):
            meta[key] = after[key]

    if after.get("kms_key_id") or after.get("kms_key_arn"):
        meta["encrypted"] = True
    if after.get("publicly_accessible") is False or after.get("block_public_acls") is True:
        meta["private"] = True

    return meta


def _extract_expr_refs(expr):
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "references" in node:
                found.extend(node["references"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(expr)
    return found


def _config_resources(plan_json):
    root = (plan_json or {}).get("configuration", {}).get("root_module", {})
    return root.get("resources", []) or []


def fold_badges(plan_json, addresses):
    """Map each surviving address to the badges its folded configuration resources imply."""
    badges = {}
    by_config = {r.get("address") or f"{r.get('type')}.{r.get('name')}": r
                 for r in _config_resources(plan_json)}

    for change in (plan_json or {}).get("resource_changes", []):
        rtype = change.get("type", "")
        if not is_folded(rtype):
            continue
        label = _FOLD_BADGES.get(rtype)
        if not label:
            continue
        config = by_config.get(_base_address(change.get("address")))
        if not config:
            continue
        for ref in _extract_expr_refs(config.get("expressions", {})):
            for address in addresses:
                if _base_address(address) == ref:
                    badges.setdefault(address, set()).add(label)

    return badges


def _base_address(address):
    return re.sub(r'\[[^\]]*\]$', "", address or "")


_S3_URI = re.compile(r"s3://([^/]+)")


def _bucket_index(resources):
    index = {}
    for res in resources:
        if res.get("type") != "aws_s3_bucket":
            continue
        name = ((res.get("change") or {}).get("after") or {}).get("bucket")
        if name:
            index[name] = res.get("address")
    return index


def _bucket_for_uri(uri, index):
    match = _S3_URI.search(uri or "")
    return index.get(match.group(1)) if match else None


_DATA_ARGUMENTS = (
    ("aws_glue_job", ("default_arguments", "--source_path"), "in"),
    ("aws_glue_job", ("default_arguments", "--target_path"), "out"),
    ("aws_athena_workgroup",
     ("configuration", "result_configuration", "output_location"), "out"),
    ("aws_kinesis_firehose_delivery_stream",
     ("extended_s3_configuration", "bucket_arn"), "out"),
)


def _walk(node, path):
    for key in path:
        if isinstance(node, list):
            node = node[0] if node else None
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node[0] if isinstance(node, list) and node else node


def _resolve_bucket(value, references, index, addresses):
    if isinstance(value, str) and value:
        found = _bucket_for_uri(value, index) or index.get(value.rsplit(":", 1)[-1])
        if found:
            return found
    for ref in references or []:
        if ref in addresses:
            return ref
    return None


def discover_data_edges(plan_json):
    """Edges the plan states through data-carrying arguments, in the order data travels.

    An argument whose value is unknown until apply still names its target through the
    reference recorded in `configuration`; only that argument's references are read.
    """
    resources = [r for r in (plan_json or {}).get("resource_changes", [])
                 if r.get("mode") == "managed"]
    index = _bucket_index(resources)
    addresses = {r.get("address") for r in resources}
    expressions = {r.get("address") or f"{r.get('type')}.{r.get('name')}":
                   r.get("expressions", {}) for r in _config_resources(plan_json)}
    pairs = []

    for res in resources:
        address = res.get("address")
        after = (res.get("change") or {}).get("after") or {}
        declared = expressions.get(_base_address(address), {})

        for rtype, path, direction in _DATA_ARGUMENTS:
            if res.get("type") != rtype:
                continue
            reference_node = _walk(declared, path)
            bucket = _resolve_bucket(
                _walk(after, path),
                (reference_node or {}).get("references") if isinstance(reference_node, dict) else None,
                index, addresses)
            if not bucket:
                continue
            pairs.append((bucket, address) if direction == "in" else (address, bucket))

    seen, edges = set(), []
    for source, target in pairs:
        if source == target or (source, target) in seen:
            continue
        seen.add((source, target))
        edges.append({"source": source, "target": target, "hop": len(edges) + 1})
    return edges


def generate_flow_ledger(hops):
    """One row per declared hop. Carries only what the plan states."""
    return [{"hop": f"[{edge['hop']}]", "source": edge["source"], "target": edge["target"]}
            for edge in hops]


def generate_flow_ledger_markdown(hops):
    """The hop ledger as a Markdown table."""
    rows = generate_flow_ledger(hops)
    lines = ["| Hop | Source | Target |", "| :--- | :--- | :--- |"]
    if not rows:
        lines.append("| - | no data flow is declared in this plan | - |")
        return "\n".join(lines)
    for row in rows:
        lines.append(f"| {row['hop']} | {row['source']} | {row['target']} |")
    return "\n".join(lines)


_URI_SAFE = "!~*'()"


def encode_drawio_url(xml_text):
    """A 1-click https://app.diagrams.net/#R link, standard library only."""
    quoted = urllib.parse.quote(xml_text, safe=_URI_SAFE)
    compressor = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=-15)
    compressed = compressor.compress(quoted.encode("utf-8")) + compressor.flush()
    return f"https://app.diagrams.net/#R{base64.b64encode(compressed).decode('utf-8').rstrip('=')}"


def decode_drawio_url(url):
    """Inverse of encode_drawio_url, following the steps diagrams.net does."""
    payload = url.split("#R", 1)[1] if "#R" in url else url
    payload += "=" * (-len(payload) % 4)
    inflated = zlib.decompress(base64.b64decode(payload), -15).decode("utf-8")
    return urllib.parse.unquote(inflated)


_LABEL_STYLE = ("verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;"
                "whiteSpace=wrap;overflow=hidden;fontSize=11;fontColor=#1e293b;fontStyle=1;")

_LAYER_COLORS = {
    "ingestion": "#E7157B",
    "storage": "#7AA116",
    "catalog": "#8C4FFF",
    "processing": "#ED7100",
    "consumption": "#01A88D",
    "governance": "#DD344C",
    "other": "#64748b",
}

_SLOT_WIDTH = 190
_STACK_HEIGHT = 110
_ORIGIN_X = 80
_Y_CATALOG = 80
_Y_SPINE = 260
_Y_ORCHESTRATION = 420
_Y_GOVERNANCE = 560


def node_label(address, badges=()):
    """A short two-line title, plus a third line when the plan states capacity or posture."""
    parts = (address or "").split(".")
    leaf_parts = [p for p in parts if not p.startswith("module")]
    if len(leaf_parts) >= 2:
        short_type = leaf_parts[-2].replace("aws_", "").replace("_", " ").title()
        label = f"{short_type}<br>{leaf_parts[-1]}"
    else:
        label = "<br>".join(parts[-2:]) if len(parts) > 2 else (address or "")
    return f"{label}<br>{', '.join(sorted(badges))}" if badges else label


def _badge_text(meta, folded):
    badges = set(folded)
    worker = meta.get("worker_type") or meta.get("instance_type")
    if worker:
        count = meta.get("number_of_workers")
        badges.add(f"{worker} x{count}" if count else str(worker))
    if meta.get("encrypted"):
        badges.add("encrypted")
    if meta.get("private"):
        badges.add("private")
    return badges


def _role_of(res):
    return architecture_model.classify_role(res.get("type", ""), "", res.get("name", ""))


def _layer_of(res):
    return architecture_model.layer_of(_role_of(res))


def _instance_key(address):
    match = re.search(r'\["([^"]+)"\]', address or "")
    return match.group(1) if match else ""


def _by_layer(resources):
    layers = {}
    for res in resources:
        layers.setdefault(_layer_of(res), []).append(res)
    return layers


def _spine_groups(layers):
    """The alternating zone / transform sequence, ordered by medallion stage."""
    zones = sorted(layers.get("storage", []),
                   key=lambda r: (architecture_model.stage_rank(
                       _instance_key(r.get("address")), r.get("name", "")),
                       r.get("address") or ""))
    transforms = [r for r in layers.get("processing", []) if _role_of(r) != "orchestrate"]

    groups = []
    for index, zone in enumerate(zones):
        groups.append([zone])
        if index < len(transforms) and index < len(zones) - 1:
            groups.append([transforms[index]])
    remaining = transforms[max(0, len(zones) - 1):]
    groups.extend([res] for res in remaining)
    return groups


def _stack(addresses, x, y, positions):
    for index, address in enumerate(addresses):
        positions[address] = (x, y + index * _STACK_HEIGHT)


def layout_positions(resources):
    """Return {address: (x, y)} plus the bands to draw around them.

    Flow layers run left to right with the medallion zones ordered by stage and the
    transforms sitting between them. Cataloging sits above the spine, security and
    monitoring below it, matching the AWS analytics reference architecture.
    """
    layers = _by_layer(resources)
    positions, bands = {}, []
    x = _ORIGIN_X

    ingestion = [r.get("address") for r in layers.get("ingestion", [])]
    if ingestion:
        _stack(ingestion, x, _Y_SPINE, positions)
        bands.append(("ingestion", x - 20, _Y_SPINE - 50, _SLOT_WIDTH,
                      len(ingestion) * _STACK_HEIGHT + 70))
        x += _SLOT_WIDTH

    spine_start = x
    for group in _spine_groups(layers):
        _stack([r.get("address") for r in group], x, _Y_SPINE, positions)
        x += _SLOT_WIDTH

    orchestration = [r.get("address") for r in layers.get("processing", [])
                     if r.get("address") not in positions]
    for index, address in enumerate(orchestration):
        positions[address] = (spine_start + index * _SLOT_WIDTH, _Y_ORCHESTRATION)

    spine_width = max(x - spine_start, _SLOT_WIDTH)
    if x > spine_start:
        height = (_Y_ORCHESTRATION - _Y_SPINE + 120) if orchestration else 170
        bands.append(("storage", spine_start - 20, _Y_SPINE - 50, spine_width, height))

    consumption = [r.get("address") for r in layers.get("consumption", [])]
    if consumption:
        _stack(consumption, x, _Y_SPINE, positions)
        bands.append(("consumption", x - 20, _Y_SPINE - 50, _SLOT_WIDTH,
                      len(consumption) * _STACK_HEIGHT + 70))
        x += _SLOT_WIDTH

    total_width = max(x - _ORIGIN_X, _SLOT_WIDTH)
    per_row = max(1, total_width // _SLOT_WIDTH)

    def band(layer, addresses, origin_x, top, width=None):
        if not addresses:
            return
        for index, address in enumerate(addresses):
            positions[address] = (origin_x + (index % per_row) * _SLOT_WIDTH,
                                  top + (index // per_row) * _STACK_HEIGHT)
        rows = (len(addresses) + per_row - 1) // per_row
        bands.append((layer, origin_x - 20, top - 50,
                      width or min(len(addresses), per_row) * _SLOT_WIDTH,
                      rows * _STACK_HEIGHT + 70))

    band("catalog", [r.get("address") for r in layers.get("catalog", [])],
         spine_start, _Y_CATALOG)
    band("governance",
         [r.get("address") for r in layers.get("governance", [])]
         + [r.get("address") for r in layers.get("other", [])],
         _ORIGIN_X, _Y_GOVERNANCE, width=total_width)

    return positions, bands


def _create_mxgraph_xml(page_width=1800, page_height=1000):
    model = ET.Element("mxGraphModel", {
        "dx": "1600", "dy": "1000", "grid": "1", "gridSize": "10",
        "guides": "1", "tooltips": "1", "connect": "1", "arrows": "1",
        "fold": "1", "page": "1", "pageScale": "1", "pageWidth": str(page_width),
        "pageHeight": str(page_height), "math": "0", "shadow": "0",
        "background": "#ffffff",
    })
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    return model, root


_BAND_LABELS = {
    "ingestion": "INGESTION",
    "storage": "STORAGE, PROCESSING",
    "catalog": "CATALOGING, GOVERNANCE &amp; SEARCH",
    "consumption": "CONSUMPTION",
    "governance": "SECURITY &amp; MONITORING",
}


def _append_bands(root, bands):
    for layer, x, y, width, height in bands:
        container = ET.SubElement(root, "mxCell", {
            "id": f"layer_box_{layer}",
            "value": f"<b>{_BAND_LABELS.get(layer, layer.upper())}</b>",
            "style": (f"swimlane;startSize=28;fillColor=none;strokeColor="
                      f"{_LAYER_COLORS.get(layer, '#64748b')};strokeWidth=1.5;"
                      "fontColor=#1e293b;fontStyle=1;fontSize=12;rounded=1;arcSize=8;"
                      "dashed=1;dashPattern=6 4;container=0;collapsible=0;"),
            "vertex": "1",
            "parent": "1",
        })
        ET.SubElement(container, "mxGeometry", {
            "x": str(x), "y": str(y), "width": str(width), "height": str(height),
            "as": "geometry",
        })


def generate_drawio_from_plan(plan_json, title="Architecture Blueprint"):
    """Render one plan as draw.io XML, a 1-click URL, and the declared-hop ledger."""
    resources = [r for r in (plan_json.get("resource_changes", []) if plan_json else [])
                 if r.get("mode") == "managed" and not is_folded(r.get("type", ""))]

    positions, bands = layout_positions(resources)
    badges = fold_badges(plan_json, [r.get("address") for r in resources])

    max_x = max([x for x, _ in positions.values()], default=800)
    max_y = max([y for _, y in positions.values()], default=600)
    model, root = _create_mxgraph_xml(max(1800, max_x + 350), max(1000, max_y + 250))

    _append_bands(root, bands)

    node_map = {}
    for index, res in enumerate(resources):
        address = res.get("address")
        x, y = positions[address]
        node_id = f"node_{index}"
        node_map[address] = node_id

        cell = ET.SubElement(root, "mxCell", {
            "id": node_id,
            "value": node_label(address, _badge_text(extract_node_metadata(res),
                                                     badges.get(address, set()))),
            "tooltip": address,
            "style": resolve_stencil(res.get("type")) + _LABEL_STYLE,
            "vertex": "1",
            "parent": "1",
        })
        ET.SubElement(cell, "mxGeometry", {
            "x": str(x), "y": str(y), "width": "68", "height": "68", "as": "geometry",
        })

    edges = discover_data_edges(plan_json)
    for index, edge in enumerate(edges):
        source_id = node_map.get(edge["source"])
        target_id = node_map.get(edge["target"])
        if not source_id or not target_id:
            continue
        cell = ET.SubElement(root, "mxCell", {
            "id": f"edge_{index}",
            "value": f"[{edge['hop']}]",
            "style": ("edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;"
                      "jettySize=auto;html=1;strokeColor=#475569;strokeWidth=2;"
                      "endArrow=block;endFill=1;fontSize=10;fontColor=#334155;"
                      "labelBackgroundColor=#ffffff;"),
            "edge": "1",
            "parent": "1",
            "source": source_id,
            "target": target_id,
        })
        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})

    xml_str = ET.tostring(model, encoding="utf-8", xml_declaration=False).decode("utf-8")
    return {
        "xml": xml_str,
        "url": encode_drawio_url(xml_str),
        "ledger": generate_flow_ledger(edges),
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
