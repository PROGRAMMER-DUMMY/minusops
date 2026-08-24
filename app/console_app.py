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

import access_model  # noqa: E402
import agent_flow_graph  # noqa: E402
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


# --- View 2: flow (data + delivery) -------------------------------------------------------

# Which kind of thing a lineage hop is, for the chain. The engine models hops by medallion
# layer; the chain reads better as dataset / job / consumer, which is the same information
# said the way an operator asks for it.
_HOP_KIND = {"bronze": "dataset", "silver": "dataset", "gold": "dataset",
             "transform": "job", "quality_gate": "job", "quarantine": "dataset",
             "ingress": "job", "serving": "consumer"}


def _kind_of(node):
    return _HOP_KIND.get(node.get("layer") or node.get("id"), "dataset")


def flow_chain(graph, selected):
    """The dataset chain. Selecting a node filters the hops and columns below it."""
    children = []
    for index, node in enumerate(graph.get("nodes") or []):
        if index:
            children.append(html.Span("->", className="link"))
        classes = f"node {_kind_of(node)}" + (" sel" if node["id"] == selected else "")
        children.append(html.Button(
            id={"kind": "flownode", "node": node["id"]}, n_clicks=0, className=classes,
            children=[
                html.Span(node.get("layer", "").replace("_", " "), className="lab"),
                html.Span(node.get("label", node["id"]), className="nlabel"),
                html.Span(_kind_of(node), className="nkind"),
            ]))
    return children


def flow_node_detail(graph, selected):
    """Facts for one hop. Absent ones are named, never blank: an empty retention cell on a
    governance surface reads as "no retention", which is a different claim from "this stack
    did not declare one"."""
    node = lineage_graph.find_node(graph or {}, selected) if selected else None
    if not node:
        return html.P("Select a dataset or job above. Everything below filters to it.",
                      className="muted")
    facts = [
        ("Format", node.get("table_format")),
        ("Partitioned by", node.get("partitioning")),
        ("Retention", node.get("retention")),
        ("Encryption", node.get("encryption")),
        ("Detail", node.get("detail")),
        ("Observed", None),
    ]
    return html.Div([
        html.Div(className="dhead", children=[
            html.B(node.get("label", node["id"])),
            html.Span(_kind_of(node), className="lab"),
            html.Span(node.get("layer", ""), className="addr"),
        ]),
        html.Div(className="facts", children=[
            html.Div([html.Span(label, className="lab"),
                      html.B(value) if value
                      else html.B("not declared" if label != "Observed"
                                  else "never -- no pipeline has run", className="absent")])
            for label, value in facts]),
    ])


def _vpc_endpoints(plan):
    """Whether the plan declares any VPC endpoint.

    This is a derivation from ABSENCE, which is legitimate and useful: with no endpoint in
    the plan, S3 and Glue traffic reaches the public AWS endpoint rather than staying on the
    VPC, and a reviewer asking "does PII leave my network" has no other way to see it.
    """
    for change in (plan or {}).get("resource_changes") or []:
        if str(change.get("type", "")).startswith("aws_vpc_endpoint"):
            return True
    return False


