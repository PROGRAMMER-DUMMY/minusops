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
    """Kept as a cheap guard; the REAL wiring assertions are the FR-05 block at the end of
    this file. This one only ever proved the word appeared in the file, and it passed for
    weeks while the console made zero calls to the reconciler."""
    import re
    source = open(os.path.join(ROOT, "app", "console_app.py"), encoding="utf-8").read()
    body = re.sub(r'"""(?:.|\n)*?"""', "", source)

    assert "reconciler.propose(" in body, "the console never asks for a proposal"
    assert "reconciler.confirm(" in body, "the console never applies one"


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


# ---------------------------------------------------------------------------------------
# FR-05: the reconciliation loop is WIRED, not merely imported.
#
# The test this replaces was `assert "main.tf" not in source or "reconciler" in source`.
# It passed on the module docstring, which mentions the reconciler, while the console made
# zero calls to it -- so the console reported AC-01 satisfied on the strength of having no
# canvas edit path at all. These assert against the callback registry and the rendered
# modal instead, neither of which a comment can satisfy.
# ---------------------------------------------------------------------------------------

RECONCILE_CHANGE = {
    "target": "aws_glue_job.etl",
    "attribute": "--source_path",
    "from": "module.storage.gold_bucket_arn",
    "to": "module.storage.bronze_bucket_arn",
}

HCL_FIXTURE = """resource "aws_glue_job" "etl" {
  default_arguments = {
    "--source_path" = module.storage.gold_bucket_arn
  }
}
"""


@pytest.fixture
def reconcile_run(tmp_path, monkeypatch):
    """A run on disk, with `assemble` pointed at it.

    The callbacks take a run ID and resolve it through the runs index, which is correct for
    production -- the store holds an id, not a path. Stubbing the resolver keeps these tests
    about the reconciliation logic rather than about run lookup, which runs.py already owns.
    """
    root = tmp_path / "runs" / "acme"
    (root / "terraform").mkdir(parents=True)
    (root / "terraform" / "main.tf").write_text(HCL_FIXTURE, encoding="utf-8")
    (root / "architecture_decision.json").write_text(
        json.dumps({"architecture": "lakehouse", "selected_modules": []}), encoding="utf-8")
    monkeypatch.setattr(console_app, "assemble",
                        lambda _run_id=None: {"run": {"run_id": "acme"}, "root": str(root)})
    return str(root)


def test_the_reconcile_callbacks_are_registered_with_dash():
    """Registry, not source text. A docstring cannot put an entry in callback_map."""
    outputs = " ".join(console_app.app.callback_map.keys())

    assert "reconcile-modal" in outputs, "the review modal has no callback behind it"
    assert "reconcile-result" in outputs, "confirmation has no callback behind it"


def test_the_confirm_button_is_an_input_to_the_apply_callback():
    """The gate is the confirm click. If it is not an Input, the modal is decorative."""
    entry = next(v for k, v in console_app.app.callback_map.items() if "reconcile-result" in k)
    inputs = " ".join(str(i) for i in entry["inputs"])

    assert "reconcile-confirm" in inputs


def test_previewing_a_change_renders_the_four_things_the_modal_must_show(reconcile_run):
    modal, _stored = console_app.reconcile_preview(1, RECONCILE_CHANGE["target"],
                                                   RECONCILE_CHANGE["attribute"],
                                                   RECONCILE_CHANGE["from"],
                                                   RECONCILE_CHANGE["to"], reconcile_run)
    text = json.dumps(modal, default=str)

    assert "gold_bucket_arn" in text and "bronze_bucket_arn" in text   # plain-English diff
    assert "aws_glue_job.etl" in text                                   # what changed
    assert "revoked" in text.lower() or "stale" in text.lower()         # safety warning
    assert "---" in text and "+++" in text                              # unified HCL diff


