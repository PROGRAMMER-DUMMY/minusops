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


# --- Plan-derived facts versus the pattern's defaults ------------------------------------
#
# Every attribute in _NODES is the medallion PATTERN's default: 90 days to Glacier because
# that is storage-medallion-s3's default, `ingest_date=YYYY/MM/DD` because that is the shape
# the module suggests. Rendered on a governance surface with no provenance, an auditor reads
# each one as a fact about the stack in front of them.

def _composed_plan():
    """A module-composed medallion stack, shaped the way the synthesizer emits one."""
    def bucket(zone, name):
        return {"address": f'module.storage_medallion_s3.aws_s3_bucket.zone["{zone}"]',
                "type": "aws_s3_bucket", "mode": "managed", "name": "zone",
                "change": {"actions": ["create"], "after": {"bucket": name}}}

    def sse(zone):
        return {"address": ('module.storage_medallion_s3.'
                            f'aws_s3_bucket_server_side_encryption_configuration.zone["{zone}"]'),
                "type": "aws_s3_bucket_server_side_encryption_configuration",
                "mode": "managed", "name": "zone",
                "change": {"actions": ["create"], "after": {"rule": [
                    {"apply_server_side_encryption_by_default": [
                        {"sse_algorithm": "aws:kms"}]}]}}}

    def lifecycle(zone, days):
        return {"address": ('module.storage_medallion_s3.'
                            f'aws_s3_bucket_lifecycle_configuration.zone["{zone}"]'),
                "type": "aws_s3_bucket_lifecycle_configuration", "mode": "managed",
                "name": "zone",
                "change": {"actions": ["create"], "after": {"rule": [
                    {"transition": [{"days": days, "storage_class": "GLACIER"}]}]}}}

    changes = []
    for zone, name in (("bronze", "lake-bronze"), ("silver", "lake-silver"),
                       ("gold", "lake-gold")):
        changes += [bucket(zone, name), sse(zone), lifecycle(zone, 30)]
    changes.append({"address": "module.compute_glue_etl.aws_glue_job.etl",
                    "type": "aws_glue_job", "mode": "managed", "name": "etl",
                    "change": {"actions": ["create"], "after": {}}})
    changes.append({"address": "module.query_athena.aws_athena_workgroup.wg",
                    "type": "aws_athena_workgroup", "mode": "managed", "name": "wg",
                    "change": {"actions": ["create"], "after": {}}})

    return {"resource_changes": changes,
            "configuration": {"root_module": {"module_calls": {
                "storage_medallion_s3": {"module": {"resources": [
                    {"type": "aws_s3_bucket_server_side_encryption_configuration",
                     "name": "zone",
                     "expressions": {"bucket": {"references": ["each.value.id", "each.value"]}}},
                    {"type": "aws_s3_bucket_lifecycle_configuration", "name": "zone",
                     "expressions": {"bucket": {"references": ["each.value.id", "each.value"]}}},
                ]}},
            }}}}


def test_a_plan_only_call_populates_the_graph():
    """A Terraform address segment cannot contain a hyphen, so `storage-medallion-s3` appears
    as `module.storage_medallion_s3`. Matching the address form against catalog ids produced
    an empty graph from any plan, and only a decision record ever populated one."""
    graph = lg.build_lineage(decision=None, plan_json=_composed_plan())

    assert [n["id"] for n in graph["nodes"]]


def test_a_dataset_node_carries_the_bucket_the_plan_names():
    graph = lg.build_lineage(decision=None, plan_json=_composed_plan())

    assert "lake-gold" in lg.find_node(graph, "gold")["label"]


def test_stated_retention_replaces_the_patterns_ninety_days():
    """The module default is 90. This plan says 30, and the panel must say 30."""
    graph = lg.build_lineage(decision=None, plan_json=_composed_plan())

    assert "30 days" in lg.find_node(graph, "bronze")["retention"]
    assert "90" not in lg.find_node(graph, "bronze")["retention"]


def test_a_node_says_whether_its_facts_came_from_the_plan_or_the_pattern():
    from_plan = lg.build_lineage(decision=None, plan_json=_composed_plan())
    from_pattern = lg.build_lineage(_decision(FULL))

    assert lg.find_node(from_plan, "gold")["facts_source"] == "plan"
    assert lg.find_node(from_pattern, "gold")["facts_source"] == "pattern default"


def test_a_fact_the_plan_never_states_is_dropped_rather_than_shown_as_planned():
    """Partitioning is not derivable from a plan. Leaving the pattern's value in place under
    a "plan" label is the exact defect this change exists to remove."""
    graph = lg.build_lineage(decision=None, plan_json=_composed_plan())
    gold = lg.find_node(graph, "gold")

    assert gold["facts_source"] == "plan"
    assert not gold.get("partitioning")
