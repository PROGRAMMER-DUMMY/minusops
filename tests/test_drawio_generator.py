"""
The canvas: what it draws, what it refuses to claim, and whether the URL actually opens.

Three groups, and the middle one is the reason this file is long. The URL tests decode the
way diagrams.net does -- atob, inflateRaw, decodeURIComponent -- because a Python round trip
that decodes what it encoded is self-consistent and proved nothing about the browser; two
real defects (the urlsafe alphabet, and a raw percent sign) passed a green suite. The edge
tests assert what is NOT drawn: a Terraform dependency is not data movement, and a
walkthrough step may not use a verb the plan does not support. The layout tests assert
geometry, because a band that grows through its neighbour breaks nothing a node-level
assertion can see.

Depends on: core/reporting/drawio_generator.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import itertools
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
    """The names are draw.io's, not ours. This once asserted `aws4.iam`, which the library
    has no shape for -- the test held the typo in place while every IAM role in every
    diagram rendered blank. tests/test_diagram_check.py checks the whole table against the
    published list; these are the spot checks."""
    assert "aws4.s3" in drawio_generator.resolve_stencil("aws_s3_bucket")
    assert "aws4.glue" in drawio_generator.resolve_stencil("aws_glue_job")
    assert "aws4.identity_and_access_management" in drawio_generator.resolve_stencil(
        "aws_iam_role")
    assert "aws4.glue_crawlers" in drawio_generator.resolve_stencil("aws_glue_crawler")


def test_a_partner_product_gets_a_frame_and_a_colour_not_a_shape_that_does_not_exist():
    """aws4 carries no Databricks or Snowflake stencil. Referencing one renders a blank tile
    with no error; the generic resource frame in the partner's colour is at least true."""
    for rtype, colour in (("snowflake_database", "#29B5E8"),
                          ("databricks_catalog", "#FF3621")):
        style = drawio_generator.resolve_stencil(rtype)
        assert "shape=mxgraph.aws4.resource;" in style
        assert colour in style


def test_resolve_stencil_has_no_azure_or_gcp_branch():
    """base.get_provider raises ValueError for anything but AWS. A stencil for a provider
    the product refuses to talk to is a claim of support that does not exist."""
    assert drawio_generator.resolve_stencil("azurerm_storage_account") == "shape=mxgraph.aws4.resource;"
    assert drawio_generator.resolve_stencil("google_bigquery_dataset") == "shape=mxgraph.aws4.resource;"


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
    
    # An mxfile wrapping one page per view. A stack with no VPC gets the logical page alone.
    root = ET.fromstring(result["xml"])
    assert root.tag == "mxfile"
    assert [d.get("name") for d in root.findall("diagram")] == ["Logical"]
    assert root.find("diagram/mxGraphModel") is not None

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


# --- The URL has to actually open -------------------------------------------------------

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


# --- The ledger describes THIS plan -----------------------------------------------------

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


def _wired_plan():
    """A plan whose Glue job states the paths it reads from and writes to."""
    def rc(address, rtype, after):
        return {"address": address, "type": rtype, "mode": "managed",
                "name": address.split(".")[-1],
                "change": {"actions": ["create"], "after": after}}
    return {"resource_changes": [
        rc('aws_s3_bucket.zones["raw"]', "aws_s3_bucket", {"bucket": "lake-raw"}),
        rc('aws_s3_bucket.zones["curated"]', "aws_s3_bucket", {"bucket": "lake-curated"}),
        rc("aws_glue_job.etl", "aws_glue_job", {
            "default_arguments": {"--source_path": "s3://lake-raw/data/",
                                  "--target_path": "s3://lake-curated/data/"},
            "worker_type": "G.1X", "number_of_workers": 4}),
    ]}


def test_the_ledger_states_only_what_the_plan_declares():
    """The protocol, latency and safeguard columns were produced by matching a substring
    against the TARGET address, so a KMS key referenced by an Athena workgroup was reported
    as carrying JDBC/SQL at seconds-per-query. A diagram that invents a transport claim is
    the same defect as a report that invents a price."""
    ledger = drawio_generator.generate_flow_ledger(
        drawio_generator.discover_data_edges(_wired_plan()))

    assert ledger
    for row in ledger:
        assert set(row) == {"hop", "source", "target"}