def test_previewing_writes_absolutely_nothing(reconcile_run):
    """The whole safety property. Preview is a read."""
    tf = os.path.join(reconcile_run, "terraform", "main.tf")
    before = open(tf, encoding="utf-8").read()

    console_app.reconcile_preview(1, RECONCILE_CHANGE["target"], RECONCILE_CHANGE["attribute"],
                                  RECONCILE_CHANGE["from"], RECONCILE_CHANGE["to"], reconcile_run)

    assert open(tf, encoding="utf-8").read() == before


def test_a_change_that_matches_no_hcl_is_refused_in_the_modal(reconcile_run):
    modal, stored = console_app.reconcile_preview(1, "aws_glue_job.etl", "--source_path",
                                                  "module.storage.does_not_exist",
                                                  "module.storage.bronze_bucket_arn",
                                                  reconcile_run)

    assert stored is None, "an inapplicable change must not be stashed for confirmation"
    assert "refus" in json.dumps(modal, default=str).lower()


def test_cancelling_applies_nothing(reconcile_run):
    tf = os.path.join(reconcile_run, "terraform", "main.tf")
    before = open(tf, encoding="utf-8").read()
    _modal, stored = console_app.reconcile_preview(1, RECONCILE_CHANGE["target"],
                                                   RECONCILE_CHANGE["attribute"],
                                                   RECONCILE_CHANGE["from"],
                                                   RECONCILE_CHANGE["to"], reconcile_run)

    result, modal = console_app.reconcile_apply(0, 1, stored)

    assert open(tf, encoding="utf-8").read() == before
    assert "cancel" in json.dumps(result, default=str).lower()
    assert modal is None or modal == []


def test_confirming_rewrites_the_hcl_and_reports_the_next_command(reconcile_run, monkeypatch):
    monkeypatch.setattr(console_app.reconciler, "_gate_approval_dir", lambda _r: "")
    _modal, stored = console_app.reconcile_preview(1, RECONCILE_CHANGE["target"],
                                                   RECONCILE_CHANGE["attribute"],
                                                   RECONCILE_CHANGE["from"],
                                                   RECONCILE_CHANGE["to"], reconcile_run)

    result, _modal2 = console_app.reconcile_apply(1, 0, stored)
    text = open(os.path.join(reconcile_run, "terraform", "main.tf"), encoding="utf-8").read()

    assert "bronze_bucket_arn" in text and "gold_bucket_arn" not in text
    assert "minusctl gate plan" in json.dumps(result, default=str)


def test_the_browser_never_supplies_the_hcl_that_gets_written(reconcile_run, monkeypatch):
    """The store round-trips through the client, so it carries the change SPEC only. If it
    carried `updated_hcl`, a tampered payload would be written to main.tf verbatim -- the
    console would become an arbitrary-file-write endpoint wearing a governance modal."""
    _modal, stored = console_app.reconcile_preview(1, RECONCILE_CHANGE["target"],
                                                   RECONCILE_CHANGE["attribute"],
                                                   RECONCILE_CHANGE["from"],
                                                   RECONCILE_CHANGE["to"], reconcile_run)

    assert "updated_hcl" not in json.dumps(stored)
    assert "diff" not in json.dumps(stored)

    # And a tampered store must not smuggle HCL through confirm().
    monkeypatch.setattr(console_app.reconciler, "_gate_approval_dir", lambda _r: "")
    tampered = dict(stored)
    tampered["updated_hcl"] = "resource \"aws_iam_role\" \"backdoor\" {}"
    console_app.reconcile_apply(1, 0, tampered)
    written = open(os.path.join(reconcile_run, "terraform", "main.tf"), encoding="utf-8").read()

    assert "backdoor" not in written


# ---------------------------------------------------------------------------------------
# Priority 2: the partial UI features. Each of these was previously "the data is there, the
# interaction is not" -- which reads as done in a screenshot and is not.
# ---------------------------------------------------------------------------------------

PLAN_FIXTURE = {"resource_changes": [
    {"address": "module.storage.aws_s3_bucket.bronze", "type": "aws_s3_bucket",
     "mode": "managed", "change": {"actions": ["create"], "after": {}}},
    {"address": "module.compute.aws_glue_job.etl", "type": "aws_glue_job",
     "mode": "managed", "change": {"actions": ["create"], "after": {}}}]}


