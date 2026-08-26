"""
The 5-hop Medallion lineage graph.

What this asserts is that the graph describes THIS stack, not the medallion pattern in
general. A lineage diagram that always draws Bronze -> Silver -> Gold is a picture of the
architecture textbook; if the run has no data-quality module there is no quality gate and
no quarantine fork, and drawing them anyway tells an auditor a control exists that does not.

The same doctrine `serving.py` follows: a node is emitted only when the stack provisions it,
and a half-known node is dropped rather than rendered with blanks.

Depends on: core/reporting/lineage_graph.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import pytest

import lineage_graph as lg


def _decision(modules):
    return {"selected_modules": list(modules), "architecture": "test lakehouse"}


FULL = ("ingestion-webhook", "storage-medallion-s3", "compute-glue-etl",
        "dq-great-expectations", "query-athena", "governance-lakeformation")


# --- The graph describes the stack, not the pattern ------------------------------------

def test_the_five_medallion_hops_are_present_for_a_full_stack():
    graph = lg.build_lineage(_decision(FULL))
    layers = [n["layer"] for n in graph["nodes"]]

    for expected in ("ingress", "bronze", "transform", "silver", "gold", "serving"):
        assert expected in layers, f"{expected} missing from a full medallion stack"


def test_a_stack_without_data_quality_has_no_gate_and_no_quarantine_fork():
    """The fork exists because a quality gate routes bad records somewhere. Without the
    gate there is nothing doing the routing, and drawing the branch would claim a control
    the stack does not have."""
    graph = lg.build_lineage(_decision(
        ["storage-medallion-s3", "compute-glue-etl", "query-athena"]))
    ids = {n["id"] for n in graph["nodes"]}

    assert "quality_gate" not in ids
    assert "quarantine" not in ids
    assert not [e for e in graph["edges"] if e["to"] == "quarantine"]


def test_the_quarantine_fork_branches_off_the_quality_gate():
    graph = lg.build_lineage(_decision(FULL))
    forks = [e for e in graph["edges"] if e["from"] == "quality_gate"]
    targets = {e["to"] for e in forks}

    assert {"gold", "quarantine"} <= targets, "the gate must route both ways"
    assert any(e.get("branch") == "reject" for e in forks)


def test_edges_only_ever_reference_nodes_that_exist():
    """A dangling edge renders as a line into empty space."""
    for modules in (FULL, ["storage-medallion-s3"], ["compute-glue-etl", "query-athena"], []):
        graph = lg.build_lineage(_decision(modules))
        ids = {n["id"] for n in graph["nodes"]}
        for edge in graph["edges"]:
            assert edge["from"] in ids, f"{edge} has no source node"
            assert edge["to"] in ids, f"{edge} has no target node"


def test_an_empty_stack_produces_an_empty_graph_not_a_default_one():
    graph = lg.build_lineage(_decision([]))

    assert graph["nodes"] == []
    assert graph["edges"] == []


def test_the_graph_is_acyclic():
    """It is a DAG by definition; a cycle would mean the renderer never terminates."""
    graph = lg.build_lineage(_decision(FULL))
    outgoing = {}
    for edge in graph["edges"]:
        outgoing.setdefault(edge["from"], []).append(edge["to"])

    seen, stack = set(), []

    def walk(node):
        assert node not in stack, f"cycle through {node}"
        stack.append(node)
        for nxt in outgoing.get(node, []):
            walk(nxt)
        stack.pop()
        seen.add(node)

    for node in graph["nodes"]:
        walk(node["id"])


# --- Lake Formation PII masking --------------------------------------------------------

def test_masking_is_reported_only_when_lake_formation_governs_the_stack():
    """Claiming a column is masked when nothing enforces it is the worst thing this graph
    could say -- an auditor reads it as a control."""
    without = lg.build_lineage(_decision(
        ["storage-medallion-s3", "compute-glue-etl", "query-athena"]))
    with_lf = lg.build_lineage(_decision(FULL))

    assert without["masking"]["enforced"] is False
    assert without["masking"]["columns"] == []
    assert with_lf["masking"]["enforced"] is True


def test_masking_names_both_the_privileged_and_the_restricted_view():
    """FR-03.2 asks for the contrast: what billing sees against what analytics sees."""
    masking = lg.build_lineage(_decision(FULL))["masking"]

    assert masking["columns"], "a governed stack should declare at least one masked column"
    for column in masking["columns"]:
        assert column["unmasked_for"], column
        assert column["masked_for"], column
        assert column["masked_example"].count("*") >= 3, column


def test_declared_columns_override_the_defaults():
    """The defaults are a starting point for a governed stack, never a claim about which
    columns this particular pipeline actually holds."""
    decision = _decision(FULL)
    decision["pii_columns"] = [
        {"column": "passport_no", "unmasked_for": ["compliance"], "masked_for": ["analytics"]}]

    masking = lg.build_lineage(decision)["masking"]

    assert [c["column"] for c in masking["columns"]] == ["passport_no"]


# --- Node inspection -------------------------------------------------------------------

def test_storage_nodes_carry_the_facts_the_inspector_panel_shows():
    graph = lg.build_lineage(_decision(FULL))
    gold = lg.find_node(graph, "gold")

    assert gold["table_format"], "click-to-inspect needs a table format"
    assert gold["partitioning"], "and a partitioning scheme"
    assert gold["retention"], "and a retention lifecycle"


def test_find_node_returns_none_rather_than_raising_for_an_absent_node():
    graph = lg.build_lineage(_decision([]))

    assert lg.find_node(graph, "gold") is None


# --- Invariants ------------------------------------------------------------------------

def test_the_module_imports_only_the_standard_library():
    """AC-05: core engine modules must be importable on a bare interpreter."""
    import ast
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "core", "reporting", "lineage_graph.py")
    tree = ast.parse(open(path, encoding="utf-8").read())

    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    import sys
    assert roots <= set(sys.stdlib_module_names), f"non-stdlib imports: {sorted(roots)}"


def test_the_graph_carries_no_emoji():
    """Zero-emoji doctrine, on a surface that renders into a browser and a terminal."""
    import json
    import re
    payload = json.dumps(lg.build_lineage(_decision(FULL)), ensure_ascii=False)
    assert not re.search("[\U0001F000-\U0001FAFF\u2600-\u27BF]", payload)