def flow_hops(graph, plan, selected):
    """The hop ledger, deep. Each hop expands into what carries the data and what protects
    it -- the flat table said "SSE-KMS, public access blocked", which describes the bucket
    and answers nothing about the traffic."""
    edges = graph.get("edges") or []
    if selected:
        edges = [e for e in edges if e["from"] == selected or e["to"] == selected]
    if not edges:
        return html.P("No hop touches this step." if selected
                      else "No dataset flow is declared for this stack.", className="muted")

    labels = {n["id"]: n.get("label", n["id"]) for n in graph.get("nodes") or []}
    encryption = {n["id"]: n.get("encryption") for n in graph.get("nodes") or []}
    private = _vpc_endpoints(plan)

    rows = []
    for edge in edges:
        source, target = edge["from"], edge["to"]
        at_rest = encryption.get(target) or encryption.get(source)
        rows.append(html.Details(children=[
            html.Summary(children=[
                html.Span(className="path", children=[
                    html.B(labels.get(source, source)), " -> ",
                    html.B(labels.get(target, target)), " . ", edge.get("label", "")]),
                html.Span(edge.get("branch") or "primary path", className="tp"),
                html.Span("never observed", className="st"),
            ]),
            html.Div(className="body", children=[
                html.Div([html.Span("In transit", className="lab"),
                          html.B("not declared by this plan", className="absent")]),
                html.Div([html.Span("At rest", className="lab"),
                          html.B(at_rest) if at_rest
                          else html.B("not declared", className="absent")]),
                html.Div(className="risk", children=[
                    html.Span("Network path", className="lab"),
                    html.B("VPC endpoint declared" if private
                           else "no VPC endpoint declared -- traverses the public endpoint")]),
                html.Div([html.Span("Identity", className="lab"),
                          html.B("role model not built yet", className="absent")]),
                html.Div([html.Span("Branch", className="lab"),
                          html.B(edge.get("branch") or "primary")]),
                html.Div([html.Span("Latency budget", className="lab"),
                          html.B("not measured", className="absent")]),
            ]),
        ]))
    return html.Div(rows, className="hops2")


def flow_columns(graph, selected):
    """Column-level policy. The engine knows which columns Lake Formation governs and for
    whom; it does not know which upstream column each was derived from, so that cell says so
    rather than guessing a passthrough."""
    masking = (graph or {}).get("masking") or {}
    columns = masking.get("columns") or []
    if selected and columns:
        # A column is governed at the gold hop; filtering to any other hop should not
        # silently show all of them as though they applied there.
        columns = columns if selected in {"gold", "serving"} else []
    head = html.Thead(html.Tr([html.Th("Column"), html.Th("Derived from"),
                               html.Th("Unmasked for"), html.Th("Masked for"),
                               html.Th("Masked value")]))
    if not columns:
        reason = masking.get("reason") or "no column policy is declared for this stack"
        return html.Div([
            html.Table(className="table", children=[head]),
            html.P(reason, className="muted"),
        ])
    return html.Table(className="table", children=[head, html.Tbody([
        html.Tr([
            html.Td(column.get("column", "")),
            html.Td("not declared", className="absent"),
            html.Td(", ".join(column.get("unmasked_for") or []) or "-"),
            html.Td(", ".join(column.get("masked_for") or []) or "-"),
            html.Td(column.get("masked_value", "-")),
        ]) for column in columns])])


# --- Delivery flow: the path a change takes to production ---------------------------------

_LANES = (
    ("Lane 1", "Migration", "schema and state moves",
     ("Detects Terraform address churn that would destroy data",
      "Runs on fork PRs -- needs no cloud credentials",
      "Blocks merge on an unreviewed state move")),
    ("Lane 2", "Contracts", "data contracts and schema lint",
     ("Validates declared column contracts against the catalog schema",
      "Runs on fork PRs", "Blocks merge on a breaking contract change")),
    ("Lane 3", "Terraform", "fmt, validate, plan, policy",
     ("fmt and validate run everywhere",
      "plan needs cloud credentials, so fork PRs get the static half only",
      "Rego policy gates (SEC-*, COST-*) evaluate the plan JSON")),
    ("Lane 4", "Unit", "module and generator tests",
     ("Full pytest suite for modules and generators", "Runs on fork PRs",
      "Blocks merge on any failure")),
)


def _pipeline_generated(root):
    return bool(root) and os.path.isdir(os.path.join(root, ".github", "workflows"))


