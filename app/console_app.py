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

VIEWS = (
    ("topology", "1. Architecture Topology"),
    ("lineage", "2. Data Lineage & Governance"),
    ("trace", "3. Multi-Agent Execution Trace"),
    ("vault", "4. Deliverables & Compliance Vault"),
)

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


# --- Shared chrome ----------------------------------------------------------------------

def run_header(state):
    """FR-01.2. Every fact here is read, never derived -- an unproven plan hash on a
    governance header is exactly the claim this product exists to refuse."""
    record = state.get("run") or {}
    plan_hash = (state.get("plan") or {}).get("plan_hash") or record.get("plan_hash")
    cost = record.get("estimated_monthly_cost")
    chips = [
        ("Domain", record.get("domain") or "not declared"),
        ("Tier", record.get("tier") or "UNCLASSIFIED"),
        ("Plan hash", (plan_hash[:16] if plan_hash else "not planned")),
        ("BCM cost", f"${cost}/mo" if cost else "not priced"),
        ("Status", record.get("governance_status") or "GENERATED"),
    ]
    return html.Div(className="run-header", children=[
        html.Div(className="run-id", children=record.get("run_id", "no run selected")),
        html.Div(className="chips", children=[
            html.Span(className="chip", children=[
                html.Small(label), html.Strong(value)]) for label, value in chips]),
    ])


