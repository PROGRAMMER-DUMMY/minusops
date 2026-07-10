"""
Real-behavior tests for dispatcher.py (2026-07-06, Item 4 follow-up): the natural-language
intent router sits directly in the live operator path but had zero dedicated test coverage.
These exercise real keyword classification, the resolve_intent short-circuit for
requirements/blueprint-shaped queries, the dir_flag requirement for DEPLOY/OPTIMIZE, and real
subprocess dispatch (mocked at the subprocess boundary, not the dispatcher's own logic).
"""
import os

import pytest

import dispatcher


def test_classify_intent_picks_health_from_keywords():
    assert dispatcher.classify_intent("is everything running fine?") == "HEALTH"


def test_classify_intent_picks_deploy_from_keywords():
    assert dispatcher.classify_intent("please deploy this infrastructure") == "DEPLOY"


def test_classify_intent_picks_budget_from_multiword_keyword():
    # "how much will" is a multi-word keyword in BUDGET's list -- proves substring matching
    # (not just single-word \b matches) actually works.
    assert dispatcher.classify_intent("how much will this pipeline cost per month") == "BUDGET"


def test_classify_intent_picks_finops_over_budget_when_more_specific():
    assert dispatcher.classify_intent("why did our spend spike last week, root cause please") == "FINOPS"


def test_classify_intent_returns_unknown_for_meaningless_query(monkeypatch):
    monkeypatch.setattr(dispatcher, "resolve_intent", None)
    assert dispatcher.classify_intent("asdkjhasdkjh qqq zzz") == "UNKNOWN"


def test_classify_intent_defers_to_resolve_intent_for_requirements(monkeypatch):
    """A build/create request must route to the requirements-first resolver, not get
    keyword-scored against the ops intents (HEALTH/DEPLOY/OPTIMIZE/BUDGET/FINOPS)."""
    monkeypatch.setattr(dispatcher, "resolve_intent",
                        lambda q: {"intent": "REQUIREMENTS", "note": "needs grill-me"})
    assert dispatcher.classify_intent("build me a data pipeline") == "REQUIREMENTS"


def test_dispatch_requirements_prints_resolution_and_succeeds(monkeypatch, capsys):
    monkeypatch.setattr(dispatcher, "resolve_intent", lambda q: {"intent": "REQUIREMENTS", "q": q})
    monkeypatch.setattr(dispatcher, "format_resolution", lambda r: f"resolved: {r['q']}")
    assert dispatcher.dispatch_task("REQUIREMENTS", "build me a thing") is True
    assert "resolved: build me a thing" in capsys.readouterr().out


def test_dispatch_ask_clarification_is_not_a_success_outcome(monkeypatch, capsys):
    """ASK_CLARIFICATION means the resolver needs more from the user -- it printed its
    question, but that's not the same as successfully dispatching a task."""
    monkeypatch.setattr(dispatcher, "resolve_intent", lambda q: {"intent": "ASK_CLARIFICATION"})
    monkeypatch.setattr(dispatcher, "format_resolution", lambda r: "which cloud?")
    assert dispatcher.dispatch_task("ASK_CLARIFICATION", "build me a thing") is False


def test_dispatch_deploy_without_target_dir_is_refused(monkeypatch, capsys):
    """DEPLOY needs an explicit --dir (workload-agnostic engine, no bundled default) --
    must refuse cleanly rather than dispatching against nothing."""
    ran = []
    monkeypatch.setattr(dispatcher.subprocess, "run", lambda *a, **k: ran.append(a))
    assert dispatcher.dispatch_task("DEPLOY", "deploy this", target_dir=None) is False
    assert "needs a target directory" in capsys.readouterr().err
    assert ran == []  # never got as far as invoking a subprocess


def test_dispatch_health_invokes_the_real_script_path(monkeypatch, capsys):
    """HEALTH has no dir_flag and lives alongside dispatcher.py in core/reporting/ -- confirms
    the actual constructed command line, not just that *some* subprocess call happened."""
    captured = {}

    class FakeCompleted:
        returncode = 0

    def fake_run(cmd, check):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)
    assert dispatcher.dispatch_task("HEALTH", "check health") is True
    assert captured["cmd"][0] == dispatcher.sys.executable
    assert captured["cmd"][1].endswith("health_checker.py")
    assert os.path.exists(captured["cmd"][1]), "the resolved script path must actually exist on disk"


def test_dispatch_deploy_resolves_cross_subpackage_script_path(monkeypatch):
    """DEPLOY's script lives in core/governance/, not alongside dispatcher.py in
    core/reporting/ -- this is exactly the path that broke silently during the core/
    restructure until the ../governance/plan_gate.py relative path was added."""
    captured = {}

    class FakeCompleted:
        returncode = 0

    def fake_run(cmd, check):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)
    assert dispatcher.dispatch_task("DEPLOY", "deploy it", target_dir="runs/x/terraform") is True
    assert captured["cmd"][1].endswith(os.path.join("governance", "plan_gate.py"))
    assert os.path.exists(captured["cmd"][1])
    assert "--dir" in captured["cmd"]
    assert "runs/x/terraform" in captured["cmd"]


def test_dispatch_reports_missing_script_without_crashing(monkeypatch, capsys):
    monkeypatch.setitem(dispatcher.INTENT_MAPPING, "HEALTH",
                        {**dispatcher.INTENT_MAPPING["HEALTH"], "script": "does_not_exist.py"})
    assert dispatcher.dispatch_task("HEALTH", "check health") is False
    assert "Script not found" in capsys.readouterr().err


def test_dispatch_returns_false_on_subprocess_failure(monkeypatch, capsys):
    import subprocess as real_subprocess

    def fake_run(cmd, check):
        raise real_subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)
    assert dispatcher.dispatch_task("HEALTH", "check health") is False
    assert "execution error" in capsys.readouterr().err
