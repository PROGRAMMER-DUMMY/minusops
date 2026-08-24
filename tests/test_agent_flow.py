"""
Agent flow lineage and audit-chain verification (PRD v14 FR-04, FR-05, FR-06).

Two things are under test here, and they fail in opposite directions.

The chain verifier is a tamper detector, so it fails CLOSED: a parse error, a broken link,
a record with no prev_hash -- none of those are "verified". It reports three separate
facts, never one boolean: the chain is VERIFIED, the chain is BROKEN at a named record, or
there is no chain present at all. Collapsing "no chain" into "not verified" tells an
auditor a fresh run was tampered with; collapsing it into "verified" tells them a log with
no hashes is evidence. Both are lies, so both states exist.

The flow graph fails OPEN, like the trace it is built from: a stage with no evidence is
`not-run`, never `completed`. A DAG showing nine green nodes next to a cryptographic audit
link is read as a record of what happened.

Depends on: core/governance/agent_tracer.py, core/reporting/agent_flow_graph.py,
    core/governance/audit_chain.py (fixture writer -- the real chain format, not a mock)
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import json
import os
import re

import pytest

import agent_flow_graph as flow
import agent_tracer as tracer
import audit_chain

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _chained(tmp_path, records, name="audit.jsonl"):
    """A real hash-chained log, written by the real writer.

    Handrolling the hashes in the fixture would test the verifier against this file's idea
    of the format rather than against the chain the control plane actually appends to.
    """
    path = str(tmp_path / name)
    for record in records:
        entry = dict(record)
        entry.setdefault("operator", "shubh")
        audit_chain.append(path, entry)
    return path


def _lines(path):
    with open(path, encoding="utf-8") as fh:
        return [line for line in fh.read().splitlines() if line.strip()]


def _rewrite(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _three_records():
    return [
        {"action": "synthesize", "details": "d1", "timestamp": "2026-08-23T10:00:00+00:00"},
        {"action": "plan", "details": "d2", "timestamp": "2026-08-23T10:00:30+00:00"},
        {"action": "approve", "details": "d3", "timestamp": "2026-08-23T10:01:00+00:00"},
    ]


# --- FR-06: hash chain verification, three states ---------------------------------------

def test_an_untouched_chain_verifies(tmp_path):
    path = _chained(tmp_path, _three_records())

    result = tracer.verify_chain(path)

    assert result["state"] == tracer.CHAIN_VERIFIED
    assert result["broken_at"] is None
    assert result["checked"] == 3


def test_a_tampered_record_names_the_record_it_broke_at(tmp_path):
    """The whole point of the chain. Editing record 2 must be located, not just reported."""
    path = _chained(tmp_path, _three_records())
    lines = _lines(path)
    record = json.loads(lines[1])
    record["details"] = "d2 quietly edited after the fact"
    lines[1] = json.dumps(record)
    _rewrite(path, lines)

    result = tracer.verify_chain(path)

    assert result["state"] == tracer.CHAIN_BROKEN
    assert result["broken_at"] == 2
    assert result["errors"]


def test_a_deleted_record_breaks_the_chain(tmp_path):
    path = _chained(tmp_path, _three_records())
    lines = _lines(path)
    _rewrite(path, [lines[0], lines[2]])

    result = tracer.verify_chain(path)

    assert result["state"] == tracer.CHAIN_BROKEN
    assert result["broken_at"] == 2


def test_a_record_with_no_prev_hash_link_fails_closed(tmp_path):
    """"Cannot verify" is never "verified" -- a record with the link stripped out is exactly
    what someone splicing in a forged entry leaves behind."""
    path = _chained(tmp_path, _three_records())
    lines = _lines(path)
    record = json.loads(lines[1])
    record.pop("prev_hash")
    lines[1] = json.dumps(record)
    _rewrite(path, lines)

    result = tracer.verify_chain(path)

    assert result["state"] == tracer.CHAIN_BROKEN
    assert result["state"] != tracer.CHAIN_VERIFIED


def test_an_unparseable_line_fails_closed(tmp_path):
    path = _chained(tmp_path, _three_records())
    lines = _lines(path)
    lines[1] = "{not json at all"
    _rewrite(path, lines)

    result = tracer.verify_chain(path)

    assert result["state"] == tracer.CHAIN_BROKEN
    assert result["broken_at"] == 2


def test_a_log_that_carries_no_hashes_at_all_is_no_chain_present(tmp_path):
    """A plain log is not a broken chain. Reporting tampering for a file that was never
    chained sends an auditor hunting for an incident that did not happen."""
    path = tmp_path / "audit.jsonl"
    path.write_text('{"action": "plan", "timestamp": "2026-08-23T10:00:00+00:00"}\n',
                    encoding="utf-8")

    result = tracer.verify_chain(str(path))

    assert result["state"] == tracer.CHAIN_ABSENT
    assert result["broken_at"] is None


def test_a_missing_audit_file_is_no_chain_present_not_verified(tmp_path):
    result = tracer.verify_chain(str(tmp_path / "nope.jsonl"))

    assert result["state"] == tracer.CHAIN_ABSENT


def test_an_unchained_record_spliced_after_chaining_began_is_broken(tmp_path):
    path = _chained(tmp_path, _three_records())
    lines = _lines(path)
    lines.insert(2, json.dumps({"action": "apply", "details": "no hashes on this one"}))
    _rewrite(path, lines)

    result = tracer.verify_chain(path)

    assert result["state"] == tracer.CHAIN_BROKEN


def test_the_three_chain_states_stay_three_distinct_facts():
    """They must never collapse into one boolean: verified, broken, and never-chained are
    three different things to tell an auditor."""
    states = {tracer.CHAIN_VERIFIED, tracer.CHAIN_BROKEN, tracer.CHAIN_ABSENT}

    assert len(states) == 3


# --- FR-02/FR-05: per-step latency ------------------------------------------------------

def test_step_latency_is_the_gap_between_consecutive_recorded_stages(tmp_path):
    path = _chained(tmp_path, _three_records())

    by_key = {s["key"]: s for s in tracer.trace(audit_path=path)["stages"]}

    assert by_key["plan"]["latency_seconds"] == 30.0
    assert by_key["approval"]["latency_seconds"] == 30.0


def test_the_first_recorded_stage_has_no_latency_rather_than_zero(tmp_path):
    """Nothing records when a step STARTED, only when it landed. Zero would read as an
    instantaneous step; None reads as what it is -- not measurable from this evidence."""
    path = _chained(tmp_path, _three_records())

    by_key = {s["key"]: s for s in tracer.trace(audit_path=path)["stages"]}

    assert by_key["synthesis"]["latency_seconds"] is None


def test_a_stage_that_did_not_run_has_no_latency(tmp_path):
    path = _chained(tmp_path, _three_records())

    by_key = {s["key"]: s for s in tracer.trace(audit_path=path)["stages"]}

    assert by_key["proving"]["latency_seconds"] is None


def test_an_unparseable_timestamp_yields_no_latency_not_a_crash(tmp_path):
    path = _chained(tmp_path, [
        {"action": "synthesize", "details": "d", "timestamp": "2026-08-23T10:00:00+00:00"},
        {"action": "plan", "details": "d", "timestamp": "whenever"},
    ])

    by_key = {s["key"]: s for s in tracer.trace(audit_path=path)["stages"]}

    assert by_key["plan"]["latency_seconds"] is None


# --- FR-05: decision branches -----------------------------------------------------------

def test_decision_branches_reports_absent_when_there_is_no_decision_file(tmp_path):
    result = tracer.decision_branches(str(tmp_path))

    assert result["present"] is False
    assert result["chosen_modules"] == []
    assert result["rejected_alternatives"] == []


def test_decision_branches_extracts_the_chosen_modules_and_the_rejected_alternatives(tmp_path):
    (tmp_path / "architecture_decision.json").write_text(json.dumps({
        "selected_architecture": "AWS governed lakehouse",
        "decision_summary": "Glue 4.0 PySpark on a 15-minute batch cadence.",
        "selected_modules": ["storage-medallion-s3", "compute-glue-etl"],
        "alternatives": [
            {"name": "EMR Serverless", "decision": "rejected",
             "reason": "Higher floor cost for a 50GB/day batch."},
            {"name": "AWS Glue 4.0", "decision": "accepted", "reason": "Cheapest fit."},
        ],
        "failure_modes": ["FM-03"],
    }), encoding="utf-8")

    result = tracer.decision_branches(str(tmp_path))

    assert result["present"] is True
    assert result["chosen_modules"] == ["storage-medallion-s3", "compute-glue-etl"]
    assert result["justification"] == "Glue 4.0 PySpark on a 15-minute batch cadence."
    assert result["architecture"] == "AWS governed lakehouse"
    assert [a["name"] for a in result["rejected_alternatives"]] == ["EMR Serverless"]
    assert result["rejected_alternatives"][0]["reason"].startswith("Higher floor cost")
    assert result["failure_modes_addressed"] == ["FM-03"]


def test_a_decision_recording_no_alternatives_says_so_rather_than_inventing_one(tmp_path):
    """The decision files this repo has actually written carry no `alternatives` at all.
    An empty list is the honest answer; a placeholder tradeoff is a fabricated one."""
    (tmp_path / "architecture_decision.json").write_text(json.dumps({
        "selected_modules": ["query-athena"], "architecture": "lakehouse",
    }), encoding="utf-8")

    result = tracer.decision_branches(str(tmp_path))

    assert result["present"] is True
    assert result["rejected_alternatives"] == []
    assert result["justification"] is None


def test_a_corrupt_decision_file_is_absent_not_a_crash(tmp_path):
    (tmp_path / "architecture_decision.json").write_text("{ broken", encoding="utf-8")

    assert tracer.decision_branches(str(tmp_path))["present"] is False


# --- FR-04: the DAG ---------------------------------------------------------------------

def test_every_traced_stage_becomes_a_node_and_the_edges_chain_them(tmp_path):
    path = _chained(tmp_path, _three_records())

    graph = flow.build_flow(tracer.trace(audit_path=path))

    assert len(graph["nodes"]) == len(tracer.STAGES)
    assert len(graph["edges"]) == len(graph["nodes"]) - 1
    ids = [n["id"] for n in graph["nodes"]]
    for edge in graph["edges"]:
        assert edge["from"] in ids and edge["to"] in ids


def test_every_node_status_comes_from_the_fr04_vocabulary(tmp_path):
    path = _chained(tmp_path, _three_records())

    graph = flow.build_flow(tracer.trace(audit_path=path))

    assert {n["status"] for n in graph["nodes"]} <= set(flow.STATUSES)


def test_a_recorded_stage_is_completed_and_carries_its_audit_seal(tmp_path):
    path = _chained(tmp_path, _three_records())

    graph = flow.build_flow(tracer.trace(audit_path=path))
    node = flow.find_node(graph, "synthesis")

    assert node["status"] == flow.COMPLETED
    assert len(node["audit_hash"]) == 64


def test_a_stage_with_no_evidence_is_not_run_and_carries_no_seal(tmp_path):
    path = _chained(tmp_path, _three_records())

    graph = flow.build_flow(tracer.trace(audit_path=path))
    node = flow.find_node(graph, "proving")

    assert node["status"] == flow.NOT_RUN
    assert node["audit_hash"] is None


def test_a_rejected_gate_record_renders_blocked_not_completed(tmp_path):
    path = _chained(tmp_path, [
        {"action": "plan", "details": "d", "status": "REJECTED",
         "timestamp": "2026-08-23T10:00:00+00:00"},
    ])

    graph = flow.build_flow(tracer.trace(audit_path=path))

    assert flow.find_node(graph, "plan")["status"] == flow.BLOCKED


def test_a_denied_approval_renders_blocked(tmp_path):
    path = _chained(tmp_path, [
        {"action": "approve", "details": "d", "decision": "DENIED_NOT_AUTHORIZED",
         "timestamp": "2026-08-23T10:00:00+00:00"},
    ])

    graph = flow.build_flow(tracer.trace(audit_path=path))

    assert flow.find_node(graph, "approval")["status"] == flow.BLOCKED


def test_a_gate_awaiting_a_human_renders_waiting_on_human(tmp_path):
    path = _chained(tmp_path, [
        {"action": "approve", "details": "d", "status": "PENDING_APPROVAL",
         "timestamp": "2026-08-23T10:00:00+00:00"},
    ])

    graph = flow.build_flow(tracer.trace(audit_path=path))

    assert flow.find_node(graph, "approval")["status"] == flow.WAITING_ON_HUMAN


def test_a_stage_renders_running_only_when_a_supervisor_says_it_is(tmp_path):
    path = _chained(tmp_path, _three_records())
    traced = tracer.trace(audit_path=path)

    assert flow.find_node(flow.build_flow(traced), "proving")["status"] == flow.NOT_RUN
    graph = flow.build_flow(traced, active=["proving"])
    assert flow.find_node(graph, "proving")["status"] == flow.RUNNING


def test_nodes_carry_the_persona_and_model_tier_of_the_agent_that_owns_them(tmp_path):
    graph = flow.build_flow(tracer.trace(audit_path=str(tmp_path / "none.jsonl")))

    for node in graph["nodes"]:
        assert node["persona"], node["id"]
        assert node["model_tier"], node["id"]
    assert flow.find_node(graph, "synthesis")["model_tier"] == "stdlib"


def test_node_handoffs_name_the_artifact_in_and_the_artifact_out(tmp_path):
    graph = flow.build_flow(tracer.trace(audit_path=str(tmp_path / "none.jsonl")))

    assert flow.find_node(graph, "requirements")["inputs"] == []
    assert flow.find_node(graph, "synthesis")["outputs"] == [
        flow.find_node(graph, "synthesis")["artifact"]]
    assert flow.find_node(graph, "synthesis")["inputs"] == [
        flow.find_node(graph, "architecture")["artifact"]]


def test_the_decision_branch_is_attached_to_the_architecture_node(tmp_path):
    (tmp_path / "architecture_decision.json").write_text(json.dumps({
        "selected_modules": ["compute-glue-etl"],
        "decision_summary": "Glue over EMR.",
        "alternatives": [{"name": "EMR Serverless", "decision": "rejected",
                          "reason": "cost floor"}],
    }), encoding="utf-8")
    traced = tracer.trace(audit_path=str(tmp_path / "none.jsonl"))

    graph = flow.build_flow(traced, decision=tracer.decision_branches(str(tmp_path)))
    node = flow.find_node(graph, "architecture")

    assert node["decision"]["chosen_modules"] == ["compute-glue-etl"]
    assert node["decision"]["rejected_alternatives"][0]["name"] == "EMR Serverless"
    assert flow.find_node(graph, "proving")["decision"] is None


def test_the_graph_reports_the_chain_state_it_was_given(tmp_path):
    path = _chained(tmp_path, _three_records())
    traced = tracer.trace(audit_path=path)

    graph = flow.build_flow(traced, chain=tracer.verify_chain(path))

    assert graph["chain"]["state"] == tracer.CHAIN_VERIFIED


def test_the_graph_never_claims_integrity_it_was_not_given(tmp_path):
    """The DAG renders next to a "verify audit trail" indicator. A caller that skipped
    verification must not light it green."""
    traced = tracer.trace(audit_path=str(tmp_path / "none.jsonl"))

    graph = flow.build_flow(traced)

    assert graph["chain"]["state"] != tracer.CHAIN_VERIFIED
    assert graph["chain"]["state"] == flow.CHAIN_NOT_CHECKED


# --- Invariants -------------------------------------------------------------------------

def test_the_graph_is_plain_json_data_with_no_markup_in_it():
    """WP-03 compiles a structure; the console picks the renderer. A module that emits HTML
    has made that choice for it."""
    graph = flow.build_flow(tracer.trace(audit_path=os.devnull))

    text = json.dumps(graph)
    assert "<" not in text and "</" not in text


@pytest.mark.parametrize("relative", [
    os.path.join("core", "reporting", "agent_flow_graph.py"),
    os.path.join("core", "governance", "agent_tracer.py"),
])
def test_the_v14_flow_modules_import_only_the_standard_library(relative):
    import ast
    import sys
    tree = ast.parse(open(os.path.join(ROOT, relative), encoding="utf-8").read())

    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    assert roots <= set(sys.stdlib_module_names), f"non-stdlib imports: {sorted(roots)}"


@pytest.mark.parametrize("relative", [
    os.path.join("core", "reporting", "agent_flow_graph.py"),
    os.path.join("core", "governance", "agent_tracer.py"),
    os.path.join("tests", "test_agent_flow.py"),
])
def test_the_v14_flow_modules_carry_no_emoji(relative):
    text = open(os.path.join(ROOT, relative), encoding="utf-8").read()

    assert not re.search("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]", text)