def test_the_markdown_ledger_export_exists_and_is_a_table():
    """FR-06.2 asks for a Markdown export for PR comments; only a list of dicts existed."""
    markdown = drawio_generator.generate_flow_ledger_markdown(
        drawio_generator.discover_data_edges(_wired_plan()))

    assert markdown.startswith("|")
    assert "| :--- |" in markdown
    assert markdown.count("\n") >= 3


def test_the_markdown_ledger_says_so_rather_than_emitting_an_empty_table():
    markdown = drawio_generator.generate_flow_ledger_markdown([])

    assert "no data flow" in markdown.lower()


# --- No format may be a stub ------------------------------------------------------------

def test_the_bundle_does_not_ship_a_mock_svg():
    """A literal "<svg></svg>" was returned as a deliverable and offered as --format svg.
    An empty document presented as a diagram is worse than an absent one."""
    bundle = drawio_generator.generate_drawio_from_plan(_two_hop_plan(), title="t")

    assert bundle.get("svg") != "<svg></svg>"


# --- Three diverse fixtures -------------------------------------------------------------

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
    """Absolute canvas positions, keyed by Terraform address.

    Bands are real containers, so a node's own geometry is relative to the band that holds
    it. Every caller here compares positions ACROSS bands, which means the band origin has
    to be added back or a transform in one band and a bucket in another are being compared
    in two different coordinate systems.
    """
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_text)
    origins = {"1": (0.0, 0.0)}
    for cell in root.iter("mxCell"):
        cell_id = cell.get("id") or ""
        if cell_id.startswith("layer_box_"):
            geom = cell.find("mxGeometry")
            origins[cell_id] = (float(geom.get("x")), float(geom.get("y")))

    positions = {}
    for cell in root.iter("mxCell"):
        # Band boxes and walkthrough steps are vertices too; both are captions.
        cell_id = cell.get("id") or ""
        if cell.get("vertex") != "1" or cell_id.startswith(("layer_", "legend_")):
            continue
        geom = cell.find("mxGeometry")
        offset_x, offset_y = origins.get(cell.get("parent") or "1", (0.0, 0.0))
        # Keyed by the full address, which lives on the tooltip -- the visible label is
        # deliberately shortened to the last two segments.
        positions[cell.get("tooltip")] = (float(geom.get("x")) + offset_x,
                                          float(geom.get("y")) + offset_y)
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


def test_the_medallion_zones_run_left_to_right_in_stage_order():
    """architecture_model.stage_rank has ranked landing/raw/bronze/clean/silver/curated/gold
    since the model was written; the layout never called it, so the zones stacked in plan
    order and the spine the reference architecture is built around was invisible."""
    positions = _node_positions(
        drawio_generator.generate_drawio_from_plan(_layered_plan())["xml"])

    bronze = positions["module.storage.aws_s3_bucket.bronze"]
    gold = positions["module.storage.aws_s3_bucket.gold"]

    assert bronze[0] < gold[0], "gold is not downstream of bronze"
    assert bronze[1] == gold[1], "the zones are not on one horizontal spine"


def test_a_transform_sits_between_the_zones_it_moves_data_across():
    positions = _node_positions(
        drawio_generator.generate_drawio_from_plan(_layered_plan())["xml"])

    bronze = positions["module.storage.aws_s3_bucket.bronze"]
    gold = positions["module.storage.aws_s3_bucket.gold"]
    etl = positions["module.compute.aws_glue_job.etl"]

    assert bronze[0] < etl[0] < gold[0], "the transform is not on the spine"