def delivery_steps(state):
    """Where THIS run sits on the path to production.

    The old delivery panel began at "four lanes" and ended at "merge gate" -- the middle of
    the story, with no plan, approval or apply, which is exactly the half that ties delivery
    to the rest of the console.
    """
    root = state.get("root") or ""
    plan = state.get("plan") or {}
    plan_hash = plan.get("plan_hash")
    approved = bool((state.get("trace") or {}).get("approved"))
    generated = _pipeline_generated(root)

    lanes = html.Div(className="lanebox", children=[
        html.Button(id={"kind": "lane", "lane": name}, n_clicks=0, className="lane",
                    children=[html.Span(number, className="lab"), html.B(name),
                              html.Span(runs, className="sub")])
        for number, name, runs, _detail in _LANES])

    # A run workspace records no commit, branch or pull request -- see runs.py's registry
    # fields. So these two steps report that nothing was recorded, which is the fact we have.
    # Saying "created locally, not from a branch" would be inventing a provenance.
    steps = [
        ("Commit", "Change pushed to a branch", "absent",
         "Not recorded. Nothing in this run's metadata names a commit or branch.",
         (), None),
        ("Pull request", "Pull request opened", "absent",
         "Not recorded. Nothing in this run's metadata names a pull request.", (), None),
        ("Validation", "Four lanes run in parallel",
         "absent" if not generated else "done",
         ("No pipeline has been generated into this workspace, so no lane has ever "
          "executed. The lanes below are what WOULD run." if not generated
          else "Pipeline files are present in this run."), (), lanes),
        ("Merge gate", "All four lanes must pass", "absent",
         "No evidence this was reached.",
         ("`needs` already fails this job if any lane fails",
          "The explicit check exists so a skipped lane can never read as a pass"), None),
        ("Merge", "Merged to main", "absent", "No evidence this was reached.",
         (), None),
        ("Plan", "terraform plan, bound to a hash",
         "done" if plan_hash else "absent",
         (f"{len(plan.get('resource_changes') or [])} resource changes."
          if plan_hash else "No plan has been generated for this run."),
         ((f"Plan hash {plan_hash[:16]}",) if plan_hash else ()), None),
        ("Approval gate", "A human approves that exact hash",
         "done" if approved else "blocked",
         ("Approved." if approved else
          (f"No approval record exists for hash {plan_hash[:16]}. An approval bound to any "
           f"other hash would not count." if plan_hash
           else "No plan hash to approve yet.")),
         ("This is the same deploy-gate stage shown in 05 What ran",
          "Editing the canvas revokes any standing approval and returns here"), None),
        ("Apply", "terraform apply", "done" if approved else "blocked",
         "Blocked: the approval gate above has not been satisfied." if not approved
         else "Eligible.", (), None),
    ]

    labels = {"done": "Done", "blocked": "Blocked", "absent": "Never run",
              "skipped": "Not applicable"}
    rendered = []
    for number, name, state_key, note, detail, extra in steps:
        body = [html.Div(className="phead", children=[
                    html.Span(number, className="lab"), html.B(name),
                    html.Span(labels[state_key], className="pstate")]),
                html.P(note, className="pnote")]
        if detail:
            body.append(html.Ul([html.Li(d) for d in detail]))
        if extra is not None:
            body.append(extra)
            body.append(html.Div(id="lane-detail", className="lanedetail", children=[
                html.Span("Select a lane to see what it runs and what it blocks",
                          className="lab")]))
        rendered.append(html.Div(className=f"pstep {state_key}", children=[
            html.Span(className="pdot"), html.Div(body, className="pbody")]))
    return html.Div(rendered, className="pipeline")


