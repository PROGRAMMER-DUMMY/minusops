import os
import sys
import xml.etree.ElementTree as ET
import zlib
import base64
import json
import urllib.parse
import pytest

from core.reporting import drawio_generator

def test_resolve_stencil():
    assert "aws4.s3" in drawio_generator.resolve_stencil("aws_s3_bucket")
    assert "aws4.glue" in drawio_generator.resolve_stencil("aws_glue_job")
    assert "aws4.iam" in drawio_generator.resolve_stencil("aws_iam_role")
    assert "azure2.storage" in drawio_generator.resolve_stencil("azurerm_storage_account")
    assert "gcp2.bigquery" in drawio_generator.resolve_stencil("google_bigquery_dataset")
    assert "aws4.snowflake" in drawio_generator.resolve_stencil("snowflake_database")

def test_discover_clusters():
    plan = {
        "resource_changes": [
            {"type": "aws_vpc", "address": "aws_vpc.main"},
            {"type": "aws_subnet", "address": "aws_subnet.pub"},
            {"type": "aws_s3_bucket", "address": "aws_s3_bucket.data"}
        ]
    }
    clusters = drawio_generator.discover_clusters(plan)
    assert len(clusters["vpc"]) == 1
    assert len(clusters["subnets"]) == 1
    assert len(clusters["storage"]) == 1

def test_extract_node_metadata():
    res = {
        "change": {
            "after": {
                "worker_type": "G.1X",
                "kms_key_id": "arn:aws:kms...",
                "publicly_accessible": False
            }
        }
    }
    meta = drawio_generator.extract_node_metadata(res)
    assert meta["worker_type"] == "G.1X"
    assert meta["encrypted"] is True
    assert meta["private"] is True

def test_encode_drawio_url():
    xml_str = "<mxGraphModel><root></root></mxGraphModel>"
    url = drawio_generator.encode_drawio_url(xml_str)
    assert url.startswith("https://app.diagrams.net/#R")
    
    # URL round-trip. This originally stopped at the inflate step and asserted the result
    # equalled the raw XML -- which only holds for an encoder that SKIPS encodeURIComponent,
    # and such a URL throws URIError in diagrams.net on any percent sign. AC-03 as literally
    # worded ("decompressing must yield the exact original XML") and FR-05 ("a 1-click URL
    # that opens") cannot both be satisfied; the inverse is completed here so AC-03's intent
    # -- round-trip fidelity -- still holds against the format diagrams.net actually reads.
    encoded_b64 = url.replace("https://app.diagrams.net/#R", "")
    padded = encoded_b64 + '=' * (-len(encoded_b64) % 4)
    compressed = base64.urlsafe_b64decode(padded)
    decompressed = zlib.decompress(compressed, -15).decode('utf-8')
    assert urllib.parse.unquote(decompressed) == xml_str      # decodeURIComponent

def test_generate_drawio_from_plan():
    plan = {
        "resource_changes": [
            {"mode": "managed", "type": "aws_s3_bucket", "address": "aws_s3_bucket.source"},
            {"mode": "managed", "type": "aws_glue_job", "address": "aws_glue_job.transform"}
        ]
    }
    result = drawio_generator.generate_drawio_from_plan(plan)
    
    assert "xml" in result
    assert "url" in result
    assert "ledger" in result
    
    # Check if we generated valid XML
    root = ET.fromstring(result["xml"])
    assert root.tag == "mxGraphModel"