def test_consumption_is_downstream_of_every_storage_zone():
    positions = _node_positions(
        drawio_generator.generate_drawio_from_plan(_layered_plan())["xml"])

    workgroup = positions["module.query.aws_athena_workgroup.analysts"]
    zones = [positions["module.storage.aws_s3_bucket.bronze"],
             positions["module.storage.aws_s3_bucket.gold"]]

    assert all(zone[0] < workgroup[0] for zone in zones)


def _band_geometry(xml_text, full=False):
    bands = {}
    for cell in ET.fromstring(xml_text).iter("mxCell"):
        cell_id = cell.get("id") or ""
        if not cell_id.startswith("layer_box_"):
            continue
        geom = cell.find("mxGeometry")
        box = tuple(float(geom.get(k)) for k in ("x", "y", "width", "height"))
        bands[cell_id[len("layer_box_"):]] = box if full else box[:3]
    return bands


def _banded_plan():
    def rc(address, rtype):
        return {"address": address, "type": rtype, "mode": "managed",
                "name": address.split(".")[-1],
                "change": {"actions": ["create"], "after": {}}}
    return {"resource_changes": [
        rc('aws_s3_bucket.zones["raw"]', "aws_s3_bucket"),
        rc('aws_s3_bucket.zones["curated"]', "aws_s3_bucket"),
        rc("aws_glue_job.etl", "aws_glue_job"),
        rc("aws_glue_catalog_database.gold", "aws_glue_catalog_database"),
        rc("aws_athena_workgroup.analysts", "aws_athena_workgroup"),
        rc("aws_kms_key.lake", "aws_kms_key"),
        rc("aws_cloudwatch_metric_alarm.failures", "aws_cloudwatch_metric_alarm"),
    ]}


def test_cataloging_sits_above_the_spine_and_security_below_it():
    """The reference architecture gives three spatial roles, not six equal columns: flow
    left to right, cataloging drawn above arrowing down, security a full-width band at the
    bottom. Six columns put IAM and KMS beside the consumption tier competing for the same
    visual weight."""
    bands = _band_geometry(drawio_generator.generate_drawio_from_plan(_banded_plan())["xml"])

    assert bands["catalog"][1] < bands["storage"][1], "cataloging is not above the spine"
    assert bands["governance"][1] > bands["storage"][1], "security is not below the spine"


def _crowded_plan():
    """More catalog and consumption resources than fit on one row of each band."""
    def rc(index, rtype, prefix):
        address = f"{rtype}.{prefix}{index}"
        return {"address": address, "type": rtype, "mode": "managed", "name": f"{prefix}{index}",
                "change": {"actions": ["create"], "after": {}}}
    changes = [rc(0, "aws_s3_bucket", "zone"), rc(0, "aws_glue_job", "etl")]
    changes += [rc(i, "aws_glue_catalog_database", "cat") for i in range(9)]
    changes += [rc(i, "aws_athena_workgroup", "wg") for i in range(4)]
    changes += [rc(i, "aws_iam_role", "role") for i in range(12)]
    return {"resource_changes": changes}


def test_no_two_bands_overlap():
    """The band offsets were fixed constants, so a catalog layer that wrapped to a second
    row grew straight through the spine below it -- 110px of overlap on a real run, with
    every node-level assertion still passing because the NODES cleared each other."""
    bands = _band_geometry(
        drawio_generator.generate_drawio_from_plan(_crowded_plan())["xml"], full=True)

    for (left, a), (right, b) in itertools.combinations(bands.items(), 2):
        horizontal = a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
        vertical = a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
        assert not (horizontal and vertical), f"{left} overlaps {right}: {a} {b}"


def test_every_node_sits_inside_a_band():
    xml_text = drawio_generator.generate_drawio_from_plan(_crowded_plan())["xml"]
    bands = _band_geometry(xml_text, full=True).values()

    for address, (x, y) in _node_positions(xml_text).items():
        assert any(bx <= x and by <= y and x + 68 <= bx + bw and y + 68 <= by + bh
                   for bx, by, bw, bh in bands), f"{address} escaped every band"