def view_flow(state, tab="data", selected=None):
    graph = state.get("lineage") or {}
    plan = state.get("plan") or {}
    hint = ("Select a lane to see what it runs and what it blocks" if tab != "data"
            else (f"Filtered to {selected} -- click again to clear" if selected
                  else "Select any step to filter everything below to it"))
    return html.Div([
        html.Div(className="switch", role="tablist", children=[
            html.Button("Data flow", id={"kind": "flowtab", "tab": "data"}, n_clicks=0,
                        className="on" if tab == "data" else "", role="tab"),
            html.Button("Delivery flow", id={"kind": "flowtab", "tab": "delivery"},
                        n_clicks=0, className="" if tab == "data" else "on", role="tab"),
            html.Span(hint, className="lab"),
        ]),
        html.Div(hidden=tab != "data", children=[
            html.Div(flow_chain(graph, selected), className="chain"),
            html.Div(flow_node_detail(graph, selected), className="detail"),
            html.H2(["Hops ", html.Span(_hop_count(graph, selected), className="lab")]),
            html.P("What carries the data between two steps, and what protects it. Expand a "
                   "hop for transport, network path and account boundary.", className="hint"),
            flow_hops(graph, plan, selected),
            html.H2("Column lineage"),
            flow_columns(graph, selected),
            html.Div(className="notice", children=[
                html.Span("These hops are declared, not proven", className="lab"),
                html.P("Edges come from the plan's declared references. Whether data has "
                       "ever moved along one, and how long it took, needs runtime events. "
                       "Nothing is emitting OpenLineage into this workspace, so every hop "
                       "reads never observed and no hop claims a measured latency."),
            ]),
        ]),
        html.Div(hidden=tab == "data", children=[
            html.P("Four lanes run in parallel and converge on one merge gate. Parallel on "
                   "purpose: a reviewer who waits eleven minutes for lane 4 to reveal a lint "
                   "error stops reading lanes.", className="hint"),
            delivery_steps(state),
        ]),
    ])


def _hop_count(graph, selected):
    total = len(graph.get("edges") or [])
    if not selected:
        return f"{total} total"
    shown = len([e for e in (graph.get("edges") or [])
                 if e["from"] == selected or e["to"] == selected])
    return f"{shown} of {total}"


# --- View 5: what ran ---------------------------------------------------------------------

_CHAIN_COPY = {
    agent_tracer.CHAIN_VERIFIED: ("Verified", "ok"),
    agent_tracer.CHAIN_BROKEN: ("Broken", "bad"),
    agent_tracer.CHAIN_ABSENT: ("No chain present", "absent"),
}


def _chain_cell(chain):
    """FR-06. Three states, kept three, because "cannot verify" is not "tampered" and
    neither is "verified"."""
    state = (chain or {}).get("state")
    label, tone = _CHAIN_COPY.get(state, ("Not checked", "absent"))
    checked = (chain or {}).get("checked") or 0
    broken_at = (chain or {}).get("broken_at")
    if state == agent_tracer.CHAIN_BROKEN and broken_at:
        detail = f"first mismatch at record {broken_at} of {checked}"
    elif state == agent_tracer.CHAIN_VERIFIED:
        detail = f"{checked} records, each linked to the one before"
    elif state == agent_tracer.CHAIN_ABSENT:
        detail = "no audit log has been written for this run"
    else:
        detail = "the chain was not examined"
    return label, tone, detail


def _headline_cells(state, chain):
    label, tone, detail = _chain_cell(chain)
    stages = {s["key"]: s for s in (state.get("trace") or {}).get("stages") or []}

    approval = stages.get("approval") or {}
    approved = approval.get("status") == agent_tracer.RECORDED
    plan_hash = (state.get("plan") or {}).get("plan_hash")

    reflection = stages.get("reflection") or {}
    reflected = reflection.get("status") == agent_tracer.RECORDED

    # The destructive classifier is a separate engine and is not run from the console, so
    # this cell reports what the plan carries rather than re-deriving a verdict here.
    plan = state.get("plan") or {}
    destructive = [c for c in plan.get("resource_changes") or []
                   if "delete" in (c.get("change") or {}).get("actions", [])]
    if not plan:
        change_class, change_detail = None, "no plan has been generated"
    elif destructive:
        change_class = f"{len(destructive)} destructive"
        change_detail = "routes to the staged, guarded path"
    else:
        change_class, change_detail = "Non-destructive", "eligible for ship-on-green"

    cells = [
        ("Audit chain", label, tone, detail),
        ("Change class", change_class, "ok" if change_class == "Non-destructive" else "warn",
         change_detail),
        ("Human approval", "recorded" if approved else None, "ok",
         (f"bound to hash {plan_hash[:16]}" if plan_hash
          else "no plan hash to approve yet")),
        ("Independent review", "recorded" if reflected else None, "ok",
         "reflector verdict" if reflected else "reflector produced no verdict"),
    ]
    return html.Div(className="cells c4", children=[
        html.Div([
            html.Span(title, className="lab"),
            html.B(value, className="ok" if tone == "ok" else "")
            if value else html.B("none recorded", className="absent"),
            html.Div(detail_text, className="sub"),
        ]) for title, value, tone, detail_text in cells])


