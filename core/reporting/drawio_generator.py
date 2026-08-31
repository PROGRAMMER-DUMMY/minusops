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
    ("glue_crawler", "glue_crawlers", "#8C4FFF"),
    ("glue_catalog", "glue_data_catalog", "#8C4FFF"),
    ("glue_database", "glue_data_catalog", "#8C4FFF"),
    ("aws_s3", "s3", "#7AA116"),
    ("aws_athena", "athena", "#8C4FFF"),
    ("aws_sfn", "step_functions", "#E7157B"),
    ("aws_emr", "emr", "#8C4FFF"),
    ("aws_kinesis_firehose", "kinesis_data_firehose", "#8C4FFF"),
    ("aws_kinesis", "kinesis", "#8C4FFF"),
    ("aws_redshift", "redshift", "#8C4FFF"),
    ("lakeformation", "lake_formation", "#DD344C"),
    ("aws_iam", "identity_and_access_management", "#DD344C"),
    ("aws_kms", "key_management_service", "#DD344C"),
    ("aws_secretsmanager", "secrets_manager", "#DD344C"),
    ("security_group", "security_group", "#DD344C"),
    ("event_rule", "eventbridge", "#E7157B"),
    ("event_target", "eventbridge", "#E7157B"),
    ("cloudwatch", "cloudwatch", "#E7157B"),
    ("sns", "sns", "#E7157B"),
    ("sqs", "sqs", "#E7157B"),
    ("dynamodb", "dynamodb", "#2E73B8"),
    ("lambda", "lambda", "#ED7100"),
    ("budget", "budgets", "#2E73B8"),
    ("vpc", "virtual_private_cloud", "#8C4FFF"),
    ("subnet", "group_subnet", "#8C4FFF"),
    ("route_table", "route_table", "#8C4FFF"),
    ("internet_gateway", "internet_gateway", "#8C4FFF"),
    ("nat_gateway", "nat_gateway", "#8C4FFF"),
    ("waf", "waf", "#DD344C"),
    ("api_gateway", "api_gateway", "#ED7100"),
)

# aws4 carries no stencil for a partner product, and a shape reference the library cannot
# resolve renders as a blank tile with no error. These take the generic resource frame in
# the partner's own colour instead of a name draw.io will silently drop.
_PARTNER_STENCILS = (
    ("databricks", "#FF3621"),
    ("snowflake", "#29B5E8"),
)


def resolve_stencil(resource_type):
    """Return the draw.io style string for a Terraform resource type."""
    rtype = resource_type or ""
    for needle, icon, fill in _STENCILS:
        if needle in rtype:
            return _ICON_STYLE.format(fill=fill, icon=icon)
    for needle, fill in _PARTNER_STENCILS:
        if rtype.startswith(needle):
            return _GENERIC_STENCIL + f"fillColor={fill};strokeColor=#232F3E;"
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
    ("aws_glue_job", ("default_arguments", "--quarantine_path"), "out"),
    ("aws_athena_workgroup",
     ("configuration", "result_configuration", "output_location"), "out"),
    ("aws_kinesis_firehose_delivery_stream",
     ("extended_s3_configuration", "bucket_arn"), "out"),
    ("aws_kinesis_firehose_delivery_stream", ("s3_configuration", "bucket_arn"), "out"),
    ("aws_dms_s3_endpoint", ("bucket_name",), "out"),
)

# Resources that name BOTH ends of a hop and are neither of them. A replication
# configuration is not a place data rests; it states that one bucket copies to another.
_BUCKET_TO_BUCKET = (
    ("aws_s3_bucket_replication_configuration",
     ("bucket",), ("rule", "destination", "bucket")),
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

        for rtype, source_path, target_path in _BUCKET_TO_BUCKET:
            if res.get("type") != rtype:
                continue
            ends = []
            for path in (source_path, target_path):
                node = _walk(declared, path)
                ends.append(_resolve_bucket(
                    _walk(after, path),
                    node.get("references") if isinstance(node, dict) else None,
                    index, addresses))
            if all(ends):
                pairs.append((ends[0], ends[1]))

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
                "whiteSpace=wrap;overflow=visible;labelWidth=170;fontSize=11;"
                "fontColor=#1e293b;fontStyle=0;")