def _medallion_plan():
    def rc(address, rtype):
        return {"address": address, "type": rtype, "mode": "managed",
                "name": address.split(".")[-1],
                "change": {"actions": ["create"], "after": {}}}
    return {"resource_changes": [
        rc('aws_s3_bucket.zones["raw"]', "aws_s3_bucket"),
        rc('aws_s3_bucket.zones["cleaned"]', "aws_s3_bucket"),
        rc('aws_s3_bucket.zones["curated"]', "aws_s3_bucket"),
        rc("aws_s3_bucket.athena_results", "aws_s3_bucket"),
        rc("aws_glue_job.raw_to_cleaned", "aws_glue_job"),
        rc("aws_glue_job.cleaned_to_curated", "aws_glue_job"),
    ]}


def test_processing_sits_above_storage_rather_than_beside_it():
    """The spine used to alternate zone, transform, zone along one row, which reads as a line
    of tiles. Processing above storage is what makes the hops zigzag, and a zigzag is what
    tells a reader which direction the data went."""
    bands = _band_geometry(
        drawio_generator.generate_drawio_from_plan(_medallion_plan())["xml"], full=True)

    assert bands["processing"][1] + bands["processing"][3] <= bands["storage"][1]


def test_a_transform_is_offset_to_land_between_the_zones_it_moves_data_between():
    positions = _node_positions(
        drawio_generator.generate_drawio_from_plan(_medallion_plan())["xml"])

    raw = positions['aws_s3_bucket.zones["raw"]'][0]
    cleaned = positions['aws_s3_bucket.zones["cleaned"]'][0]
    first_job = positions["aws_glue_job.raw_to_cleaned"][0]

    assert raw < first_job < cleaned


def test_the_medallion_zones_run_in_stage_order():
    positions = _node_positions(
        drawio_generator.generate_drawio_from_plan(_medallion_plan())["xml"])

    ordered = [positions[f'aws_s3_bucket.zones["{stage}"]'][0]
               for stage in ("raw", "cleaned", "curated")]
    assert ordered == sorted(ordered)


def test_a_bucket_with_no_medallion_stage_does_not_ride_the_spine():
    """`classify_role` puts every bucket in the storage layer and `stage_rank` returns 40 for
    a name it does not know, so a results bucket sorted to the end and drew as the stage
    after Gold. It is storage; it is not a medallion zone."""
    positions = _node_positions(
        drawio_generator.generate_drawio_from_plan(_medallion_plan())["xml"])

    spine_row = positions['aws_s3_bucket.zones["curated"]'][1]
    assert positions["aws_s3_bucket.athena_results"][1] > spine_row


def test_the_canvas_carries_a_legend_that_says_what_an_absent_arrow_means():
    """A colour that means something to whoever generated the canvas and nothing to whoever
    opens it is decoration. The reading this generator most often loses is that a missing
    arrow means the plan declares no path, not that no path exists."""
    xml_text = drawio_generator.generate_drawio_from_plan(_medallion_plan())["xml"]
    legend = [cell.get("value") or "" for cell in ET.fromstring(xml_text).iter("mxCell")
              if (cell.get("id") or "").startswith("legend_")]
    joined = " ".join(legend)

    assert "Legend" in joined
    assert "DECLARES" in joined
    assert "not that none exists" in joined


def test_every_legend_swatch_uses_a_colour_a_band_actually_draws_with():
    """A key whose swatch does not match the band it explains is worse than no key."""
    xml_text = drawio_generator.generate_drawio_from_plan(_medallion_plan())["xml"]
    swatches = [cell.get("style") or "" for cell in ET.fromstring(xml_text).iter("mxCell")
                if (cell.get("id") or "").startswith("legend_swatch_")]

    assert swatches
    for style in swatches:
        assert any(colour in style for colour in drawio_generator._LAYER_COLORS.values())