def view_trace(state):
    chain = agent_tracer.verify_chain(
        os.path.join(state.get("root") or "", ".agents", "logs", "audit.jsonl"))
    result = state.get("trace") or {}
    stages = result.get("stages") or []
    active = agent_tracer.active_agents(state.get("root"))
    order = [spec["key"] for spec in agent_tracer.STAGES]
    graph = agent_flow_graph.build_flow(
        result, chain=chain, active=active,
        decision=agent_tracer.decision_branches(state.get("root")), order=order)

    return html.Div([
        _headline_cells(state, chain),
        html.H2("The machine, start to end"),
        html.P(f"{len(stages)} stages. Click any one for what it was asked, what it did, "
               f"what it touched and the audit record behind it.", className="hint"),
        html.Div(className="machine", children=[
            html.Button(id={"kind": "step", "step": node["id"]}, n_clicks=0,
                        className=("step ran" if node["status"] == agent_flow_graph.COMPLETED
                                   else "step"),
                        children=[
                            html.Span(className="pdot" if False else "dot"),
                            html.Span(node["label"], className="who"),
                            html.Span(node["summary"] or "", className="what"),
                            html.Span(_status_label(node["status"]), className="st"),
                        ]) for node in graph["nodes"]]),
        html.Div(className="notice", children=[
            html.Span("Where this stops short", className="lab"),
            html.P("Stage status is inferred from artifacts on disk and lines in the audit "
                   "chain, not from instrumentation at the call site. So this is an honest "
                   "record of what was produced, and not yet a trace of every tool "
                   "invocation. Per-step timings, inputs and tool calls are the work that "
                   "would make it one."),
        ]),
    ])


_STATUS_LABELS = {
    agent_flow_graph.COMPLETED: "Ran",
    agent_flow_graph.BLOCKED: "Blocked",
    agent_flow_graph.WAITING_ON_HUMAN: "Awaiting human",
    agent_flow_graph.RUNNING: "Running",
    agent_flow_graph.NOT_RUN: "Not run",
}


def _status_label(status):
    return _STATUS_LABELS.get(status, "Not run")


def trace_step_record(state, step_id):
    """FR-05. What one step was asked, what it did, and the seal behind it.

    An absent stage is described as absent rather than given an empty record: "no audit
    record; this stage did not run" and "this stage ran but wrote no hash" are different
    failures and a reviewer needs to tell them apart.
    """
    chain = agent_tracer.verify_chain(
        os.path.join(state.get("root") or "", ".agents", "logs", "audit.jsonl"))
    order = [spec["key"] for spec in agent_tracer.STAGES]
    graph = agent_flow_graph.build_flow(
        state.get("trace") or {}, chain=chain,
        decision=agent_tracer.decision_branches(state.get("root")), order=order)
    node = agent_flow_graph.find_node(graph, step_id)
    if not node:
        return html.P("Select a stage above.", className="muted")

    rows = [
        ("What it was asked", node["summary"]),
        ("Persona", node["persona"]),
        ("Model tier", node["model_tier"]),
        ("Artifact", (f"{node['artifact']} "
                      f"({'present' if node['artifact_present'] else 'not produced'})")
         if node["artifact"] else None),
        ("Operator", node["operator"]),
        ("At", node["at"]),
        ("Latency", (f"{node['latency_seconds']}s"
                     if node["latency_seconds"] is not None else None)),
        ("Audit seal", node["audit_hash"]),
    ]
    body = [html.Div(className="dhead", children=[
        html.B(node["label"]), html.Span(_status_label(node["status"]), className="lab")])]
    for label, value in rows:
        body.append(html.Div(className="k", children=label))
        body.append(html.Div(className="v", children=(
            value if value else html.Span(
                "no audit record; this stage did not run" if label == "Audit seal"
                else "not recorded", className="absent"))))
    if node["decision"] and node["decision"].get("present"):
        decision = node["decision"]
        body.append(html.Div("Decision branch", className="k"))
        body.append(html.Div(className="v", children=", ".join(
            decision.get("chosen_modules") or []) or "no module recorded"))
        rejected = decision.get("rejected_alternatives") or []
        body.append(html.Div("Rejected alternatives", className="k"))
        body.append(html.Div(className="v", children=(
            ", ".join(str(a.get("name", a)) for a in rejected) if rejected
            else html.Span("none recorded", className="absent"))))
    return html.Div(body, className="trace")


