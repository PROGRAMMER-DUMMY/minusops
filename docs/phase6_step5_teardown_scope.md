# Phase 6 Step 5 scope — catalog teardown

Scope document only. No `modules/*` directory is deleted, no `MODULES` entry is removed, no
`compose()` code path is retired, as a result of this document. This is Phase 6's last step
(`docs/phase6_scope.md` section 5), reachable now that Step 0 (G5 fail-closed) and Step 1 (the
authoring pipeline, G9 wired in, SEC-08/09/10) are both closed and proven. This is **a proof
step, not a build step** — its job is to define a checkable bar and, where the bar is met, retire
the mechanism that copies catalog content as unreviewed source of truth. Where the bar is not
met, the honest output is a named blocker, not a workaround.

## 0. THE KEY GAP — there is no generation engine, and this scope must not pretend otherwise

`docs/phase6_scope.md` section 5's original proof bar says "the new pipeline authors a
configuration." Read literally, that assumes an authoring mechanism that decides *what* HCL to
write from a free-text requirement. **That mechanism does not exist.** Verified directly against
Step 1's own build: `synthesizer.synthesize(authored_content=...)` (`core/generation/
synthesizer.py`) takes already-written HCL text supplied by the caller and validates + composes
it — no LLM call, no template engine, no decision logic that produces HCL from a resource-type
name anywhere in this codebase. Step 1's own design note said this explicitly at the time: it
built "the real pipeline... fully testable via test-supplied fixtures, without inventing the
generator itself." That was the right call then (avoids building speculative infrastructure); it
means the phrase "the new pipeline authors a configuration" in the original proof bar cannot be
satisfied as literally written, and pretending otherwise here would be exactly the kind of
overclaim this project has spent five phases refusing to make.

**What this scope proves instead, precisely, and why that's still the correct bar**: not "an
autonomous generator can reinvent `orchestrator-mwaa` from a sentence" (a real, different,
unbuilt capability), but **"the authored-content composition path Step 1 actually built is
capability-equivalent to the catalog-copy path it would replace, for the same content."**
This is the honest, buildable claim available today, and it is sufficient, because of a fact
verified directly against the code, not assumed: **G1, G5, G6, and G9 are all origin-blind by
construction.**

- `destructive_change_gate.classify()` (G5) reads `terraform show -json` `resource_changes` —
  nothing in its signature or logic can tell whether a `.tf` file arrived via `shutil.copytree`
  or via an `authored_*.tf` file written from `authored_content`. Confirmed by inspection: no
  origin flag exists anywhere in a plan JSON document.
- `rego_gate.evaluate()` (G6) reads the identical plan JSON — same argument, same conclusion.
- `ephemeral_apply.run_ephemeral_apply()` (G9) runs `terraform init/plan/apply/destroy` against
  whatever HCL is physically present in a directory at call time — it has no code path that
  reads or cares how those files were written.
- `schema_lint.gate_content()`/`gate_module()` (G2) — the one gate that WOULD need separate
  proof per path, since `gate_module()` reads disk and `gate_content()` reads a string — already
  has that proof: last session's byte-identical parity test across all 16 real modules.

Because three of the four downstream gates are structurally incapable of distinguishing origin,
the entire regression question collapses to one thing per module: **does composing this
module's real content through the new authored-file path produce a `terraform plan` equivalent
to the plan produced by the old catalog-copy path?** If yes, every proof already on record for
that module's old plan (G5's create-only+reviewed-type classification, G6's zero-FP shadow
result, G9's per-type fidelity status) carries over automatically, by construction, not by
re-argument. If no, the two paths are not equivalent and the module is not ready for teardown.
This is proof-bar item 1 below, made mechanically checkable — **but read section 0.1 before
treating "proof-bar item 1 passes" as "therefore the catalog comes out."** Those are two
different claims, and this scope must not let the second one ride in quietly on the first.

## 0.1 The decision this gap forces — a decision, not a finding, and not mine to resolve here

Section 0's pivot was framed, from the start of this project, as replacing a frozen catalog with
generation-time **authoring**: research an unfamiliar requirement, write fresh HCL for it,
validate it, plan it. That authoring step does not exist. What Step 1 actually built and proved
is the other three: research (`match_modules()`, not yet even repurposed — section 3), validate
(G1/G2/G5/G6, and now G9 wired in), and compose (the `authored_content` plumbing). **The thing
that turns a requirement into HCL text in the first place is still a human or an external agent
typing it by hand and handing it to `synthesize()` as a parameter.** That is a real and useful
capability — validated, safety-netted, audited hand-authored-content composition — but it is not
generation, and this scope must say so as plainly as this paragraph just did, not let
"capability-equivalent path" imply more than it means.

This forces a real question about what teardown *for* means right now, with two honest answers,
not one:

- **Option A — teardown now.** Every module that passes the harness (section 1) has its
  `modules/<id>/` directory relocated to a demoted, re-verified-example role (section 4), and
  `compose()`'s copy-path for that id retires. The claim this makes: "the composition mechanism
  no longer needs to trust a frozen, pre-pinned catalog to build governed infrastructure." That
  claim is TRUE of the mechanism — but with no generator, the practical effect is that every
  requirement compose() used to satisfy by copying a catalog module now has to be satisfied by
  a human/agent hand-authoring equivalent HCL and passing it in as `authored_content` instead.
  **That is strictly more manual work for the exact same outcome, dressed as a milestone.** The
  catalog was never dead weight being superseded by something better — until a generator exists,
  it is the only thing in this codebase that can supply real HCL for a standard requirement
  without a human writing it fresh every time.
- **Option B — prove readiness, keep the catalog as the real composition source.** The harness
  (section 1) still runs, still proves the pipe is sound, still closes the two test gaps (1.3) —
  all of that work stands regardless. `module_provenance.py` still retires (section 3) and
  `match_modules()` still gets repurposed toward retrieval (section 3) — both are safe,
  additive, non-destructive changes with value independent of teardown. **But `compose()` keeps
  copying from `modules/<id>/` as its actual source**, for every module, proven or not, until a
  real authoring mechanism exists that can independently supply equivalent content. Nothing is
  relocated out of its composition-source role. The catalog is demoted in TRUST status (no
  longer a blindly-pinned, never-re-verified artifact — every draw re-checks live schema, same as
  section 4 describes) but not in FUNCTION.

**My reasoned lean, recorded, not decided for you**: Option B. The regression harness proves the
*pipe* — that validated, gated composition of caller-supplied content works as well as
catalog-copy composition did. It does not and cannot prove anything can *supply* that content
without the catalog already existing to draw it from (the harness's own input, per section 1.1,
is each module's own real text, fed back through the new path — it is a proof about the plumbing,
not a demonstration of an independent source). Retiring the catalog's role as composition source
on the strength of a plumbing proof, with nothing yet on the other end of the pipe, would be
removing a working, load-bearing asset to claim a milestone ("we generate now") that isn't true
yet. Option A is the right call the moment a real authoring mechanism exists to supersede the
catalog; it is premature before that.

**This is the one decision in this document I am not making.** Everything else below (the
harness, the two test gaps, `module_provenance`'s retirement, `match_modules()`'s repurposing)
is scoped to be correct and worth doing under EITHER option — read it that way. Section 5's proof
bar and section 2/3/4's per-module actions are written to make the A/B fork explicit at each
point it matters, not to assume an answer.

## 1. The regression-baseline proof bar — concrete, buildable, and its real limits disclosed

### 1.1 The harness (new code this scope authorizes building, not a generator)

For each of the 16 real modules (`os.listdir(modules.MODULES_DIR)`, currently 16, discovered at
runtime — the existing 16-module tests already do this, not a hardcoded count):

1. Read the module's real `main.tf`. Decompose it into its constituent top-level `resource`/
   `data` blocks using `schema_lint.iter_hcl_blocks()` — this is mechanical text-splitting
   (already-built, already-proven parsing logic), never generation.
2. For each block, construct a synthetic `novel_resources` entry (`resource_type`, a placeholder
   `justification`/`alternatives_considered` sufficient to pass `architecture_decision.validate
   ()`'s completeness check) and an `authored_content` entry mapping that type to the block's own
   original HCL text.
3. Call `synthesizer.synthesize(..., authored_content=...)` with these entries substituting for
   the module's normal catalog selection (no `selected_modules` entry for this module id).
4. Run a real `terraform plan` on the result (`mock_provider`-backed, matching the existing
   16-module baseline's own real-plan convention) and capture `resource_changes`.
5. Compare against the plan produced by the OLD path (the module composed normally via
   `compose()`'s `shutil.copytree`) for the same inputs: same set of resource types, same action
   shape (create-only, matching the existing G5 baseline), same attribute values where both are
   determinable. Exact byte-identical HCL is NOT the bar (file layout differs by construction —
   flat root files vs. a module subdirectory); **plan-JSON equivalence is the bar**, because
   plan JSON is what every downstream gate actually consumes.
6. If equivalent: the module's existing G5/G6/G9 proof-on-record is inherited for the new path,
   per section 0's argument, with no separate per-gate re-proof required. If not equivalent: the
   module is a named blocker (section 2), not silently marked done.

### 1.2 A real, disclosed granularity limit on what this proves

Decomposing a module into independent, directly-cross-referenced flat files (the only shape
`authored_content` currently supports — see `docs/phase6_step1_authoring_scope.md` section 2's
own deliberate rejection of a synthetic child-module wrapper) proves equivalence for **one
resolved, one-shot composition** of that module's resources. It does **not** prove:

- That the module could be composed **more than once in the same run with different inputs**
  (true module reusability via `variable`/`output` boundaries) — no catalog module is currently
  composed more than once per run anyway (confirmed: `compose()` takes a flat `module_ids` list,
  one entry per id), so this is a real but currently-inapplicable gap, not a live regression.
- That a module needing genuine external parameterization **across separate runs with different
  concrete values** (e.g. a different `bucket` name per customer) is provably equivalent in the
  general case — the harness above proves equivalence for the SPECIFIC values a given run
  resolves to, not for the input contract in the abstract.

Any module where this limit is load-bearing (none identified yet by inspection, but not
exhaustively checked here) is a named blocker, per section 2 — not something this scope asserts
away.

### 1.3 Two proof gaps found while grounding this scope, neither closed yet, both required before the harness can run for real

- **G2's "is clean" claim, per module, is currently implicit, not asserted.** The existing parity
  test (`test_gate_content_is_byte_identical_to_gate_module_for_every_real_module`) proves the two
  call paths agree; it does not itself assert `blocking is False` for all 16 (true today only
  because every real module is, in fact, already pinned and clean — but that's an inference, not
  a regression-tested fact). Add one assertion to close this before the harness depends on it.
- **G6's 16-module zero-false-positive proof is prose, not a test.** `docs/g6_iam_extension_scope.
  md` section 7.2 is a manually-produced, one-time table from a real `terraform plan` run against
  every module — real evidence, but not re-verified by CI on every future change the way G5's and
  G2's 16-module proofs are (`test_destructive_change_gate.py`, `test_schema_lint.py`). Before
  this scope's harness can compare "old path's G6 result" against "new path's G6 result"
  programmatically, G6's existing per-module zero-FP claim needs to become an actual parametrized
  regression test (same shape as the other two), not re-derived by re-reading a doc. This is Step
  5's own required work, named here so it isn't silently assumed already done.

### 1.4 G9's real fidelity state — disclosed, not glossed, because it changes what "the same bar" means

`ephemeral_apply.RESOURCE_TYPE_ALLOWLIST` is keyed by `(resource type, emulator)`, not by module,
and — verified directly, not assumed — most of the 41 real AWS types are `verified=False` on
every emulator; the three security-critical types (`aws_iam_role`, `aws_kms_key`,
`aws_s3_bucket_policy`) are `negative_fidelity_verified=False` on both free emulators, meaning
**most modules today are, honestly, G9-*unverified*, not G9-*proven-safe***. This is not a new
gap this scope introduces — it's the pre-existing, already-disclosed state from Phase 5 and
Step 1. The consequence for this scope: "the new path passes the same G9 bar the old path passed"
means, for most modules, **"the new path also correctly blocks/stages on `resource_type_
unverified`/`negative_fidelity_unverified`, exactly like the old path's plan would"** — not "the
new path proves real apply-time fidelity," because that proof does not exist for the old path
either, for most types. Demanding more of the new path than the old path has ever actually earned
would be a double standard, not rigor.

## 2. Both-direction / no-regression discipline

Teardown is reversible-until-proven, per module, not a single switch:

- A module whose harness (1.1) shows plan-JSON equivalence, whose existing G5/G2 proof already
  carries over, and whose G6/G9 proof-on-record (once 1.3 is closed) also carries over, is
  **proof-ready**. What that means depends on section 0.1's still-open decision: **under Option
  A**, its `modules/<id>/` directory's role changes (section 4) and `compose()`'s `shutil.
  copytree` branch for that specific id becomes dead code, removed for that id only, never a
  blanket rewrite of `compose()`. **Under Option B**, "proof-ready" is recorded as a fact (the
  pipe is sound for this module's content) but `compose()` keeps copying from `modules/<id>/`
  exactly as it does today — readiness is not itself a trigger for relocation.
- A module that fails the harness, or that trips the granularity limit in 1.2 in a way that's
  actually load-bearing for it, is a **named blocker**: it keeps its current catalog-copy path,
  full stop, under either option. "Built the pipeline, could not prove parity for module X,
  disclosed it" is the expected, acceptable shape of that outcome — not a defect to be argued
  around, retried with a looser bar, or quietly excluded from the count.
- **Partial teardown is the expected outcome under Option A, not a fallback; zero teardown is the
  expected outcome under Option B, and is equally not a fallback.** Nothing in this scope requires
  all 16 to pass together, and nothing authorizes deleting `compose()`'s shared copy-loop or the
  `modules/` directory wholesale under either option — `module_provenance.py`'s retirement is the
  one action this scope treats as decided regardless of A/B (section 3), since it depends only on
  the re-verify-live argument, not on whether the catalog keeps its composition-source role.

## 3. The dependency map, executed — per component, against current code, not the original plan

`docs/phase6_scope.md` section 3's table made the calls; this section carries each one out or
states precisely why it hasn't happened yet.

- **`synthesizer.compose()`'s `shutil.copytree` copy-path — "Dies," originally.** Verified: still
  runs, unconditionally, for every selected module (`core/generation/synthesizer.py` lines
  ~316–321). Per section 0.1, whether this dies at all — even per-module, even for a proof-ready
  module — is exactly the open decision: under Option A it retires per module, gated on that
  module's own harness result, never a blanket deletion; under Option B it does not retire at all
  until a real generation mechanism exists, regardless of how many modules are proof-ready. Not
  touched by this scope document itself either way.
- **`modules.py`'s `MODULES` list + `match_modules()` — "Repurposed," originally.** Verified: NOT
  yet done. Every real call site (`synthesizer.select_modules()`, `patterns.py`'s pattern-reuse
  cache, `modules.py`'s own CLI) still uses it exclusively for final-selection. This scope
  authorizes the actual repurposing as real, scoped Step 5 work: a new function (not a rewrite of
  the scorer, matching the original scope's own framing) that calls the existing scoring logic to
  rank retrieved examples for the harness's own decomposition step (1.1) and, longer-term, for
  whatever authors novel content in practice — `match_modules()`'s output shape (`score`,
  `matched`) already fits a retrieval-ranking role without changes.
- **`module_provenance.py`'s `pin()`/`verify()` — "repurposed or retired, genuinely unresolved,"
  originally.** Resolved here, with reasoning recorded, same standard as every other design call
  this session: **retire, not repurpose.** `pin()`'s entire value proposition is "trust this
  content because it was checked once, at pin time, and nothing has changed since" (`verify()`'s
  hash-drift check). That value proposition evaporates once nothing is copied-and-trusted-from-
  history: section 4's "grounding examples" role re-verifies live, via `gate_content()`, at
  every actual draw — the same fresh live-schema check `pin()` used to gate once, now happening
  every time instead of once. A stale historical hash adds no safety a fresh live check doesn't
  already subsume. `PROVENANCE.json` files may be kept as a historical record of when a module was
  last reviewed as a real, working catalog module (useful context, not a safety mechanism), but
  `pin()`'s gating role in the `minus-update-module` CLI path retires once nothing routes through
  it as a trust boundary.
- **`source_guard.py` — "Survives unchanged," originally.** Confirmed correct and already
  sufficient, not a Step 5 deliverable: the authored-vs-copied distinction Step 1 needed already
  lives one layer up, in `synthesizer._write_manifest()`'s `"modules"` vs `"authored_resources"`
  keys and `write_baseline(..., extra=...)` — exactly the layer this distinction belongs at
  (`source_guard.py` is deliberately tree-generic; teaching it a catalog-specific concept would be
  a regression in its own design, not an improvement). No code change to `source_guard.py` itself
  is needed for this scope.
- **`modules/*/main.tf` themselves — "Dies, conditionally," originally.** See section 4: under
  either option they do not die at all (no HCL content is ever deleted by this scope) — what's
  actually conditional, per section 0.1, is whether their *privileged, verbatim-trusted status*
  dies (Option A, per module, on proof) or whether that status is merely loosened to
  "re-verified live, not blindly pinned" while the composition-source role stays (Option B).

## 4. What stays — under either option, nothing is deleted; what changes ROLE differs by option

Nothing in this scope deletes the 16 modules' actual HCL content, under Option A or Option B.
What retires, regardless of which option is chosen, is the CLAIM that content carried until now:
"copy this verbatim, once, pin it, trust the pin forever" (see `module_provenance.py`'s
retirement, section 3, decided independent of A/B). What differs by option is whether `compose()`
keeps depending on the catalog as its real source:

- **Under Option A**: the `.tf` content of a proof-ready module physically relocates, as a
  grounding/few-shot example for section 3's repurposed retrieval step — proposed location:
  `modules/<id>/` → a new `catalog_examples/<id>/` (or equivalent — the exact path is an
  implementation detail for an actual teardown PR, not this scope), signaling by location alone
  that it is no longer the compose-verbatim source of truth. `compose()` no longer copies from it.
- **Under Option B**: the content stays exactly where it is (`modules/<id>/`), continues to be
  what `compose()` copies from for every real requirement it satisfies, and is ADDITIONALLY
  indexed by section 3's repurposed retrieval step — the same content serving both roles
  (composition source AND grounding example) simultaneously, until a real generation mechanism
  exists to take over the first role.
- **`MODULES` list metadata (`id`, `title`, `services`, `satisfies`, `inputs`) survives** — it is
  exactly the index `match_modules()`'s retrieval role (section 3) needs; only its role changes,
  from "the final answer" to "a ranked reference."
- **Every retrieved example is re-verified live, at the point it's drawn on**, via
  `schema_lint.gate_content()` against the then-current real provider schema — never assumed
  still valid because it passed once. This is the direct replacement for `pin()`'s retired
  historical-trust role (section 3), and it is a strictly stronger guarantee: live-checked on
  every use instead of checked once and trusted until someone remembers to re-verify.
- **`PROVENANCE.json` history may be retained** as a dated record of "this was a real, working,
  human-reviewed catalog module as of this pin" — informative provenance, not a gate, once
  `pin()`'s gating role retires per section 3.

## 5. Proof bar

0. **Section 0.1's decision (Option A vs. Option B) is made explicitly, by the user, before any
   of the following items are treated as authorizing relocation or copy-path retirement.** This
   scope does not resolve it and does not default to either reading if left unanswered.
1. **The harness (1.1) exists and runs against all 16 real modules** — real `terraform plan`
   comparisons, not asserted from reading the diff. Required under either option: it is the thing
   that makes "proof-ready" a checked fact rather than an assumption, whether or not readiness
   triggers relocation.
2. **G2's per-module "is clean" assertion (1.3) and G6's per-module zero-FP regression test
   (1.3) both exist as real, automated, parametrized tests** before the harness is considered to
   produce a trustworthy verdict — not re-derived from prose each time.
3. **Every module's harness result is recorded, per module, with its verdict (pass/blocker) and
   reasoning** — no silent aggregate "looks like it mostly works."
4. **Under Option A**: for every module that passes, its `compose()` copy-path is retired for
   that id specifically, its content is relocated per section 4, and its old `modules/<id>/` path
   no longer participates in `shutil.copytree`. **Under Option B**: passing modules are recorded
   as proof-ready; `compose()`'s copy-path is untouched for every module regardless of harness
   result. Under either option, a module that doesn't pass keeps its current path, named as a
   blocker, with reasoning recorded to the same standard as every prior exclusion this session.
5. **`match_modules()`'s repurposing (section 3) and `module_provenance.py`'s retirement
   (section 3) are both actually implemented**, not just decided here — required under either
   option, since neither depends on the A/B answer.
6. **The both-direction check**: nothing that currently ships (any module not attempted, or any
   module whose regression proof is still pending, or — under Option B — any module at all, since
   none relocate) regresses — a partial-teardown (or zero-teardown) outcome must leave every
   untouched module exactly as capable and as proven as it is today.

## Ordering invariant

No implementation starts until this scope is reviewed and agreed — the same discipline every
prior phase and step in this project has been held to, not relaxed for being the last one.
**Additionally, and separately: no catalog directory is relocated and no `compose()` copy-path
is retired until section 0.1's Option A/Option B decision is made explicitly** — the harness,
the two test-gap closures, and `match_modules()`/`module_provenance.py`'s changes may proceed
under either answer, but relocation/retirement specifically wait on that one decision, not on
this scope's review alone. G6's shadow-only status and its own separate, still-open
enforcement-flip decision are unaffected by any of this. G9's LocalStack fidelity column stays
unverified pending a provisioned paid account, independent of this step's own work. This scope
explicitly accepts partial teardown (Option A) or zero teardown (Option B) as equally
legitimate, honest outcomes — success here is a correct, checked verdict per module and an
honest answer to section 0.1, not a specific number of deleted directories.
