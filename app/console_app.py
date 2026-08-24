"""
MinusOps Enterprise Visual Governance Console (PRD v13 FR-01).

Replaces `app/dashboard_app.py`, which mixed FinOps charts, CLI command execution and
report viewers across five tabs that each served a different person. This is four views
scoped to ONE run, in the order an architect actually reviews one:

  1. Architecture topology  -- what was built, on an editable canvas
  2. Data lineage           -- where a record goes and what governs it
  3. Execution trace        -- what actually ran, bound to the audit chain
  4. Deliverables vault     -- the evidence, downloadable

THE VIEW LAYER OWNS NO LOGIC. Every fact on screen comes from an engine that is tested
independently -- `lineage_graph`, `agent_tracer`, `vault`, `reconciler`, `drawio_generator`.
This file arranges them and does not decide anything, which is why it can use Dash while
those engines stay standard-library-only (PRD v13 invariant 4 binds the engines, not the
presentation).

The canvas proposes; Git decides. A connection edit here never writes HCL on its own -- it
routes through `reconciler.propose()`, renders the Architecture Change Review Modal, and
writes only on explicit confirmation. See `core/architecture/reconciler.py` for why that
split is the whole safety property.

Depends on: core/reporting/lineage_graph.py, core/governance/agent_tracer.py,
    core/reporting/vault.py, core/architecture/reconciler.py,
    core/reporting/drawio_generator.py, core/reporting/runs.py
Shells out to: nothing. The console never invokes a cloud mutation (PRD v13 invariant 2).
Used by: core/cli/commands/console.py (`minusctl console`)
"""
import hmac
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers",
             "integrations"):
    _path = os.path.join(ROOT, "core", _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)