def test_a_hop_between_two_rows_leaves_the_bottom_and_enters_the_top():
    """Unanchored, draw.io leaves sideways before turning, which routes the arrow straight
    through the boxes either side of it."""
    assert drawio_generator._edge_anchors((100, 100), (100, 400)) == (
        "exitX=0.5;exitY=1;entryX=0.5;entryY=0;")
    assert drawio_generator._edge_anchors((100, 400), (100, 100)) == (
        "exitX=0.5;exitY=0;entryX=0.5;entryY=1;")
    assert drawio_generator._edge_anchors((100, 100), (500, 120)) == (
        "exitX=1;exitY=0.5;entryX=0;entryY=0.5;")


def test_a_band_owns_the_nodes_it_holds_rather_than_merely_covering_them():
    """The bands were decoration: nodes sat at absolute coordinates that happened to fall
    inside them, so dragging a band in draw.io left its contents behind and dropping a node
    on one did not move it in. As real containers the geometry says what the picture says."""
    root = ET.fromstring(drawio_generator.generate_drawio_from_plan(_medallion_plan())["xml"])
    parents = {cell.get("tooltip"): cell.get("parent") for cell in root.iter("mxCell")
               if cell.get("vertex") == "1" and cell.get("tooltip")}
    bands = {cell.get("id") for cell in root.iter("mxCell")
             if (cell.get("id") or "").startswith("layer_box_")}

    assert parents
    assert all(parent in bands for parent in parents.values()), parents
    assert parents["aws_glue_job.raw_to_cleaned"] == "layer_box_processing"
    assert parents['aws_s3_bucket.zones["raw"]'] == "layer_box_storage"


def test_a_band_is_a_container_so_draw_io_moves_what_is_inside_it():
    root = ET.fromstring(drawio_generator.generate_drawio_from_plan(_medallion_plan())["xml"])
    for cell in root.iter("mxCell"):
        if (cell.get("id") or "").startswith("layer_box_"):
            assert "container=1" in (cell.get("style") or "")


def test_the_security_band_spans_the_whole_diagram():
    bands = _band_geometry(drawio_generator.generate_drawio_from_plan(_banded_plan())["xml"])

    assert bands["governance"][2] >= bands["storage"][2]


def test_nothing_in_the_security_band_carries_an_edge():
    """In the reference the security layer is a legend, not a participant in the flow. It
    had twenty edges crossing back into storage, which is what made the canvas unreadable."""
    bundle = drawio_generator.generate_drawio_from_plan(_banded_plan())
    xml_text = bundle["xml"]

    governance = {c.get("id") for c in ET.fromstring(xml_text).iter("mxCell")
                  if c.get("tooltip") in ("aws_kms_key.lake",
                                          "aws_cloudwatch_metric_alarm.failures")}
    endpoints = set()
    for cell in ET.fromstring(xml_text).iter("mxCell"):
        if cell.get("edge") == "1":
            endpoints |= {cell.get("source"), cell.get("target")}

    assert governance and not (governance & endpoints)


def test_node_labels_are_short_enough_not_to_run_into_the_next_column():
    """A node is 80px wide and a full address is ~45 characters. Rendered centred on the
    shape with no wrapping, every label ran straight through its neighbours -- five columns
    of overlapping text. The full address stays reachable as the tooltip."""
    xml_text = drawio_generator.generate_drawio_from_plan(_layered_plan())["xml"]

    import xml.etree.ElementTree as ET
    nodes = [c for c in ET.fromstring(xml_text).iter("mxCell")
             if c.get("vertex") == "1"
             and not (c.get("id") or "").startswith(("layer_", "legend_"))]

    assert nodes
    for cell in nodes:
        assert cell.get("tooltip", "").startswith("module."), "full address was dropped"
        assert "module." not in cell.get("value", ""), cell.get("value")
        assert "whiteSpace=wrap" in cell.get("style", "")
        assert "verticalLabelPosition=bottom" in cell.get("style", "")


# --- Edges are data movement, never a Terraform dependency ------------------------------
#
# Edges used to come from `configuration` references, which record what Terraform must
# create first -- not what carries data. A KMS key is referenced by every encrypted bucket
# and every workgroup, so the medallion spine was buried under fifty confident arrows that
# no byte ever travels.

