# core/cli Context Index

The `minusctl` front door (PRD-ARCH-2026-007, FR-02/FR-03). One command surface over the whole
control plane, so an operator names a capability instead of a script path.

**This package is a front door, not a rewrite.** [`core/reporting/minusctl.py`](../reporting/minusctl.py)
carries nineteen subcommands and the tests that prove each of them. Moving that code would have
risked a regression in the deploy lifecycle to gain a directory layout. So this package owns the
commands that had to be written new — `use`, `runs list/describe`, `gate`, `cost`, `source` — and
hands every other subcommand to the existing implementation unchanged. Nothing moved, so nothing
broke, and the operator sees one CLI either way.

Imports inside this package are **package-relative** (`from .. import context`). The rest of the
repo puts each `core/` subdirectory on `sys.path` and imports by bare name; doing both here would
give every file two module objects, and a `monkeypatch` on one would not be seen by the other.

---

## [`main.py`](./main.py)
- **Entry point** declared as `minusctl = "core.cli.main:main"` in `pyproject.toml`.
- **The help screen is rendered by hand** ([`format_help()`](./main.py)), not by argparse.
  Argparse renders subparsers as one flat blob plus a `{a,b,c,...}` usage line, which across
  24 commands is the wall this replaces. [`COMMAND_GROUPS`](./main.py) orders them by
  lifecycle stage and [`COMMAND_HELP`](./main.py) gives each a sentence -- an operator
  choosing between `conformance` and `readiness` cannot do it from two words. A test asserts
  the grouping and `known_commands()` stay in step, so adding a command without placing it
  fails rather than silently vanishing from the help.
- **Every command is registered as a subparser**, delegated ones bare and `add_help=False`,
  so `minusctl <command>` parses and `minusctl <command> --help` reaches the owning
  implementation's real flag list.
- **Dispatch:** [`NATIVE`](./main.py) is handled here; [`DELEGATED`](./main.py) goes to
  `minusctl.main(argv)` verbatim. Both lists are written out rather than discovered, so losing a
  subcommand is a visible edit in this file instead of a silent behaviour change —
  [`known_commands()`](./main.py) is what the test asserts against.
- **`runs` is shared:** `list` and `describe` are native, anything else falls through, so an
  existing `minusctl runs show` invocation keeps working.
- **Unknown commands are delegated** so the error message comes from one parser. Two parsers
  producing two different lists of valid commands is how they start disagreeing.
- Run it as `python -m core.cli.main` or through the console script; the relative imports mean
  direct file execution is not an entry point.

## [`context.py`](./context.py)
- **Purpose:** which run is the operator on. `minusctl use <run-id>` records it in
  `.minus/context.json`; `gate`, `cost`, `source`, `prove` and `export` default to it.
- **Precedence (PRD v6 FR-01, ratified 2026-08-22):** explicit `--run`/`--dir`, then
  [`discover_run_from_cwd()`](./context.py) (the cwd is inside `runs/<run-id>/`), then the
  stored context, then **refusal**. Discovery outranks the stored context because where you
  ARE is a stronger statement of intent than what you last selected. There is no fifth step:
  "most recently created" was struck from the draft, because a prototype somebody else
  generated five minutes ago is not a safe default for `gate apply`.
- **This file decides which infrastructure a later `gate apply` touches**, so every failure is
  loud:
  - A corrupt context file raises [`ContextError`](./context.py). Falling back to "the newest
    run" would point an apply at different infrastructure than the operator selected, silently.
  - An active run whose directory was deleted raises, for the same reason.
  - **No** context file returns `None` — there is genuinely no selection, and inventing one from
    `latest_run()` is the same guess wearing a different hat.
  - A run id with a path separator or `..` is refused before it is stored; the value is read back
    off disk later and joined into a path.
- **Atomic writes** (NFR-04): temp file plus `os.replace`. A half-written `context.json` is
  unparseable and "delete the file" is not an obvious recovery.

## [`theme.py`](./theme.py)
- **Purpose:** ANSI colour for the help screen, and the rules for when NOT to emit it.
- **Off by default; on only for an interactive terminal.** Colour helps a human scan; it is
  corruption everywhere else. `[1m` in a CI log, a redirected file or a `grep` result is
  noise someone eventually writes a sed script to strip.
- **Precedence:** `NO_COLOR` (any value) beats everything -- people who set it have a reason
  and an opt-out must outrank our opt-in; then `MINUS_COLOR=1|0`; then `TERM=dumb`; then
  `isatty()`.
- **Styles take `enabled` explicitly** rather than reading global state, so a caller decides
  once per render and the functions stay pure -- which is what makes them testable without a
  fake terminal.
- **[`visible_width()`](./theme.py)** exists because padding a coloured string by `len()`
  counts the invisible bytes and every column after it drifts. Colour the name, pad outside
  the escape.

