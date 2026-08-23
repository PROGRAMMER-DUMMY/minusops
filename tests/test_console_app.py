"""
The visual governance console (PRD v13 FR-01, AC-01/04/07).

What is worth pinning here is not that the page renders -- Dash will render almost
anything -- but that the console stays a READER. Its one dangerous capability is the canvas
edit, and the property that makes it safe is that the view layer cannot write HCL on its
own: it can only ask `reconciler.propose()` for a diff and `reconciler.confirm()` to apply
one, and confirm refuses anything that is not literally True.

The other tests are about honesty: four views must exist because the PRD promises four, and
the vault export must refuse an empty run rather than hand an auditor a zip full of nothing.

Depends on: app/console_app.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import console_app  # noqa: E402


# --- The four views (FR-01.1) -----------------------------------------------------------

def test_exactly_the_four_declared_views_are_registered():
    keys = [key for key, _label in console_app.VIEWS]

    assert keys == ["topology", "lineage", "trace", "vault"]
    assert set(console_app.RENDERERS) == set(keys), "a view with no renderer is a blank tab"


def test_every_view_renders_without_a_run_rather_than_raising():
    """A fresh install has no runs. The console must say so, not stack-trace."""
    state = {"run": None}
    for key in console_app.RENDERERS:
        assert console_app.RENDERERS[key]({"run": None, "root": "", "plan": {},
                                           "lineage": {"nodes": [], "edges": [], "masking": {}},
                                           "trace": {"stages": []}, "vault": {},
                                           "documents": []}) is not None


def test_the_run_header_reports_absent_facts_as_absent(tmp_path):
    """FR-01.2. An unpriced run must not show a dollar figure and an untiered one must not
    show a tier -- this header is the governance summary."""
    header = console_app.run_header({"run": {"run_id": "r1"}, "plan": {}})
    text = json.dumps(header, default=str)

    assert "not priced" in text
    assert "UNCLASSIFIED" in text
    assert "not planned" in text


# --- The console is a reader (PRD v13 invariant 2) --------------------------------------

def test_the_console_never_shells_out():
    """Invariant 2: no direct cloud mutations from the UI. The cheapest way to keep that
    true is for this module to have no way to run anything."""
    source = open(os.path.join(ROOT, "app", "console_app.py"), encoding="utf-8").read()

    for forbidden in ("subprocess", "os.system", "popen", "boto3"):
        assert forbidden not in source, f"console imports {forbidden}"


def test_the_console_writes_nothing_except_through_the_vault_bundle():
    """Every other write path in the console would be an ungoverned edit."""
    source = open(os.path.join(ROOT, "app", "console_app.py"), encoding="utf-8").read()
    writes = re.findall(r'open\([^)]*["\'](?:w|a)[b+]?["\']', source)

    assert not writes, f"console_app writes directly: {writes}"


def test_hcl_edits_can_only_travel_through_the_reconciler():
    source = open(os.path.join(ROOT, "app", "console_app.py"), encoding="utf-8").read()

    assert "main.tf" not in source or "reconciler" in source
    assert "reconciler" in source, "the canvas edit path must route through the reconciler"


# --- The vault export refuses rather than misleads --------------------------------------

def test_the_bundle_callback_reports_a_refusal_instead_of_claiming_success(tmp_path):
    import vault
    result = vault.bundle(str(tmp_path / "empty"), str(tmp_path / "out.zip"))

    assert result["ok"] is False
    assert not os.path.exists(str(tmp_path / "out.zip"))


# --- Zero-emoji doctrine (AC-07) --------------------------------------------------------

@pytest.mark.parametrize("relative", [
    os.path.join("app", "console_app.py"),
    os.path.join("core", "architecture", "reconciler.py"),
    os.path.join("core", "reporting", "lineage_graph.py"),
    os.path.join("core", "reporting", "vault.py"),
    os.path.join("core", "governance", "agent_tracer.py"),
    os.path.join("core", "cli", "commands", "console.py"),
])
def test_v13_modules_carry_no_emoji(relative):
    text = open(os.path.join(ROOT, relative), encoding="utf-8").read()

    assert not re.search("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]", text)


# --- The legacy dashboard is deprecated (FR-01) -----------------------------------------

def test_the_legacy_dashboard_says_it_is_superseded():
    """Deleting it outright would break `minusctl` links and 9 existing tests in one step.
    It stays, carrying a deprecation notice that points at the console."""
    path = os.path.join(ROOT, "app", "dashboard_app.py")
    if not os.path.exists(path):
        pytest.skip("legacy dashboard already removed")
    head = open(path, encoding="utf-8").read()[:3000]

    assert "DEPRECATED" in head
    assert "console_app" in head


def test_the_console_command_is_registered_with_its_aliases():
    from cli import main as cli_main
    from cli.commands import console as console_cmd

    assert "console" in cli_main.known_commands()
    assert cli_main.NATIVE["console"] is console_cmd
    assert set(console_cmd.ALIASES) == {"ui", "dashboard"}

    # Registration in NATIVE is NOT enough, and asserting only that is how this shipped
    # broken: `console` appeared in --help but build_parser never gave it a subparser, so
    # `minusctl console` died with "invalid choice". Parse a real invocation instead.
    args = cli_main.build_parser().parse_args(["console", "--run", "some-run"])
    assert args.command == "console"
    assert args.run == "some-run"


def test_callbacks_targeting_on_demand_views_do_not_error_on_page_load():
    """The console renders views on demand, so the vault exporter is not in the initial
    layout. Dash validates callbacks against that first layout and raises "ID not found in
    layout" on load unless told the tree is dynamic. A structural test of the layout cannot
    see this -- it only appears in a browser, which is where it was found."""
    assert console_app.app.config.suppress_callback_exceptions is True


def test_the_console_reads_runs_through_the_real_runs_api():
    """This caught a live bug: `_run_record` called `runs.load_index()`, which does not
    exist, inside a blanket `except Exception`. The console rendered "No runs found" over a
    workspace holding twenty-five runs, and every structural test still passed. A reader
    that is broken must not be indistinguishable from a workspace that is empty."""
    import runs as runs_engine

    for name in ("list_runs", "get_run", "latest_run"):
        assert hasattr(runs_engine, name), f"console depends on runs.{name}"

    # The CALL, not the word: the docstring above names the dead function to explain the
    # bug, and matching bare text would flag its own explanation.
    source = open(os.path.join(ROOT, "app", "console_app.py"), encoding="utf-8").read()
    assert "load_index(" not in source


def test_the_topology_view_renders_for_a_run_that_actually_has_a_plan(tmp_path):
    """The regression this pins: the view called encode_drawio_url() on the BUNDLE that
    generate_drawio_from_plan() returns, which is a dict. It raised AttributeError on every
    run with a plan, and no test saw it because the fixture run had no plan.json -- so every
    assertion ran down the "No plan analyzed" branch instead."""
    state = {
        "run": {"run_id": "r1"}, "root": str(tmp_path),
        "plan": {"resource_changes": [
            {"address": "module.storage.aws_s3_bucket.bronze", "type": "aws_s3_bucket",
             "mode": "managed",
             "change": {"actions": ["create"], "after": {"bucket": "acme-bronze"}}}]},
        "lineage": {"nodes": [], "edges": [], "masking": {}},
        "trace": {"stages": []}, "vault": {}, "documents": [],
    }

    rendered = console_app.view_topology(state)

    assert rendered is not None
    text = json.dumps(rendered, default=str)
    assert "app.diagrams.net" in text, "the 1-click editor link must be present"


def test_the_step_flow_ledger_renders_as_a_table_not_a_python_repr(tmp_path):
    """`generate_flow_ledger()` returns a list of dicts. The first version passed it to
    html.Pre, which would have printed `[{'hop': '[1]', ...}]` on the page."""
    state = {
        "run": {"run_id": "r1"}, "root": str(tmp_path),
        "plan": {"resource_changes": [
            {"address": "module.storage.aws_s3_bucket.bronze", "type": "aws_s3_bucket",
             "mode": "managed", "change": {"actions": ["create"], "after": {}}},
            {"address": "module.compute.aws_glue_job.etl", "type": "aws_glue_job",
             "mode": "managed", "change": {"actions": ["create"], "after": {}}}]},
        "lineage": {"nodes": [], "edges": [], "masking": {}},
        "trace": {"stages": []}, "vault": {}, "documents": [],
    }

    text = json.dumps(console_app.view_topology(state), default=str)

    assert "'hop':" not in text, "the ledger leaked a Python repr into the page"