def test_data_edges_come_from_the_paths_the_job_declares():
    edges = drawio_generator.discover_data_edges(_wired_plan())

    pairs = {(e["source"], e["target"]) for e in edges}
    assert ('aws_s3_bucket.zones["raw"]', "aws_glue_job.etl") in pairs, pairs
    assert ("aws_glue_job.etl", 'aws_s3_bucket.zones["curated"]') in pairs, pairs
    assert len(edges) == 2, f"invented an edge no argument declares: {pairs}"


def test_a_dependency_reference_is_not_drawn_as_a_data_flow():
    """The KMS key that encrypts the results bucket does not send data to the workgroup."""
    plan = {"resource_changes": [
        {"address": "aws_kms_key.cmk", "type": "aws_kms_key", "mode": "managed",
         "name": "cmk", "change": {"actions": ["create"], "after": {}}},
        {"address": "aws_athena_workgroup.wg", "type": "aws_athena_workgroup",
         "mode": "managed", "name": "wg", "change": {"actions": ["create"], "after": {}}},
    ], "configuration": {"root_module": {"resources": [
        {"address": "aws_athena_workgroup.wg", "type": "aws_athena_workgroup", "name": "wg",
         "expressions": {"configuration": {"references": ["aws_kms_key.cmk"]}}},
    ]}}}

    assert drawio_generator.discover_data_edges(plan) == []


def test_a_plan_that_declares_no_paths_yields_no_edges_and_says_so():
    """The Glue jobs in a hand-authored stack often carry only --job-bookmark-option. That
    stack has no declared data flow, and reporting one would be a guess."""
    plan = {"resource_changes": [
        {"address": "aws_glue_job.etl", "type": "aws_glue_job", "mode": "managed",
         "name": "etl", "change": {"actions": ["create"], "after": {
             "default_arguments": {"--job-bookmark-option": "job-bookmark-enable"}}}},
    ]}
    bundle = drawio_generator.generate_drawio_from_plan(plan)

    assert bundle["ledger"] == []
    assert "no data flow" in bundle["ledger_markdown"].lower()


# --- A bucket is one node, not five -----------------------------------------------------

def _configured_bucket_plan():
    def rc(address, rtype, after=None):
        return {"address": address, "type": rtype, "mode": "managed",
                "name": address.split(".")[-1],
                "change": {"actions": ["create"], "after": after or {}}}
    return {
        "resource_changes": [
            rc("aws_s3_bucket.gold", "aws_s3_bucket", {"bucket": "lake-gold"}),
            rc("aws_s3_bucket_versioning.gold", "aws_s3_bucket_versioning"),
            rc("aws_s3_bucket_server_side_encryption_configuration.gold",
               "aws_s3_bucket_server_side_encryption_configuration"),
            rc("aws_kms_alias.lake", "aws_kms_alias"),
        ],
        "configuration": {"root_module": {"resources": [
            {"address": "aws_s3_bucket_versioning.gold", "type": "aws_s3_bucket_versioning",
             "name": "gold", "expressions": {"bucket": {"references": ["aws_s3_bucket.gold"]}}},
            {"address": "aws_s3_bucket_server_side_encryption_configuration.gold",
             "type": "aws_s3_bucket_server_side_encryption_configuration", "name": "gold",
             "expressions": {"bucket": {"references": ["aws_s3_bucket.gold"]}}},
        ]}},
    }


def test_bucket_configuration_resources_are_not_drawn_as_their_own_nodes():
    """Versioning, public-access-block, SSE and lifecycle are properties of a bucket. Drawn
    as peers they turn one medallion zone into five stacked cards."""
    positions = _node_positions(
        drawio_generator.generate_drawio_from_plan(_configured_bucket_plan())["xml"])

    assert set(positions) == {"aws_s3_bucket.gold"}