def test_no_external_dependencies():
    import ast
    with open("core/reporting/drawio_generator.py", "r") as f:
        tree = ast.parse(f.read())
        
    # `architecture_model` is a first-party sibling, not a dependency: the invariant this
    # test guards is "no third-party packages", and it reaches only stdlib itself (json, os,
    # re, sys, plus plan_reader, which imports nothing). The generator uses it so resource
    # classification on the canvas cannot drift from the model that drives conformance.
    allowed_imports = {"xml.etree.ElementTree", "zlib", "base64", "urllib.parse", "json",
                       "re", "os", "sys", "architecture_model"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in allowed_imports, f"Disallowed import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            assert node.module in allowed_imports, f"Disallowed import: {node.module}"


# ---------------------------------------------------------------------------------------
# PRD v12 gap closure. The tests above verified the generator against ITSELF --
# `test_encode_drawio_url` decodes exactly what it encoded, which proves the round trip is
# self-consistent and proves nothing about whether diagrams.net can open the result.
# ---------------------------------------------------------------------------------------

import re
import urllib.parse

_BAD_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def _open_like_drawio(url):
    """What diagrams.net does with a #R payload: base64 -> inflateRaw -> decodeURIComponent.

    JavaScript decodeURIComponent throws URIError on a percent sign not followed by two hex
    digits. Python unquote silently leaves it alone, which is exactly why a Python-only
    round-trip test cannot see this class of failure.
    """
    payload = url.split("#R", 1)[1]
    payload += "=" * (-len(payload) % 4)
    inflated = zlib.decompress(base64.urlsafe_b64decode(payload), -15).decode("utf-8")
    if _BAD_ESCAPE.search(inflated):
        raise ValueError("URIError: URI malformed")
    return urllib.parse.unquote(inflated)


# --- FR-05: the URL has to actually open ------------------------------------------------

def test_the_url_opens_in_drawio_when_a_label_contains_a_percent_sign():
    """The defect this closes. Deflating raw XML skips encodeURIComponent, so any percent
    sign in the diagram -- a "99% uptime" label, a Terraform interpolation, an encoded
    bucket name -- makes diagrams.net throw URIError and render nothing. Plain diagrams
    happened to work, which is why it went unnoticed."""
    xml = '<mxGraphModel><root><mxCell value="99% uptime"/></root></mxGraphModel>'

    assert _open_like_drawio(drawio_generator.encode_drawio_url(xml)) == xml


def test_the_url_opens_for_non_ascii_labels():
    """encodeURIComponent is also what makes the payload ASCII-safe before deflate."""
    xml = '<mxGraphModel><root><mxCell value="caf\u00e9 M\u00fcnchen"/></root></mxGraphModel>'

    assert _open_like_drawio(drawio_generator.encode_drawio_url(xml)) == xml


def test_the_encoder_and_decoder_are_inverses():
    """AC-03 round-trip, expressed against the published decoder rather than our own."""
    xml = '<mxGraphModel dx="100"><root><mxCell id="1" value="a&amp;b 50%"/></root></mxGraphModel>'
    url = drawio_generator.encode_drawio_url(xml)

    assert drawio_generator.decode_drawio_url(url) == xml
    assert _open_like_drawio(url) == xml


# --- FR-06: the ledger describes THIS plan ----------------------------------------------

def _two_hop_plan():
    """Two hops the plan genuinely declares.

    This fixture used to carry `resource_changes` alone, and got its hops from the adjacency
    chaining that discover_flow_edges no longer does. The properties below -- per-resource
    protocol, a real markdown table -- are still the right things to assert; they just have
    to be asserted over edges something references.
    """
    return {
        "resource_changes": [
            {"address": "module.storage.aws_s3_bucket.bronze", "type": "aws_s3_bucket",
             "mode": "managed", "change": {"actions": ["create"], "after": {}}},
            {"address": "module.compute.aws_glue_job.etl", "type": "aws_glue_job",
             "mode": "managed", "change": {"actions": ["create"], "after": {}}},
            {"address": "module.query.aws_athena_workgroup.wg", "type": "aws_athena_workgroup",
             "mode": "managed", "change": {"actions": ["create"], "after": {}}},
        ],
        "configuration": {"root_module": {"module_calls": {
            "storage": {"expressions": {}},
            "compute": {"expressions": {
                "source_bucket_arn": {"references": ["module.storage.bronze_arn",
                                                     "module.storage"]}}},
            "query": {"expressions": {
                "database": {"references": ["module.compute.catalog", "module.compute"]}}},
        }}},
    }


def test_the_ledger_protocol_is_derived_from_the_resources_not_a_constant():
    """Invariant 2 (Zero Hardcoding). Every hop previously reported protocol HTTPS,
    latency 100ms, safeguards TLS 1.2+ regardless of what the hop actually was -- an S3
    read is not HTTPS with a 100ms budget, and a table of constants is decoration."""
    edges = drawio_generator.discover_flow_edges(_two_hop_plan())
    ledger = drawio_generator.generate_flow_ledger(edges)

    assert len(ledger) >= 2
    protocols = {row["protocol"] for row in ledger}
    assert len(protocols) > 1, f"every hop reported the same protocol: {protocols}"


def test_the_markdown_ledger_export_exists_and_is_a_table():
    """FR-06.2 asks for a Markdown export for PR comments; only a list of dicts existed."""
    edges = drawio_generator.discover_flow_edges(_two_hop_plan())

    markdown = drawio_generator.generate_flow_ledger_markdown(edges)

    assert markdown.startswith("|")
    assert "| :--- |" in markdown
    assert markdown.count("\n") >= 3


def test_the_markdown_ledger_says_so_rather_than_emitting_an_empty_table():
    markdown = drawio_generator.generate_flow_ledger_markdown([])

    assert "no flow" in markdown.lower()


# --- FR-07: no format may be a stub -----------------------------------------------------

def test_the_bundle_does_not_ship_a_mock_svg():
    """A literal "<svg></svg>" was returned as a deliverable and offered as --format svg.
    An empty document presented as a diagram is worse than an absent one."""
    bundle = drawio_generator.generate_drawio_from_plan(_two_hop_plan(), title="t")

    assert bundle.get("svg") != "<svg></svg>"


# --- AC-02: three diverse fixtures ------------------------------------------------------

LAKEHOUSE = _two_hop_plan()
INGESTION = {"resource_changes": [
    {"address": "module.ingest.aws_sfn_state_machine.flow", "type": "aws_sfn_state_machine",
     "mode": "managed", "change": {"actions": ["create"], "after": {}}},
    {"address": "module.ingest.aws_kinesis_stream.events", "type": "aws_kinesis_stream",
     "mode": "managed", "change": {"actions": ["create"], "after": {}}},
]}
REDSHIFT_BI = {"resource_changes": [
    {"address": "module.warehouse.aws_redshiftserverless_workgroup.bi",
     "type": "aws_redshiftserverless_workgroup", "mode": "managed",
     "change": {"actions": ["create"], "after": {}}},
    {"address": "module.warehouse.aws_kms_key.cmk", "type": "aws_kms_key",
     "mode": "managed", "change": {"actions": ["create"], "after": {}}},
]}


@pytest.mark.parametrize("name,plan", [
    ("lakehouse", LAKEHOUSE), ("ingestion", INGESTION), ("redshift_bi", REDSHIFT_BI)])
def test_no_node_title_is_hardcoded_across_three_diverse_plans(name, plan):
    """AC-02 claims this coverage; one inline fixture existed."""
    xml = drawio_generator.generate_drawio_from_plan(plan, title=name)["xml"]

    for change in plan["resource_changes"]:
        leaf = change["address"].rsplit(".", 1)[-1]
        assert leaf in xml, f"{leaf} missing from the {name} diagram"


@pytest.mark.parametrize("name,plan", [
    ("lakehouse", LAKEHOUSE), ("ingestion", INGESTION), ("redshift_bi", REDSHIFT_BI)])
def test_every_diagram_is_well_formed_xml(name, plan):
    """A diagram that does not parse cannot open, and string assertions never notice."""
    ET.fromstring(drawio_generator.generate_drawio_from_plan(plan, title=name)["xml"])


def test_the_payload_uses_the_alphabet_atob_can_actually_decode():
    """The bug the browser found and three green tests did not.

    diagrams.net decodes a #R payload with atob(), which accepts ONLY the standard base64
    alphabet. The encoder used urlsafe_b64encode, so any payload containing '-' or '_' --
    a real three-resource plan contains both -- died with "Failed to execute 'atob' on
    'Window'" and rendered nothing. Every test passed because they all decoded with
    urlsafe_b64decode: self-consistent, and self-consistently wrong.
    """
    import re
    plan = {"resource_changes": [
        {"address": "module.storage.aws_s3_bucket.bronze", "type": "aws_s3_bucket",
         "mode": "managed", "change": {"actions": ["create"], "after": {}}},
        {"address": "module.compute.aws_glue_job.etl", "type": "aws_glue_job",
         "mode": "managed", "change": {"actions": ["create"], "after": {}}},
        {"address": "module.query.aws_athena_workgroup.wg", "type": "aws_athena_workgroup",
         "mode": "managed", "change": {"actions": ["create"], "after": {}}}]}

    payload = drawio_generator.generate_drawio_from_plan(plan, title="t")["url"].split("#R", 1)[1]

    assert not re.search(r"[-_]", payload), "urlsafe base64 cannot be decoded by atob()"
    # And it must still be valid base64 in the standard alphabet.
    base64.b64decode(payload + "=" * (-len(payload) % 4))


def test_the_decoder_still_round_trips_with_the_standard_alphabet():
    xml = '<mxGraphModel><root><mxCell value="a b 50% c-d_e"/></root></mxGraphModel>'

    assert drawio_generator.decode_drawio_url(drawio_generator.encode_drawio_url(xml)) == xml


# --- The canvas has to be readable, which is a property of the layout -------------------

def _layered_plan():
    """Storage, processing and consumption resources -- three canonical layers."""
    def rc(address, rtype):
        return {"address": address, "type": rtype, "mode": "managed",
                "name": address.split(".")[-1],
                "change": {"actions": ["create"], "after": {}}}
    return {"resource_changes": [
        rc("module.storage.aws_s3_bucket.bronze", "aws_s3_bucket"),
        rc("module.storage.aws_s3_bucket.gold", "aws_s3_bucket"),
        rc("module.compute.aws_glue_job.etl", "aws_glue_job"),
        rc("module.query.aws_athena_workgroup.analysts", "aws_athena_workgroup"),
    ]}


def _node_positions(xml_text):
    import xml.etree.ElementTree as ET
    positions = {}
    for cell in ET.fromstring(xml_text).iter("mxCell"):
        # The column headers are vertices too; they are labels, not resources.
        if cell.get("vertex") != "1" or (cell.get("id") or "").startswith("layer_"):
            continue
        geom = cell.find("mxGeometry")
        # Keyed by the full address, which lives on the tooltip -- the visible label is
        # deliberately shortened to the last two segments.
        positions[cell.get("tooltip")] = (float(geom.get("x")), float(geom.get("y")))
    return positions


def test_the_canvas_lays_resources_out_by_layer_not_in_one_tall_column():
    """Every resource was placed at x=100 with y stepping 150, so a ten-resource stack was
    80px wide and 1500px tall. Fitted into a viewer that is a vertical thread: the labels
    are sub-pixel and the architecture is unreadable, which is the whole point of the view.
    """
    xml_text = drawio_generator.generate_drawio_from_plan(_layered_plan())["xml"]

    positions = _node_positions(xml_text)
    assert len(positions) == 4
    columns = {x for x, _ in positions.values()}
    assert len(columns) > 1, "every resource is still in one column"

    width = max(x for x, _ in positions.values()) - min(x for x, _ in positions.values())
    height = max(y for _, y in positions.values()) - min(y for _, y in positions.values())
    assert width > height, f"the diagram is still taller than it is wide ({width}x{height})"


def test_resources_in_the_same_layer_share_a_column():
    positions = _node_positions(
        drawio_generator.generate_drawio_from_plan(_layered_plan())["xml"])

    bronze = positions["module.storage.aws_s3_bucket.bronze"]
    gold = positions["module.storage.aws_s3_bucket.gold"]
    etl = positions["module.compute.aws_glue_job.etl"]

    assert bronze[0] == gold[0], "two storage buckets landed in different columns"
    assert bronze[1] != gold[1], "two storage buckets landed on top of each other"
    assert etl[0] != bronze[0], "processing and storage share a column"


def test_node_labels_are_short_enough_not_to_run_into_the_next_column():
    """A node is 80px wide and a full address is ~45 characters. Rendered centred on the
    shape with no wrapping, every label ran straight through its neighbours -- five columns
    of overlapping text. The full address stays reachable as the tooltip."""
    xml_text = drawio_generator.generate_drawio_from_plan(_layered_plan())["xml"]

    import xml.etree.ElementTree as ET
    nodes = [c for c in ET.fromstring(xml_text).iter("mxCell")
             if c.get("vertex") == "1" and not (c.get("id") or "").startswith("layer_")]

    assert nodes
    for cell in nodes:
        assert cell.get("tooltip", "").startswith("module."), "full address was dropped"
        assert "module." not in cell.get("value", ""), cell.get("value")
        assert "whiteSpace=wrap" in cell.get("style", "")
        assert "verticalLabelPosition=bottom" in cell.get("style", "")


# --- Flow edges must be traced, never assumed ------------------------------------------
#
# discover_flow_edges carried the comment "Simple mock extraction for now" and chained
# resources by their ADJACENCY IN THE PLAN FILE. Every consumer treated the result as a
# traced data flow: the canvas drew arrows, and generate_flow_ledger dressed each hop in a
# protocol, a latency budget and a list of safeguards. A gold bucket "flowing" into a KMS
# key is not a data flow, and presenting one on a governance surface is the exact claim this
# product exists to refuse.

def _plan_with_references():
    """A plan whose `configuration` records real module-to-module references."""
    def rc(address, rtype):
        return {"address": address, "type": rtype, "mode": "managed",
                "name": address.split(".")[-1],
                "change": {"actions": ["create"], "after": {}}}
    return {
        # DELIBERATELY not in flow order. Listed in flow order, plan-file adjacency and
        # the real references coincide, and a test asserting the real edges passes on the
        # mock -- proving nothing.
        "resource_changes": [
            rc("module.query.aws_athena_workgroup.wg", "aws_athena_workgroup"),
            rc("module.storage.aws_s3_bucket.bronze", "aws_s3_bucket"),
            rc("module.compute.aws_glue_job.etl", "aws_glue_job"),
        ],
        "configuration": {"root_module": {"module_calls": {
            "storage": {"expressions": {}},
            "compute": {"expressions": {
                "source_bucket_arn": {"references": ["module.storage.bronze_arn",
                                                     "module.storage"]}}},
            "query": {"expressions": {
                "database": {"references": ["module.compute.catalog", "module.compute"]}}},
        }}},
    }


def test_flow_edges_come_from_declared_references():
    edges = drawio_generator.discover_flow_edges(_plan_with_references())

    pairs = {(e["source"], e["target"]) for e in edges}
    assert ("module.storage.aws_s3_bucket.bronze",
            "module.compute.aws_glue_job.etl") in pairs, pairs
    assert ("module.compute.aws_glue_job.etl",
            "module.query.aws_athena_workgroup.wg") in pairs, pairs
    assert len(edges) == 2, f"invented an edge nothing references: {pairs}"


def test_a_plan_with_no_configuration_yields_no_edges_rather_than_a_guessed_chain():
    """Without `configuration` there is nothing to trace. Emitting a chain anyway is how a
    ten-resource plan grew nine confident hops that no reference supports."""
    plan = {"resource_changes": _plan_with_references()["resource_changes"]}

    assert drawio_generator.discover_flow_edges(plan) == []


def test_the_ledger_says_nothing_rather_than_describing_untraced_hops():
    plan = {"resource_changes": _plan_with_references()["resource_changes"]}
    bundle = drawio_generator.generate_drawio_from_plan(plan)

    assert bundle["ledger"] == []
    assert "no flow" in bundle["ledger_markdown"].lower()


# --- Reading a diagram back: the input side of FR-05.1 ----------------------------------

_EDITED = '''<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="layer_storage" value="STORAGE" style="text;" vertex="1" parent="1">
<mxGeometry x="60" y="30" width="240" height="24" as="geometry"/></mxCell>
<mxCell id="n1" value="bronze" tooltip="module.storage.aws_s3_bucket.bronze" vertex="1"
 parent="1"><mxGeometry x="60" y="90" width="80" height="80" as="geometry"/></mxCell>
<mxCell id="n2" value="etl" tooltip="module.compute.aws_glue_job.etl" vertex="1" parent="1">
<mxGeometry x="340" y="90" width="80" height="80" as="geometry"/></mxCell>
<mxCell id="e1" value="[1]" edge="1" parent="1" source="n1" target="n2">
<mxGeometry relative="1" as="geometry"/></mxCell></root></mxGraphModel>'''


def test_parse_graph_keys_nodes_by_their_terraform_address():
    """The tooltip carries the full address. Without it an edge the architect drags is a
    line between two anonymous box ids, and cannot be mapped back to infrastructure."""
    graph = drawio_generator.parse_graph(_EDITED)

    assert graph["nodes"]["n1"] == "module.storage.aws_s3_bucket.bronze"
    assert graph["edges"]["e1"] == {"source": "n1", "target": "n2"}


def test_parse_graph_ignores_the_layer_headers_the_generator_draws():
    """They are captions, not architecture. Deleting one is an edit to the picture, and
    reporting it as a removed resource puts noise in an unbypassable review."""
    graph = drawio_generator.parse_graph(_EDITED)

    assert "layer_storage" not in graph["nodes"]


def test_parse_graph_carries_no_geometry_so_moving_a_box_is_not_a_change():
    """Raising a review for a layout tidy-up would train an operator to click through the
    gate, which is the one thing this gate cannot survive."""
    moved = _EDITED.replace('x="60" y="90"', 'x="500" y="400"')

    assert drawio_generator.parse_graph(moved) == drawio_generator.parse_graph(_EDITED)


def test_parse_graph_survives_a_diagram_it_cannot_read():
    assert drawio_generator.parse_graph("not xml at all") == {"nodes": {}, "edges": {}}