# What a resource DOES, in the words the role classifier already assigns it. The icon
# already states the service, so a label reading "S3 Bucket / medallion_buckets" says the
# product twice and the purpose never.
_ROLE_LABELS = {
    "ingest": "Ingest",
    "stage": "Zone",
    "store_other": "Store",
    "catalog": "Catalog",
    "transform": "Transform",
    "orchestrate": "Orchestrate",
    "consume": "Serve",
    "security": "Secure",
    "network": "Network",
    "observability": "Observe",
}

# Colour belongs to the resource, where it is AWS's own service category and means the same
# thing on every AWS diagram anyone has read. A second scheme on the bands competes with it:
# a Glue job is analytics purple, and in a coloured PROCESSING band it was a purple tile in an
# orange box saying two different things at once. The bands are structure now, drawn in one
# neutral grey. These stay for the legend, which explains the icons rather than the boxes.
_LAYER_COLORS = {
    "ingestion": "#E7157B",
    "storage": "#7AA116",
    "catalog": "#8C4FFF",
    "processing": "#ED7100",
    "consumption": "#01A88D",
    "governance": "#DD344C",
    "other": "#64748b",
}
_BAND_STROKE = "#94a3b8"

_SLOT_WIDTH = 190
_STACK_HEIGHT = 110
_ORIGIN_X = 80
_MARGIN_Y = 30
_LABEL_STRIP = 50
_BAND_PAD = 20
_GUTTER = 60


def node_label(address, badges=(), role=None):
    """The role this resource plays and the name the operator gave it, then any stated posture.

    The role comes from `classify_role`, which is the same call that decides which band the
    resource lands in -- so the label and the placement cannot disagree.
    """
    parts = (address or "").split(".")
    leaf_parts = [p for p in parts if not p.startswith("module")]
    name = _instance_key(address) or (leaf_parts[-1] if leaf_parts else (address or ""))
    kind = _ROLE_LABELS.get(role)
    if kind is None and len(leaf_parts) >= 2:
        kind = leaf_parts[-2].replace("aws_", "").replace("_", " ").title()
    label = f"<b>{name}</b><br>{kind}" if kind else f"<b>{name}</b>"
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
    """The resource's role, resolved with its instance key.

    `classify_role` splits a bucket into `stage` or `store_other` by that key, so dropping it
    made every medallion zone read as a generic store -- the label saying "Store" while the
    layout placed it on the spine as a zone.
    """
    return architecture_model.classify_role(
        res.get("type", ""), _instance_key(res.get("address")), res.get("name", ""))


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


_EXTERNAL_ACTORS = {
    "Batch files landing in S3 (CSV, JSON, Parquet)":
        ("Upstream system", "drops files into the landing prefix"),
    "Database CDC replication (DMS or Glue JDBC)":
        ("Source database", "outside this account; replicated in"),
    "Partner file drops over SFTP (Transfer Family)":
        ("Partner", "authenticates and drops files over SFTP"),
    "Continuous event feed (Kinesis, MSK, API Gateway)":
        ("Event producer", "application or device emitting events"),
}


def external_actors(requirements):
    """Who sends data in, from what the interview recorded -- never from the plan.

    A plan states the resources inside the account and cannot state what is outside it. The
    actor is drawn because an operator said it exists, and is labelled with their answer
    rather than a system name nobody gave.
    """
    pillars = ((requirements or {}).get("pillars") or {})
    answer = pillars.get("ingestion_source")
    choice = (answer or {}).get("choice") if isinstance(answer, dict) else answer
    named = _EXTERNAL_ACTORS.get(choice)
    if not named:
        return []
    label, detail = named
    return [{"key": "external_source", "label": label, "detail": detail, "choice": choice}]


_UNRANKED_STAGE = 40