## [`formatters.py`](./formatters.py)
- **ASCII only**, enforced by `_ascii_only` rather than by convention (NFR-01). No emoji and no
  box-drawing either: these outputs are pasted into tickets and CI logs, and a terminal that
  cannot render `U+2502` turns a table into noise.
- **`None` renders as `-`, never as `0`.** [`money()`](./formatters.py) returns `unpriced`
  for an absent estimate. `$0.00` would be the one number on the card an executive remembers,
  and it would be wrong. Same doctrine as [`budget_calculator.py`](../cost/budget_calculator.py).

---

## `commands/`

Each module exposes `add_parser(subparsers)` and `run(args)`. Modules fronting an existing engine
keep a `_delegate(argv)` seam — one named place where control leaves the module — so a test can
assert what was passed through without reaching AWS or Terraform.

### [`use.py`](./commands/use.py)
Selects the active run. Refuses an id that does not resolve, rather than storing it and failing
later, far from the typo that caused it.

### [`runs.py`](./commands/runs.py)
`list` renders the FR-02 table — Active, Run Name, Domain, Engine, Orchestrator, Cost/Mo,
Status — with `[*]` on the active run and `[ ]` elsewhere so the marker reads as a column
rather than a rendering bug. Filters on `--domain`, `--tier`, `--orchestrator`; an undeclared
field never matches a filter on it, because silence is not a wildcard and an unclassified run
in a `--tier prod` listing is how one ends up treated as production. "no runs match that
filter" and "there are no runs yet" are distinct messages: at the end of a filtered command
they are very different statements to read.

`describe` renders the FR-03 card — `[Metadata]`, `[Architecture Attributes]`,
`[FinOps & Resource Endpoints]`, `[Artifact Paths]` — pulling each fact from its canonical
source. [`ARCHITECTURE_FIELDS`](./commands/runs.py) maps each PRD label to the keys that may
hold it, because the PRD names attributes (`table_format`, `serving_layer`) that no schema
declares under those exact names. Source order matters: the decision record and requirements
win, and `run.json` trails — its `compute_engine` is the short label the list table shows
("Glue 4.0"), not the architecture statement the decision carries, and reading it first would
let the summary outrank the decision it summarises. Nothing is inferred from a module list:
"we generated compute-glue-etl" is not the same claim as "the compute engine is Glue 4.0 with
10 G.1X workers".

[`_spend()`](./commands/runs.py) is shared by both views. Two views of one run disagreeing
about cost is worse than neither showing it — the reader believes whichever they saw last.
Artifact paths render workspace-relative and are annotated `(missing)` when the file is not
there; a path printed for a file that does not exist sends the reader to an empty directory
and makes them doubt the tool rather than the run.

A broken context does not stop `list`: that is exactly where an operator goes to fix it.

### [`gate.py`](./commands/gate.py)
Fronts [`plan_gate.py`](../governance/plan_gate.py). **Deliberately thin.** The gate's stages are
the governance contract — plan-hash binding, the destructive-change classifier, fail-closed
policy — and a wrapper that reordered, renamed or short-circuited a stage would change what is
enforced while looking like a usability change. The stage passes through verbatim; the only
addition is `--dir`, resolved from the active run. With no active run and no flag it refuses.
Also forwards `--with-telemetry` (PRD v6 FR-07) so drift findings can carry the CloudTrail
identity and the failure signature that preceded the change, and `--role-arn`, which asserts
the active session is a given deploy role; a flag the wrapper drops is a flag that does not
exist.

`gate status` is the one action handled **here** rather than delegated
([`_status`](./commands/gate.py)). It is a read of state the gate already recorded, not a
sixth stage -- forwarding it would hand `plan_gate` a stage it does not implement -- and it
never invokes Terraform, so it stays instant and credential-free. Nobody runs a status
command that takes ten seconds.

### [`cost.py`](./commands/cost.py)
Fronts [`bcm_pricing_calculator.py`](../cost/bcm_pricing_calculator.py). `estimate` maps to the
engine's `run` stage (`run` already means something else in this CLI). Nothing here computes,
interpolates, or defaults a cost.

### [`source.py`](./commands/source.py)
Fronts [`source_guard.py`](../governance/source_guard.py). `anchor` is the only write and is
opt-in: anchoring a drifted directory is how an unreviewed manual edit becomes the baseline.

---

## Code Hygiene Audit

- **Dead code:** None.
- **Unwired:** None. Every module is reachable from `main.NATIVE`.
- **Duplication:** The delegation list in `main.DELEGATED` mirrors the legacy parser's
  subcommands. Duplicated on purpose — see the note on `known_commands()` above — and
  [`tests/test_cli_package.py`](../../tests/test_cli_package.py) fails if they drift apart.
- **Mismatches:** None.

---

## Tests

[`tests/test_cli_package.py`](../../tests/test_cli_package.py) — context switching and its
failure modes, `[*]` marking, the spec card, `--dir` defaulting and its refusal, stage
pass-through, the no-subcommand-lost guard, the stdlib-only import check (NFR-02), and the
no-emoji checks (NFR-01).