def _state(tmp_path, **over):
    base = {"run": {"run_id": "r1"}, "root": str(tmp_path), "plan": PLAN_FIXTURE,
            "decision": {}, "lineage": {"nodes": [], "edges": [], "masking": {}},
            "trace": {"stages": []}, "vault": {}, "documents": []}
    base.update(over)
    return base


# --- FR-02.1: the canvas is embedded, not just linked -----------------------------------

def test_the_topology_view_embeds_a_viewer_and_not_only_an_external_link(tmp_path):
    rendered = json.dumps(console_app.view_topology(_state(tmp_path)), default=str)

    assert "Iframe" in rendered, "FR-02.1 asks for an embedded canvas, not a link alone"
    assert "viewer.diagrams.net" in rendered or "app.diagrams.net" in rendered


def test_the_embedded_viewer_and_the_button_point_at_the_same_diagram(tmp_path):
    """Two encodings of the same plan that drift is worse than one: the operator reviews
    the embed and opens the link, and they would be looking at different architectures."""
    import drawio_generator
    bundle = drawio_generator.generate_drawio_from_plan(PLAN_FIXTURE,
                                                        title="Architecture Blueprint")
    rendered = json.dumps(console_app.view_topology(_state(tmp_path)), default=str)

    payload = bundle["url"].split("#R", 1)[1][:60]
    assert payload in rendered


# --- FR-03.3: click-to-inspect on a lineage node ----------------------------------------

def test_lineage_nodes_are_clickable_and_the_sidebar_callback_is_registered():
    outputs = " ".join(console_app.app.callback_map.keys())

    assert "lineage-inspector" in outputs, "click-to-inspect has no callback behind it"


def test_inspecting_a_node_returns_the_facts_the_prd_asks_for():
    graph = {"nodes": [{"id": "gold", "label": "S3 Gold", "layer": "gold",
                        "table_format": "Apache Iceberg v2", "partitioning": "event_date",
                        "retention": "vacuum expired snapshots"}],
             "edges": [], "masking": {}}

    panel = json.dumps(console_app.inspect_lineage_node("gold", graph), default=str)

    assert "Apache Iceberg v2" in panel      # table format
    assert "event_date" in panel             # partitioning
    assert "vacuum" in panel                 # retention lifecycle


def test_inspecting_an_unknown_node_says_so_rather_than_rendering_blanks():
    panel = json.dumps(console_app.inspect_lineage_node("nope", {"nodes": []}), default=str)

    assert "select" in panel.lower() or "not found" in panel.lower()


# --- FR-06.1 / FR-06.2: preview and a download the browser can actually perform ---------

def test_the_vault_exposes_a_download_route_the_browser_can_reach():
    """The export previously wrote a zip server-side and told the operator a path on the
    SERVER. For anyone not sitting at that machine that is not an export."""
    rules = [str(r) for r in console_app.app.server.url_map.iter_rules()]

    assert any("vault" in r and "download" in r for r in rules), rules


def test_the_download_route_refuses_a_path_outside_the_run(tmp_path, monkeypatch):
    """The route takes a document name. Without a guard, `..` walks out of the run and the
    console serves arbitrary files off the host."""
    monkeypatch.setattr(console_app, "assemble",
                        lambda _r=None: {"run": {"run_id": "r"}, "root": str(tmp_path)})
    client = console_app.app.server.test_client()

    resp = client.get("/runs/r/vault/download/../../../../etc/passwd")

    assert resp.status_code in (400, 404), resp.status_code


def test_previewable_documents_render_in_a_viewer(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "report.html").write_text("<h1>x</h1>", encoding="utf-8")
    import vault
    state = _state(tmp_path, documents=vault.catalog(str(tmp_path)),
                   vault=vault.summary(str(tmp_path)))

    rendered = json.dumps(console_app.view_vault(state), default=str)

    assert "vault-preview" in rendered, "FR-06.1 asks for an in-browser previewer"
