# CONTEXT-app.md — Control Plane Console Context

## `console_app.py` -- the Visual Governance Console (PRD v13)

`minusctl console`. Four views scoped to ONE run, replacing the five-tab dashboard that
mixed FinOps charts, CLI execution and report viewers for three different audiences:

1. **Architecture topology** -- Draw.io canvas from [`drawio_generator.py`](../core/reporting/drawio_generator.py), with the 1-click diagrams.net URL.
2. **Data lineage** -- medallion dataset flow from [`lineage_graph.py`](../core/reporting/lineage_graph.py), quarantine fork and Lake Formation masking.
3. **Execution trace** -- what actually ran, from [`agent_tracer.py`](../core/governance/agent_tracer.py), each stage bound to its audit hash.
4. **Deliverables vault** -- evidence catalog and signed bundle from [`vault.py`](../core/reporting/vault.py).

**The view layer owns no logic.** Every fact comes from an engine tested independently, which
is why the console may use Dash while those engines stay standard-library-only -- PRD v13
invariant 4 binds the engines, not the presentation.

**The canvas proposes; Git decides.** A connection edit never writes HCL directly. It routes
through [`reconciler.py`](../core/architecture/reconciler.py), which splits the operation in
two: `propose()` is inert and returns a diff, `confirm()` writes and only when `confirmed is
True` -- an identity check, because `confirmed="no"` is truthy and would turn a dismissed
modal into an infrastructure edit. Confirming deletes the standing approval records, so
`plan_gate.gate_status()` reports `approved: False`. That reuses the gate's own answer
rather than adding a second staleness flag that could disagree with it.

Two bugs in this module were found by running it, not by testing it: a callback targeting
an on-demand view errored on page load until `suppress_callback_exceptions` was set, and
`_run_record` called a `runs` function that does not exist inside a blanket `except
Exception`, so the console rendered "No runs found" over twenty-five runs. Both now have
tests.

---

## Detailed File Breakdown

### [`app/console_app.py`](./console_app.py)

#### 1. Architectural role
The console is the graphical surface over the governance engines in [`core/`](../core). It is
scoped to ONE run at a time and owns no logic of its own: every fact it renders comes from an
engine that is tested independently. It replaced `app/dashboard_app.py`, a five-tab console
that mixed FinOps charts, CLI execution and report viewers for three different audiences.

#### 2. Inputs and environment
- **`CONSOLE_PORT`** -- port to bind (default `8050`), or `--port`.
- **`CONSOLE_HOST`** -- interface to bind (default `127.0.0.1`), or `--host`.
- **`MINUS_DASH_TOKEN`** / **`DASH_TOKEN`** -- shared bearer/query/cookie token. Required for
  any non-loopback bind, and once set it is enforced on every request.
- Run state is read from the run workspace on disk. The console never calls a cloud API and
  never invokes a cloud mutation (PRD v13 invariant 2).

#### 3. The bind and auth guard
Two controls, ported from the retired dashboard when it was removed:

- **At bind time**, `main()` refuses to start on a non-loopback host unless a token is set,
  and returns a non-zero exit code. Nothing listens at all.
- **At request time**, a `before_request` hook rejects anything not presenting the token,
  once one is configured. Comparison is `hmac.compare_digest`, never `==`.

`_request_authorized()` returns True when no token is configured. That branch is reachable
only on a loopback bind, because the bind-time guard refuses the alternative; failing closed
there instead would break plain `minusctl console` for everyone.

#### 4. HTTP endpoints
Beyond the Dash SPA at `/`:
- **`/runs/<run_id>/vault/download/<name>`** -- serves one catalogued deliverable. The guard
  is an ALLOWLIST, not a sanitiser: the requested name is matched against the vault catalog
  for that run and served from the path the catalog resolved, so nothing from the URL is ever
  joined onto a directory. `..`, an absolute path and a symlink name all fail identically --
  they are not in the catalog.
- **`/runs/<run_id>/vault/bundle`** -- builds and serves the signed compliance zip. Refuses an
  empty run rather than handing an auditor a zip full of nothing.

#### 5. What the view layer may not do
The reconciliation store round-trips through the browser and deliberately carries the change
SPEC and the run id only -- never `updated_hcl`. A tampered payload therefore cannot be
written to `main.tf`; the server re-derives the diff from the spec on confirmation.
