"""
Multi-agent execution trace.

The trace is evidence, not a diagram of the happy path. FR-04.2 names a lifecycle that runs
grill-me -> architect -> synthesizer -> diagrammer -> reflector -> orchestrator -> proving
-> notify, but the audit chain only records what actually executed: `synthesize`, `plan`,
`verify`, `approve`, `apply`, `export`. Several of the named stages have no audit action at
all today.

So the rule here is the one the proving harness learned: a stage with no evidence is
NOT_RUN, never PASSED. A timeline that renders eight green nodes for a run that only ever
synthesised is worse than no timeline, because it is read as a record of what happened.

Depends on: core/governance/agent_tracer.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import json
import os

import pytest

import agent_tracer as tracer


def _audit(tmp_path, records):
    path = tmp_path / "audit.jsonl"
    prev = ""
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            entry = dict(record)
            entry.setdefault("operator", "shubh")
            entry.setdefault("status", "RECORDED")
            entry["prev_hash"] = prev
            entry["entry_hash"] = f"hash{len(prev)}{entry['action']}"
            prev = entry["entry_hash"]
            fh.write(json.dumps(entry) + "\n")
    return str(path)


# --- The catalog ------------------------------------------------------------------------

def test_every_lifecycle_stage_the_prd_names_is_in_the_catalog():
    keys = {stage["key"] for stage in tracer.STAGES}

    for expected in ("requirements", "architecture", "synthesis", "diagram",
                     "reflection", "plan", "proving", "notify"):
        assert expected in keys, f"{expected} missing from the relay catalog"


def test_every_stage_declares_the_artifact_that_proves_it_ran():
    """FR-04.3: clicking a node shows its output artifact. A stage with no artifact has no
    evidence to show."""
    for stage in tracer.STAGES:
        assert stage["produces"], stage["key"]
        assert stage["agent"], stage["key"]


# --- Evidence, not optimism -------------------------------------------------------------

def test_a_stage_with_no_audit_evidence_is_not_run(tmp_path):
    path = _audit(tmp_path, [{"action": "synthesize", "details": "run x",
                              "timestamp": "2026-08-23T10:00:00+00:00"}])

    result = tracer.trace(audit_path=path)
    by_key = {s["key"]: s for s in result["stages"]}

    assert by_key["synthesis"]["status"] == "RECORDED"
    assert by_key["proving"]["status"] == tracer.NOT_RUN
    assert by_key["reflection"]["status"] == tracer.NOT_RUN


def test_no_stage_is_ever_reported_as_run_without_an_audit_hash(tmp_path):
    path = _audit(tmp_path, [{"action": "plan", "details": "d",
                              "timestamp": "2026-08-23T10:00:00+00:00"}])

    for stage in tracer.trace(audit_path=path)["stages"]:
        if stage["status"] == "RECORDED":
            assert stage["audit_hash"], f"{stage['key']} claims it ran with no audit link"


def test_the_timeline_is_chronological(tmp_path):
    path = _audit(tmp_path, [
        {"action": "apply", "details": "d", "timestamp": "2026-08-23T12:00:00+00:00"},
        {"action": "synthesize", "details": "d", "timestamp": "2026-08-23T10:00:00+00:00"},
        {"action": "plan", "details": "d", "timestamp": "2026-08-23T11:00:00+00:00"},
    ])

    ran = [s for s in tracer.trace(audit_path=path)["stages"] if s["status"] == "RECORDED"]
    stamps = [s["at"] for s in ran]

    assert stamps == sorted(stamps), "a relay timeline out of order is not a timeline"


def test_the_most_recent_entry_wins_for_a_repeated_stage(tmp_path):
    """A run planned three times shows the plan that stands, not the first attempt."""
    path = _audit(tmp_path, [
        {"action": "plan", "details": "first", "timestamp": "2026-08-23T10:00:00+00:00"},
        {"action": "plan", "details": "latest", "timestamp": "2026-08-23T12:00:00+00:00"},
    ])

    by_key = {s["key"]: s for s in tracer.trace(audit_path=path)["stages"]}
    assert by_key["plan"]["details"] == "latest"


# --- Fail-open: the trace is advisory ---------------------------------------------------

def test_a_missing_audit_file_yields_an_empty_trace_not_a_crash(tmp_path):
    result = tracer.trace(audit_path=str(tmp_path / "nope.jsonl"))

    assert all(s["status"] == tracer.NOT_RUN for s in result["stages"])
    assert result["evidence_available"] is False


def test_a_corrupt_line_does_not_discard_the_rest_of_the_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text('{"action": "synthesize", "timestamp": "2026-08-23T10:00:00+00:00", '
                    '"entry_hash": "a"}\n'
                    "{not json at all\n"
                    '{"action": "plan", "timestamp": "2026-08-23T11:00:00+00:00", '
                    '"entry_hash": "b"}\n', encoding="utf-8")

    by_key = {s["key"]: s for s in tracer.trace(audit_path=str(path))["stages"]}

    assert by_key["synthesis"]["status"] == "RECORDED"
    assert by_key["plan"]["status"] == "RECORDED"


# --- Artifacts --------------------------------------------------------------------------

def test_artifacts_are_reported_as_present_only_when_they_are_on_disk(tmp_path):
    run = tmp_path / "run"
    (run / "terraform").mkdir(parents=True)
    (run / "terraform" / "main.tf").write_text("resource {}", encoding="utf-8")
    path = _audit(tmp_path, [{"action": "synthesize", "details": "d",
                              "timestamp": "2026-08-23T10:00:00+00:00"}])

    by_key = {s["key"]: s for s in
              tracer.trace(run_root=str(run), audit_path=path)["stages"]}

    assert by_key["synthesis"]["artifact_present"] is True
    assert by_key["proving"]["artifact_present"] is False


# --- Live monitor -----------------------------------------------------------------------

def test_active_agents_is_empty_rather_than_invented_when_nothing_is_running(tmp_path):
    """There is no agent supervisor in this repo to ask. Reporting a plausible-looking
    running agent would be fabrication on a monitoring surface."""
    assert tracer.active_agents(str(tmp_path)) == []


# --- Invariants -------------------------------------------------------------------------

def test_the_module_imports_only_the_standard_library():
    import ast
    import sys
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "core", "governance", "agent_tracer.py")
    tree = ast.parse(open(path, encoding="utf-8").read())

    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    assert roots <= set(sys.stdlib_module_names), f"non-stdlib imports: {sorted(roots)}"
