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

## [`formatters.py`](./formatters.py)
- **ASCII only**, enforced by `_ascii_only` rather than by convention (NFR-01). No emoji and no
  box-drawing either: these outputs are pasted into tickets and CI logs, and a terminal that
  cannot render `U+2502` turns a table into noise.
- **`None` renders as `-`, never as `0`.** [`money()`](./formatters.py) returns `not priced`
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
`list` renders the table with `[*]` on the active run. `describe` renders the specification card
— Metadata, Architecture, FinOps, Resource endpoints, Artifact paths — reading what the run has
actually produced rather than restating the request. A broken context does not stop `list`:
that is exactly where an operator goes to fix it.

### [`gate.py`](./commands/gate.py)
Fronts [`plan_gate.py`](../governance/plan_gate.py). **Deliberately thin.** The gate's stages are
the governance contract — plan-hash binding, the destructive-change classifier, fail-closed
policy — and a wrapper that reordered, renamed or short-circuited a stage would change what is
enforced while looking like a usability change. The stage passes through verbatim; the only
addition is `--dir`, resolved from the active run. With no active run and no flag it refuses.
Also forwards `--with-telemetry` (PRD v6 FR-07) so drift findings can carry the CloudTrail
identity and the failure signature that preceded the change; a flag the wrapper drops is a
flag that does not exist.

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