# --- View 6: evidence ---------------------------------------------------------------------

# Which console section each deliverable is the print of. A document nothing renders is
# marked "-" rather than guessed into a section, because a wrong mapping here sends a
# reviewer to a screen that does not contain what they were promised.
_DOCUMENT_SECTION = {
    "plan.pdf": "01 Topology", "report.html": "01 Topology", "plan.json": "01 Topology",
    "architecture.drawio": "01 Topology", "architecture.svg": "01 Topology",
    "architecture_url.txt": "01 Topology",
    "dataflow.svg": "02 Flow",
    "inspect.pdf": "03 Access",
    "cost.pdf": "04 Cost", "cost.html": "04 Cost",
    "executive_project_summary.xlsx": "04 Cost",
    "pipeline_detailed_ledger.xlsx": "04 Cost",
    "proving_report.json": "05 What ran",
    "manifest.json": "06 Evidence",
}


def view_vault(state):
    stats = state.get("vault") or {}
    documents = state.get("documents") or []
    run_id = (state.get("run") or {}).get("run_id", "")
    return html.Div([
        html.Div(className="actions", children=[
            html.A("Export compliance bundle", className="btn pri",
                   href=f"/runs/{run_id}/vault/bundle"),
            html.Span(f"{stats.get('present', 0)} of {stats.get('total', 0)} documents "
                      f"present", className="lab"),
            html.Span(id="vault-status", className="lab"),
        ]),
        _document_table(documents, run_id),
    ])


def _document_table(documents, run_id):
    """Present documents first, absent ones folded away.

    Listing all fifteen inline made the vault a wall of "not produced" -- the documents that
    exist were outnumbered, which buries the evidence the view is for. The absent ones still
    appear, because a category that vanishes when empty hides that the evidence was never
    produced; they are just not the headline.
    """
    present = [d for d in documents if d["present"]]
    absent = [d for d in documents if not d["present"]]

    def _row(document):
        name = document["name"]
        return html.Tr(className="docrow", children=[
            html.Td(html.Button(name, id={"kind": "doc", "name": name}, n_clicks=0,
                                className="link-button")),
            html.Td(_DOCUMENT_SECTION.get(name, "-")),
            html.Td(document["category_title"]),
            html.Td(f"{document['size_bytes']:,} B", className="right"),
            html.Td(html.A("Download", className="link-button",
                           href=f"/runs/{run_id}/vault/download/{name}")),
        ])

    blocks = [html.Table(className="table", children=[
        html.Thead(html.Tr([html.Th("Document"), html.Th("Renders the section"),
                            html.Th("Category"), html.Th("Size", className="right"),
                            html.Th("")])),
        html.Tbody([_row(d) for d in present]),
    ])] if present else [
        html.P("No deliverables have been produced for this run yet.", className="muted")]

    if absent:
        blocks.append(html.Details(children=[
            html.Summary(f"{len(absent)} not produced"),
            html.Div(className="body", children=[html.Ul(
                [html.Li(f"{d['name']} -- {_DOCUMENT_SECTION.get(d['name'], '-')}")
                 for d in absent])]),
        ]))
    return html.Div(blocks)


