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


def _module_of(address):
    """`module.storage.aws_s3_bucket.bronze` -> `storage`. A root resource has no module."""
    parts = (address or "").split(".")
    return parts[1] if len(parts) > 2 and parts[0] == "module" else ""


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
_MARGIN_Y = 30
_LABEL_STRIP = 50
_BAND_PAD = 20
_GUTTER = 60


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


def _band_height(rows):
    return _LABEL_STRIP + max(1, rows) * _STACK_HEIGHT + _BAND_PAD


def _addresses(resources):
    return [r.get("address") for r in resources]


def layout_positions(resources):
    """Return {address: (x, y)} plus the bands to draw around them.

    Flow layers run left to right with the medallion zones ordered by stage and the
    transforms sitting between them. Cataloging sits above the spine, security and
    monitoring below it, matching the AWS analytics reference architecture. Every band
    height is derived from what the band holds, so a band never grows into its neighbour.
    """
    layers = _by_layer(resources)
    positions, bands = {}, []

    ingestion = _addresses(layers.get("ingestion", []))
    groups = _spine_groups(layers)
    consumption = _addresses(layers.get("consumption", []))
    catalog = _addresses(layers.get("catalog", []))
    footer = _addresses(layers.get("governance", [])) + _addresses(layers.get("other", []))

    columns = ([ingestion] if ingestion else []) + [_addresses(group) for group in groups]
    spine_start = _ORIGIN_X + (_SLOT_WIDTH if ingestion else 0)
    spine_end = spine_start + _SLOT_WIDTH * len(groups)
    if consumption:
        columns.append(consumption)
    total_width = max(_SLOT_WIDTH * len(columns), _SLOT_WIDTH)
    per_row = max(1, total_width // _SLOT_WIDTH)

    def place(addresses, origin_x, top, wrap):
        for index, address in enumerate(addresses):
            positions[address] = (origin_x + (index % wrap) * _SLOT_WIDTH,
                                  top + (index // wrap) * _STACK_HEIGHT)
        return (len(addresses) + wrap - 1) // wrap

    top = _MARGIN_Y
    if catalog:
        rows = place(catalog, spine_start, top + _LABEL_STRIP, per_row)
        bands.append(("catalog", spine_start - 20, top,
                      min(len(catalog), per_row) * _SLOT_WIDTH, _band_height(rows)))
        top += _band_height(rows) + _GUTTER

    node_top = top + _LABEL_STRIP
    depth = max([len(column) for column in columns] or [1])

    if ingestion:
        place(ingestion, _ORIGIN_X, node_top, 1)
    for index, group in enumerate(groups):
        place(_addresses(group), spine_start + index * _SLOT_WIDTH, node_top, 1)

    orchestration = [address for address in _addresses(layers.get("processing", []))
                     if address not in positions]
    orchestration_rows = 0
    if orchestration:
        orchestration_rows = place(orchestration, spine_start,
                                   node_top + depth * _STACK_HEIGHT,
                                   max(1, len(groups) or 1))

    spine_rows = depth + orchestration_rows
    if ingestion:
        bands.append(("ingestion", _ORIGIN_X - 20, top, _SLOT_WIDTH,
                      _band_height(len(ingestion))))
    if groups:
        bands.append(("storage", spine_start - 20, top,
                      max(spine_end - spine_start, _SLOT_WIDTH), _band_height(spine_rows)))
    if consumption:
        place(consumption, spine_end, node_top, 1)
        bands.append(("consumption", spine_end - 20, top, _SLOT_WIDTH,
                      _band_height(len(consumption))))

    flow_bottom = top + max(_band_height(rows) for rows in
                            ([spine_rows] if groups else [])
                            + ([len(ingestion)] if ingestion else [])
                            + ([len(consumption)] if consumption else [1]))

    if footer:
        governance_top = flow_bottom + _GUTTER
        rows = place(footer, _ORIGIN_X, governance_top + _LABEL_STRIP, per_row)
        bands.append(("governance", _ORIGIN_X - 20, governance_top, total_width,
                      _band_height(rows)))

    return positions, bands


_STEP_STYLE = ("rounded=1;arcSize=6;whiteSpace=wrap;html=1;align=left;verticalAlign=top;"
               "spacing=8;fontSize=11;fontColor=#1e293b;fillColor=#f8fafc;"
               "strokeColor=#cbd5e1;")


def walkthrough_steps(edges, by_address):
    """One numbered step per declared hop, in the words the plan supports.

    Derived, never written. "Glue validates and cleans the records" is a sentence about a
    job we know exists and whose behaviour we do not know; what the plan states is which
    paths it reads and writes, and how big it is.
    """
    if not edges:
        return ["This plan declares no data flow. No resource states a source or target "
                "path, so there is no hop to describe -- the arrows are absent because the "
                "wiring is, not because the diagram is incomplete."]

    steps = []
    for edge in edges:
        target = by_address.get(edge["target"], {})
        meta = extract_node_metadata(target)
        capacity = _badge_text(meta, set())
        detail = f" ({', '.join(sorted(capacity))})" if capacity else ""
        steps.append(f"{edge['source']} to {edge['target']}{detail}")
    return steps


def _append_walkthrough(root, steps, x, y, width):
    for index, text in enumerate(steps, 1):
        cell = ET.SubElement(root, "mxCell", {
            "id": f"legend_step_{index}",
            "value": f"<b>{index}.</b> {text}" if len(steps) > 1 or "declares no" not in text
                     else text,
            "style": _STEP_STYLE, "vertex": "1", "parent": "1",
        })
        ET.SubElement(cell, "mxGeometry", {
            "x": str(x), "y": str(y + (index - 1) * 54), "width": str(width),
            "height": "46", "as": "geometry"})


def deployment_containment(plan_json):
    """Which resource sits inside which subnet, and which sits inside no subnet at all.

    Nesting comes from a resource's own subnet reference, never from its type. A service that
    names two subnets is placed at VPC level rather than in the first one: it spans them, and
    drawing it inside one would state a placement the plan does not.
    """
    resources = [r for r in (plan_json or {}).get("resource_changes", [])
                 if r.get("mode") == "managed" and not is_folded(r.get("type", ""))]
    by_type = {}
    for res in resources:
        by_type.setdefault(res.get("type"), []).append(res.get("address"))

    vpc = (by_type.get("aws_vpc") or [None])[0]
    subnets = list(by_type.get("aws_subnet") or [])
    expressions = {r.get("address") or f"{r.get('type')}.{r.get('name')}":
                   r.get("expressions", {}) for r in _config_resources(plan_json)}

    placed = {subnet: [] for subnet in subnets}
    spanning, regional = [], []

    # A composed stack wires subnets through module OUTPUTS, so a root-level reference to
    # aws_subnet never appears. The dependency then names a module, not a subnet -- which is
    # genuinely all the plan says, so those land at VPC level rather than being placed in a
    # subnet chosen for them.
    vpc_module = _module_of(vpc) if vpc else ""
    module_consumers = set()
    if vpc_module:
        for consumer, producers in architecture_model.module_dependencies(plan_json).items():
            if vpc_module in producers:
                module_consumers.add(consumer)

    for res in resources:
        address = res.get("address")
        if address == vpc or address in placed:
            continue
        refs = set(_extract_expr_refs(expressions.get(_base_address(address), {})))
        touched = [s for s in subnets if _base_address(s) in refs or s in refs]
        if len(touched) == 1:
            placed[touched[0]].append(address)
        elif touched or _module_of(address) in module_consumers:
            spanning.append(address)
        else:
            regional.append(address)

    return {"vpc": vpc, "subnets": placed, "spanning": spanning, "regional": regional}


_CONTAINER_STYLE = ("rounded=1;arcSize=4;fillColor=none;strokeColor={stroke};strokeWidth=2;"
                    "dashed={dashed};dashPattern=8 4;verticalAlign=top;align=left;"
                    "spacingLeft=12;spacingTop=6;fontSize=12;fontStyle=1;fontColor={stroke};"
                    "container=1;collapsible=0;")

_CLOUD_STROKE = "#232F3E"
_VPC_STROKE = "#8C4FFF"
_SUBNET_STROKE = "#01A88D"


def _container(root, parent, cell_id, label, x, y, width, height, stroke, dashed=0):
    cell = ET.SubElement(root, "mxCell", {
        "id": cell_id, "value": label,
        "style": _CONTAINER_STYLE.format(stroke=stroke, dashed=dashed),
        "vertex": "1", "parent": parent,
    })
    ET.SubElement(cell, "mxGeometry", {
        "x": str(x), "y": str(y), "width": str(width), "height": str(height),
        "as": "geometry"})
    return cell_id


def _append_deployment(root, containment, by_address, badges):
    """The deployment page: AWS Cloud > VPC > subnet > service, nested for real.

    Child geometry is relative to its container, which is why this cannot reuse the logical
    page's absolute positions.
    """
    subnets = containment["subnets"]
    spanning, regional = containment["spanning"], containment["regional"]

    subnet_rows = max([len(v) for v in subnets.values()] or [0])
    subnet_h = _LABEL_STRIP + max(1, subnet_rows) * _STACK_HEIGHT
    vpc_w = max(len(subnets), 1) * (_SLOT_WIDTH + 20) + 40
    vpc_h = _LABEL_STRIP + subnet_h + (_STACK_HEIGHT if spanning else 0) + 40
    regional_rows = (len(regional) + 2) // 3
    cloud_w = vpc_w + 3 * _SLOT_WIDTH + 80
    cloud_h = max(vpc_h, _LABEL_STRIP + regional_rows * _STACK_HEIGHT) + 80

    cloud = _container(root, "1", "layer_box_cloud", "<b>AWS Cloud</b>",
                       40, 40, cloud_w, cloud_h, _CLOUD_STROKE)

    def node(parent, address, x, y, index):
        res = by_address.get(address, {})
        cell = ET.SubElement(root, "mxCell", {
            "id": f"dep_node_{index}",
            "value": node_label(address, _badge_text(extract_node_metadata(res),
                                                     badges.get(address, set()))),
            "tooltip": address,
            "style": resolve_stencil(res.get("type")) + _LABEL_STYLE,
            "vertex": "1", "parent": parent,
        })
        ET.SubElement(cell, "mxGeometry", {
            "x": str(x), "y": str(y), "width": "68", "height": "68", "as": "geometry"})

    index = 0
    if containment["vpc"]:
        vpc = _container(root, cloud, "layer_box_vpc",
                         f"<b>VPC</b> {containment['vpc'].rsplit('.', 1)[-1]}",
                         30, _LABEL_STRIP, vpc_w, vpc_h, _VPC_STROKE)
        for position, (subnet, members) in enumerate(sorted(subnets.items())):
            box = _container(root, vpc, f"layer_box_subnet_{position}",
                             subnet.rsplit(".", 1)[-1],
                             20 + position * (_SLOT_WIDTH + 20), _LABEL_STRIP,
                             _SLOT_WIDTH, subnet_h, _SUBNET_STROKE, dashed=1)
            for row, address in enumerate(members):
                node(box, address, 60, _LABEL_STRIP + row * _STACK_HEIGHT, index)
                index += 1
        for column, address in enumerate(spanning):
            node(vpc, address, 20 + column * _SLOT_WIDTH, _LABEL_STRIP + subnet_h + 20, index)
            index += 1

    # Regional services sit inside the cloud boundary and outside the VPC, which is where
    # S3, KMS and Athena actually are. Drawing them inside the VPC would be a network
    # placement the plan does not state.
    origin_x = (vpc_w + 60) if containment["vpc"] else 30
    for position, address in enumerate(regional):
        node(cloud, address, origin_x + (position % 3) * _SLOT_WIDTH,
             _LABEL_STRIP + (position // 3) * _STACK_HEIGHT, index)
        index += 1


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

    steps = walkthrough_steps(edges, {r.get("address"): r for r in resources})
    bottom = max([y + h for _, _, y, _, h in bands],
                 default=_MARGIN_Y + _band_height(1))
    _append_walkthrough(root, steps, _ORIGIN_X - 20, bottom + _GUTTER,
                        max(max_x - _ORIGIN_X + 200, 600))

    logical = ET.tostring(model, encoding="utf-8", xml_declaration=False).decode("utf-8")

    # The deployment page is emitted only when the stack actually provisions a VPC. An empty
    # VPC container is a picture of infrastructure that does not exist, and the same doctrine
    # lineage_graph.py applies to its own nodes.
    containment = deployment_containment(plan_json)
    pages = [("Logical", logical)]
    if containment["vpc"]:
        model2, root2 = _create_mxgraph_xml(2200, 1400)
        _append_deployment(root2, containment,
                           {r.get("address"): r for r in resources}, badges)
        pages.append(("Deployment", ET.tostring(
            model2, encoding="utf-8", xml_declaration=False).decode("utf-8")))

    xml_str = "".join(
        ['<mxfile host="app.diagrams.net">']
        + [f'<diagram name="{name}">{body}</diagram>' for name, body in pages]
        + ["</mxfile>"])

    return {
        "xml": xml_str,
        "url": encode_drawio_url(xml_str),
        "pages": [name for name, _ in pages],
        "ledger": generate_flow_ledger(edges),
        "ledger_markdown": generate_flow_ledger_markdown(edges),
    }


def parse_graph(xml_text):
    """Read a diagram back into {"nodes": {id: address}, "edges": {id: {source, target}}}.

    Only the FIRST page is read. Reconciliation compares what the operator edited against
    what was generated, and the operator edits the logical page; merging the deployment
    page's cells in would report every regional service as a second copy of itself.
    """
    nodes, edges = {}, {}
    try:
        root = ET.fromstring(xml_text or "")
    except ET.ParseError:
        return {"nodes": nodes, "edges": edges}

    if root.tag == "mxfile":
        first = root.find("diagram")
        if first is None:
            return {"nodes": nodes, "edges": edges}
        root = first

    for cell in root.iter("mxCell"):
        cell_id = cell.get("id") or ""
        if cell_id.startswith("layer_") or cell_id.startswith("legend_"):
            continue
        if cell.get("vertex") == "1":
            nodes[cell_id] = cell.get("tooltip") or cell.get("value") or cell_id
        elif cell.get("edge") == "1":
            edges[cell_id] = {"source": cell.get("source"), "target": cell.get("target")}
    return {"nodes": nodes, "edges": edges}
