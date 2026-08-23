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
        
    allowed_imports = {"xml.etree.ElementTree", "zlib", "base64", "urllib.parse", "json", "re", "os", "sys"}
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
    return {"resource_changes": [
        {"address": "module.storage.aws_s3_bucket.bronze", "type": "aws_s3_bucket",
         "mode": "managed", "change": {"actions": ["create"], "after": {}}},
        {"address": "module.compute.aws_glue_job.etl", "type": "aws_glue_job",
         "mode": "managed", "change": {"actions": ["create"], "after": {}}},
        {"address": "module.query.aws_athena_workgroup.wg", "type": "aws_athena_workgroup",
         "mode": "managed", "change": {"actions": ["create"], "after": {}}},
    ]}


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