def document_sheet(name, run_id):
    """The body of the reader for one document.

    Renders by type, and says plainly when there is no in-browser reader rather than showing
    a broken embed: a workbook rendered as text is mojibake, which reads as a corrupt file.
    """
    document = _catalogued_document(run_id, name)
    if not document:
        return "document", "", html.P("That document is not in this run's catalog.",
                                      className="muted")
    href = f"/runs/{run_id}/vault/download/{document['name']}"
    kind = {"inline": "Rendered", "text": "Text", "download": "Binary"}.get(
        document["preview"], document["preview"])

    if document["preview"] == "inline":
        body = html.Iframe(src=href, title=document["name"])
    elif document["preview"] == "text":
        try:
            with open(document["path"], encoding="utf-8", errors="replace") as handle:
                body = html.Pre(handle.read(200000))
        except OSError as exc:
            body = html.P(f"Could not read it: {exc}", className="muted")
    else:
        body = html.Div(className="none", children=[
            html.B(f"No in-browser reader for {document['name']}"),
            "Download it, or read the same figures in the section it renders.",
        ])
    return document["name"], kind, body


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


def _empty(title, detail):
    return html.Div(className="empty", children=[
        html.H3(title), html.P(detail, className="muted")])


def _statement_count(entry):
    """How many statements a document resolved to, or why it could not be read."""
    document = entry.get("trust") or entry.get("document") or {}
    if not document.get("resolved"):
        return None, document.get("reason") or "not determinable from this plan"
    return len(document.get("statements") or []), None


def view_access(state):
    """03 Access. Roles, who may assume them, and what the plan could not resolve."""
    plan = state.get("plan") or {}
    if not plan:
        return _empty("No plan analyzed",
                      "Run `minusctl gate plan` to produce the plan this view reads.")

    model = access_model.access_model(plan)
    roles = model["roles"]
    policies = model["policies"]
    unresolved = model["unresolved"]

    cells = [
        ("IAM roles", len(roles) or None, "declared in this plan"),
        ("Policies", len(policies) or None,
         f"{sum(1 for p in policies if p.get('kind') == 'inline')} inline, "
         f"{sum(1 for p in policies if p.get('kind') == 'attachment')} attached"),
        ("Unresolved", len(unresolved) or None,
         "documents this plan does not settle until apply"),
        ("Malformed", model["malformed"] or None, "resource entries that could not be read"),
    ]

    blocks = [html.Div(className="cells c4", children=[
        html.Div([html.Span(title, className="lab"),
                  html.B(str(value)) if value else html.B("none", className="absent"),
                  html.Div(detail, className="sub")])
        for title, value, detail in cells])]

    if roles:
        rows = []
        for role in roles:
            count, reason = _statement_count(role)
            principals = role.get("trusted_principals") or []
            rows.append(html.Tr([
                html.Td(role.get("name") or role["address"]),
                html.Td(", ".join(principals) if principals
                        else html.Span(reason or "none declared", className="absent")),
                html.Td(str(count) if count is not None
                        else html.Span("not determinable", className="absent")),
                html.Td(", ".join(role.get("attached_policies") or [])
                        or html.Span("none in this plan", className="absent")),
                html.Td(role.get("module") or "-"),
            ]))
        blocks.append(html.H2("Roles and who may assume them"))
        blocks.append(html.Table(className="table", children=[
            html.Thead(html.Tr([html.Th("Role"), html.Th("Trusted principals"),
                                html.Th("Trust statements"), html.Th("Attached policies"),
                                html.Th("Module")])),
            html.Tbody(rows)]))
    else:
        blocks.append(html.H2("Roles and who may assume them"))
        blocks.append(html.P("This plan declares no IAM roles.", className="muted"))

    if unresolved:
        blocks.append(html.H2("What this plan does not settle"))
        blocks.append(html.P("These documents are computed at apply time. Their permissions "
                             "are real but not visible here, and are reported rather than "
                             "counted as zero.", className="hint"))
        blocks.append(html.Table(className="table", children=[
            html.Thead(html.Tr([html.Th("Resource"), html.Th("Field"), html.Th("Reason")])),
            html.Tbody([html.Tr([html.Td(item["address"]), html.Td(item["field"]),
                                 html.Td(item["reason"])]) for item in unresolved])]))

    blocks.append(html.Div(className="notice", children=[
        html.Span("Not yet derived", className="lab"),
        html.P("Which dataset each role can reach, and the Lake Formation grants over those "
               "datasets, are not extracted yet. The policy findings from the G6 rule set "
               "are not joined onto these roles either. Those cells are absent rather than "
               "estimated: an access screen that under-reports is worse than one that says "
               "it cannot see."),
    ]))
    return html.Div(blocks)