for _path in (os.path.join(ROOT, "core"), ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import dash  # noqa: E402
from dash import dcc, html, Input, Output, State  # noqa: E402

import agent_tracer  # noqa: E402
import drawio_generator  # noqa: E402
import lineage_graph  # noqa: E402
import reconciler  # noqa: E402
import runs as runs_engine  # noqa: E402
import vault  # noqa: E402


# Monad design tokens (DESIGN.md). Kept in one dict so the stylesheet below and any inline
# style read the same values.
C = {
    "bg": "#f6f3f1", "elev": "#cfdaf5", "line": "#cecac8", "accent": "#2b59d1",
    "ink": "#242424", "graphite": "#4e4d4d", "smoke": "#797776",
    "good": "#2f6b4f", "warn": "#8a6516", "crit": "#8f2d18",
}


# --- Data assembly ----------------------------------------------------------------------

def _run_record(run_id=None):
    """The run this console is scoped to, or None when there genuinely are none.

    No blanket `except Exception` here. An earlier version had one, and it swallowed a call
    to a function that does not exist (`load_index`) -- so the console rendered a calm
    "No runs found" over a workspace holding twenty-five runs. A missing-runs message and a
    broken reader must not look the same.
    """
    if run_id:
        return runs_engine.get_run(run_id)
    # `list_runs()` sorts newest-first, so [-1] was the OLDEST run in the workspace -- the
    # console opened on the first run ever made and every view honestly reported that it
    # held nothing.
    return runs_engine.latest_run()


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def assemble(run_id=None):
    """Everything the four views need, gathered once."""
    record = _run_record(run_id)
    if not record:
        return {"run": None}
    root = record.get("root") or ""
    decision = _load_json(os.path.join(root, "architecture_decision.json"))
    plan = _load_json(os.path.join(root, "reports", "plan.json"))
    return {
        "run": record,
        "root": root,
        "decision": decision,
        "plan": plan,
        "lineage": lineage_graph.build_lineage(decision, plan or None),
        "trace": agent_tracer.trace(run_root=root),
        "vault": vault.summary(root),
        "documents": vault.catalog(root),
    }


# --- Shared chrome ------------------------------------------------------------------------

VIEW_LABELS = (
    ("topology", "01", "Topology"),
    ("flow", "02", "Flow"),
    ("access", "03", "Access"),
    ("cost", "04", "Cost"),
    ("trace", "05", "What ran"),
    ("evidence", "06", "Evidence"),
)


def _run_options():
    """Every run, newest first, for the picker in the bar.

    The `run-id` store existed from the beginning and NOTHING ever wrote it, so the console
    could only ever show whatever latest_run() returned and no other run was reachable.
    """
    return [{"label": r.get("run_id", ""), "value": r.get("run_id", "")}
            for r in runs_engine.list_runs()]


def top_bar(active_view, run_id):
    options = _run_options()
    return html.Header(className="bar", children=[
        html.Span(className="brand", children=[html.Span(className="dot"), " MinusOps"]),
        html.Nav(role="tablist", children=[
            html.Button(id={"kind": "nav", "view": key}, n_clicks=0, role="tab",
                        **{"aria-selected": "true" if key == active_view else "false"},
                        children=[html.Span(number, className="n"), label])
            for key, number, label in VIEW_LABELS]),
        html.Span(className="spacer"),
        html.Span(className="util", children=[
            html.A("Docs", href="/docs"), html.A("Policies", href="/policies"),
            html.Button("Settings", id={"kind": "nav", "view": "settings"}, n_clicks=0,
                        className="utilbtn"),
            html.A("About", href="/about"),
        ]),
        html.Span(className="picker", children=[
            dcc.Dropdown(id="run-select", className="runpick", options=options,
                         value=run_id or (options[0]["value"] if options else None),
                         clearable=False, searchable=False),
        ]),
    ])


def run_band(state):
    """FR-01.2. Every fact here is read, never derived -- an unproven plan hash on a
    governance header is exactly the claim this product exists to refuse."""
    record = state.get("run") or {}
    plan_hash = (state.get("plan") or {}).get("plan_hash") or record.get("plan_hash")
    cost = record.get("estimated_monthly_cost")
    vault_stats = state.get("vault") or {}
    facts = [
        ("Domain", record.get("domain"), "not declared"),
        ("Tier", record.get("tier"), "unclassified"),
        ("Resources", len((state.get("plan") or {}).get("resource_changes") or []) or None,
         "not planned"),
        ("Forecast", f"${cost}/mo" if cost else None, "not priced"),
        ("Evidence", (f"{vault_stats.get('present', 0)} of {vault_stats.get('total', 0)}"
                      if vault_stats.get("total") else None), "no catalog"),
    ]
    status = (record.get("governance_status") or "").strip()
    verdict = status or "unproven"
    return html.Div(className="run", children=[
        html.Span(record.get("run_id", "no run selected"), className="name"),
        html.Span(className="hash", children=[
            html.Span("Bound to plan", className="lab"),
            plan_hash[:16] if plan_hash else html.Span("not planned", className="absent"),
        ]),
        html.Span(className="facts", children=[
            html.Div(children=[
                html.Span(label, className="lab"),
                html.B(str(value)) if value else html.B(absent, className="absent"),
            ]) for label, value, absent in facts]),
        html.Span(verdict, className="chip" if status.upper() == "PROVEN"
                  else "chip unproven"),
    ])


# --- View 1: topology -------------------------------------------------------------------

def _viewer_url(edit_url):
    """The read-only diagrams.net viewer for the same deflated payload as the edit link.

    `viewer.diagrams.net` renders with pan, zoom and layer controls and never writes; the
    edit link stays a separate, explicit action. Same `#R<payload>` on both sides.
    """
    payload = edit_url.split("#R", 1)[1] if "#R" in edit_url else ""
    return ("https://viewer.diagrams.net/?lightbox=0&nav=1&layers=1&edit=_blank"
            "#R" + payload)


def view_topology(state):
    plan = state.get("plan") or {}
    if not plan:
        return _empty("No plan analyzed",
                      "Run `minusctl gate plan` to generate the plan this canvas draws from.")
    # generate_drawio_from_plan returns a BUNDLE -- {"xml", "url", "ledger", "svg"} -- not
    # an XML string. Passing its return value to encode_drawio_url() raises AttributeError
    # on every run that actually has a plan; the first version of this view did exactly
    # that and the tests never caught it, because the fixture run had no plan.json and only
    # ever exercised the "No plan analyzed" branch.
    bundle = drawio_generator.generate_drawio_from_plan(plan, title="Architecture Blueprint")
    xml = bundle["xml"]
    url = bundle["url"]
    ledger = bundle["ledger"]
    return html.Div([
        html.Div(className="view-actions", children=[
            html.A("Open in diagrams.net", href=url, target="_blank",
                   className="btn dark"),
            html.Span(f"{len(plan.get('resource_changes', []))} planned resources",
                      className="muted"),
        ]),
        # FR-02.1. The embed and the button are built from the SAME payload, deliberately:
        # two encodings of one plan that drift apart mean the operator reviews the canvas
        # and opens a link showing a different architecture.
        html.Iframe(src=_viewer_url(url), className="canvas",
                    title="Architecture topology"),
        _ledger_table(ledger),
        _reconcile_panel(state),
        html.Details(className="drawer", children=[
            html.Summary("Draw.io XML (canvas source)"),
            html.Pre(xml[:4000], className="code"),
        ]),
    ])


# --- View 2: lineage --------------------------------------------------------------------

def inspect_lineage_node(node_id, graph):
    """FR-03.3. The facts a reviewer asks for about one dataset hop.

    Absent facts are named as absent rather than rendered blank: an empty retention row on
    a governance surface reads as "no retention", which is a different claim from "this
    stack did not declare one".
    """
    node = lineage_graph.find_node(graph or {}, node_id) if node_id else None
    if not node:
        return html.P("Select a hop above to inspect its schema, partitioning and retention.",
                      className="muted")
    rows = [
        ("Layer", node.get("layer", "-")),
        ("Table format", node.get("table_format") or "not applicable to this hop"),
        ("Partitioning", node.get("partitioning") or "not declared"),
        ("Retention", node.get("retention") or "not declared"),
        ("Encryption", node.get("encryption") or "not declared"),
        ("Detail", node.get("detail") or "-"),
    ]
    return html.Div([
        html.H4(node["label"]),
        html.Table(className="table", children=[html.Tbody([
            html.Tr([html.Td(label), html.Td(value)]) for label, value in rows])]),
    ])


def _hop_chain(graph):
    """The medallion hops with connectors, so the row reads as a flow.

    The quarantine hop is marked as a branch rather than another link in the chain: it is
    where rejected records LEAVE the pipeline, and drawing it inline would say they carry on
    to Gold.
    """
    children = []
    for index, node in enumerate(graph["nodes"]):
        if index:
            branch = node["id"] == "quarantine"
            children.append(html.Span("|" if branch else "->",
                                      className="hop-link branch" if branch else "hop-link"))
        children.append(html.Button(
            id={"kind": "hop", "node": node["id"]}, n_clicks=0,
            className=f"hop hop-{node['layer']}", children=[
                html.Small(node["layer"].upper()),
                html.Strong(node["label"]),
                html.Span(node.get("table_format") or node.get("detail") or "",
                          className="muted"),
            ]))
    return children


def _flow_table(graph):
    """The hop ledger as a table. This used to render `lineage_graph.as_markdown()` inside a
    <pre>, which put raw pipe-and-dash markdown source on the page -- the export format
    printed where the rendered thing belonged."""
    rows = graph.get("edges") or []
    if not rows:
        return html.P("No dataset flow to trace for this stack.", className="muted")
    labels = {n["id"]: n["label"] for n in graph["nodes"]}
    return html.Table(className="table", children=[
        html.Thead(html.Tr([html.Th("Hop"), html.Th("From"), html.Th("To"), html.Th("Branch")])),
        html.Tbody([
            html.Tr(className="reject" if e.get("branch") == "reject" else "", children=[
                html.Td(e["label"]),
                html.Td(labels.get(e["from"], e["from"])),
                html.Td(labels.get(e["to"], e["to"])),
                html.Td(e.get("branch") or "-"),
            ]) for e in rows]),
    ])


def view_lineage(state):
    graph = state.get("lineage") or {"nodes": [], "edges": [], "masking": {}}
    if not graph["nodes"]:
        return _empty("No lineage to draw",
                      "This run provisions no medallion hops, so there is no dataset flow "
                      "to trace.")
    masking = graph.get("masking") or {}
    return html.Div([
        html.Div(className="lineage", children=_hop_chain(graph)),
        html.Div(id="lineage-inspector", className="inspector",
                 children=inspect_lineage_node(None, graph)),
        _flow_table(graph),
        html.Div(className="panel", children=[
            html.H4("Lake Formation column masking"),
            html.P(masking.get("reason", ""), className="muted"),
            html.Table(className="table", children=[
                html.Thead(html.Tr([html.Th("Column"), html.Th("Unmasked for"),
                                    html.Th("Masked for"), html.Th("Masked value")])),
                html.Tbody([
                    html.Tr([html.Td(c["column"]),
                             html.Td(", ".join(c["unmasked_for"])),
                             html.Td(", ".join(c["masked_for"])),
                             html.Td(c["masked_example"])])
                    for c in masking.get("columns", [])]),
            ]) if masking.get("enforced") else html.P("No column-level controls enforced.",
                                                      className="muted"),
        ]),
    ])


# --- View 3: execution trace ------------------------------------------------------------

def view_trace(state):
    result = state.get("trace") or {}
    stages = result.get("stages", [])
    active = agent_tracer.active_agents(state.get("root"))
    return html.Div([
        html.Div(className="panel", children=[
            html.H4("Live agent monitor"),
            html.P("No subagent supervisor is running; nothing to report."
                   if not active else f"{len(active)} active", className="muted"),
        ]),
        html.Div(className="timeline", children=[
            html.Div(className=f"stage {'ran' if s['status'] == agent_tracer.RECORDED else 'pending'}",
                     children=[
                         html.Div(className="stage-head", children=[
                             html.Strong(s["agent"]),
                             html.Span(s["status"], className="stage-status"),
                         ]),
                         html.P(s["summary"], className="muted"),
                         html.Small(f"artifact: {s['artifact']} "
                                    f"({'present' if s['artifact_present'] else 'not produced'})"),
                         html.Small(f"audit {s['audit_hash'][:16]}" if s["audit_hash"]
                                    else "no audit evidence", className="audit"),
                     ]) for s in stages]),
    ])


# --- View 4: vault ----------------------------------------------------------------------

def view_vault(state):
    stats = state.get("vault") or {}
    documents = state.get("documents") or []
    return html.Div([
        html.Div(className="view-actions", children=[
            html.A("Export compliance bundle", className="btn primary",
                   href=f"/runs/{(state.get('run') or {}).get('run_id', '')}/vault/bundle"),
            html.Span(f"{stats.get('present', 0)} of {stats.get('total', 0)} documents present",
                      className="muted"),
        ]),
        html.Div(id="vault-status", className="muted"),
        _document_table(documents, (state.get("run") or {}).get("run_id", "")),
        # The preview sits BELOW the list. Above it, the first thing on the view was an
        # empty box asking to be filled by a control further down the page.
        html.Div(id="vault-preview", className="inspector",
                 children=html.P("Select a document to preview it.", className="muted")),
    ])


def _document_table(documents, run_id):
    """Present documents first, absent ones folded away.

    Listing all fifteen inline made the vault a wall of "not produced" -- the two documents
    that exist were outnumbered six to one, which buries the evidence the view is for. The
    absent ones still appear, because a category that vanishes when empty hides the fact
    that the evidence was never produced; they are just not the headline.
    """
    present = [d for d in documents if d["present"]]
    absent = [d for d in documents if not d["present"]]

    def _row(document):
        href = f"/runs/{run_id}/vault/download/{document['name']}"
        return html.Tr(children=[
            html.Td(html.Button(document["name"],
                                id={"kind": "doc", "name": document["name"]},
                                n_clicks=0, className="link-button")),
            html.Td(document["category_title"]),
            html.Td(f"{document['size_bytes']:,} B"),
            html.Td(html.A("Download", href=href, className="link-button")),
        ])

    blocks = []
    if present:
        blocks.append(html.Table(className="table", children=[
            html.Thead(html.Tr([html.Th("Document"), html.Th("Category"),
                                html.Th("Size"), html.Th("")])),
            html.Tbody([_row(d) for d in present]),
        ]))
    else:
        blocks.append(html.P("No deliverables have been produced for this run yet.",
                             className="muted"))
    if absent:
        blocks.append(html.Details(className="drawer", children=[
            html.Summary(f"{len(absent)} not produced"),
            html.Div(className="drawer-body", children=[
                html.Ul([html.Li(f"{d['name']} -- {d['category_title']}") for d in absent],
                        className="absent-list")]),
        ]))
    return html.Div(blocks)


def _ledger_table(ledger):
    """The step-flow ledger (FR-02.3).

    `generate_flow_ledger()` returns a list of dicts, not text -- rendering it with html.Pre
    prints a Python repr on the page. Its protocol/latency/safeguard values are the same
    constants for every hop today, so they are shown as declared defaults rather than as
    measurements of this pipeline.
    """
    if not ledger:
        return html.P("No flow hops discovered in this plan.", className="muted")
    columns = ("hop", "source", "target", "protocol", "latency", "safeguards")
    return html.Table(className="table", children=[
        html.Thead(html.Tr([html.Th(c.title()) for c in columns])),
        html.Tbody([html.Tr([html.Td(str(row.get(c, "-"))) for c in columns])
                    for row in ledger]),
    ])


# --- FR-05: governed visual reconciliation ----------------------------------------------

def _reconcile_panel(state):
    """Where a topological correction enters the system.

    FR-05.1 says the console intercepts a canvas connection edit. The embedded viewer is a
    VIEWER -- diagrams.net does not post edit events back to an embedding page -- so the
    intake is an explicit change form rather than a drag. That is a smaller claim than the
    PRD's wording and it is the honest one: what FR-05 actually protects is that a proposed
    edit cannot reach main.tf without the review modal, and that property does not depend on
    whether a mouse or a form produced the proposal.
    """
    return html.Details(className="drawer", children=[
        html.Summary("Propose an architecture change"),
        html.Div(className="drawer-body", children=[
            html.P("Re-route a reference in the generated Terraform. Nothing is written "
                   "until you confirm the review.", className="muted"),
            html.Div(className="reconcile-form", children=[
                _field("Resource", "reconcile-target", "aws_glue_job.etl"),
                _field("Attribute", "reconcile-attribute", "--source_path"),
                _field("From", "reconcile-from", "module.storage.gold_bucket_arn"),
                _field("To", "reconcile-to", "module.storage.bronze_bucket_arn"),
            ]),
            html.Button("Review change", id="reconcile-review", n_clicks=0,
                        className="btn"),
            html.Div(id="reconcile-modal"),
            html.Div(id="reconcile-result", className="muted"),
        ]),
    ])


def _field(label, element_id, placeholder):
    return html.Label(className="field", children=[
        html.Small(label),
        dcc.Input(id=element_id, type="text", placeholder=placeholder,
                  className="control-input", debounce=True),
    ])


def _change_spec(target, attribute, from_ref, to_ref):
    return {"kind": "reconnect", "target": (target or "").strip(),
            "attribute": (attribute or "").strip(),
            "from": (from_ref or "").strip(), "to": (to_ref or "").strip()}


def reconcile_preview(n_clicks, target, attribute, from_ref, to_ref, run_id):
    """FR-05.2. Compute what the edit would do and render the review modal. Writes nothing.

    Returns (modal_children, stored_spec). `stored_spec` is None whenever the change cannot
    be applied, so there is nothing for a confirm click to act on.
    """
    if not n_clicks:
        return None, None
    change = _change_spec(target, attribute, from_ref, to_ref)
    if not change["from"] or not change["to"]:
        return html.Div("Enter both a From and a To reference.", className="status-warn"), None

    state = assemble(run_id)
    run_root = state.get("root")
    if not run_root:
        return html.Div("No run selected.", className="status-warn"), None

    proposal = reconciler.propose(run_root, change)
    if not proposal["applicable"]:
        return (html.Div(className="status-bad", children=[
            html.Strong("Change refused"),
            html.P(proposal["reason"], className="muted"),
        ]), None)

    modal = html.Div(className="review-modal", children=[
        html.H4("Architecture change review"),
        html.Div(className="review-meta", children=[
            html.Span(f"Author: {proposal['author']}"),
            html.Span(f"At: {proposal['at']}"),
        ]),
        html.P(proposal["summary"], className="review-summary"),
        html.Ul([html.Li(w) for w in proposal["warnings"]], className="review-warnings"),
        html.Pre(proposal["diff"], className="ledger"),
        html.Div(className="review-actions", children=[
            html.Button("Confirm and rewrite main.tf", id="reconcile-confirm",
                        n_clicks=0, className="btn primary"),
            html.Button("Cancel", id="reconcile-cancel", n_clicks=0, className="btn"),
        ]),
    ])
    # The store round-trips through the browser, so it carries the change SPEC and the run
    # only. Putting `updated_hcl` in here would let a tampered payload be written to main.tf
    # verbatim -- an arbitrary-file-write endpoint wearing a governance modal. On confirm the
    # proposal is recomputed server-side from these same inputs.
    return modal, {"change": change, "run_id": run_id}


def reconcile_apply(confirm_clicks, cancel_clicks, stored):
    """FR-05.3. Apply a reviewed change, but only on an explicit confirm click."""
    if cancel_clicks and not confirm_clicks:
        return html.Div("Change cancelled. Nothing was written.", className="muted"), None
    if not confirm_clicks:
        return None, dash.no_update
    if not stored:
        return html.Div("Nothing to confirm.", className="status-warn"), None

    state = assemble(stored.get("run_id"))
    run_root = state.get("root")
    if not run_root:
        return html.Div("No run selected.", className="status-warn"), None

    # Recomputed here, never taken from the client.
    proposal = reconciler.propose(run_root, stored["change"])
    result = reconciler.confirm(proposal, confirmed=True)
    if not result["applied"]:
        return html.Div(f"Not applied: {result['reason']}", className="status-bad"), None

    return (html.Div(className="status-good", children=[
        html.Strong(f"Applied. Run is now {result['status']}."),
        html.P(f"Approvals revoked: {result['approvals_revoked']}", className="muted"),
        html.P(f"Next: {result['next_command']}", className="muted"),
    ]), None)


# --- FR-06.1 / FR-06.2: preview and download --------------------------------------------
#
# The export used to write a zip and print a path on the SERVER. For anyone not sitting at
# that machine that is not an export, so both of these are real HTTP routes the browser can
# follow.
#
# THE GUARD IS AN ALLOWLIST, NOT A SANITISER. The requested name is matched against the
# vault catalog for that run and the file is served from the path the catalog resolved.
# Nothing from the URL is ever joined onto a directory, so `..`, an absolute path and a
# symlink name all fail the same way: they are not in the catalog.

def _catalogued_document(run_id, name):
    state = assemble(run_id)
    root = state.get("root")
    if not root:
        return None
    for document in vault.catalog(root):
        if document["present"] and document["name"] == name:
            return document
    return None


def preview_document(name, run_id):
    """Render a document inline where that is meaningful, or offer it for download."""
    document = _catalogued_document(run_id, name)
    if not document:
        return html.P("Select a document to preview it.", className="muted")

    run = (assemble(run_id).get("run") or {}).get("run_id", "")
    href = f"/runs/{run}/vault/download/{document['name']}"
    header = html.Div(className="preview-head", children=[
        html.H4(document["name"]),
        html.A("Download", href=href, className="btn"),
    ])
    if document["preview"] == "inline":
        return html.Div([header, html.Iframe(src=href, className="preview-frame",
                                             title=document["name"])])
    if document["preview"] == "text":
        try:
            with open(document["path"], encoding="utf-8", errors="replace") as handle:
                body = handle.read(20000)
        except OSError as exc:
            return html.Div([header, html.P(f"Could not read it: {exc}", className="muted")])
        return html.Div([header, html.Pre(body, className="ledger")])
    return html.Div([header, html.P("Binary document; use Download.", className="muted")])


def _empty(title, detail):
    return html.Div(className="empty", children=[
        html.H3(title), html.P(detail, className="muted")])


def view_access(state):
    """03 Access. Roles, cross-account trust and the policy findings against them.

    Deliberately not populated from a guess: the Rego findings are readable today, but
    "which role can reach which dataset" needs IAM statements parsed out of the plan, and
    that engine does not exist yet. Showing invented rows on an access-control screen is
    worse than showing none.
    """
    return _empty("Access analysis not available",
                  "The policy findings are readable, but the role-to-resource model "
                  "(core/architecture/access_model.py) has not been built yet.")


def view_cost(state):
    """04 Cost. BCM forecast, per-service breakdown, scale curve and the assumptions."""
    return _empty("No cost evidence for this run",
                  "Run `minusctl cost estimate` to produce a BCM forecast.")


def view_settings(state):
    """Workspace scope: teams and connectors, which outlive any single run."""
    return _empty("Settings not wired yet",
                  "Teams come from configs/teams.yaml via team_resolver; connectors from "
                  "core/integrations.")


RENDERERS = {"topology": view_topology, "flow": view_lineage, "access": view_access,
             "cost": view_cost, "trace": view_trace, "evidence": view_vault,
             "settings": view_settings}


# --- App --------------------------------------------------------------------------------

app = dash.Dash(__name__, title="MinusOps Governance Console")
server = app.server
# The four views are rendered on demand, so components a callback targets (the vault
# exporter, for one) are absent from the initial layout. Without this Dash validates
# callbacks against that first layout and errors on page load -- which the tests did not
# catch, because a structural check of the layout tree cannot see it. The browser did.
app.config.suppress_callback_exceptions = True

app.layout = html.Div(children=[
    dcc.Store(id="run-id"),
    dcc.Store(id="view", data="topology"),
    dcc.Store(id="reconcile-proposal"),
    html.Div(id="bar-slot"),
    html.Div(className="wrap", children=[
        html.Div(id="band-slot"),
        html.Div(id="view-slot", className="view on"),
    ]),
    html.Div(id="overlay", className="overlay", children=[
        html.Div(className="sheet", role="dialog", children=[
            html.Header(children=[
                html.Span("document", className="name", id="sheet-name"),
                html.Span("", className="lab", id="sheet-kind"),
                html.Span(className="spacer"),
                html.Button("Close (Esc)", id="sheet-close", n_clicks=0, className="btn"),
            ]),
            html.Div(id="sheet-body", className="body"),
        ]),
    ]),
])


@app.callback(
    Output("view", "data"),
    Input({"kind": "nav", "view": dash.ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _switch_view(_clicks):
    """Which view was asked for comes from the triggering component id, not from an index
    into a list -- an index breaks the moment a nav item is added or reordered."""
    triggered = dash.callback_context.triggered_id
    if isinstance(triggered, dict) and triggered.get("view"):
        return triggered["view"]
    return dash.no_update


@app.callback(
    Output("run-id", "data"),
    Input("run-select", "value"),
)
def _select_run(run_id):
    return run_id


@app.callback(
    Output("bar-slot", "children"),
    Output("band-slot", "children"),
    Output("view-slot", "children"),
    Input("view", "data"),
    Input("run-id", "data"),
)
def _render(view, run_id):
    state = assemble(run_id)
    bar = top_bar(view, run_id)
    if not state.get("run"):
        return bar, html.Div(), _empty(
            "No runs found", "Create one with `minusctl create`.")
    # Settings is workspace-scoped: showing a run identity above it would say these teams
    # and connectors belong to that run.
    band = html.Div() if view == "settings" else run_band(state)
    renderer = RENDERERS.get(view, view_topology)
    return bar, band, renderer(state)



@app.callback(
    Output("vault-status", "children"),
    Input("vault-bundle", "n_clicks"),
    State("run-id", "data"),
    prevent_initial_call=True,
)
def _bundle(_clicks, run_id):
    state = assemble(run_id)
    root = state.get("root")
    if not root:
        return "No run selected."
    out = os.path.join(root, "reports", "compliance-bundle.zip")
    result = vault.bundle(root, out)
    if not result["ok"]:
        return f"Refused: {result['reason']}"
    return f"Wrote {result['document_count']} documents to {out}"


@app.callback(
    Output("reconcile-modal", "children"),
    Output("reconcile-proposal", "data"),
    Input("reconcile-review", "n_clicks"),
    State("reconcile-target", "value"),
    State("reconcile-attribute", "value"),
    State("reconcile-from", "value"),
    State("reconcile-to", "value"),
    State("run-id", "data"),
    prevent_initial_call=True,
)
def _reconcile_preview_cb(n_clicks, target, attribute, from_ref, to_ref, run_id):
    return reconcile_preview(n_clicks, target, attribute, from_ref, to_ref, run_id)


@app.callback(
    Output("reconcile-result", "children"),
    Output("reconcile-modal", "children", allow_duplicate=True),
    Input("reconcile-confirm", "n_clicks"),
    Input("reconcile-cancel", "n_clicks"),
    State("reconcile-proposal", "data"),
    prevent_initial_call=True,
)
def _reconcile_apply_cb(confirm_clicks, cancel_clicks, stored):
    return reconcile_apply(confirm_clicks, cancel_clicks, stored)


@app.callback(
    Output("lineage-inspector", "children"),
    Input({"kind": "hop", "node": dash.ALL}, "n_clicks"),
    State("run-id", "data"),
    prevent_initial_call=True,
)
def _inspect_hop_cb(_clicks, run_id):
    """FR-03.3. Which hop was clicked comes from the triggering component id."""
    triggered = dash.callback_context.triggered_id
    node_id = triggered.get("node") if isinstance(triggered, dict) else None
    return inspect_lineage_node(node_id, assemble(run_id).get("lineage") or {})


@app.callback(
    Output("vault-preview", "children"),
    Input({"kind": "doc", "name": dash.ALL}, "n_clicks"),
    State("run-id", "data"),
    prevent_initial_call=True,
)
def _preview_document_cb(_clicks, run_id):
    triggered = dash.callback_context.triggered_id
    name = triggered.get("name") if isinstance(triggered, dict) else None
    return preview_document(name, run_id)


@app.server.route("/runs/<run_id>/vault/download/<path:name>")
def _vault_download(run_id, name):
    from flask import abort, send_file
    document = _catalogued_document(run_id, os.path.basename(name))
    if not document or os.path.basename(name) != name:
        abort(404)
    return send_file(document["path"], as_attachment=True,
                     download_name=document["name"])


@app.server.route("/runs/<run_id>/vault/bundle")
def _vault_bundle_download(run_id):
    from flask import abort, send_file
    root = assemble(run_id).get("root")
    if not root:
        abort(404)
    out = os.path.join(root, "reports", "compliance-bundle.zip")
    result = vault.bundle(root, out)
    if not result["ok"]:
        abort(404, result["reason"])
    return send_file(out, as_attachment=True, download_name="compliance-bundle.zip")


app.index_string = """<!DOCTYPE html>
<html>
<head>
  {%metas%}<title>{%title%}</title>{%favicon%}{%css%}
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Instrument+Serif&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
  <style>

:root{
  --paper:#f6f3f1; --ink:#242424; --black:#000; --ash:#cecac8; --smoke:#797776;
  --graphite:#4e4d4d; --blue:#2b59d1; --mist:#cfdaf5;
  --good:#2f6b4f; --warn:#8a6516; --crit:#8f2d18;
  --tint:rgba(43,89,209,.05);
  --mono:'JetBrains Mono',ui-monospace,monospace;
  --serif:'Instrument Serif',Georgia,serif;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--paper);color:var(--ink);font-family:var(--mono);font-size:13px;
  line-height:1.5;-webkit-font-smoothing:antialiased}
body.locked{overflow:hidden}
::selection{background:var(--mist);color:var(--ink)}
:focus-visible{outline:2px solid var(--blue);outline-offset:2px}
a{color:inherit}
.lab{font-size:9px;letter-spacing:2.5px;text-transform:uppercase;color:var(--smoke)}

.bar{background:var(--black);color:var(--paper);height:46px;display:flex;align-items:stretch;
  padding:0 18px;position:sticky;top:0;z-index:20}
.bar .brand{display:flex;align-items:center;gap:9px;font-size:11px;letter-spacing:2.5px;
  text-transform:uppercase;padding-right:22px;white-space:nowrap}
.bar .dot{width:8px;height:8px;background:var(--blue)}
.bar nav{display:flex;align-items:stretch}
.bar nav button{background:none;border:0;border-bottom:2px solid transparent;cursor:pointer;
  font-family:var(--mono);font-size:10px;letter-spacing:2px;text-transform:uppercase;
  color:#8d8d8b;padding:0 13px;transition:color .12s ease,border-color .12s ease;white-space:nowrap}
.bar nav button:hover{color:var(--paper)}
.bar nav button .n{color:#4a4a48;margin-right:6px;transition:color .12s ease}
.bar nav button[aria-selected="true"]{color:var(--paper);border-bottom-color:var(--blue)}
.bar nav button[aria-selected="true"] .n{color:var(--blue)}
.bar .spacer{flex:1}
.bar .util{display:flex;align-items:center;gap:18px;padding-right:18px}
.bar .util a{font-size:10px;letter-spacing:2px;text-transform:uppercase;color:#8d8d8b;
  text-decoration:none;transition:color .12s ease}
.bar .util a:hover,.bar .util a.on{color:var(--paper)}
.picker select{appearance:none;background:transparent;border:1px solid #333331;color:var(--paper);
  font-family:var(--mono);font-size:10px;padding:6px 24px 6px 9px;cursor:pointer;
  transition:border-color .12s ease;
  background-image:linear-gradient(45deg,transparent 50%,#8d8d8b 50%),
    linear-gradient(135deg,#8d8d8b 50%,transparent 50%);
  background-position:calc(100% - 12px) 50%,calc(100% - 7px) 50%;
  background-size:5px 5px,5px 5px;background-repeat:no-repeat}
.picker{display:flex;align-items:center;align-self:center}
.picker select:hover{border-color:#6a6a68}

.wrap{max-width:1360px;margin:0 auto;padding:0 18px}
.run{display:flex;align-items:center;gap:24px;padding:15px 0;border-bottom:1px solid var(--ash);
  flex-wrap:wrap}
.run .name{font-family:var(--serif);font-weight:400;font-size:22px;letter-spacing:-0.3px;
  white-space:nowrap}
.run .hash{font-size:12px;font-weight:500;color:var(--graphite);white-space:nowrap}
.run .hash .lab{margin-right:8px}
.facts{display:flex;gap:22px;flex-wrap:wrap;margin-left:auto}
.facts b{font-weight:500;margin-left:7px;font-size:12px}
.chip{border:1px solid var(--good);color:var(--good);padding:5px 13px;font-size:10px;
  font-weight:700;letter-spacing:2px;text-transform:uppercase}

.view{display:none;padding:22px 0 70px} .view.on{display:block}
.actions{display:flex;align-items:center;gap:12px;margin-bottom:18px;flex-wrap:wrap}
.btn{font-family:var(--mono);font-size:10px;letter-spacing:2.2px;text-transform:uppercase;
  border:1px solid var(--ink);background:transparent;color:var(--ink);padding:10px 18px;
  cursor:pointer;text-decoration:none;display:inline-block;
  transition:background-color .14s ease,color .14s ease,border-color .14s ease}
.btn:hover{background:var(--ink);color:var(--paper)}
.btn.pri{background:var(--blue);border-color:var(--blue);color:#fff}
.btn.pri:hover{background:#1f3f96;border-color:#1f3f96}
.btn.ghost{border-color:var(--ash);color:var(--graphite)}
.btn.ghost:hover{border-color:var(--ink);color:var(--ink);background:transparent}

h2{font-size:10px;letter-spacing:2.5px;text-transform:uppercase;font-weight:700;margin:30px 0 0}
h2:first-child{margin-top:0}
.hint{color:var(--smoke);font-size:11px;margin-top:6px}

table{width:100%;border-collapse:collapse;font-size:12px;margin-top:12px}
th{text-align:left;font-weight:400;font-size:9px;letter-spacing:2.5px;text-transform:uppercase;
  color:var(--smoke);padding:8px 12px 8px 10px;border-bottom:1px solid var(--ash)}
td{padding:9px 12px 9px 10px;border-bottom:1px solid var(--ash);color:var(--graphite);
  transition:background-color .12s ease}
td:first-child{color:var(--ink);box-shadow:inset 2px 0 0 transparent;transition:box-shadow .12s ease}
tbody tr:hover td{background:var(--tint)}
tbody tr:hover td:first-child{box-shadow:inset 2px 0 0 var(--blue)}
.num{color:var(--smoke)} .right{text-align:right}
.absent{color:var(--smoke)}
.absent::before{content:"";display:inline-block;width:10px;height:1px;background:var(--ash);
  vertical-align:middle;margin-right:7px}

/* Provenance tag. Every number says where it came from -- a BCM forecast and a Cost
   Explorer actual are different claims and must never share a typeface with no label. */
.src{font-size:9px;letter-spacing:1.6px;text-transform:uppercase;border:1px solid var(--ash);
  padding:2px 6px;color:var(--smoke);white-space:nowrap}
.src.actual{border-color:var(--good);color:var(--good)}
.src.forecast{border-color:var(--blue);color:var(--blue)}

.sev{font-size:9px;letter-spacing:1.6px;text-transform:uppercase;font-weight:700}
.sev.high{color:var(--crit)} .sev.med{color:var(--warn)} .sev.low{color:var(--smoke)}

.cells{display:grid;gap:1px;background:var(--ash);border:1px solid var(--ash)}
.cells.c4{grid-template-columns:repeat(4,1fr)}
.cells.c5{grid-template-columns:repeat(5,1fr)}
.cells>div{background:var(--paper);padding:13px 15px}
.cells b{display:block;font-size:17px;font-weight:500;margin-top:5px}
.cells .sub{font-size:10px;color:var(--smoke);margin-top:4px}

.canvas{width:100%;height:400px;border:1px solid var(--ash);display:block;background:var(--paper)}
.hops{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:var(--ash);
  border:1px solid var(--ash);margin-top:12px}
.hops button{background:var(--paper);border:0;border-top:2px solid transparent;text-align:left;
  padding:13px 14px;cursor:pointer;font-family:var(--mono);
  transition:border-color .14s ease,background-color .14s ease}
.hops button:hover{border-top-color:var(--blue);background:var(--tint)}
.hops .name{display:block;font-size:13px;margin:5px 0 3px;color:var(--ink)}
.hops .sub{font-size:11px;color:var(--graphite)}
.hops .gold{border-top-color:var(--good)}

.stages{border:1px solid var(--ash);border-bottom:0;margin-top:12px}
.stage{display:grid;grid-template-columns:190px 1fr 110px;gap:18px;padding:11px 14px;
  border-bottom:1px solid var(--ash);align-items:baseline;transition:background-color .12s ease}
.stage:hover{background:var(--tint)}
.stage .who{font-size:12px;font-weight:500}
.stage .what{color:var(--graphite);font-size:12px}
.stage .st{font-size:9px;letter-spacing:2.2px;text-transform:uppercase;color:var(--smoke);
  text-align:right}
.stage.ran{box-shadow:inset 2px 0 0 var(--good)}

/* Scale curve: bars, not a chart library. Width is the only encoding and it is honest. */
.curve{margin-top:12px;border:1px solid var(--ash)}
.curve .row{display:grid;grid-template-columns:90px 1fr 120px;gap:14px;align-items:center;
  padding:11px 14px;border-bottom:1px solid var(--ash)}
.curve .row:last-child{border-bottom:0}
/* display:block -- a percentage width is ignored on an inline span, so the fill was
   invisible and every row looked identical. */
.curve .track{display:block;height:10px;background:rgba(206,202,200,.35)}
.curve .fill{display:block;height:10px;background:var(--blue)}
.curve .amt{text-align:right;font-weight:500}

details{border-top:1px solid var(--ash);margin-top:16px}
summary{cursor:pointer;list-style:none;padding:12px 0;font-size:9px;letter-spacing:2.5px;
  text-transform:uppercase;color:var(--smoke);transition:color .12s ease}
summary:hover{color:var(--ink)}
summary::-webkit-details-marker{display:none}
summary::before{content:"+ "}
details[open] summary::before{content:"- "}
details ul{padding:0 0 16px 16px;color:var(--smoke);font-size:12px;columns:3}

.docrow{cursor:pointer}

/* ---------------------------------------------------------------------------------------
   Document viewer. A governance document is the thing you came to read, so it gets the
   screen -- not a 440px column beside a list.
   --------------------------------------------------------------------------------------- */
.overlay{position:fixed;inset:0;background:rgba(36,36,36,.55);z-index:40;display:none;
  padding:34px}
.overlay[open]{display:block}
.sheet{background:var(--paper);border:1px solid var(--ash);height:100%;display:flex;
  flex-direction:column;max-width:1180px;margin:0 auto}
.sheet header{display:flex;align-items:center;gap:16px;padding:14px 18px;
  border-bottom:1px solid var(--ash)}
.sheet header .name{font-size:13px;font-weight:500}
.sheet header .spacer{flex:1}
.sheet .body{flex:1;overflow:auto;padding:18px;min-height:0}
.sheet .body pre{font-size:12px;line-height:1.7;white-space:pre;color:var(--graphite)}
.sheet .body iframe,.sheet .body embed{width:100%;height:100%;border:1px solid var(--ash);
  background:#fff}
.sheet .body .none{color:var(--smoke);text-align:center;padding:70px 20px}
.sheet .body .none b{display:block;color:var(--ink);font-weight:500;margin-bottom:8px}

.chart{width:100%;height:auto;display:block;margin-top:14px;border:1px solid var(--ash);
  padding:10px 6px;background:var(--paper)}
.chart .ax{font-family:var(--mono);font-size:9px;fill:var(--smoke);letter-spacing:1.4px}
/* paint-order:stroke draws a parchment halo behind the glyphs, so a series label sitting
   on its own line stays readable without moving it off the data it names. */
.chart .ser{font-family:var(--mono);font-size:10px;letter-spacing:0.6px;
  paint-order:stroke;stroke:var(--paper);stroke-width:4px;stroke-linejoin:round}
/* Canvas + interception ---------------------------------------------------------------- */
.canvaswrap{border:1px solid var(--ash)}
.canvaswrap .canvas{border:0;height:520px}
.intercept{border-top:1px solid var(--ash);background:var(--mist)}
.intercept-head{display:flex;align-items:center;gap:12px;padding:12px 14px}
.intercept-head .spacer{flex:1}
.changelist{list-style:none;padding:0 14px 14px;margin:0}
.changelist li{font-size:12px;padding:7px 0;border-top:1px solid rgba(36,36,36,.14);
  color:var(--ink)}
.changelist li .lab{margin-right:10px}
.changelist li b{font-weight:500}
.changelist li .from{color:var(--crit)} .changelist li .to{color:var(--good)}
.changelist .noop{color:var(--graphite)}

/* Review modal: FR-05.2. Periwinkle is spent here, on the one screen that asks rather
   than reports. */
.review{background:var(--mist);border-color:transparent}
.review h3{font-family:var(--serif);font-weight:400;font-size:22px;margin-bottom:4px}
.review .meta{display:flex;gap:20px;flex-wrap:wrap;margin:10px 0 16px}
.review .warn{border-left:2px solid var(--warn);padding:8px 0 8px 12px;margin-bottom:16px;
  font-size:12px;color:var(--graphite)}
.review pre{font-size:11px;line-height:1.7;white-space:pre;overflow:auto;
  background:var(--paper);border:1px solid var(--ash);padding:12px;max-height:260px}
.review pre .add{color:var(--good)} .review pre .del{color:var(--crit)}
.review .foot{display:flex;gap:12px;margin-top:18px;flex-wrap:wrap;align-items:center}
/* 02 Lineage rail: datasets and the jobs between them ---------------------------------- */
.rail{margin-top:12px;border:1px solid var(--ash)}
/* A dataset row has four children (label, name, meta, run state); three columns wrapped
   the run state onto a second line under the label. */
.rail .ds{padding:13px 15px;border-bottom:1px solid var(--ash);display:grid;
  grid-template-columns:80px 300px 1fr auto;gap:14px;align-items:baseline}
.rail .job{padding:13px 15px;border-bottom:1px solid var(--ash);display:grid;
  grid-template-columns:80px auto 1fr;gap:14px;align-items:baseline}
.rail>div:last-child{border-bottom:0}
.rail .job{background:rgba(206,202,200,.16)}
.rail .dsname{font-size:14px;color:var(--ink)}
.rail .dsmeta,.rail .jobmeta{font-size:11px;color:var(--graphite)}
.rail .job b{font-size:13px;font-weight:500;white-space:nowrap}
.rail .dsrun{font-size:10px;letter-spacing:1.4px;text-transform:uppercase}
.rail .ds.gold{box-shadow:inset 2px 0 0 var(--good)}

.notice{border:1px solid var(--ash);border-left:2px solid var(--warn);padding:13px 15px;
  margin-top:18px;background:rgba(206,202,200,.12)}
.notice p{font-size:12px;color:var(--graphite);margin-top:6px}

/* 05 The machine ------------------------------------------------------------------------ */
.machine{margin-top:12px;border:1px solid var(--ash)}
.machine .step{display:grid;grid-template-columns:22px 190px 1fr 90px;gap:14px;width:100%;
  text-align:left;background:var(--paper);border:0;border-bottom:1px solid var(--ash);
  padding:12px 14px;cursor:pointer;font-family:var(--mono);align-items:baseline;
  transition:background-color .12s ease}
.machine .step:last-child{border-bottom:0}
.machine .step:hover{background:var(--tint)}
.machine .dot{width:9px;height:9px;background:var(--ash);align-self:center;
  box-shadow:0 0 0 3px var(--paper)}
.machine .step.ran .dot{background:var(--good)}
.machine .step .who{font-size:12px;font-weight:500;color:var(--ink)}
.machine .step .what{font-size:12px;color:var(--graphite)}
.machine .step .st{font-size:9px;letter-spacing:2.2px;text-transform:uppercase;
  color:var(--smoke);text-align:right}
.trace dl{margin-top:4px}
.trace .k{font-size:9px;letter-spacing:2.5px;text-transform:uppercase;color:var(--smoke);
  margin-top:16px}
.trace .v{font-size:13px;margin-top:5px;color:var(--ink)}

/* 06 Delivery lanes ---------------------------------------------------------------------- */
.lanes{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--ash);
  border:1px solid var(--ash);margin-top:12px}
.lanes .lane{background:var(--paper);padding:14px 15px;border-top:2px solid var(--blue)}
.lanes .lane b{display:block;font-size:14px;font-weight:500;margin:5px 0 3px}
.lanes .lane .sub{font-size:11px;color:var(--graphite)}
.converge{display:grid;grid-template-columns:repeat(4,1fr);height:20px}
.converge span{border-right:1px solid var(--ash);border-bottom:1px solid var(--ash)}
.converge span:last-child{border-right:0}
.gate{border:1px solid var(--ink);padding:14px 16px;display:flex;gap:16px;align-items:baseline}
.gate b{font-size:14px;font-weight:500}
.gate .sub{font-size:11px;color:var(--graphite)}

/* Settings ------------------------------------------------------------------------------- */
.btn.sm{padding:6px 12px;font-size:9px}
.locked{font-size:9px;letter-spacing:1.6px;text-transform:uppercase;color:var(--smoke);
  border:1px solid var(--ash);padding:1px 6px;margin-left:8px}
.secref{font-size:11px;color:var(--graphite)}
.ok{color:var(--good)}
.cells b.ok{color:var(--good)} .cells b.absent{color:var(--smoke);font-weight:400}
/* 02 Flow -------------------------------------------------------------------------------- */
.switch{display:flex;align-items:center;gap:0;border-bottom:1px solid var(--ash);
  margin-bottom:20px}
.switch button{background:none;border:0;border-bottom:2px solid transparent;cursor:pointer;
  font-family:var(--mono);font-size:10px;letter-spacing:2.2px;text-transform:uppercase;
  color:var(--smoke);padding:10px 18px;margin-bottom:-1px;transition:color .12s ease}
.switch button:hover{color:var(--ink)}
.switch button.on{color:var(--ink);border-bottom-color:var(--ink)}
.switch .lab{margin-left:auto}

.chain{display:flex;align-items:stretch;gap:0;flex-wrap:wrap;margin-bottom:20px}
.chain .link{align-self:center;color:var(--smoke);padding:0 10px;user-select:none}
.chain .node,.chain .lane{background:var(--paper);border:1px solid var(--ash);
  border-top:2px solid var(--ash);padding:12px 16px;cursor:pointer;font-family:var(--mono);
  text-align:left;min-width:150px;transition:border-color .14s ease,background-color .14s ease}
.chain .node:hover,.chain .lane:hover{border-top-color:var(--blue);background:var(--tint)}
.chain .node.sel,.chain .lane.sel{border-top-color:var(--blue);background:var(--mist);
  border-color:var(--blue)}
.chain .nlabel{display:block;font-size:14px;color:var(--ink);margin:5px 0 3px}
.chain .nkind{font-size:10px;letter-spacing:1.6px;text-transform:uppercase;color:var(--smoke)}
.chain .node.job{background:rgba(206,202,200,.2)}
.chain .node.job:hover{background:var(--tint)}
.lanes2{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--ash);
  border:1px solid var(--ash);margin-bottom:0}
.lanes2 .lane{border:0;border-top:2px solid var(--blue);min-width:0}
.lanes2 .lane b{display:block;font-size:14px;font-weight:500;margin:5px 0 3px}
.lanes2 .lane .sub{font-size:11px;color:var(--graphite)}

.detail{border:1px solid var(--ash);padding:16px 18px;margin-bottom:8px}
.detail .dhead{display:flex;align-items:baseline;gap:14px;margin-bottom:12px}
.detail .dhead b{font-size:15px;font-weight:500}
.detail .dhead .addr{font-size:11px;color:var(--graphite)}
.detail .facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;
  background:var(--ash);border:1px solid var(--ash)}
.detail .facts>div{background:var(--paper);padding:10px 12px}
.detail .facts b{display:block;font-size:12px;font-weight:500;margin-top:4px}
.detail ul{margin:6px 0 0 16px;font-size:12px;color:var(--graphite)}
.detail li{margin-bottom:4px}

.hops2{border:1px solid var(--ash)}
.hops2 details{border:0;border-bottom:1px solid var(--ash);margin:0}
.hops2 details:last-child{border-bottom:0}
.hops2 summary{padding:11px 14px;font-size:12px;letter-spacing:0;text-transform:none;
  color:var(--ink);display:grid;grid-template-columns:22px 1fr 170px 120px;gap:14px;
  align-items:baseline}
.hops2 summary::before{content:"+";color:var(--smoke)}
.hops2 details[open] summary::before{content:"-"}
.hops2 summary .path{color:var(--graphite)}
.hops2 summary .tp{font-size:11px;color:var(--graphite)}
.hops2 summary .st{font-size:9px;letter-spacing:2.2px;text-transform:uppercase;
  color:var(--smoke);text-align:right}
/* No padding: the grid gap paints in --ash, so padding on the container framed the whole
   panel in grey. Margin positions it; the cells fill it edge to edge. */
.hops2 .body{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;
  background:var(--ash);margin:0 14px 14px 50px;border:1px solid var(--ash)}
.hops2 .body>div{background:var(--paper);padding:10px 12px}
.hops2 .body b{display:block;font-size:12px;font-weight:400;color:var(--ink);margin-top:4px}
.hops2 .body .risk b{color:var(--warn)}
/* Delivery pipeline: a vertical spine ---------------------------------------------------- */
.pipeline{margin-top:14px;border:1px solid var(--ash)}
.pstep{display:grid;grid-template-columns:44px 1fr;border-bottom:1px solid var(--ash);
  position:relative}
.pstep:last-child{border-bottom:0}
/* The spine: a hairline through the dot column, broken at the first and last step. */
.pstep::before{content:"";position:absolute;left:21px;top:0;bottom:0;width:1px;
  background:var(--ash)}
.pstep:first-child::before{top:22px}
.pstep:last-child::before{bottom:calc(100% - 22px)}
.pdot{width:9px;height:9px;margin:18px auto 0;background:var(--ash);position:relative;z-index:1;
  box-shadow:0 0 0 4px var(--paper)}
.pstep.done .pdot{background:var(--good)}
.pstep.blocked .pdot{background:var(--crit)}
.pstep.absent .pdot{background:var(--ash)}
.pstep.skipped .pdot{background:var(--paper);border:1px solid var(--ash)}
.pbody{padding:13px 16px 15px 0}
.phead{display:flex;align-items:baseline;gap:14px}
.phead b{font-size:14px;font-weight:500}
.phead .pstate{margin-left:auto;font-size:9px;letter-spacing:2.2px;text-transform:uppercase;
  color:var(--smoke)}
.pstep.done .phead .pstate{color:var(--good)}
.pstep.blocked .phead .pstate{color:var(--crit)}
.pnote{font-size:12px;color:var(--graphite);margin-top:5px;max-width:78ch}
.pstep ul{margin:8px 0 0 16px;font-size:12px;color:var(--graphite)}
.pstep li{margin-bottom:3px}
.pstep.skipped .phead b,.pstep.skipped .pnote{color:var(--smoke)}
/* The blocked step is where the run actually stands, so it gets the one strong mark. */
.pstep.blocked{background:rgba(143,45,24,.04)}
.pstep.blocked .pbody{box-shadow:inset 2px 0 0 var(--crit)}
.pstep.blocked .pbody{padding-left:14px}

.lanebox{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--ash);
  border:1px solid var(--ash);margin-top:12px}
.lanebox .lane{background:var(--paper);border:0;border-top:2px solid var(--blue);
  padding:11px 13px;cursor:pointer;font-family:var(--mono);text-align:left;
  transition:background-color .14s ease}
.lanebox .lane:hover{background:var(--tint)}
.lanebox .lane.sel{background:var(--mist)}
.lanebox .lane b{display:block;font-size:13px;font-weight:500;margin:4px 0 2px}
.lanebox .lane .sub{font-size:11px;color:var(--graphite)}
.lanedetail{border:1px solid var(--ash);border-top:0;padding:12px 14px}
.lanedetail ul{margin:6px 0 0 16px}
@media (max-width:1000px){.cells.c4,.cells.c5{grid-template-columns:repeat(2,1fr)}
  .hops{grid-template-columns:repeat(2,1fr)} .stage{grid-template-columns:1fr}
  details ul{columns:1} .overlay{padding:12px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
    /* --- Dash-owned DOM ------------------------------------------------------------------
       The run picker is a dcc.Dropdown, so its markup is react-select's rather than a bare
       <select>. These rules exist only to make it look like the mockup's control; nothing
       here changes the design. */
    /* Settings is a control, not a link -- it switches a view rather than navigating --
       but it has to read as one of the utility links, not as browser form chrome. */
    .bar .util .utilbtn{background:none;border:0;padding:0;cursor:pointer;
      font-family:var(--mono);font-size:10px;letter-spacing:2px;text-transform:uppercase;
      color:#8d8d8b;transition:color .12s ease}
    .bar .util .utilbtn:hover{color:var(--paper)}
    .runpick{min-width:330px}
    .runpick .Select-control,.runpick .Select-menu-outer,.runpick .Select-value,
    .runpick .Select-placeholder,.runpick .Select-input{background:transparent!important;
      border-radius:0!important;font-family:var(--mono)!important;font-size:10px!important;
      color:var(--paper)!important}
    .runpick .Select-control{border:1px solid #333331!important;height:28px!important;
      cursor:pointer}
    .runpick .Select-control:hover{border-color:#6a6a68!important}
    .runpick .Select-value,.runpick .Select-placeholder{line-height:26px!important;
      padding-left:9px!important}
    .runpick .Select-value-label{color:var(--paper)!important}
    .runpick .Select-arrow{border-top-color:#8d8d8b!important}
    .runpick .Select-menu-outer{background:var(--black)!important;
      border:1px solid #333331!important;margin-top:1px}
    .runpick .Select-option{background:var(--black)!important;color:#8d8d8b!important;
      font-size:10px!important;padding:8px 9px!important}
    .runpick .Select-option.is-focused{background:#1a1a19!important;color:var(--paper)!important}
    .runpick .Select-option.is-selected{color:var(--paper)!important}
    /* Dash injects an update spinner; the console is not a live dashboard. */
    ._dash-loading,._dash-undo-redo{display:none}

  </style>
</head>
<body>{%app_entry%}<footer>{%config%}{%scripts%}{%renderer%}</footer></body>
</html>"""



# --- Bind and auth guard ------------------------------------------------------------------
#
# Ported from app/dashboard_app.py when that was retired. This console reads governance
# evidence, BCM cost figures and IAM detail for a live AWS account, so the two controls it
# carried are not decoration:
#
#   1. At BIND time, refuse to listen on a non-loopback interface unless a token is set.
#   2. At REQUEST time, once a token IS set, reject anything that does not present it.
#
# Loosening either puts live account data on the LAN.

def _console_token():
    return os.environ.get("MINUS_DASH_TOKEN") or os.environ.get("DASH_TOKEN")


def _is_loopback_host(host):
    host = (host or "").strip().lower()
    return host in {"", "localhost", "127.0.0.1", "::1"} or host.startswith("127.")


def _remote_bind_requires_token(host):
    return not _is_loopback_host(host) and not _console_token()


def _valid_token(value):
    # compare_digest, never `==`: a comparison that short-circuits on the first wrong byte
    # leaks the token's length and prefix to anyone who can time it.
    token = _console_token()
    return bool(token and value and hmac.compare_digest(str(value), str(token)))


def _request_authorized():
    """True when no token is configured, which is reachable only on a loopback bind.

    Failing closed here instead would break plain `minusctl console` for everyone; the guard
    that matters is at bind time, and main() refuses a non-loopback host with no token.
    """
    token = _console_token()
    if not token:
        return True
    from flask import request
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer ") and _valid_token(auth[7:].strip()):
        return True
    if _valid_token(request.args.get("token")):
        return True
    return _valid_token(request.cookies.get("minus_console_token"))


@app.server.before_request
def _enforce_console_auth():
    if _request_authorized():
        return None
    from flask import Response
    return Response("console authentication required", 401,
                    {"WWW-Authenticate": 'Bearer realm="minusops-console"'})


@app.server.after_request
def _persist_console_token(response):
    """Carry a `?token=` through to later requests, so the browser is not asked to append it
    to every asset URL."""
    from flask import request
    if _valid_token(request.args.get("token")):
        response.set_cookie("minus_console_token", _console_token(),
                            httponly=True, samesite="Lax")
    return response


def main(argv=None):
    """Serve the console. Loopback by default -- this surface reads governance evidence."""
    import argparse
    parser = argparse.ArgumentParser(prog="minusctl console")
    parser.add_argument("--host", default=os.environ.get("CONSOLE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("CONSOLE_PORT", "8050")))
    args = parser.parse_args(argv)
    if _remote_bind_requires_token(args.host):
        print("\n  Refusing to serve on {0}: this console exposes governance evidence,"
              "\n  cost figures and IAM detail for a live account.\n"
              "\n  Set a token first:"
              "\n    MINUS_DASH_TOKEN=\"$(openssl rand -hex 32)\" "
              "minusctl console --host {0}\n".format(args.host), file=sys.stderr)
        return 2
    print(f"\n  MinusOps Governance Console  ->  http://{args.host}:{args.port}"
          f"   (Ctrl+C to stop)\n")
    app.run(host=args.host, port=args.port, debug=False, dev_tools_hot_reload=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
