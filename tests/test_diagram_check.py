"""The diagram checker, and the defects it has to catch.

Every check gets a canvas that is broken in exactly one way. A checker asserted only against
correct input passes forever without ever having verified anything.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core", "reporting"))
sys.path.insert(0, os.path.join(ROOT, "core", "architecture"))

import diagram_check  # noqa: E402
import drawio_generator  # noqa: E402


def _canvas(cells, page_width=1800, page_height=1000):
    return (f'<mxfile host="app.diagrams.net"><diagram name="Logical">'
            f'<mxGraphModel pageWidth="{page_width}" pageHeight="{page_height}"><root>'
            f'<mxCell id="0" /><mxCell id="1" parent="0" />'
            f'{cells}</root></mxGraphModel></diagram></mxfile>')


def _node(cell_id, x, y, parent="1", width=68, height=68, style=""):
    return (f'<mxCell id="{cell_id}" value="{cell_id}" style="{style}" vertex="1" '
            f'parent="{parent}"><mxGeometry x="{x}" y="{y}" width="{width}" '
            f'height="{height}" as="geometry" /></mxCell>')


def _edge(cell_id, source, target, value="[1]"):
    ends = "".join(f' {name}="{ref}"' for name, ref in
                   (("source", source), ("target", target)) if ref is not None)
    return (f'<mxCell id="{cell_id}" value="{value}" edge="1" parent="1"{ends}>'
            f'<mxGeometry relative="1" as="geometry" /></mxCell>')


def _checks(result):
    return {f["check"] for f in result["findings"]}


# --- The defects ----------------------------------------------------------------------------

def test_an_edge_pointing_at_a_cell_that_does_not_exist_is_an_error():
    """The loose connection. draw.io drops the arrow and reports nothing, so the diagram
    opens looking complete with a hop silently missing."""
    result = diagram_check.check(_canvas(
        _node("node_0", 100, 100) + _edge("edge_0", "node_0", "node_9")))
    assert result["verdict"] == "FAIL"
    assert "edge_endpoint_unknown" in _checks(result)


def test_an_edge_with_no_endpoint_at_all_is_an_error():
    result = diagram_check.check(_canvas(
        _node("node_0", 100, 100) + _edge("edge_0", "node_0", None)))
    assert result["verdict"] == "FAIL"
    assert "edge_endpoint_missing" in _checks(result)


def test_a_resource_wired_to_itself_is_an_error():
    result = diagram_check.check(_canvas(
        _node("node_0", 100, 100) + _edge("edge_0", "node_0", "node_0")))
    assert result["verdict"] == "FAIL"
    assert "edge_self_loop" in _checks(result)


def test_an_unlabeled_edge_is_a_warning_not_a_failure():
    """An edge in this generator asserts declared data movement. Unlabeled it still points
    somewhere real, so it is wrong without being broken."""
    result = diagram_check.check(_canvas(
        _node("node_0", 100, 100) + _node("node_1", 400, 100)
        + _edge("edge_0", "node_0", "node_1", value="")))
    assert result["verdict"] == "WARN"
    assert "edge_unlabeled" in _checks(result)


def test_two_resources_drawn_on_top_of_each_other_is_an_error():
    result = diagram_check.check(_canvas(
        _node("node_0", 100, 100) + _node("node_1", 140, 100)))
    assert result["verdict"] == "FAIL"
    assert "sibling_overlap" in _checks(result)


def test_two_bands_drawn_through_each_other_is_an_error():
    """The band collision that shipped once: every node cleared every other node, and the
    bands containing them overlapped by 110px."""
    band = 'swimlane;startSize=28;'
    result = diagram_check.check(_canvas(
        _node("layer_box_storage", 40, 100, width=1200, height=200, style=band)
        + _node("layer_box_catalog", 40, 250, width=1200, height=200, style=band)))
    assert result["verdict"] == "FAIL"
    assert "sibling_overlap" in _checks(result)


def test_a_container_and_the_node_inside_it_are_not_reported_as_overlapping():
    """Containment is the point of the deployment page. Comparing a box with its own
    contents would report every correct nesting as a collision."""
    box = 'container=1;'
    result = diagram_check.check(_canvas(
        _node("layer_box_vpc", 40, 40, width=600, height=400, style=box)
        + _node("dep_node_0", 60, 60, parent="layer_box_vpc")))
    assert "sibling_overlap" not in _checks(result)
    assert result["verdict"] == "PASS"


def test_a_child_drawn_outside_its_container_is_an_error():
    """Child geometry is relative to the container. 700 inside a 600-wide VPC draws the
    resource outside the boundary that is supposed to contain it."""
    result = diagram_check.check(_canvas(
        _node("layer_box_vpc", 40, 40, width=600, height=400, style="container=1;")
        + _node("dep_node_0", 700, 60, parent="layer_box_vpc")))
    assert result["verdict"] == "FAIL"
    assert "child_escapes_parent" in _checks(result)


def test_a_cell_whose_parent_does_not_exist_is_an_error():
    result = diagram_check.check(_canvas(_node("node_0", 10, 10, parent="layer_box_ghost")))
    assert result["verdict"] == "FAIL"
    assert "parent_unknown" in _checks(result)


def test_a_duplicate_cell_id_is_an_error():
    result = diagram_check.check(_canvas(_node("node_0", 100, 100)
                                         + _node("node_0", 400, 100)))
    assert result["verdict"] == "FAIL"
    assert "duplicate_id" in _checks(result)


def test_a_cell_past_the_declared_page_is_a_warning():
    result = diagram_check.check(_canvas(_node("node_0", 100, 2400), page_height=1000))
    assert result["verdict"] == "WARN"
    assert "cell_off_page" in _checks(result)


def test_a_document_that_is_not_xml_fails_rather_than_returning_nothing():
    result = diagram_check.check("<mxfile><diagram>")
    assert result["verdict"] == "FAIL"
    assert _checks(result) == {"parse"}


def test_a_shape_name_drawio_cannot_resolve_is_an_error():
    """`iam` shipped for months. draw.io has no shape by that name, so every IAM role in
    every diagram rendered as a blank tile and nothing anywhere said so."""
    result = diagram_check.check(_canvas(_node(
        "node_0", 100, 100,
        style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.iam;")))
    assert result["verdict"] == "FAIL"
    assert "icon_unknown" in _checks(result)
    finding = [f for f in result["findings"] if f["check"] == "icon_unknown"][0]
    assert finding["evidence"]["shape"] == "mxgraph.aws4.iam"


def test_the_shape_names_the_generator_emits_all_resolve():
    """The generator's own stencil table, checked against draw.io's published library."""
    shapes = diagram_check.known_shapes()
    assert shapes, "the aws4 shape list is missing"
    unknown = [(needle, icon) for needle, icon, _ in drawio_generator._STENCILS
               if icon not in shapes]
    assert not unknown, f"stencil names draw.io cannot resolve: {unknown}"


def test_the_resource_frames_are_not_reported_as_unknown_services():
    """`resource` and `resourceIcon` are the frame the glyph sits in, not a service."""
    result = diagram_check.check(_canvas(_node(
        "node_0", 100, 100,
        style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.s3;")))
    assert "icon_unknown" not in _checks(result)


# --- What is correct, and must stay correct -------------------------------------------------

def test_a_node_no_edge_touches_is_reported_without_failing_the_canvas():
    """The security band carries no edges by design, and a plan declaring no data movement
    correctly draws none. Isolation is worth naming and is not a defect."""
    result = diagram_check.check(_canvas(
        _node("node_0", 100, 100) + _node("node_1", 400, 100) + _node("node_2", 700, 100)
        + _edge("edge_0", "node_0", "node_1")))
    assert result["verdict"] == "PASS"
    assert "node_isolated" in _checks(result)
    isolated = [f for f in result["findings"] if f["check"] == "node_isolated"][0]
    assert isolated["evidence"]["unbanded"] == ["node_2"]
    assert isolated["evidence"]["wired"] == 2


def test_band_attribution_resolves_nesting_before_comparing_geometry():
    """mxGraph geometry is relative to the parent, and the bands are nested inside the
    account boundary. Comparing a top-level cell's box against a band's own box compares two
    coordinate systems: a sender drawn at x=80, outside a boundary starting at x=290, was
    reported inside the catalog band because that band's RELATIVE x was 20."""
    canvas = _canvas(
        _node("layer_box_account", 290, 30, width=800, height=600, style="container=1;")
        + _node("layer_box_catalog", 20, 50, parent="layer_box_account",
                width=570, height=400, style="swimlane;")
        + _node("node_0", 40, 80, parent="layer_box_catalog")
        + _node("node_1", 140, 80, parent="layer_box_catalog")
        + _node("outsider", 80, 80, width=150)
        + _edge("edge_0", "node_0", "node_1"))
    result = diagram_check.check(canvas)

    isolated = [f for f in result["findings"] if f["check"] == "node_isolated"]
    assert isolated
    assert "catalog" not in isolated[0]["evidence"], isolated[0]["evidence"]
    assert isolated[0]["evidence"]["unbanded"] == ["outsider"]