def test_a_folded_resource_becomes_a_badge_on_the_bucket_it_configures():
    """The badge has to come from the resource that carries the fact. Reading the bucket's
    own attributes would report an unencrypted bucket as encrypted whenever a sibling SSE
    resource existed for a different bucket."""
    xml_text = drawio_generator.generate_drawio_from_plan(_configured_bucket_plan())["xml"]

    node = [c for c in ET.fromstring(xml_text).iter("mxCell")
            if c.get("tooltip") == "aws_s3_bucket.gold"][0]

    assert "encrypted" in node.get("value")
    assert "versioned" in node.get("value")


def test_a_bucket_with_no_encryption_resource_gets_no_encrypted_badge():
    plan = {"resource_changes": [
        {"address": "aws_s3_bucket.plain", "type": "aws_s3_bucket", "mode": "managed",
         "name": "plain", "change": {"actions": ["create"], "after": {"bucket": "b"}}}]}
    xml_text = drawio_generator.generate_drawio_from_plan(plan)["xml"]

    node = [c for c in ET.fromstring(xml_text).iter("mxCell")
            if c.get("tooltip") == "aws_s3_bucket.plain"][0]

    assert "encrypted" not in node.get("value")


def test_capacity_badges_come_from_the_plan():
    xml_text = drawio_generator.generate_drawio_from_plan(_wired_plan())["xml"]

    node = [c for c in ET.fromstring(xml_text).iter("mxCell")
            if c.get("tooltip") == "aws_glue_job.etl"][0]

    assert "G.1X x4" in node.get("value")


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


# --- The deployment page: containment, not a flat row -----------------------------------
#
# Every cell was parented to "1". A plan with a VPC, four subnets, a NAT and an IGW drew all
# six as 68px icons in the security band, which is the one place none of them belong.

def _networked_plan():
    def rc(address, rtype, expressions=None):
        return {"address": address, "type": rtype, "mode": "managed",
                "name": address.split(".")[-1],
                "change": {"actions": ["create"], "after": {}}}, {
                "address": address, "type": rtype, "name": address.split(".")[-1],
                "expressions": expressions or {}}

    pairs = [
        rc("aws_vpc.main", "aws_vpc"),
        rc("aws_subnet.private[0]", "aws_subnet",
           {"vpc_id": {"references": ["aws_vpc.main"]}}),
        rc("aws_subnet.public[0]", "aws_subnet",
           {"vpc_id": {"references": ["aws_vpc.main"]}}),
        rc("aws_mwaa_environment.airflow", "aws_mwaa_environment",
           {"subnet_ids": {"references": ["aws_subnet.private[0]"]}}),
        rc("aws_nat_gateway.nat", "aws_nat_gateway",
           {"subnet_id": {"references": ["aws_subnet.public[0]"]}}),
        rc("aws_emr_cluster.spark", "aws_emr_cluster",
           {"subnet_ids": {"references": ["aws_subnet.private[0]", "aws_subnet.public[0]"]}}),
        rc("aws_s3_bucket.gold", "aws_s3_bucket"),
        rc("aws_kms_key.lake", "aws_kms_key"),
    ]
    return {"resource_changes": [a for a, _ in pairs],
            "configuration": {"root_module": {"resources": [b for _, b in pairs]}}}


def test_a_service_nests_into_the_subnet_it_names():
    containment = drawio_generator.deployment_containment(_networked_plan())

    assert containment["vpc"] == "aws_vpc.main"
    assert "aws_mwaa_environment.airflow" in containment["subnets"]["aws_subnet.private[0]"]
    assert "aws_nat_gateway.nat" in containment["subnets"]["aws_subnet.public[0]"]


def test_a_service_naming_two_subnets_is_not_drawn_inside_one_of_them():
    """It spans them. Placing it in the first would state a placement the plan does not."""
    containment = drawio_generator.deployment_containment(_networked_plan())

    assert "aws_emr_cluster.spark" in containment["spanning"]
    for members in containment["subnets"].values():
        assert "aws_emr_cluster.spark" not in members


def test_s3_and_kms_are_regional_rather_than_inside_the_vpc():
    """They are not in the VPC, and every reference diagram draws them outside it."""
    containment = drawio_generator.deployment_containment(_networked_plan())

    assert set(containment["regional"]) >= {"aws_s3_bucket.gold", "aws_kms_key.lake"}


