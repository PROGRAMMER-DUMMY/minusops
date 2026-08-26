"""
The unified `minusctl` package: context switching, spec cards, gate/cost/source wrappers
(PRD-ARCH-2026-007, FR-02/FR-03).

Before this, an operator drove MinusOps by typing `python core/governance/plan_gate.py plan
--dir runs/<id>/terraform`, which requires knowing both the repo layout and which run they
are on. `minusctl use <run>` moves that second fact into `.minus/context.json` so every
later command can default to it.

The tests that matter most here are the negative ones. A context file is a piece of state
that decides which infrastructure a later `gate apply` touches, so a stale, corrupt, or
attacker-supplied one must fail loudly rather than silently select a different stack.

Depends on: core/cli/{main,context,formatters}.py, core/cli/commands/
Shells out to: nothing (delegated implementations are monkeypatched)
Used by: nothing (pytest entry point)
"""
import io
import json
import os
from contextlib import redirect_stdout

import pytest

import runs
from cli import context as cli_context
from cli import formatters
from cli import main as cli_main


def _capture(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli_main.main(argv)
    return code, out.getvalue()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(cli_context, "WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- Context switching ----------------------------------------------------------------

def test_use_persists_the_active_run(workspace):
    run = runs.new_run(name="clickstream", domain="marketing", orchestrator="mwaa")

    code, _ = _capture(["use", run["run_id"]])

    assert code == 0
    saved = json.loads((workspace / ".minus" / "context.json").read_text(encoding="utf-8"))
    assert saved["active_run"] == run["run_id"]


def test_use_refuses_a_run_that_does_not_exist(workspace):
    """Storing an unresolvable id would make every later command fail with a confusing
    error far from the typo that caused it."""
    code, _ = _capture(["use", "no-such-run"])

    assert code != 0
    assert not (workspace / ".minus" / "context.json").exists()


def test_the_context_file_is_written_atomically(workspace, monkeypatch):
    """NFR-04. A half-written context.json is unparseable, and the recovery -- 'delete the
    file' -- is not obvious to anyone who did not write this code."""
    run = runs.new_run(name="clickstream", domain="marketing")
    seen = []
    real_replace = cli_context.os.replace
    monkeypatch.setattr(cli_context.os, "replace",
                        lambda src, dst: seen.append(str(dst)) or real_replace(src, dst))

    cli_context.set_active_run(run["run_id"])

    assert any(str(p).endswith("context.json") for p in seen)


def test_a_corrupt_context_file_is_not_silently_ignored(workspace):
    """Silently falling back to 'latest run' would point a gate apply at different
    infrastructure than the operator selected, with no message saying so."""
    runs.new_run(name="clickstream", domain="marketing")
    (workspace / ".minus").mkdir()
    (workspace / ".minus" / "context.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(cli_context.ContextError):
        cli_context.active_run()


def test_an_active_run_deleted_from_disk_is_reported_not_resolved(workspace):
    run = runs.new_run(name="clickstream", domain="marketing")
    cli_context.set_active_run(run["run_id"])
    import shutil
    shutil.rmtree(workspace / "runs" / run["run_id"])

    with pytest.raises(cli_context.ContextError):
        cli_context.active_run()


def test_no_context_means_no_active_run_not_a_guess(workspace):
    """Defaulting to the newest run would make `gate apply` target whatever was generated
    most recently, which is not the same thing as what the operator is working on."""
    runs.new_run(name="clickstream", domain="marketing")

    assert cli_context.active_run() is None


@pytest.mark.parametrize("hostile", ["../../etc/passwd", "runs/../../outside", "a/b"])
def test_a_traversing_run_id_is_refused(workspace, hostile):
    """The id is read back from a file on disk and joined into a path."""
    with pytest.raises(cli_context.ContextError):
        cli_context.set_active_run(hostile)


# --- Runs list / describe -------------------------------------------------------------

def test_runs_list_marks_the_active_run(workspace):
    first = runs.new_run(name="clickstream", domain="marketing")
    second = runs.new_run(name="ledger", domain="finance")
    cli_context.set_active_run(second["run_id"])

    code, output = _capture(["runs", "list"])

    active_line = next(l for l in output.splitlines() if second["run_id"] in l)
    other_line = next(l for l in output.splitlines() if first["run_id"] in l)
    assert code == 0
    assert "[*]" in active_line
    assert "[*]" not in other_line


def test_runs_describe_renders_every_section_of_the_spec_card(workspace):
    run = runs.new_run(name="clickstream", domain="marketing", orchestrator="mwaa",
                       owner="data-eng@acme.com")

    code, output = _capture(["runs", "describe", run["run_id"]])

    assert code == 0
    # PRD v6 FR-03 merges FinOps and Resource Endpoints into one section; the card renders
    # the four bracketed headings its example shows.
    for section in ("[Metadata]", "[Architecture Attributes]",
                    "[FinOps & Resource Endpoints]", "[Artifact Paths]"):
        assert section in output


def test_runs_describe_defaults_to_the_active_run(workspace):
    run = runs.new_run(name="clickstream", domain="marketing")
    cli_context.set_active_run(run["run_id"])

    code, output = _capture(["runs", "describe"])

    assert code == 0
    assert run["run_id"] in output


def test_describe_reports_an_unpriced_run_as_unpriced(workspace):
    """NFR-03. The card must not render a missing BCM estimate as $0.00."""
    run = runs.new_run(name="clickstream", domain="marketing")

    _, output = _capture(["runs", "describe", run["run_id"]])

    assert "$0.00" not in output
    assert "unpriced" in output.lower()


# --- Commands default to the active run -----------------------------------------------

def test_gate_plan_resolves_the_active_run_without_a_dir_flag(workspace, monkeypatch):
    """AC-04. `--dir runs/<id>/terraform` is the single most-typed and most-mistyped
    argument in the whole tool."""
    run = runs.new_run(name="clickstream", domain="marketing")
    cli_context.set_active_run(run["run_id"])
    seen = {}

    def _spy(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli_main.gate, "_delegate", _spy)

    code, _ = _capture(["gate", "plan"])

    assert code == 0
    assert "--dir" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--dir") + 1] == run["terraform_dir"]


def test_gate_refuses_when_there_is_no_active_run_and_no_dir(workspace):
    """Guessing a directory here would run a plan against infrastructure the operator did
    not name."""
    code, _ = _capture(["gate", "plan"])

    assert code != 0


def test_an_explicit_dir_beats_the_active_run(workspace, monkeypatch):
    run = runs.new_run(name="clickstream", domain="marketing")
    cli_context.set_active_run(run["run_id"])
    seen = {}

    def _spy(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli_main.gate, "_delegate", _spy)

    _capture(["gate", "plan", "--dir", "/somewhere/else"])

    assert seen["argv"][seen["argv"].index("--dir") + 1] == "/somewhere/else"


def test_gate_passes_the_stage_through_unchanged(workspace, monkeypatch):
    """verify, plan, approve and apply are the deploy gate's own vocabulary. Renaming or
    reordering them here would let this wrapper change what is enforced."""
    run = runs.new_run(name="clickstream", domain="marketing")
    cli_context.set_active_run(run["run_id"])
    seen = []

    def _spy(argv):
        seen.append(argv)
        return 0

    monkeypatch.setattr(cli_main.gate, "_delegate", _spy)

    for stage in ("verify", "plan", "approve", "apply"):
        _capture(["gate", stage])

    assert [argv[0] for argv in seen] == ["verify", "plan", "approve", "apply"]


def test_cost_estimate_targets_the_active_runs_report_dir(workspace, monkeypatch):
    run = runs.new_run(name="clickstream", domain="marketing")
    cli_context.set_active_run(run["run_id"])
    seen = {}

    def _spy(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli_main.cost, "_delegate", _spy)

    code, _ = _capture(["cost", "estimate"])

    assert code == 0
    assert seen["argv"][seen["argv"].index("--report-dir") + 1] == run["reports_dir"]


def test_source_status_targets_the_active_runs_terraform_dir(workspace, monkeypatch):
    run = runs.new_run(name="clickstream", domain="marketing")
    cli_context.set_active_run(run["run_id"])
    seen = {}

    def _spy(tf_dir):
        seen["dir"] = tf_dir
        return {"status": "clean"}

    monkeypatch.setattr(cli_main.source, "_status", _spy)

    code, _ = _capture(["source", "status"])

    assert code == 0
    assert seen["dir"] == run["terraform_dir"]


# --- The package contract -------------------------------------------------------------

def test_every_legacy_subcommand_still_resolves(workspace, monkeypatch):
    """19 subcommands existed before this package. Dropping one silently would break a
    documented lifecycle step and whatever automation already calls it."""
    legacy = ("create", "policy", "runs", "guard", "reports", "next", "package",
              "readiness", "conformance", "validate", "decision", "accelerator",
              "prove", "audit", "demo", "doctor", "adopt", "seed", "export")

    known = cli_main.known_commands()

    missing = [name for name in legacy if name not in known]
    assert not missing, f"subcommands lost in the refactor: {missing}"


def test_the_new_commands_are_registered():
    known = cli_main.known_commands()

    for name in ("use", "gate", "cost", "source"):
        assert name in known


def test_help_output_carries_no_emoji():
    """NFR-01."""
    out = io.StringIO()
    with redirect_stdout(out):
        with pytest.raises(SystemExit):
            cli_main.main(["--help"])

    assert all(ord(ch) < 0x2190 for ch in out.getvalue())


def test_the_cli_core_imports_nothing_outside_the_standard_library():
    """NFR-02. The base install has no runtime dependencies; the front door must not be the
    thing that adds one."""
    import ast
    import pathlib

    allowed_first_party = {"cli", "runs", "minusctl", "context", "formatters", "seed",
                           "source_guard", "export", "plan_gate", "bcm_pricing_calculator",
                           "toolpath", "audit_chain"}
    third_party = {"dash", "plotly", "yaml", "requests", "boto3", "pandas", "jinja2",
                   "great_expectations"}
    root = pathlib.Path(cli_main.__file__).parent
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            for name in names:
                assert name not in third_party, f"{path.name} imports {name}"


# --- formatters -----------------------------------------------------------------------

def test_the_ascii_table_aligns_columns_and_uses_no_box_drawing():
    """A terminal that cannot render box-drawing characters turns a table into noise, and
    these outputs get pasted into tickets and chat."""
    text = formatters.table(["RUN", "DOMAIN"],
                            [["short", "marketing"], ["a-much-longer-run-id", "finance"]])

    lines = text.splitlines()
    assert all(ord(ch) < 0x2190 for ch in text)
    assert len({len(line.rstrip()) for line in lines if line.strip()}) <= 2


def test_the_table_renders_a_header_even_with_no_rows():
    """An empty result is a result. A blank string reads as a crash."""
    text = formatters.table(["RUN", "DOMAIN"], [])

    assert "RUN" in text


def test_a_none_cell_renders_as_a_dash_not_the_word_none(workspace):
    text = formatters.table(["A"], [[None]])

    assert "None" not in text


def test_the_gate_front_door_forwards_every_flag_plan_gate_accepts():
    """`--impact` shipped on plan_gate and was unreachable through `minusctl gate`.

    The tests passed because they called stage_plan() directly; the CLI wrapper had never
    been updated, so the documented front door rejected the flag with "unrecognized
    arguments". Found by running the real binary, not by running the suite.

    This asserts the general case rather than that one flag: every option plan_gate's own
    parser accepts must be reachable through the wrapper, or the front door silently offers
    less than the engine does.
    """
    import argparse

    import plan_gate
    from core.cli.commands import gate as gate_cmd

    def _options(parser):
        return {a for action in parser._actions for a in action.option_strings
                if a.startswith("--")}

    engine = _options(plan_gate._build_parser())

    front = argparse.ArgumentParser()
    gate_cmd.add_parser(front.add_subparsers(dest="command"))
    wrapper = _options(front._subparsers._group_actions[0].choices["gate"])

    # --run is CLI-only: it resolves the active run into --dir for the engine.
    missing = engine - wrapper - {"--help"}
    assert not missing, f"minusctl gate cannot pass these through to plan_gate: {sorted(missing)}"