def test_an_external_sender_is_not_counted_as_a_resource_with_no_data_movement():
    """The actor is not in the plan. Listing it beside resources that declare no hop reads
    as a defect in the stack rather than a statement about what is outside it."""
    canvas = _canvas(
        _node("node_0", 100, 100) + _node("node_1", 400, 100)
        + _node("actor_external_source", 700, 100, width=150)
        + _edge("edge_0", "node_0", "node_1"))
    result = diagram_check.check(canvas)

    assert result["verdict"] == "PASS"
    assert not [f for f in result["findings"] if f["check"] == "node_isolated"]


def test_a_canvas_with_no_edges_reports_no_isolation():
    """Every node is isolated when nothing is wired, which is a statement about the plan and
    not about the drawing."""
    result = diagram_check.check(_canvas(_node("node_0", 100, 100)
                                         + _node("node_1", 400, 100)))
    assert result["verdict"] == "PASS"
    assert not result["findings"]


def _plans():
    found = []
    for base, _, files in os.walk(os.path.join(ROOT, "runs")):
        if "plan.json" in files:
            found.append(os.path.join(base, "plan.json"))
    return found


@pytest.mark.parametrize("plan_path", _plans())
def test_every_plan_in_the_repository_generates_a_canvas_that_passes(plan_path):
    """The generator and the checker are held to each other. A page sized before the
    walkthrough was appended, and a deployment page sized by a constant while its content
    grew, both shipped and both are caught here."""
    with open(plan_path, encoding="utf-8") as handle:
        plan = json.load(handle)
    result = diagram_check.check(
        drawio_generator.generate_drawio_from_plan(plan)["xml"])
    fatal = [f for f in result["findings"] if f["severity"] in ("error", "warning")]
    assert result["verdict"] == "PASS", json.dumps(fatal, indent=2)


