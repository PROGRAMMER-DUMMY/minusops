"""Static verification of a generated draw.io canvas, and the verdict it earns.

A diagram is XML that renders whatever it is given. An edge naming a cell that does not
exist draws nothing, a child whose geometry escapes its container draws outside the box, and
two bands at the same offset draw through each other -- all three open cleanly in
diagrams.net and all three are wrong. `drawio_generator.py` decides what the canvas claims;
this decides whether the canvas says it.

Reads every page, including the deployment page, because containment is only expressible
there. Cells prefixed `layer_` or `legend_` are chrome -- bands, containers and the
walkthrough -- and are held to the container rules rather than the node rules.

Depends on: nothing outside the standard library
Used by: core/cli/commands/diagram.py, tests/test_diagram_check.py
"""
import argparse
import functools
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

_CHROME_PREFIXES = ("layer_", "legend_")
_SHAPE_LIST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "stencil_data", "aws4_shapes.txt")
_AWS4_REF = re.compile(r"(?:shape|resIcon)=mxgraph\.aws4\.([a-z0-9_]+)")
_AWS4_FRAMES = frozenset(("resource", "resourceIcon", "group", "productIcon"))

SEVERITIES = ("note", "warning", "error")
_VERDICTS = {"note": "PASS", "warning": "WARN", "error": "FAIL"}


@functools.lru_cache(maxsize=1)
def known_shapes():
    """draw.io's aws4 shape names. Empty when the list is absent, which skips the check
    rather than failing every diagram on a missing reference file."""
    try:
        with open(_SHAPE_LIST, encoding="utf-8") as handle:
            return frozenset(handle.read().split())
    except OSError:
        return frozenset()


def _finding(check, severity, page, detail, evidence=None):
    return {"check": check, "severity": severity, "page": page, "detail": detail,
            "evidence": evidence or {}}


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pages(xml_text):
    root = ET.fromstring(xml_text)
    if root.tag != "mxfile":
        return [("Page-1", root)]
    pages = []
    for index, diagram in enumerate(root.findall("diagram"), 1):
        model = diagram.find("mxGraphModel")
        pages.append((diagram.get("name") or f"Page-{index}", model))
    return pages


def _cells(model):
    """Every mxCell on a page, with its geometry flattened onto it."""
    if model is None:
        return []
    cells = []
    for cell in model.iter("mxCell"):
        geometry = cell.find("mxGeometry")
        cells.append({
            "id": cell.get("id") or "",
            "parent": cell.get("parent"),
            "vertex": cell.get("vertex") == "1",
            "edge": cell.get("edge") == "1",
            "source": cell.get("source"),
            "target": cell.get("target"),
            "value": cell.get("value") or "",
            "tooltip": cell.get("tooltip") or "",
            "style": cell.get("style") or "",
            "box": None if geometry is None else (
                _number(geometry.get("x")), _number(geometry.get("y")),
                _number(geometry.get("width")), _number(geometry.get("height"))),
        })
    return cells


def _name(cell):
    """The resource address when the generator recorded one, the cell id otherwise."""
    return cell["tooltip"] or cell["id"]


def _is_container(cell):
    return "swimlane" in cell["style"] or "container=1" in cell["style"]


def _is_chrome(cell):
    return cell["id"].startswith(_CHROME_PREFIXES)


def _overlaps(first, second):
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _check_ids(page, cells):
    seen, duplicates = set(), []
    for cell in cells:
        if cell["id"] in seen:
            duplicates.append(cell["id"])
        seen.add(cell["id"])
    if duplicates:
        yield _finding("duplicate_id", "error", page,
                       "mxGraph keeps the last cell with an id and drops the rest, so a "
                       "duplicate removes a node or an edge without an error",
                       {"ids": sorted(set(duplicates))})


def _check_edges(page, cells, known):
    for cell in cells:
        if not cell["edge"]:
            continue
        loose = [end for end in ("source", "target") if not cell[end]]
        if loose:
            yield _finding("edge_endpoint_missing", "error", page,
                           "an edge with no endpoint renders as an arrow anchored to the "
                           "canvas rather than to a resource",
                           {"edge": cell["id"], "missing": loose, "label": cell["value"]})
        dangling = {end: cell[end] for end in ("source", "target")
                    if cell[end] and cell[end] not in known}
        if dangling:
            yield _finding("edge_endpoint_unknown", "error", page,
                           "the endpoint names no cell on this page, so the edge is "
                           "declared and never drawn",
                           {"edge": cell["id"], "unresolved": dangling,
                            "label": cell["value"]})
        if cell["source"] and cell["source"] == cell["target"]:
            yield _finding("edge_self_loop", "error", page,
                           "a resource declared as its own source and target",
                           {"edge": cell["id"], "endpoint": cell["source"]})
        if not cell["value"].strip():
            yield _finding("edge_unlabeled", "warning", page,
                           "an edge asserts data movement; without a label it asserts a "
                           "relationship the reader has to guess at",
                           {"edge": cell["id"], "source": cell["source"],
                            "target": cell["target"]})