def requirements_drawer(state):
    """FR-01.3. Collapsed by default: it is reference material, not the working surface."""
    decision = state.get("decision") or {}
    modules = decision.get("selected_modules") or []
    missing = decision.get("missing_requirements") or []
    return html.Details(className="drawer", children=[
        html.Summary("Requirements and architecture decision"),
        html.Div(className="drawer-body", children=[
            html.P(decision.get("architecture") or "No architecture decision recorded.",
                   className="drawer-lead"),
            html.Div(className="drawer-grid", children=[
                html.Div([html.H4("Selected modules"),
                          html.Ul([html.Li(m) for m in modules] or [html.Li("none")])]),
                html.Div([html.H4("Missing or deferred"),
                          html.Ul([html.Li(m) for m in missing]
                                  or [html.Li("none recorded")])]),
            ]),
        ]),
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


RENDERERS = {"topology": view_topology, "lineage": view_lineage,
             "trace": view_trace, "vault": view_vault}


# --- App --------------------------------------------------------------------------------

app = dash.Dash(__name__, title="MinusOps Governance Console")
server = app.server
# The four views are rendered on demand, so components a callback targets (the vault
# exporter, for one) are absent from the initial layout. Without this Dash validates
# callbacks against that first layout and errors on page load -- which the tests did not
# catch, because a structural check of the layout tree cannot see it. The browser did.
app.config.suppress_callback_exceptions = True

app.layout = html.Div(className="page", children=[
    dcc.Store(id="run-id"),
    dcc.Store(id="reconcile-proposal"),
    html.Header(className="masthead", children=[
        html.Div(className="brand", children=[
            html.Span(className="dot"),
            html.Div([html.H1("MinusOps"),
                      html.Small("VISUAL GOVERNANCE CONSOLE")]),
        ]),
    ]),
    html.Div(id="header-slot"),
    html.Div(id="drawer-slot"),
    dcc.Tabs(id="view", value="topology", className="views",
             children=[dcc.Tab(label=label, value=key, className="view-tab",
                               selected_className="view-tab selected")
                       for key, label in VIEWS]),
    dcc.Loading(html.Div(id="view-slot", className="view-body"), color=C["ink"]),
])


@app.callback(
    Output("header-slot", "children"),
    Output("drawer-slot", "children"),
    Output("view-slot", "children"),
    Input("view", "value"),
    State("run-id", "data"),
)
def _render(view, run_id):
    state = assemble(run_id)
    if not state.get("run"):
        return (html.Div(), html.Div(),
                _empty("No runs found", "Create one with `minusctl create`."))
    renderer = RENDERERS.get(view, view_topology)
    return run_header(state), requirements_drawer(state), renderer(state)


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
  <link href="https://fonts.googleapis.com/css2?family=Instrument+Serif&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    /* ----------------------------------------------------------------------------------
       Monad design tokens, transcribed from DESIGN.md. Every rule below reads from these
       and nothing else, so the design file stays the source of truth instead of something
       paraphrased once into hardcoded values.
       ---------------------------------------------------------------------------------- */
    :root{
      --color-parchment:#f6f3f1;--color-lake-blue:#2b59d1;--color-periwinkle-mist:#cfdaf5;
      --color-sky-blue:#a0b5eb;--color-mint:#a7fccd;--color-coral:#ff9473;
      --color-gold:#ecda98;--color-crimson:#f37a0a;--color-off-black:#242424;
      --color-ink:#000000;--color-graphite:#4e4d4d;--color-smoke:#797776;--color-ash:#cecac8;

      /* Licensed faces first: an operator who has them gets them. The substitutes are the
         ones DESIGN.md names, with Instrument Serif standing in for Untitled Serif -- it
         carries the same stroke contrast at weight 400, which Georgia does not. */
      --font-mono:'ABC Diatype Mono','JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,
        Consolas,monospace;
      --font-serif:'Untitled Serif','Instrument Serif',Georgia,Cambria,'Times New Roman',serif;

      --text-caption:12px;--tracking-caption:-0.4px;
      --text-body-sm:14px;--tracking-body-sm:-0.28px;
      --text-body:16px;--tracking-body:-0.4px;
      --text-label:18px;--tracking-label:-0.4px;
      --text-body-lg:20px;--tracking-body-lg:-0.4px;
      --text-subheading:24px;--tracking-subheading:-0.48px;
      --text-heading-sm:32px;--tracking-heading-sm:-0.64px;
      --text-heading:40px;--tracking-heading:-0.8px;
      --leading-tight:1.2;--leading-body:1.35;

      --spacing-8:8px;--spacing-16:16px;--spacing-24:24px;--spacing-32:32px;
      --spacing-40:40px;--spacing-64:64px;--spacing-80:80px;

      --radius-2xl:16px;--radius-cards:40px;--radius-buttons:100px;--radius-pills:9999px;

      --page-max-width:1432px;--section-gap:64px;--card-padding:40px;--element-gap:16px;

      --surface-parchment:#f6f3f1;--surface-periwinkle-mist:#cfdaf5;
      --surface-off-black:#242424;--surface-ink:#000000;
      /* --shadow-md exists in the token file and is deliberately unused: the Don't list
         forbids drop shadows on cards. Elevation here is surface plus hairline only. */
      --shadow-md:rgba(0,0,0,0.1) 0px 0px 10px 0px;

      /* Verdict ink. NOT from the token file, and deliberately not from the pastels --
         Coral, Mint and Gold are declared decorative-only, so borrowing them for pass/fail
         would put brand decoration on a governance judgement. These are dark, desaturated
         neighbours chosen to clear contrast on parchment without reading as accents. */
      --good:#2f6b4f;--warn:#8a6516;--crit:#8f2d18;
    }
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:var(--surface-parchment);color:var(--color-off-black);
      font-family:var(--font-mono);font-size:var(--text-body);line-height:var(--leading-body);
      letter-spacing:var(--tracking-body);-webkit-font-smoothing:antialiased}
    h1,h2,h3,h4{font-family:var(--font-serif);font-weight:400;line-height:var(--leading-tight)}
    :focus-visible{outline:2px solid var(--color-lake-blue);outline-offset:3px}

    .page{max-width:var(--page-max-width);margin:0 auto;padding:var(--spacing-32)
      var(--spacing-40) var(--spacing-80)}

    /* Masthead ------------------------------------------------------------------------ */
    .masthead{display:flex;align-items:center;justify-content:space-between;
      padding-bottom:var(--spacing-24);border-bottom:1px solid var(--color-ash)}
    .brand{display:flex;align-items:center;gap:var(--element-gap)}
    .dot{width:12px;height:12px;border-radius:var(--radius-pills);
      background:var(--color-lake-blue)}
    .brand h1{font-size:var(--text-subheading);letter-spacing:var(--tracking-subheading)}
    .brand small,.masthead>span{font-size:var(--text-caption);
      letter-spacing:var(--tracking-caption);text-transform:uppercase;color:var(--color-smoke)}

    /* Run header ---------------------------------------------------------------------- */
    .run-header{padding:var(--spacing-40) 0 var(--spacing-24)}
    .run-id{font-family:var(--font-serif);font-weight:400;font-size:var(--text-heading);
      line-height:var(--leading-tight);letter-spacing:var(--tracking-heading)}
    .chips{display:flex;flex-wrap:wrap;gap:var(--spacing-8);margin-top:var(--spacing-24)}
    .chip{display:inline-flex;flex-direction:column;gap:2px;border:1px solid var(--color-ash);
      border-radius:var(--radius-pills);padding:12px 20px}
    .chip small{font-size:var(--text-caption);letter-spacing:var(--tracking-caption);
      text-transform:uppercase;color:var(--color-smoke)}
    .chip strong{font-weight:500;font-size:var(--text-body-sm);
      letter-spacing:var(--tracking-body-sm)}

    /* Cards: 40px radius, 40px padding, 1px ash, no shadow ---------------------------- */
    .drawer,.panel,.inspector,.stage,.empty,.review-modal{
      border:1px solid var(--color-ash);border-radius:var(--radius-cards);
      padding:var(--card-padding)}
    .drawer{margin-bottom:var(--spacing-32)}
    .drawer:not([open]){padding:var(--spacing-24) var(--card-padding)}
    .panel,.inspector{margin-top:var(--spacing-32)}
    .drawer summary{cursor:pointer;font-size:var(--text-caption);text-transform:uppercase;
      letter-spacing:var(--tracking-caption);color:var(--color-graphite);list-style:none}
    .drawer summary::-webkit-details-marker{display:none}
    .drawer summary::before{content:"+ ";color:var(--color-smoke)}
    .drawer[open] summary::before{content:"- "}
    .drawer-body{padding-top:var(--spacing-24)}
    .drawer-lead{font-family:var(--font-serif);font-size:var(--text-body-lg);
      margin-bottom:var(--spacing-16);color:var(--color-graphite)}
    .drawer-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
      gap:var(--spacing-40)}
    .drawer-grid h4{font-size:var(--text-subheading);
      letter-spacing:var(--tracking-subheading);margin-bottom:var(--spacing-8)}
    .drawer-grid li{margin-left:18px;color:var(--color-graphite);font-size:var(--text-body-sm);
      letter-spacing:var(--tracking-body-sm)}
    .panel h4,.inspector h4{font-size:var(--text-subheading);
      letter-spacing:var(--tracking-subheading);margin-bottom:var(--spacing-16)}

    /* Navigation: 18px uppercase mono ------------------------------------------------- */
    .views .tab-container{display:flex;gap:var(--spacing-32);flex-wrap:wrap;
      border-bottom:1px solid var(--color-ash)!important}
    .view-tab{flex:0 0 auto!important;width:auto!important;
      font-family:var(--font-mono)!important;font-size:var(--text-label)!important;
      letter-spacing:var(--tracking-label)!important;text-transform:uppercase!important;
      color:var(--color-smoke)!important;background:transparent!important;border:0!important;
      border-bottom:1px solid transparent!important;
      padding:var(--spacing-16) var(--spacing-24)!important}
    .view-tab.selected{color:var(--color-off-black)!important;font-weight:500!important;
      border-bottom:1px solid var(--color-off-black)!important}
    .view-body{padding-top:var(--section-gap)}
    .view-actions{display:flex;align-items:center;gap:var(--spacing-24);
      margin-bottom:var(--spacing-40);flex-wrap:wrap}

    /* Pill buttons: 100px radius, 16px 32px padding, 14px uppercase ------------------- */
    .btn{font-family:var(--font-mono);font-size:var(--text-body-sm);
      letter-spacing:var(--tracking-body-sm);text-transform:uppercase;
      border:1px solid var(--color-off-black);border-radius:var(--radius-buttons);
      padding:var(--element-gap) var(--spacing-32);background:transparent;
      color:var(--color-off-black);cursor:pointer;text-decoration:none;display:inline-block;
      line-height:var(--leading-tight);transition:background-color .15s ease,color .15s ease}
    .btn:hover{background:var(--color-off-black);color:var(--surface-parchment)}
    /* Lake Blue is the single primary action per screen. Off-Black carries every other
       filled button -- scattering the accent is what drains it of meaning. */
    .btn.primary{background:var(--color-lake-blue);border-color:var(--color-lake-blue);
      color:#fff}
    .btn.primary:hover{background:#2247a8;border-color:#2247a8;color:#fff}
    .btn.dark{background:var(--surface-off-black);border-color:var(--surface-off-black);
      color:var(--surface-parchment)}
    .btn.dark:hover{background:var(--color-ink);border-color:var(--color-ink)}

    .muted{color:var(--color-graphite);font-size:var(--text-body-sm);
      letter-spacing:var(--tracking-body-sm)}
    p.muted{margin:var(--element-gap) 0}

    /* Data surfaces ------------------------------------------------------------------- */
    .ledger,.code{border:1px solid var(--color-ash);border-radius:var(--radius-2xl);
      padding:var(--spacing-24);background:var(--surface-parchment);
      font-size:var(--text-caption);line-height:1.6;white-space:pre-wrap;overflow-x:auto;
      color:var(--color-graphite)}
    .table{width:100%;border-collapse:collapse;margin-top:var(--spacing-24);
      font-size:var(--text-body-sm);letter-spacing:var(--tracking-body-sm)}
    .table th{text-align:left;font-weight:400;font-size:var(--text-caption);
      text-transform:uppercase;letter-spacing:var(--tracking-caption);color:var(--color-smoke);
      padding:var(--spacing-8) 12px;border-bottom:1px solid var(--color-ash)}
    .table td{padding:14px 12px;border-bottom:1px solid var(--color-ash);
      color:var(--color-graphite)}
    .table tr.absent td{color:var(--color-smoke)}
    .table tr.reject td{color:var(--warn)}

    /* Lineage: pipeline node tags joined by connectors -------------------------------- */
    .canvas{width:100%;height:560px;border:1px solid var(--color-ash);
      border-radius:var(--radius-cards);background:var(--surface-parchment);display:block;
      margin-bottom:var(--spacing-40)}
    .lineage{display:flex;flex-wrap:wrap;gap:var(--spacing-8);align-items:center;
      margin-bottom:var(--spacing-40)}
    .hop{display:flex;flex-direction:column;gap:2px;background:var(--surface-parchment);
      border:1px solid var(--color-ash);border-radius:var(--radius-pills);
      padding:12px 20px;min-width:180px;cursor:pointer;text-align:left;
      font-family:var(--font-mono);transition:border-color .15s ease}
    .hop:hover{border-color:var(--color-off-black)}
    .hop small{font-size:var(--text-caption);letter-spacing:var(--tracking-caption);
      text-transform:uppercase;color:var(--color-smoke)}
    .hop strong{font-family:var(--font-serif);font-weight:400;
      font-size:var(--text-body-lg);line-height:var(--leading-tight)}
    .hop span{font-size:var(--text-caption);letter-spacing:var(--tracking-caption);
      color:var(--color-graphite)}
    .hop-quality{border-color:var(--warn)}
    .hop-gold{border-color:var(--good)}
    .hop-link{color:var(--color-smoke);font-size:var(--text-body-sm);user-select:none}
    .hop-link.branch{color:var(--warn)}

    /* Timeline ------------------------------------------------------------------------ */
    .timeline{display:flex;flex-direction:column;gap:var(--element-gap);
      margin-top:var(--spacing-40)}
    .stage{border-left:3px solid var(--color-ash);
      padding:var(--spacing-32) var(--card-padding)}
    .stage.ran{border-left-color:var(--good)}
    .stage-head{display:flex;justify-content:space-between;align-items:baseline;
      gap:var(--element-gap)}
    .stage-head strong{font-family:var(--font-serif);font-weight:400;
      font-size:var(--text-subheading);letter-spacing:var(--tracking-subheading)}
    .stage-status{font-size:var(--text-caption);letter-spacing:var(--tracking-caption);
      text-transform:uppercase;color:var(--color-smoke)}
    .stage small{display:block;color:var(--color-smoke);font-size:var(--text-caption);
      margin-top:var(--spacing-8)}
    .stage .audit{color:var(--color-graphite)}

    /* Vault --------------------------------------------------------------------------- */
    .link-button{background:none;border:0;padding:0;font-family:var(--font-mono);
      font-size:var(--text-body-sm);letter-spacing:var(--tracking-body-sm);
      color:var(--color-off-black);cursor:pointer;text-decoration:underline;
      text-underline-offset:3px;text-decoration-color:var(--color-ash)}
    .link-button:hover{text-decoration-color:var(--color-off-black)}
    .absent-list{margin-left:18px;color:var(--color-smoke);font-size:var(--text-body-sm);
      columns:2;gap:var(--spacing-40)}
    .absent-list li{margin-bottom:var(--spacing-8)}
    .preview-head{display:flex;justify-content:space-between;align-items:center;
      gap:var(--spacing-24);margin-bottom:var(--spacing-24)}
    .preview-frame{width:100%;height:440px;border:1px solid var(--color-ash);
      border-radius:var(--radius-2xl);background:var(--surface-parchment)}

    /* Reconciliation ------------------------------------------------------------------ */
    .reconcile-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
      gap:var(--spacing-24);margin:var(--spacing-24) 0 var(--spacing-32)}
    .field{display:flex;flex-direction:column;gap:var(--spacing-8)}
    .field small{font-size:var(--text-caption);letter-spacing:var(--tracking-caption);
      text-transform:uppercase;color:var(--color-smoke)}
    .control-input{width:100%;border:1px solid var(--color-ash);
      border-radius:var(--radius-buttons);background:var(--surface-parchment);
      color:var(--color-off-black);font-family:var(--font-mono);font-size:var(--text-body-sm);
      letter-spacing:var(--tracking-body-sm);padding:14px 24px;outline:none}
    .control-input:focus{border-color:var(--color-lake-blue)}
    /* Periwinkle is the one elevated surface in the system, spent here because this is the
       one moment the console asks for a decision instead of reporting one. */
    .review-modal{background:var(--surface-periwinkle-mist);border-color:transparent;
      margin-top:var(--spacing-24)}
    .review-modal h4{font-size:var(--text-subheading);
      letter-spacing:var(--tracking-subheading);margin-bottom:var(--spacing-16)}
    .review-modal .ledger{background:transparent;border-color:rgba(36,36,36,.18)}
    .review-meta{display:flex;flex-wrap:wrap;gap:var(--spacing-24);
      font-size:var(--text-caption);letter-spacing:var(--tracking-caption);
      color:var(--color-graphite);text-transform:uppercase;margin-bottom:var(--spacing-24)}
    .review-summary{font-family:var(--font-serif);font-size:var(--text-subheading);
      line-height:var(--leading-tight);letter-spacing:var(--tracking-subheading);
      margin-bottom:var(--spacing-24)}
    .review-warnings{margin:0 0 var(--spacing-24) 18px;font-size:var(--text-body-sm);
      letter-spacing:var(--tracking-body-sm);color:var(--color-graphite)}
    .review-warnings li{margin-bottom:var(--spacing-8)}
    .review-actions{display:flex;gap:var(--element-gap);flex-wrap:wrap;
      margin-top:var(--spacing-32)}

    /* Verdicts ------------------------------------------------------------------------ */
    .status-good,.status-warn,.status-bad{border:1px solid var(--color-ash);
      border-radius:var(--radius-2xl);padding:var(--spacing-24);margin-top:var(--spacing-24)}
    .status-good{border-left:3px solid var(--good)}
    .status-warn{border-left:3px solid var(--warn)}
    .status-bad{border-left:3px solid var(--crit)}

    .empty{padding:var(--spacing-80) var(--card-padding);text-align:center}
    .empty h3{font-size:var(--text-heading-sm);letter-spacing:var(--tracking-heading-sm);
      margin-bottom:var(--spacing-16)}

    @media (max-width:900px){
      .drawer-grid,.reconcile-form{grid-template-columns:1fr}
      .page{padding:var(--spacing-24) var(--spacing-24) var(--spacing-64)}
      .run-id{font-size:var(--text-heading-sm);letter-spacing:var(--tracking-heading-sm)}
      .drawer,.panel,.inspector,.stage,.review-modal{padding:var(--spacing-24)}
      .absent-list{columns:1}
    }
    @media (prefers-reduced-motion:reduce){*{transition:none!important}}
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