def view_cost(state):
    """04 Cost. BCM forecast, per-service breakdown, scale curve and the assumptions."""
    return _empty("No cost evidence for this run",
                  "Run `minusctl cost estimate` to produce a BCM forecast.")


def view_settings(state):
    """Workspace scope: teams and connectors, which outlive any single run."""
    return _empty("Settings not wired yet",
                  "Teams come from configs/teams.yaml via team_resolver; connectors from "
                  "core/integrations.")


RENDERERS = {"topology": view_topology, "flow": view_flow, "access": view_access,
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
    dcc.Store(id="flow-tab", data="data"),
    dcc.Store(id="flow-node"),
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
    Input("flow-tab", "data"),
    Input("flow-node", "data"),
)
def _render(view, run_id, flow_tab, flow_node):
    state = assemble(run_id)
    bar = top_bar(view, run_id)
    if not state.get("run"):
        return bar, html.Div(), _empty(
            "No runs found", "Create one with `minusctl create`.")
    # Settings is workspace-scoped: showing a run identity above it would say these teams
    # and connectors belong to that run.
    band = html.Div() if view == "settings" else run_band(state)
    if view == "flow":
        return bar, band, view_flow(state, flow_tab or "data", flow_node)
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
    Output("flow-tab", "data"),
    Input({"kind": "flowtab", "tab": dash.ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _switch_flow_tab(_clicks):
    triggered = dash.callback_context.triggered_id
    return triggered.get("tab") if isinstance(triggered, dict) else dash.no_update


@app.callback(
    Output("flow-node", "data"),
    Input({"kind": "flownode", "node": dash.ALL}, "n_clicks"),
    State("flow-node", "data"),
    prevent_initial_call=True,
)
def _select_flow_node(_clicks, current):
    triggered = dash.callback_context.triggered_id
    if not isinstance(triggered, dict):
        return dash.no_update
    return toggle_selection(triggered.get("node"), current)


def toggle_selection(node_id, current):
    """Clicking the selected node again clears the filter.

    Kept out of the callback so it can be tested: `dash.callback_context` only exists inside
    a live callback, and a decision that can only be exercised through the browser is a
    decision with no test.
    """
    return None if node_id == current else node_id


@app.callback(
    Output("overlay", "className"),
    Output("sheet-name", "children"),
    Output("sheet-kind", "children"),
    Output("sheet-body", "children"),
    Input({"kind": "doc", "name": dash.ALL}, "n_clicks"),
    Input({"kind": "step", "step": dash.ALL}, "n_clicks"),
    Input("sheet-close", "n_clicks"),
    State("run-id", "data"),
    prevent_initial_call=True,
)
def _open_sheet(_docs, _steps, _close, run_id):
    """One reader for both a document and a trace step.

    Both are "open one thing and read it"; two modals would be two behaviours to keep in
    step, and they would drift the first time only one of them learned to close on Escape.
    """
    triggered = dash.callback_context.triggered_id
    closed = ("overlay", dash.no_update, dash.no_update, dash.no_update)
    if not isinstance(triggered, dict):
        return closed
    if triggered.get("kind") == "doc":
        name, kind, body = document_sheet(triggered.get("name"), run_id)
        return "overlay open", name, kind, body
    if triggered.get("kind") == "step":
        step = triggered.get("step")
        return ("overlay open", step, "Trace record",
                trace_step_record(assemble(run_id), step))
    return closed


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