def _check_parents(page, cells, by_id):
    for cell in cells:
        parent = cell["parent"]
        if parent is None or parent in by_id:
            continue
        yield _finding("parent_unknown", "error", page,
                       "a cell whose parent does not exist is dropped from the model on "
                       "load, silently",
                       {"cell": cell["id"], "parent": parent})


def _check_containment(page, cells, by_id):
    for cell in cells:
        parent = by_id.get(cell["parent"] or "")
        if not cell["box"] or not parent or not parent["box"] or not parent["vertex"]:
            continue
        x, y, width, height = cell["box"]
        _, _, parent_width, parent_height = parent["box"]
        escapes = (x < 0 or y < 0 or x + width > parent_width or y + height > parent_height)
        if escapes:
            yield _finding("child_escapes_parent", "error", page,
                           "child geometry is relative to its container; a child outside "
                           "those bounds draws outside the box it is supposed to be in",
                           {"cell": cell["id"], "parent": parent["id"],
                            "child_box": [x, y, width, height],
                            "parent_size": [parent_width, parent_height]})


def _check_overlap(page, cells):
    """Siblings of the same kind must not intersect. Containers and the nodes they hold
    overlap by design, so the two classes are compared separately."""
    groups = {}
    for cell in cells:
        if not cell["vertex"] or not cell["box"] or cell["id"] in ("0", "1"):
            continue
        groups.setdefault((cell["parent"], _is_container(cell)), []).append(cell)

    for (parent, container), members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        for index, first in enumerate(members):
            for second in members[index + 1:]:
                if _overlaps(first["box"], second["box"]):
                    yield _finding("sibling_overlap", "error", page,
                                   "two containers drawn through each other" if container
                                   else "two resources drawn on top of each other",
                                   {"cells": [first["id"], second["id"]],
                                    "parent": parent,
                                    "boxes": [list(first["box"]), list(second["box"])]})


def _check_page_bounds(page, model, cells):
    width = _number(model.get("pageWidth"), 0) if model is not None else 0
    height = _number(model.get("pageHeight"), 0) if model is not None else 0
    if not width or not height:
        return
    for cell in cells:
        if not cell["vertex"] or not cell["box"] or cell["parent"] != "1":
            continue
        x, y, cell_width, cell_height = cell["box"]
        if x < 0 or y < 0 or x + cell_width > width or y + cell_height > height:
            yield _finding("cell_off_page", "warning", page,
                           "the cell sits outside the declared page, so it is missing from "
                           "an export that honours the page size",
                           {"cell": cell["id"], "box": [x, y, cell_width, cell_height],
                            "page_size": [width, height]})


def _band_of(cell, bands):
    """Which band a node was laid out in, read off the drawing rather than the plan.

    The parent settles it when the generator made the band a real container. The geometric
    fallback is for a hand-edited file, and takes the SMALLEST containing band: the account
    boundary encloses every band, so the first match is always the least specific one.
    """
    parent = cell["parent"] or ""
    if parent.startswith("layer_box_"):
        return parent[len("layer_box_"):]
    if not cell["box"]:
        return "unbanded"
    x, y, width, height = cell["box"]
    centre = (x + width / 2, y + height / 2)
    holding = [(bw * bh, name) for name, (bx, by, bw, bh) in bands.items()
               if bx <= centre[0] <= bx + bw and by <= centre[1] <= by + bh]
    return min(holding)[1] if holding else "unbanded"


def _check_icons(page, cells):
    """Every mxgraph.aws4 name we emit has to be one draw.io can resolve. It renders an
    unknown name as a blank tile and reports nothing, so a typo is invisible in the file and
    invisible on the canvas."""
    shapes = known_shapes()
    if not shapes:
        return
    unknown = {}
    for cell in cells:
        for name in _AWS4_REF.findall(cell["style"]):
            if name not in shapes and name not in _AWS4_FRAMES:
                unknown.setdefault(name, []).append(_name(cell))
    for name, users in sorted(unknown.items()):
        yield _finding("icon_unknown", "error", page,
                       "draw.io has no shape by this name, so the resource draws as a blank "
                       "tile with no error anywhere",
                       {"shape": f"mxgraph.aws4.{name}", "cells": sorted(users)[:8],
                        "affected": len(users)})


