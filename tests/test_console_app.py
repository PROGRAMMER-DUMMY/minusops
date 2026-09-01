"""
The visual governance console.

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


# --- The four views ---------------------------------------------------------------------

def test_every_connector_handler_defines_the_background_it_styles_with(monkeypatch):
    """Four connector handlers set `bg` from the result; `_handle_outlook` was copy-pasted
    without that line and references it anyway, so testing an Outlook endpoint raises
    NameError instead of reporting the result. No test drove any of these callbacks."""
    class _Ctx:
        triggered = [{"prop_id": "btn-test-outlook.n_clicks"}]

    monkeypatch.setattr(console_app.dash, "callback_context", _Ctx())
    monkeypatch.setattr(console_app.connector_config, "save_connector_config",
                        lambda *a, **k: None)
    monkeypatch.setattr(console_app.connector_config, "test_connector",
                        lambda name: {"ok": False, "status": "NOT_CONFIGURED",
                                      "detail": "no endpoint"})

    rendered = console_app._handle_outlook(1, 0, "x@example.com", "https://example.com/hook")

    assert rendered is not None
    assert "background" in rendered.style
    assert rendered.style["background"]


def test_every_navigable_view_has_a_renderer():
    """PRD v13 declared four views; Flow absorbed lineage and delivery, and Access and Cost
    were added, so the bar carries six. Settings is reachable but deliberately NOT numbered
    -- it is workspace-scoped, not run-scoped."""
    keys = [key for key, _number, _label in console_app.VIEW_LABELS]

    assert keys == ["topology", "flow", "access", "cost", "trace", "evidence"]
    for key in keys + ["settings"]:
        assert key in console_app.RENDERERS, f"{key} is a nav item with no renderer"


def test_every_view_renders_without_a_run_rather_than_raising():
    """A fresh install has no runs. The console must say so, not stack-trace."""
    state = {"run": None}
    for key in console_app.RENDERERS:
        assert console_app.RENDERERS[key]({"run": None, "root": "", "plan": {},
                                           "lineage": {"nodes": [], "edges": [], "masking": {}},
                                           "trace": {"stages": []}, "vault": {},
                                           "documents": []}) is not None


def test_the_run_band_reports_absent_facts_as_absent(tmp_path):
    """FR-01.2. An unpriced run must not show a dollar figure and an untiered one must not
    show a tier -- this band is the governance summary, and a blank cell reads as zero."""
    band = console_app.run_band({"run": {"run_id": "r1"}, "plan": {}, "vault": {}})
    text = json.dumps(band, default=str)

    assert "not priced" in text
    assert "unclassified" in text
    assert "not planned" in text
    assert "absent" in text, "absent facts must carry the absence mark, not render blank"


# --- The console is a reader ------------------------------------------------------------

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


# --- Zero-emoji doctrine ----------------------------------------------------------------

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


def test_the_console_command_is_registered_with_its_aliases():
    from core.cli import main as cli_main
    from core.cli.commands import console as console_cmd

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
    assert "embed.diagrams.net" in text, "the editable canvas must be present"
    assert "mxGraphModel" in text, "the generated diagram never reached the page"


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
# The reconciliation loop is WIRED, not merely imported.
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


def test_the_canvas_review_and_confirm_callbacks_are_registered_with_dash():
    """Registry, not source text. A docstring cannot put an entry in callback_map."""
    inputs = " ".join(str(v) for v in console_app.app.callback_map.values())

    assert "canvas-review" in inputs, "the review modal has no callback behind it"
    assert "canvas-confirm" in inputs, "confirmation has no callback behind it"


PLAN_FIXTURE = {
    "resource_changes": [
        {"address": "module.storage.aws_s3_bucket.bronze", "type": "aws_s3_bucket",
         "mode": "managed", "change": {"actions": ["create"], "after": {}}},
        {"address": "module.compute.aws_glue_job.etl", "type": "aws_glue_job",
         "mode": "managed", "change": {"actions": ["create"], "after": {}}}],
    # Without `configuration` the generator draws no edges at all -- correctly, since
    # nothing declares one -- so a fixture used to test edge EDITS has to declare a
    # reference for there to be an edge to edit.
    "configuration": {"root_module": {"module_calls": {
        "storage": {"expressions": {}},
        "compute": {"expressions": {"source": {
            "references": ["module.storage.bronze_arn", "module.storage"]}}},
    }}},
}


def _state(tmp_path, **over):
    base = {"run": {"run_id": "r1"}, "root": str(tmp_path), "plan": PLAN_FIXTURE,
            "decision": {}, "lineage": {"nodes": [], "edges": [], "masking": {}},
            "trace": {"stages": []}, "vault": {}, "documents": []}
    base.update(over)
    return base


def test_the_browser_never_supplies_what_gets_written(reconcile_run, monkeypatch):
    """The console-level property the engine tests cannot cover.

    What crosses the Store boundary is a DIAGRAM, never HCL. The original diagram is
    regenerated server-side from the plan and the replacement HCL is computed by the
    reconciler from main.tf on disk, so a tampered payload can at worst describe a change
    that does not match the file -- which propose() refuses.
    """
    source = open(os.path.join(ROOT, "app", "console_app.py"), encoding="utf-8").read()

    assert "updated_hcl" not in source.split("def _confirm_canvas")[1],         "the confirm path handles raw HCL from the browser"
    assert "generate_drawio_from_plan" in source.split("def _confirm_canvas")[1],         "the original diagram is taken from the browser rather than regenerated"


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


def test_a_present_document_opens_in_the_reader(tmp_path):
    """FR-06.1. The side pane became a full-screen reader: a governance document is the
    thing you came to read, so it gets the screen rather than a 440px column."""
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "report.html").write_text("<h1>x</h1>", encoding="utf-8")
    import vault
    state = _state(tmp_path, documents=vault.catalog(str(tmp_path)),
                   vault=vault.summary(str(tmp_path)))

    rendered = json.dumps(console_app.view_vault(state), default=str)
    assert "report.html" in rendered
    assert "'doc'" in rendered or '"doc"' in rendered, "the name is not a control"

    outputs = " ".join(console_app.app.callback_map.keys())
    assert "sheet-body.children" in outputs, "nothing renders into the reader"


def test_the_evidence_list_says_which_section_each_document_prints():
    """The list was a pile of filenames. Naming the section turns it into a map of the
    console, and makes a missing document read as a section with no export."""
    assert console_app._DOCUMENT_SECTION["cost.pdf"] == "04 Cost"
    assert console_app._DOCUMENT_SECTION["inspect.pdf"] == "03 Access"


def test_a_document_with_no_browser_reader_says_so_rather_than_embedding_junk(tmp_path):
    """A workbook rendered as text is mojibake, which a reviewer reads as a corrupt file."""
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "executive_project_summary.xlsx").write_bytes(b"PKjunk")
    import vault
    documents = vault.catalog(str(tmp_path))
    assert any(d["name"] == "executive_project_summary.xlsx" and d["present"]
               for d in documents)


def test_the_console_opens_on_the_newest_run_not_the_oldest(monkeypatch):
    """`list_runs()` returns newest-first, and the console indexed it with [-1].

    That is the OLDEST run in the workspace. An operator who opens the console after a run
    finishes is shown the first run they ever made -- which in a workspace of twenty-five
    synthesized runs is an empty one, so every view reads "not planned" / "0 of 15
    documents" and the console looks broken when the data is fine.
    """
    newest = {"run_id": "20260823-newest", "created_at": "2026-08-23T10:00:00"}
    oldest = {"run_id": "20260101-oldest", "created_at": "2026-01-01T10:00:00"}
    monkeypatch.setattr(console_app.runs_engine, "list_runs", lambda: [newest, oldest])
    monkeypatch.setattr(console_app.runs_engine, "latest_run", lambda: newest)

    assert console_app._run_record()["run_id"] == "20260823-newest"


# ---------------------------------------------------------------------------------------
# Bind and auth guard. `app/dashboard_app.py` carried this and the console did not, so
# retiring the old app without porting it would have quietly removed the only thing
# standing between a `--host 0.0.0.0` typo and live AWS cost and governance data on the LAN.
# ---------------------------------------------------------------------------------------

def test_loopback_hosts_are_recognised():
    for host in ("", "localhost", "127.0.0.1", "127.0.1.1", "::1"):
        assert console_app._is_loopback_host(host), host
    for host in ("0.0.0.0", "10.0.0.5", "192.168.1.9"):
        assert not console_app._is_loopback_host(host), host


def test_a_remote_bind_without_a_token_is_refused(monkeypatch):
    monkeypatch.delenv("MINUS_DASH_TOKEN", raising=False)
    monkeypatch.delenv("DASH_TOKEN", raising=False)

    assert console_app._remote_bind_requires_token("0.0.0.0")
    assert console_app._remote_bind_requires_token("10.0.0.5")
    assert not console_app._remote_bind_requires_token("127.0.0.1")


def test_a_remote_bind_with_a_token_is_allowed(monkeypatch):
    monkeypatch.setenv("MINUS_DASH_TOKEN", "secret-token")

    assert not console_app._remote_bind_requires_token("0.0.0.0")


def test_main_refuses_to_serve_remotely_without_a_token(monkeypatch):
    """The guard that matters is at bind time: nothing should listen at all."""
    monkeypatch.delenv("MINUS_DASH_TOKEN", raising=False)
    monkeypatch.delenv("DASH_TOKEN", raising=False)
    served = []
    monkeypatch.setattr(console_app.app, "run", lambda *a, **k: served.append(k))

    rc = console_app.main(["--host", "0.0.0.0"])

    assert rc != 0
    assert served == [], "the server started on a public interface with no token"


def test_every_request_is_rejected_without_the_token(monkeypatch):
    monkeypatch.setenv("MINUS_DASH_TOKEN", "secret-token")
    client = console_app.app.server.test_client()

    assert client.get("/").status_code == 401
    assert client.get("/", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/", headers={"Authorization": "Bearer secret-token"}).status_code != 401
    assert client.get("/?token=secret-token").status_code != 401


def test_with_no_token_configured_the_console_still_serves_locally(monkeypatch):
    """Failing closed here would break `minusctl console` for everyone; the bind-time guard
    is what makes this branch reachable only on loopback."""
    monkeypatch.delenv("MINUS_DASH_TOKEN", raising=False)
    monkeypatch.delenv("DASH_TOKEN", raising=False)
    client = console_app.app.server.test_client()

    assert client.get("/").status_code != 401


# --- The run selector: the store that nothing ever wrote --------------------------------

def test_the_bar_offers_every_run_not_just_the_newest(monkeypatch):
    runs = [{"run_id": "b-newer", "created_at": "2"}, {"run_id": "a-older", "created_at": "1"}]
    monkeypatch.setattr(console_app.runs_engine, "list_runs", lambda: runs)

    options = console_app._run_options()

    assert [o["value"] for o in options] == ["b-newer", "a-older"]


def test_selecting_a_run_writes_the_store_that_scopes_every_view():
    """`dcc.Store(id="run-id")` existed from the first version and no callback ever wrote
    it, so assemble() always fell back to the latest run and no other run was reachable."""
    outputs = " ".join(console_app.app.callback_map.keys())

    assert "run-id.data" in outputs, "nothing writes the run scope"
    assert console_app._select_run("20260101-chosen") == "20260101-chosen"


def test_the_navigation_reads_the_view_from_the_triggering_button():
    """Not from an index into a list -- an index silently retargets the moment a nav item
    is added or reordered."""
    outputs = " ".join(console_app.app.callback_map.keys())

    assert "view.data" in outputs
    assert {k for k, _n, _l in console_app.VIEW_LABELS} == {
        "topology", "flow", "access", "cost", "trace", "evidence"}


def test_settings_renders_without_a_run_identity_above_it():
    """Settings is workspace-scoped. A run band above it would say these teams and
    connectors belong to that run."""
    bar, band, _view = console_app._render("settings", None, "data", None)

    assert bar is not None
    rendered = json.dumps(band, default=str)
    assert "run-band" not in rendered and "chip" not in rendered


def test_the_delivery_flow_claims_no_provenance_the_run_does_not_record():
    """A run workspace records no commit, branch or pull request. Stating that a run "was
    created locally, not from a branch" is an invention dressed as a status -- the fact we
    hold is that nothing was recorded."""
    rendered = json.dumps(console_app.delivery_steps(
        {"root": "", "plan": {}, "trace": {}}), default=str)

    assert "created locally" not in rendered
    assert "Not recorded" in rendered
    for invented in ("from a branch,", "pull request #", "merged by"):
        assert invented not in rendered


# --- 03 Access: what the plan does not settle must reach the screen ---------------------

def test_access_reports_an_unresolved_trust_policy_rather_than_no_principals():
    """An assume_role_policy computed at apply time grants real permissions that this plan
    cannot show. Rendering it as "no principals" under-reports access, which on this screen
    is the dangerous direction to be wrong in."""
    plan = {"resource_changes": [{
        "address": "module.sec.aws_iam_role.partner", "type": "aws_iam_role",
        "mode": "managed", "name": "partner",
        "change": {"actions": ["create"], "after": {"name": "partner"},
                   "after_unknown": {"assume_role_policy": True}}}]}

    rendered = json.dumps(console_app.view_access({"plan": plan}), default=str)

    assert "not determinable" in rendered or "unresolved" in rendered.lower()
    assert "does not settle" in rendered


def test_access_says_plainly_which_facts_it_does_not_derive_yet():
    """Reach, cross-account trust and Lake Formation grants are all derived now. The G6
    findings are not joined onto roles, so the view names that rather than leaving a
    reviewer to read a clean-looking table as a clean result."""
    plan = {"resource_changes": [{
        "address": "module.sec.aws_iam_role.etl", "type": "aws_iam_role", "mode": "managed",
        "name": "etl", "change": {"actions": ["create"], "after": {"name": "etl"}}}]}

    rendered = json.dumps(console_app.view_access({"plan": plan}), default=str)

    assert "not joined onto these roles yet" in rendered
    assert "absent rather than estimated" in rendered


def test_access_shows_a_cross_account_trust_and_flags_a_missing_external_id():
    """SEC-05 is the finding, but the trust itself belongs on screen whether or not the
    Rego set ran: a reviewer needs to see who can assume into this account."""
    trust = json.dumps({"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Action": "sts:AssumeRole",
        "Principal": {"AWS": "arn:aws:iam::445566772201:root"}}]})
    plan = {"resource_changes": [{
        "address": "module.sec.aws_iam_role.partner", "type": "aws_iam_role",
        "mode": "managed", "name": "partner",
        "change": {"actions": ["create"],
                   "after": {"name": "partner", "assume_role_policy": trust}}}]}

    rendered = json.dumps(console_app.view_access({"plan": plan}), default=str)

    assert "445566772201" in rendered
    assert "SEC-05" in rendered
    assert "confused-deputy" in rendered


# --- 04 Cost and Settings read from disk, and say so when there is nothing --------------

def test_cost_names_the_command_that_produces_the_figures(tmp_path):
    """A run with no BCM output must not show a plausible number. Naming the command is
    the difference between "this costs nothing" and "nobody has priced this"."""
    rendered = json.dumps(console_app.view_cost(
        {"root": str(tmp_path), "run": {}}), default=str)

    assert "minusctl cost estimate" in rendered


def test_every_cost_figure_carries_where_it_came_from(tmp_path):
    """A BCM forecast and a Cost Explorer actual are different claims. Sharing a typeface
    with no label invites a forecast to be read as a bill."""
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "bcm-estimate.json").write_text(json.dumps({
        "total_monthly_usd": 1842,
        "by_service": [{"service": "AWS Glue", "driver": "420 DPU-hours",
                        "monthly_usd": 792}]}), encoding="utf-8")

    rendered = json.dumps(console_app.view_cost(
        {"root": str(tmp_path), "run": {}}), default=str)

    assert "BCM forecast" in rendered
    assert "not connected" in rendered, "an unlinked Cost Explorer must say so"


def test_cost_distrusts_a_forecast_with_no_assumptions_document(tmp_path):
    """A forecast rests entirely on its inputs. One that cannot be audited is worth less,
    and the view says which."""
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "bcm-estimate.json").write_text(json.dumps({"total_monthly_usd": 10}),
                                               encoding="utf-8")

    rendered = json.dumps(console_app.view_cost(
        {"root": str(tmp_path), "run": {}}), default=str)

    assert "cannot be audited" in rendered


def test_settings_never_offers_a_field_that_would_store_a_secret():
    """A webhook URL is a credential; anyone holding it can post as your bot. The console
    reads references and holds no values."""
    rendered = json.dumps(console_app.view_settings({}), default=str)

    assert "credential" in rendered.lower()
    for leak in ("hooks.slack.com", "webhook_url", "Bearer "):
        assert leak not in rendered


# --- PRD v14 sub-sections ---------------------------------------------------------------

def test_agents_cost_says_nothing_measured_rather_than_zero(tmp_path, monkeypatch):
    """A run whose linked transcript is unreadable did not cost nothing -- nothing measured
    what it cost. On a spend screen those are opposite claims."""
    monkeypatch.setenv(console_app.TRANSCRIPT_ENV, str(tmp_path / "missing.jsonl"))
    rendered = json.dumps(console_app.view_agents_cost({"root": str(tmp_path)}), default=str)

    assert "No agent telemetry" in rendered
    assert "$0" not in rendered, "an unmeasured run must not render a dollar figure"


def test_agents_cost_totals_exclude_unpriced_steps_and_say_so(tmp_path, monkeypatch):
    """The total must be a floor on what a run cost, never a ceiling, and the reader has to
    be told which steps are missing from it."""
    logs = tmp_path / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    monkeypatch.setenv(console_app.TRANSCRIPT_ENV, str(logs / "transcript.jsonl"))
    logs.joinpath("transcript.jsonl").write_text("\n".join([
        json.dumps({"step_index": 1, "created_at": "2026-08-24T10:00:00Z", "model": "pro",
                    "token_usage": {"prompt_tokens": 1000, "completion_tokens": 100,
                                    "cached_tokens": 0}}),
        json.dumps({"step_index": 2, "created_at": "2026-08-24T10:00:04Z",
                    "model": "some-unknown-model",
                    "token_usage": {"prompt_tokens": 500, "completion_tokens": 50,
                                    "cached_tokens": 0}}),
    ]), encoding="utf-8")

    rendered = json.dumps(console_app.view_agents_cost({"root": str(tmp_path)}), default=str)

    assert "Not included in the total" in rendered
    assert "unpriced model" in rendered


def test_agent_flow_never_shows_a_seal_as_proof_the_agent_was_right():
    """A hash proves a record was written and not altered. It says nothing about whether
    the agent did what the record claims."""
    rendered = json.dumps(console_app.view_agent_flow(
        {"root": "", "trace": {"stages": []}}), default=str)

    assert "does not prove" in rendered
    assert "reflector" in rendered


def test_cloud_spend_and_agent_spend_never_share_a_total():
    """Both are money and they are not the same money. One switch, two views, no sum."""
    switch = json.dumps(console_app._cost_switch("cloud"), default=str)

    assert "Cloud cost" in switch and "Agents cost" in switch


# --- The one path in this console that writes -------------------------------------------

def test_a_reroute_on_a_known_resource_type_resolves_its_argument():
    plan = {"resource_changes": [
        {"address": "module.compute.aws_glue_job.etl", "type": "aws_glue_job",
         "mode": "managed", "change": {"actions": ["create"], "after": {}}}]}
    change = {"kind": "reroute", "target": "module.compute.aws_glue_job.etl",
              "was": "module.storage.gold_arn", "now": "module.storage.bronze_arn"}

    spec, refusal = console_app.canvas_change_spec(change, plan)

    assert refusal is None
    assert spec["attribute"] == "--source_path"
    assert spec["from"] == "module.storage.gold_arn"


def test_an_unknown_resource_type_is_refused_by_name_not_guessed():
    """A diagram shows that a relationship changed, never which attribute encodes it.
    Writing the wrong argument re-points a different part of the stack, and the operator
    would have approved a sentence describing something else."""
    plan = {"resource_changes": [
        {"address": "module.stream.aws_kinesis_stream.events", "type": "aws_kinesis_stream",
         "mode": "managed", "change": {"actions": ["create"], "after": {}}}]}
    change = {"kind": "reroute", "target": "module.stream.aws_kinesis_stream.events",
              "was": "a", "now": "b"}

    spec, refusal = console_app.canvas_change_spec(change, plan)

    assert spec is None
    assert "aws_kinesis_stream" in refusal
    assert "does not know which argument" in refusal


def test_adding_a_box_is_refused_because_a_shape_carries_no_resource_type():
    spec, refusal = console_app.canvas_change_spec({"kind": "add", "what": "new thing"}, {})

    assert spec is None
    assert "generation concern" in refusal


def test_the_confirm_callback_is_the_only_registered_writer():
    """Every other callback in this module reads. If a second one ever writes, this test is
    the place that notices."""
    outputs = " ".join(console_app.app.callback_map.keys())

    assert "sheet-body.children" in outputs
    source = open(os.path.join(ROOT, "app", "console_app.py"), encoding="utf-8").read()
    calls = [line for line in source.splitlines()
             if "reconciler.confirm(" in line and line.strip().startswith(("result", "return"))]
    assert len(calls) == 1, f"more than one path writes HCL: {calls}"
    assert "confirmed=True" in source


def test_access_reports_a_wildcard_grant_as_reaching_everything():
    """A role with Resource "*" reaches every bucket. Rendering the narrow list its ARNs
    imply would understate the broadest grant there is."""
    policy = json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["s3:*"], "Resource": ["*"]}]})
    plan = {"resource_changes": [
        {"address": "module.sec.aws_iam_role.etl", "type": "aws_iam_role", "mode": "managed",
         "name": "etl", "change": {"actions": ["create"], "after": {"name": "etl"}}},
        {"address": "module.sec.aws_iam_role_policy.p", "type": "aws_iam_role_policy",
         "mode": "managed", "name": "p",
         "change": {"actions": ["create"], "after": {"role": "etl", "policy": policy}}}]}

    rendered = json.dumps(console_app.view_access({"plan": plan}), default=str)

    assert "every bucket in the account" in rendered


def test_a_run_with_no_linked_transcript_says_so_rather_than_guessing_a_path(tmp_path):
    """A transcript belongs to a conversation, not a run. An earlier version derived a path
    under the run root that never exists, which made every run look like it had no telemetry
    rather than like nothing had been linked."""
    assert console_app._transcript_path(str(tmp_path)) is None

    rendered = json.dumps(console_app.view_agents_cost({"root": str(tmp_path)}), default=str)
    assert "No transcript is linked" in rendered
    assert console_app.TRANSCRIPT_ENV in rendered


def test_an_explicitly_linked_transcript_is_used(tmp_path, monkeypatch):
    monkeypatch.setenv(console_app.TRANSCRIPT_ENV, str(tmp_path / "t.jsonl"))

    assert console_app._transcript_path("") == str(tmp_path / "t.jsonl")


# --- Docs, Policies, About: read from disk, never transcribed ----------------------------

def test_the_policy_page_lists_the_rules_that_actually_run():
    """Parsed from policy/g6/rules.rego. A hand-maintained list drifts, and a drifted policy
    page is worse than none: it tells a reviewer a rule exists that does not."""
    rendered = json.dumps(console_app.view_policies({}), default=str)

    for rule in ("SEC-01", "SEC-05", "COST-01"):
        assert rule in rendered, rule


def test_the_policy_page_says_what_a_clean_run_does_not_prove():
    """A resource type no rule mentions is unexamined, not approved. Without that sentence a
    findings-free report reads as a clean bill of health."""
    rendered = json.dumps(console_app.view_policies({}), default=str)

    assert "unexamined, not approved" in rendered


def test_the_changelog_is_parsed_rather_than_transcribed():
    rendered = json.dumps(console_app.view_docs({}), default=str)

    assert "0.1.0" in rendered
    assert "CHANGELOG.md" in rendered


def test_the_docs_page_marks_a_document_missing_from_the_checkout(monkeypatch):
    monkeypatch.setattr(console_app, "_DOC_PAGES",
                        (("does/not/exist.md", "Ghost", "not here"),))

    rendered = json.dumps(console_app.view_docs({}), default=str)

    assert "not in this checkout" in rendered


def test_about_states_what_the_console_will_not_do():
    rendered = json.dumps(console_app.view_about({}), default=str)

    assert "terraform apply" in rendered
    assert "MINUS_DASH_TOKEN" in rendered


def test_the_workspace_pages_carry_no_run_identity():
    """Docs, Policies and About outlive any run. A run band above them would say these
    documents belong to that run."""
    for view in ("docs", "policies", "about", "settings"):
        _bar, band, _content = console_app._render(view, None, "data", None)
        assert "chip" not in json.dumps(band, default=str), view