def _stage_rank_of(res):
    return architecture_model.stage_rank(_instance_key(res.get("address")),
                                         res.get("name", ""))


def _medallion_split(layers):
    """The storage layer separated into the medallion spine and the buckets that are not on it.

    `classify_role` puts every bucket in the storage layer, and `stage_rank` returns 40 for a
    name it does not recognise. An Athena results bucket and a quarantine bucket then sort to
    the end of the spine and draw as though they were the stage after Gold. They are storage,
    they are not a medallion zone, and the drawing has to say which.
    """
    storage = layers.get("storage", [])
    ranked = sorted((r for r in storage if _stage_rank_of(r) != _UNRANKED_STAGE),
                    key=lambda r: (_stage_rank_of(r), r.get("address") or ""))
    support = sorted((r for r in storage if _stage_rank_of(r) == _UNRANKED_STAGE),
                     key=lambda r: r.get("address") or "")
    return ranked, support


def _band_height(rows):
    return _LABEL_STRIP + max(1, rows) * _STACK_HEIGHT + _BAND_PAD


def _addresses(resources):
    return [r.get("address") for r in resources]


def layout_positions(resources, actors=()):
    """Return {address: (x, y)}, the bands to draw around them, and the account boundary.

    Processing sits above storage and each transform is offset half a slot, so it lands
    between the two zones it moves data between and the hops read as a zigzag rather than a
    straight line through a row of tiles. Cataloging sits above both and security below,
    matching the AWS analytics reference architecture. Every band height is derived from what
    the band holds, so a band never grows into its neighbour.
    """
    layers = _by_layer(resources)
    positions, bands = {}, []
    inset = (_SLOT_WIDTH + _GUTTER) if actors else 0

    ingestion = _addresses(layers.get("ingestion", []))
    zones, support = _medallion_split(layers)
    transforms = [r for r in layers.get("processing", []) if _role_of(r) != "orchestrate"]
    orchestration = [r for r in layers.get("processing", []) if _role_of(r) == "orchestrate"]
    consumption = _addresses(layers.get("consumption", []))
    catalog = _addresses(layers.get("catalog", []))
    footer = _addresses(layers.get("governance", [])) + _addresses(layers.get("other", []))

    spine_columns = max(len(zones), len(transforms) + 1, 1)
    origin_x = _ORIGIN_X + inset
    spine_start = origin_x + (_SLOT_WIDTH if ingestion else 0)
    spine_width = spine_columns * _SLOT_WIDTH
    spine_end = spine_start + spine_width
    total_width = spine_end - origin_x + (_SLOT_WIDTH if consumption else 0)
    per_row = max(1, total_width // _SLOT_WIDTH)

    def place(addresses, origin_x, top, wrap):
        for index, address in enumerate(addresses):
            positions[address] = (origin_x + (index % wrap) * _SLOT_WIDTH,
                                  top + (index // wrap) * _STACK_HEIGHT)
        return (len(addresses) + wrap - 1) // wrap

    # The account boundary is drawn around every band and carries its own label, so the
    # topmost band starts a label strip down rather than at the margin. Without it the
    # boundary's top edge lands above the canvas.
    top = _MARGIN_Y + _LABEL_STRIP
    if catalog:
        rows = place(catalog, spine_start, top + _LABEL_STRIP, spine_columns)
        bands.append(("catalog", spine_start - 20, top,
                      min(len(catalog), spine_columns) * _SLOT_WIDTH, _band_height(rows)))
        top += _band_height(rows) + _GUTTER

    processing_rows = 0
    processing_height = 0
    if transforms or orchestration:
        for index, res in enumerate(transforms):
            positions[res.get("address")] = (
                spine_start + index * _SLOT_WIDTH + _SLOT_WIDTH // 2,
                top + _LABEL_STRIP)
        processing_rows = 1 if transforms else 0
        if orchestration:
            processing_rows += place(_addresses(orchestration), spine_start,
                                     top + _LABEL_STRIP + processing_rows * _STACK_HEIGHT,
                                     spine_columns)
        processing_height = _band_height(processing_rows)
        bands.append(("processing", spine_start - 20, top, spine_width, processing_height))

    storage_top = top + processing_height + (_GUTTER if processing_height else 0)
    storage_rows = 0
    if zones:
        place(_addresses(zones), spine_start, storage_top + _LABEL_STRIP, spine_columns)
        storage_rows = (len(zones) + spine_columns - 1) // spine_columns
    if support:
        storage_rows += place(_addresses(support), spine_start,
                              storage_top + _LABEL_STRIP + storage_rows * _STACK_HEIGHT,
                              spine_columns)
    storage_height = _band_height(storage_rows) if (zones or support) else 0
    if storage_height:
        bands.append(("storage", spine_start - 20, storage_top, spine_width, storage_height))

    flow_height = max(processing_height + (_GUTTER if processing_height else 0)
                      + storage_height, _band_height(1))
    if ingestion:
        place(ingestion, origin_x, top + _LABEL_STRIP, 1)
        bands.append(("ingestion", origin_x - 20, top, _SLOT_WIDTH,
                      max(flow_height, _band_height(len(ingestion)))))
    if consumption:
        place(consumption, spine_end, top + _LABEL_STRIP, 1)
        bands.append(("consumption", spine_end - 20, top, _SLOT_WIDTH,
                      max(flow_height, _band_height(len(consumption)))))

    flow_bottom = top + max([height for _, _, band_top, _, height in bands
                             if band_top == top] or [flow_height])

    if footer:
        governance_top = flow_bottom + _GUTTER
        rows = place(footer, origin_x, governance_top + _LABEL_STRIP, per_row)
        bands.append(("governance", origin_x - 20, governance_top, total_width,
                      _band_height(rows)))

    boundary = _boundary(bands)
    for index, actor in enumerate(actors):
        positions[f"actor::{actor['key']}"] = (
            _ORIGIN_X, (boundary[1] if boundary else _MARGIN_Y) + _LABEL_STRIP
            + index * _STACK_HEIGHT)

    return positions, bands, boundary


def _boundary(bands):
    """The account boundary: the rectangle every band sits inside, with room for its label.

    Returned rather than drawn so the caller decides whether there is anything to enclose.
    An empty AWS Cloud box around nothing is a picture of an account with no resources in it.
    """
    if not bands:
        return None
    left = min(x for _, x, _, _, _ in bands) - _BAND_PAD
    top = min(y for _, _, y, _, _ in bands) - _LABEL_STRIP
    right = max(x + width for _, x, _, width, _ in bands) + _BAND_PAD
    bottom = max(y + height for _, _, y, _, height in bands) + _BAND_PAD
    return (left, top, right - left, bottom - top)


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


_EDGE_STYLE = ("edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;"
               "jettySize=auto;html=1;strokeColor=#475569;strokeWidth=2;"
               "endArrow=block;endFill=1;fontSize=10;fontColor=#334155;"
               "labelBackgroundColor=#ffffff;")


def _edge_anchors(source, target):
    """Which side of each box the hop leaves and enters.

    Left unset, draw.io picks the nearest pair of anchors and a hop between two rows leaves
    sideways before turning, which crosses the boxes either side of it. A hop that moves
    mostly downward should leave the bottom and enter the top.
    """
    if not source or not target:
        return ""
    dx, dy = target[0] - source[0], target[1] - source[1]
    if abs(dy) > abs(dx):
        exit_y, entry_y = (1, 0) if dy > 0 else (0, 1)
        return f"exitX=0.5;exitY={exit_y};entryX=0.5;entryY={entry_y};"
    exit_x, entry_x = (1, 0) if dx > 0 else (0, 1)
    return f"exitX={exit_x};exitY=0.5;entryX={entry_x};entryY=0.5;"


_STEP_HEIGHT = 46
_STEP_PITCH = 54


_LEGEND_ROWS = (
    ("INGESTION", "where data enters this account"),
    ("PROCESSING", "a job that reads one path and writes another"),
    ("STORAGE", "a medallion zone, or a bucket that is not one"),
    ("CATALOGING, GOVERNANCE & SEARCH", "control plane, not a data path"),
    ("CONSUMPTION", "what reads the curated data"),
    ("SECURITY & MONITORING", "cross-cutting; no resource here carries a data path"),
)

_LEGEND_NOTES = (
    "A tile's colour is AWS's own service category, not this tool's. The dashed boxes are "
    "the layer a resource was classified into; the second line of a tile is the role that "
    "classification assigned, so the label and the placement cannot disagree.",
    "An arrow is a hop the plan DECLARES: one resource names the other's path in a "
    "data-carrying argument. Absence of an arrow means the plan states no path, not that "
    "none exists at runtime. A dashed arrow comes from the interview, not the plan.",
    "A badge under a name is capacity or posture the plan states -- worker count, shard "
    "count, encryption, private access. Nothing here is inferred from a resource name.",
)

_LEGEND_HEIGHT = 28 + len(_LEGEND_ROWS) * 22 + 6 + len(_LEGEND_NOTES) * 38

_LEGEND_TITLE_STYLE = ("text;html=1;align=left;verticalAlign=middle;fontSize=12;fontStyle=1;"
                       "fontColor=#1e293b;")
_LEGEND_SWATCH_STYLE = ("rounded=1;arcSize=20;fillColor=none;strokeColor=" + _BAND_STROKE
                        + ";strokeWidth=1;dashed=1;dashPattern=3 2;")
_LEGEND_LABEL_STYLE = ("text;html=1;align=left;verticalAlign=middle;fontSize=11;"
                       "fontColor=#334155;")
_LEGEND_NOTE_STYLE = ("text;html=1;align=left;verticalAlign=top;fontSize=10;"
                      "fontColor=#475569;whiteSpace=wrap;")


def _append_legend(root, x, y, width):
    """The swatch-to-meaning key. Returns the height it used.

    A colour that means something to whoever generated the canvas and nothing to whoever
    opens it is decoration. Two of the rows are about what an arrow's ABSENCE means, which
    is the reading this generator most often gets wrong.
    """
    def cell(cell_id, value, style, cx, cy, cw, ch):
        node = ET.SubElement(root, "mxCell", {
            "id": cell_id, "value": value, "style": style, "vertex": "1", "parent": "1"})
        ET.SubElement(node, "mxGeometry", {
            "x": str(cx), "y": str(cy), "width": str(cw), "height": str(ch),
            "as": "geometry"})

    cell("legend_title", "<b>Legend</b>", _LEGEND_TITLE_STYLE, x, y, width, 24)
    row_y = y + 28
    for index, (band, text) in enumerate(_LEGEND_ROWS):
        cell(f"legend_swatch_{index}", "", _LEGEND_SWATCH_STYLE, x, row_y + 3, 20, 14)
        cell(f"legend_label_{index}", f"<b>{band}</b> -- {text}", _LEGEND_LABEL_STYLE,
             x + 28, row_y, width - 28, 20)
        row_y += 22

    row_y += 6
    for index, note in enumerate(_LEGEND_NOTES):
        cell(f"legend_note_{index}", note, _LEGEND_NOTE_STYLE, x, row_y, width, 34)
        row_y += 38
    return row_y - y


def _append_walkthrough(root, steps, x, y, width):
    for index, text in enumerate(steps, 1):
        cell = ET.SubElement(root, "mxCell", {
            "id": f"legend_step_{index}",
            "value": f"<b>{index}.</b> {text}" if len(steps) > 1 or "declares no" not in text
                     else text,
            "style": _STEP_STYLE, "vertex": "1", "parent": "1",
        })
        ET.SubElement(cell, "mxGeometry", {
            "x": str(x), "y": str(y + (index - 1) * _STEP_PITCH), "width": str(width),
            "height": str(_STEP_HEIGHT), "as": "geometry"})


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


_REGIONAL_STROKE = "#7AA116"
_DEPLOYMENT_NOTE = (
    "This page states PLACEMENT, not flow. A resource is inside a subnet because the plan "
    "names that subnet in its own arguments; a resource drawn in the regional band sits "
    "inside the account and outside the VPC, which is where S3, KMS and Athena actually are. "
    "There are no arrows here on purpose -- declared data movement is on the Logical page, "
    "and drawing it twice invites the two to disagree."
)


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

    # Regional services wrap across the full width below the VPC rather than stacking in a
    # narrow column beside it. Three per row put forty services fourteen rows deep against an
    # empty half-canvas: a region is not a side column.
    # A roughly square block. `int(sqrt) + 1` over-estimates by one column on a perfect
    # square, which costs a column of whitespace and saves importing math for a heuristic.
    per_row = max(int(len(regional) ** 0.5) + 1 if regional else 1,
                  vpc_w // _SLOT_WIDTH if containment["vpc"] else 1, 3)
    inner_w = max(vpc_w, per_row * _SLOT_WIDTH + 40)
    regional_rows = (len(regional) + per_row - 1) // per_row if regional else 0
    regional_h = (_LABEL_STRIP + regional_rows * _STACK_HEIGHT + _BAND_PAD) if regional else 0

    cloud_w = inner_w + 60
    cloud_h = (_LABEL_STRIP + (vpc_h if containment["vpc"] else 0)
               + (_GUTTER if containment["vpc"] and regional else 0) + regional_h + 40)

    cloud = _container(root, "1", "layer_box_cloud", "<b>AWS Cloud</b>",
                       40, 40, cloud_w, cloud_h, _CLOUD_STROKE)

    def node(parent, address, x, y, index):
        res = by_address.get(address, {})
        cell = ET.SubElement(root, "mxCell", {
            "id": f"dep_node_{index}",
            "value": node_label(address, _badge_text(extract_node_metadata(res),
                                                     badges.get(address, set())),
                                _role_of(res)),
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

    if regional:
        top = _LABEL_STRIP + (vpc_h + _GUTTER if containment["vpc"] else 0)
        band = _container(root, cloud, "layer_box_regional",
                          "<b>REGIONAL</b> -- in the account, outside the VPC",
                          30, top, inner_w, regional_h, _REGIONAL_STROKE, dashed=1)
        for position, address in enumerate(regional):
            node(band, address, 20 + (position % per_row) * _SLOT_WIDTH,
                 _LABEL_STRIP + (position // per_row) * _STACK_HEIGHT, index)
            index += 1

    note_top = 40 + cloud_h + _GUTTER
    note = ET.SubElement(root, "mxCell", {
        "id": "legend_deployment_note", "value": _DEPLOYMENT_NOTE,
        "style": _LEGEND_NOTE_STYLE, "vertex": "1", "parent": "1"})
    ET.SubElement(note, "mxGeometry", {
        "x": "40", "y": str(note_top), "width": str(cloud_w), "height": "70",
        "as": "geometry"})

    return 40 + cloud_w + 40, note_top + 70 + _MARGIN_Y


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
    "processing": "PROCESSING",
    "storage": "STORAGE",
    "catalog": "CATALOGING, GOVERNANCE &amp; SEARCH",
    "consumption": "CONSUMPTION",
    "governance": "SECURITY &amp; MONITORING",
}


_NODE_SIZE = 68


def _reparent(x, y, bands):
    """Place a node inside the band that holds it, in that band's coordinates.

    The bands were decoration: nodes sat at absolute coordinates that happened to fall
    inside them, so dragging a band in draw.io left its contents behind. As real containers
    the band owns what it holds, and `diagram_check` can hold this page to the same
    containment rule it already holds the deployment page to.
    """
    for layer, bx, by, width, height in bands:
        if bx <= x and by <= y and x + _NODE_SIZE <= bx + width                 and y + _NODE_SIZE <= by + height:
            return f"layer_box_{layer}", x - bx, y - by
    return "1", x, y


_BOUNDARY_STYLE = ("rounded=1;arcSize=2;fillColor=none;strokeColor=#232F3E;strokeWidth=2;"
                   "verticalAlign=top;align=left;spacingLeft=14;spacingTop=8;fontSize=13;"
                   "fontStyle=1;fontColor=#232F3E;container=1;collapsible=0;")

_ACTOR_STYLE = ("rounded=1;arcSize=8;whiteSpace=wrap;html=1;fillColor=#f1f5f9;"
                "strokeColor=#475569;strokeWidth=2;dashed=1;dashPattern=6 4;fontSize=11;"
                "fontColor=#1e293b;verticalAlign=middle;")


def _append_boundary(root, boundary):
    """The account boundary, drawn only when there is something inside it."""
    if not boundary:
        return "1"
    x, y, width, height = boundary
    cell = ET.SubElement(root, "mxCell", {
        "id": "layer_box_account", "value": "<b>AWS Cloud</b>",
        "style": _BOUNDARY_STYLE, "vertex": "1", "parent": "1"})
    ET.SubElement(cell, "mxGeometry", {
        "x": str(x), "y": str(y), "width": str(width), "height": str(height),
        "as": "geometry"})
    return "layer_box_account"


def _append_actors(root, actors, positions, boundary):
    """Senders that live outside the account, drawn outside the boundary.

    Their shape is deliberately not an AWS stencil: nothing here is an AWS service, and
    giving a partner's SFTP client a service icon would say the account provisions it.
    """
    for actor in actors:
        x, y = positions[f"actor::{actor['key']}"]
        cell_id = f"actor_{actor['key']}"
        cell = ET.SubElement(root, "mxCell", {
            "id": cell_id,
            "value": f"<b>{actor['label']}</b><br/><font style='font-size:9px'>"
                     f"{actor['detail']}</font>",
            "tooltip": f"external: {actor['choice']}",
            "style": _ACTOR_STYLE, "vertex": "1", "parent": "1"})
        ET.SubElement(cell, "mxGeometry", {
            "x": str(x), "y": str(y), "width": str(_SLOT_WIDTH - 40), "height": "68",
            "as": "geometry"})
        yield f"actor::{actor['key']}", cell_id


def _append_bands(root, bands, parent="1", boundary=None):
    offset_x, offset_y = (boundary[0], boundary[1]) if (boundary and parent != "1") else (0, 0)
    for layer, x, y, width, height in bands:
        x, y = x - offset_x, y - offset_y
        container = ET.SubElement(root, "mxCell", {
            "id": f"layer_box_{layer}",
            "value": f"<b>{_BAND_LABELS.get(layer, layer.upper())}</b>",
            "style": ("swimlane;startSize=28;fillColor=none;strokeColor=#94a3b8;"
                      "strokeWidth=1;fontColor=#475569;fontStyle=1;fontSize=11;"
                      "rounded=1;arcSize=8;dashed=1;dashPattern=6 4;container=1;"
                      "collapsible=0;"),
            "vertex": "1",
            "parent": parent,
        })
        ET.SubElement(container, "mxGeometry", {
            "x": str(x), "y": str(y), "width": str(width), "height": str(height),
            "as": "geometry",
        })


def generate_drawio_from_plan(plan_json, title="Architecture Blueprint", requirements=None):
    """Render one plan as draw.io XML, a 1-click URL, and the declared-hop ledger.

    `requirements` is the interview record. It contributes only what a plan cannot state --
    who sends data in from outside the account -- and nothing inside the boundary is drawn
    from it.
    """
    resources = [r for r in (plan_json.get("resource_changes", []) if plan_json else [])
                 if r.get("mode") == "managed" and not is_folded(r.get("type", ""))]

    actors = external_actors(requirements)
    positions, bands, boundary = layout_positions(resources, actors)
    badges = fold_badges(plan_json, [r.get("address") for r in resources])

    edges = discover_data_edges(plan_json)
    steps = walkthrough_steps(edges, {r.get("address"): r for r in resources})
    bands_bottom = max([y + h for _, _, y, _, h in bands],
                       default=_MARGIN_Y + _band_height(1))
    walkthrough_top = bands_bottom + _GUTTER
    walkthrough_bottom = walkthrough_top + max(len(steps) - 1, 0) * _STEP_PITCH + _STEP_HEIGHT
    legend_top = walkthrough_bottom + _GUTTER
    legend_bottom = legend_top + _LEGEND_HEIGHT

    max_x = max([x for x, _ in positions.values()], default=800)
    max_y = max([y for _, y in positions.values()], default=600)
    model, root = _create_mxgraph_xml(max(1800, max_x + 350),
                                      max(1000, max_y + 250, legend_bottom + _MARGIN_Y))

    boundary_id = _append_boundary(root, boundary)
    _append_bands(root, bands, boundary_id, boundary)
    node_map = dict(_append_actors(root, actors, positions, boundary))
    for index, res in enumerate(resources):
        address = res.get("address")
        x, y = positions[address]
        node_id = f"node_{index}"
        node_map[address] = node_id
        parent, x, y = _reparent(x, y, bands)

        cell = ET.SubElement(root, "mxCell", {
            "id": node_id,
            "value": node_label(address, _badge_text(extract_node_metadata(res),
                                                     badges.get(address, set())),
                                _role_of(res)),
            "tooltip": address,
            "style": resolve_stencil(res.get("type")) + _LABEL_STYLE,
            "vertex": "1",
            "parent": parent,
        })
        ET.SubElement(cell, "mxGeometry", {
            "x": str(x), "y": str(y), "width": "68", "height": "68", "as": "geometry",
        })

    ingestion_addresses = [r.get("address") for r in resources
                           if _layer_of(r) == "ingestion"]
    for actor in actors:
        if len(ingestion_addresses) != 1:
            continue
        cell = ET.SubElement(root, "mxCell", {
            "id": f"edge_actor_{actor['key']}",
            "value": actor["detail"],
            "style": _EDGE_STYLE + "dashed=1;dashPattern=6 4;"
                     + _edge_anchors(positions[f"actor::{actor['key']}"],
                                     positions[ingestion_addresses[0]]),
            "edge": "1", "parent": "1",
            "source": node_map[f"actor::{actor['key']}"],
            "target": f"node_{[r.get('address') for r in resources].index(ingestion_addresses[0])}",
        })
        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})

    for index, edge in enumerate(edges):
        source_id = node_map.get(edge["source"])
        target_id = node_map.get(edge["target"])
        if not source_id or not target_id:
            continue
        cell = ET.SubElement(root, "mxCell", {
            "id": f"edge_{index}",
            "value": f"[{edge['hop']}]",
            "style": _EDGE_STYLE + _edge_anchors(positions.get(edge["source"]),
                                                 positions.get(edge["target"])),
            "edge": "1",
            "parent": "1",
            "source": source_id,
            "target": target_id,
        })
        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})

    canvas_width = max(max_x - _ORIGIN_X + 200, 600)
    _append_walkthrough(root, steps, _ORIGIN_X - 20, walkthrough_top, canvas_width)
    _append_legend(root, _ORIGIN_X - 20, legend_top, canvas_width)

    logical = ET.tostring(model, encoding="utf-8", xml_declaration=False).decode("utf-8")

    # The deployment page is emitted only when the stack actually provisions a VPC. An empty
    # VPC container is a picture of infrastructure that does not exist, and the same doctrine
    # lineage_graph.py applies to its own nodes.
    containment = deployment_containment(plan_json)
    pages = [("Logical", logical)]
    if containment["vpc"]:
        model2, root2 = _create_mxgraph_xml(2200, 1400)
        page_width, page_height = _append_deployment(
            root2, containment, {r.get("address"): r for r in resources}, badges)
        model2.set("pageWidth", str(max(2200, int(page_width))))
        model2.set("pageHeight", str(max(1400, int(page_height))))
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