def test_the_page_grows_to_hold_the_walkthrough_it_appends():
    plans = _plans()
    if not plans:
        pytest.skip("no plan in the repository to render")
    with open(plans[0], encoding="utf-8") as handle:
        xml = drawio_generator.generate_drawio_from_plan(json.load(handle))["xml"]
    pages = diagram_check._pages(xml)
    model = pages[0][1]
    steps = [c for c in diagram_check._cells(model) if c["id"].startswith("legend_step_")]
    assert steps
    assert max(c["box"][1] + c["box"][3] for c in steps) <= float(model.get("pageHeight"))


# --- The CLI ---------------------------------------------------------------------------------

def test_the_cli_returns_zero_on_a_clean_canvas_and_one_on_a_broken_one(tmp_path):
    clean = tmp_path / "clean.drawio"
    clean.write_text(_canvas(_node("node_0", 100, 100)), encoding="utf-8")
    broken = tmp_path / "broken.drawio"
    broken.write_text(_canvas(_node("node_0", 100, 100)
                              + _edge("edge_0", "node_0", "node_9")), encoding="utf-8")

    script = os.path.join(ROOT, "core", "reporting", "diagram_check.py")
    ok = subprocess.run([sys.executable, script, str(clean)],
                        capture_output=True, text=True)
    bad = subprocess.run([sys.executable, script, str(broken), "--json"],
                         capture_output=True, text=True)

    assert ok.returncode == 0, ok.stderr
    assert "PASS" in ok.stdout
    assert bad.returncode == 1
    assert json.loads(bad.stdout)["verdict"] == "FAIL"


def test_the_report_is_ascii_only():
    """NFR-01. These land in tickets and CI logs, where a box-drawing character becomes
    noise somebody writes a sed script to strip."""
    result = diagram_check.check(_canvas(
        _node("node_0", 100, 100) + _edge("edge_0", "node_0", "node_9")))
    report = diagram_check.format_report(result)
    assert report.isascii(), [c for c in report if not c.isascii()]