def _check_isolated(page, cells):
    """Nodes no edge touches, grouped by the band that holds them. Reported, never fatal:
    the security band carries no edges by design, and a plan that declares no data movement
    correctly draws none. Twenty addresses in one list is a wall nobody reads; the band is
    what says whether an absent edge is expected."""
    wired = set()
    for cell in cells:
        if cell["edge"]:
            wired.update(end for end in (cell["source"], cell["target"]) if end)
    nodes = [c for c in cells
             if c["vertex"] and not _is_chrome(c) and not _is_container(c)
             and c["id"] not in ("0", "1")]
    if not wired or not nodes:
        return

    bands = {c["id"][len("layer_box_"):]: c["box"] for c in cells
             if c["id"].startswith("layer_box_") and c["box"]}
    grouped = {}
    for cell in nodes:
        if cell["id"] not in wired:
            grouped.setdefault(_band_of(cell, bands), []).append(_name(cell))
    if grouped:
        yield _finding("node_isolated", "note", page,
                       "the page declares data movement but these resources take part in "
                       "none of it",
                       dict({band: sorted(members) for band, members in grouped.items()},
                            wired=len(nodes) - sum(len(v) for v in grouped.values()),
                            total=len(nodes)))


def check(xml_text):
    """Verify one .drawio document. Returns findings, per-page counts, and a verdict."""
    try:
        pages = _pages(xml_text or "")
    except ET.ParseError as error:
        return {"verdict": "FAIL", "pages": [], "counts": {},
                "findings": [_finding("parse", "error", None,
                                      "the document is not well-formed XML, so nothing "
                                      "downstream can read it", {"error": str(error)})]}

    findings, counts = [], {}
    for name, model in pages:
        cells = _cells(model)
        by_id = {c["id"]: c for c in cells}
        nodes = [c for c in cells
                 if c["vertex"] and not _is_chrome(c) and not _is_container(c)]
        counts[name] = {
            "cells": len(cells),
            "nodes": len(nodes),
            "edges": sum(1 for c in cells if c["edge"]),
            "containers": sum(1 for c in cells if c["vertex"] and _is_container(c)),
        }
        findings.extend(_check_ids(name, cells))
        findings.extend(_check_edges(name, cells, set(by_id)))
        findings.extend(_check_parents(name, cells, by_id))
        findings.extend(_check_containment(name, cells, by_id))
        findings.extend(_check_overlap(name, cells))
        findings.extend(_check_page_bounds(name, model, cells))
        findings.extend(_check_icons(name, cells))
        findings.extend(_check_isolated(name, cells))

    worst = max((f["severity"] for f in findings), key=SEVERITIES.index, default="note")
    return {"verdict": _VERDICTS[worst] if findings else "PASS",
            "pages": [name for name, _ in pages], "counts": counts, "findings": findings}


def format_report(result, width=94):
    """The verdict as a block an operator reads in a terminal or pastes into a ticket."""
    rule = "-" * width
    lines = [rule, f"DIAGRAM CHECK: {result['verdict']}", rule]
    for name in result["pages"]:
        counts = result["counts"].get(name, {})
        lines.append(f"  {name}: {counts.get('nodes', 0)} nodes, {counts.get('edges', 0)} "
                     f"edges, {counts.get('containers', 0)} containers")
    if not result["findings"]:
        lines.extend(["", "  No dangling edges, no escaped containment, no overlap.", rule])
        return "\n".join(lines)

    lines.append("")
    for severity in reversed(SEVERITIES):
        for finding in result["findings"]:
            if finding["severity"] != severity:
                continue
            lines.append(f"  [{severity.upper()}] {finding['check']} "
                         f"({finding['page']})")
            lines.append(f"    {finding['detail']}")
            for key, value in sorted(finding["evidence"].items()):
                lines.append(f"    {key}: {value}")
            lines.append("")
    lines.append(rule)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="diagram_check",
        description="Verify a generated .drawio canvas and return a verdict.")
    parser.add_argument("path", help="Path to a .drawio file")
    parser.add_argument("--json", action="store_true", help="Structured output")
    args = parser.parse_args(argv)

    try:
        with open(args.path, encoding="utf-8") as handle:
            result = check(handle.read())
    except OSError as error:
        print(f"cannot read {args.path}: {error}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2) if args.json else format_report(result))
    return 1 if result["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