def test_the_deployment_page_is_emitted_only_when_a_vpc_exists():
    with_vpc = drawio_generator.generate_drawio_from_plan(_networked_plan())
    without = drawio_generator.generate_drawio_from_plan(_layered_plan())

    assert with_vpc["pages"] == ["Logical", "Deployment"]
    assert without["pages"] == ["Logical"]


def test_the_deployment_page_nests_cells_instead_of_parenting_everything_to_one():
    xml_text = drawio_generator.generate_drawio_from_plan(_networked_plan())["xml"]
    deployment = ET.fromstring(xml_text).findall("diagram")[1]

    parents = {c.get("id"): c.get("parent") for c in deployment.iter("mxCell")}
    subnet_ids = [i for i in parents if (i or "").startswith("layer_box_subnet_")]

    assert parents["layer_box_vpc"] == "layer_box_cloud"
    assert subnet_ids and all(parents[s] == "layer_box_vpc" for s in subnet_ids)

    nested = [i for i, p in parents.items()
              if (i or "").startswith("dep_node_") and p in subnet_ids]
    assert nested, "no service was placed inside a subnet"


def test_parse_graph_reads_the_logical_page_only():
    """Reconciliation compares what the operator edited against what was generated, and the
    operator edits the logical page. Merging both would report every regional service twice."""
    bundle = drawio_generator.generate_drawio_from_plan(_networked_plan())

    addresses = list(drawio_generator.parse_graph(bundle["xml"])["nodes"].values())

    assert addresses.count("aws_s3_bucket.gold") == 1


# --- The walkthrough beneath the canvas -------------------------------------------------
#
# AWS numbers the groups in its reference diagrams so the arrows stay clean and a numbered
# note carries the sentence. The trap is that the sentence has to be DERIVED: "Glue validates
# and cleans the records" is a claim about behaviour we do not know, from a plan that states
# only which paths the job reads and writes.

def test_a_step_states_the_paths_rather_than_the_behaviour():
    steps = drawio_generator.walkthrough_steps(
        drawio_generator.discover_data_edges(_wired_plan()),
        {r["address"]: r for r in _wired_plan()["resource_changes"]})

    assert len(steps) == 2
    joined = " ".join(steps).lower()
    for invented in ("validate", "clean", "enrich", "transform the", "ensures"):
        assert invented not in joined, f"the walkthrough claims {invented!r}"


def test_a_step_carries_the_capacity_the_plan_states():
    steps = drawio_generator.walkthrough_steps(
        drawio_generator.discover_data_edges(_wired_plan()),
        {r["address"]: r for r in _wired_plan()["resource_changes"]})

    assert any("G.1X x4" in step for step in steps)


def test_a_plan_with_no_declared_flow_says_the_wiring_is_absent_not_the_diagram():
    steps = drawio_generator.walkthrough_steps([], {})

    assert len(steps) == 1
    assert "no data flow" in steps[0]
    assert "wiring is" in steps[0]


def test_the_steps_are_drawn_below_every_band():
    xml_text = drawio_generator.generate_drawio_from_plan(_banded_plan())["xml"]
    root = ET.fromstring(xml_text)

    bands = _band_geometry(xml_text, full=True)
    lowest = max(y + h for _, y, _, h in bands.values())
    steps = [c for c in root.iter("mxCell") if (c.get("id") or "").startswith("legend_step_")]

    assert steps
    for cell in steps:
        assert float(cell.find("mxGeometry").get("y")) >= lowest


def test_parse_graph_ignores_the_walkthrough_the_generator_draws():
    """A step box is a caption. Reporting one as a removed resource would put noise into an
    unbypassable review, exactly as a deleted band header would."""
    bundle = drawio_generator.generate_drawio_from_plan(_wired_plan())

    graph = drawio_generator.parse_graph(bundle["xml"])

    assert not any(k.startswith("legend_") for k in graph["nodes"])
