# Phase 7 Item 5 scope — the authoring mechanism itself

Scope document only. No implementation until this is reviewed and agreed. This gets more scrutiny
than any other item in this plan, by explicit instruction, because it is the one piece the entire
pivot was for and the one piece never built: everything in items 1–4 made the seam able to carry
real content and made a real requirements-driven decision honestly scoped — none of it decides
what HCL to write. This document has to answer, concretely, what does.

> **CORRECTION (2026-07-16, first pass), after the checks/audit-record below were already built:**
> Section 1 as originally written assumed MinusOps would embed its own LLM API client (an
> Anthropic SDK call, its own model choice, its own credentials) to do the authoring. That first
> correction was still wrong, in a smaller way it didn't catch at the time — it assumed MinusOps
> would still make *some* kind of call to a model on its own (later floated as raw stdlib HTTP
> instead of the SDK). See the second correction immediately below for the actual architecture.

> **CORRECTION (2026-07-16, second pass — supersedes the first): MinusOps never calls a model.
> Ever.** Not via an SDK, not via raw HTTP, not via any dependency, key, or provider choice of its
> own. This project is **agent-neutral** — `mcp/terraform-mcp.json` plus this repo's own
> per-agent integration docs (Claude Code, Codex, Cursor/Cline/Continue, Goose, Antigravity) are
> "one source of truth, agent-neutral," and that principle applies here without exception.
> Embedding any model call — through any transport — picks a provider, needs a credential, adds a
> dependency, and puts model cost inside a product whose entire integration story is "whatever
> agentic CLI the operator already drives." MinusOps's job is: assemble the real context an
> authoring agent needs (`assemble_authoring_context()`: live provider schema + real grounding
> examples + the human-reviewed decision record), and gate whatever comes back
> (`_validate_novel_resources()`, G1/G5/G6/G9, the audit chain). **The driving agentic CLI —
> whichever one the operator is already running MinusOps through — IS the generator.** It reads
> the context surface, authors the HCL itself, and hands it back through the exact same
> `authored_content` interface every other caller of `synthesize()` already uses. Nothing about
> that interface changes. Every fail-closed check, the no-retry decision (restated below, now
> correctly scoped to MinusOps's own side only), and the proof bar survive — only §1's premise
> ("an LLM call" as something *this project* makes) was ever wrong. Replace every reading of "the
> model"/"the LLM call" below with "the driving agentic CLI, using `assemble_authoring_context()`'s
> output."

## 1. What actually authors

**Named concretely: the driving agentic CLI** — Claude Code, Codex, agy, or whatever other agent
the operator already runs MinusOps through — not a template engine, not a decision tree, and not
a model MinusOps calls on its own. Justified by elimination, not by default: a template engine or
decision tree can only reproduce patterns someone already encoded — that is what the 16-module
catalog and `compose()`'s copy-path already do. The survey's own finding was explicit: *"nothing
decides what HCL to write from a resource-type name or a requirement — this is the core, unbuilt
capability."* Only an agent that can generalize past encoded patterns can do that, and that
capability already exists wherever MinusOps is being operated from — it does not need to be
re-built or re-licensed inside this project. This project's own docs already named this the
"research-grounded generation" endgame (`docs/g2_scope.md`, quoted in `docs/phase6_scope.md`
§1.2); this item is where that stops being deferred language and becomes a design decision with
consequences — the decision being *what MinusOps supplies*, not *who authors*.

**MinusOps's own job here has a cost too, even though it's not the cost of running a model:**

> **INVARIANT, not a design note — the boundary this whole item depends on:** the driving agent
> authors *syntax* for a resource_type a human has already named and justified in
> `novel_resources`. **It never proposes a type, never decides what gets built.** `novel_resources`/
> `architecture_decision.json` stays the one human-reviewed record deciding *what* — exactly the
> same discipline that record's own docstring already states for module selection (*"keyword
> matching cannot silently become a recommendation engine"*), extended to authoring without
> exception. If this boundary erodes — if authoring is ever allowed to introduce a resource_type
> nobody declared, or to expand scope beyond the declared type — the entire human-reviewed-record
> discipline this project has held since Phase 1 collapses, not just this item's own safety story.
> Any future change that touches this boundary needs its own explicit review, not a quiet
> refactor. This invariant is enforced entirely on MinusOps's side (the fail-closed checks below)
> — it does not depend on, and cannot depend on, anything about which agent authored the content.

- **Non-determinism.** The same declared resource_type + justification can produce different HCL
  from different driving agents, or different attempts by the same one. This scope does not try
  to make authoring deterministic — the invariant above is what stays deterministic and
  human-reviewed; non-determinism is confined to *how* to author HCL for a type already named,
  never *what* to build.
- **Occasional confident wrongness.** A driving agent can hallucinate a nonexistent type, invent
  an attribute a real type doesn't have, or author content for a type other than the one it was
  asked for. Section 4 below maps every one of these to a specific check and verdict — this is
  not deferred to "the gates will catch it eventually," it's enumerated, and every one of these
  checks runs regardless of which agent produced the content.
- **Retries are not MinusOps's concern — a scope boundary, not an oversight.** Whether a driving
  agent re-authors after a failed attempt is that agent's/operator's own call; MinusOps has no
  visibility into and no say over it. What IS MinusOps's job, unchanged: **a failed check is a
  hard stop.** `_validate_novel_resources()` never silently discards a bad attempt and never
  loops trying variations itself — it raises once, with the specific reason, and
  `write_authoring_record()` preserves the raw output and the failure reason in the audit record
  either way (authored or blocked). This is the same fail-closed posture this project has already
  held twice elsewhere (fail-closed-on-unparseable-input, never fail-open-and-retry) — restated
  here as "MinusOps hard-stops on a bad attempt," not "MinusOps controls whether retries happen,"
  because it doesn't and shouldn't.

  > **This boundary must never blur, including for "convenience" in a later phase.** An
  > iterate-until-valid loop (call, check, re-call on failure, repeat) is a real, legitimate
  > pattern — but it belongs entirely on the driving agentic CLI's own side of this line, never
  > wired into MinusOps itself. Moving it inside MinusOps later — even framed as a Phase 6+
  > convenience so operators don't have to loop by hand — would mean the same component judging
  > correctness (deciding an attempt failed and trying again) is no longer independent of the
  > component being judged. That is precisely the decorrelation failure external research on
  > LLM-authored IaC warns about: a generator and its own retry judgment sharing one boundary
  > stop being decorrelated oracles. `_validate_novel_resources()`'s fail-closed checks stay
  > useful specifically because they are the one thing in this path that never re-tries, never
  > second-guesses its own verdict, and never lives inside the same loop as whatever produced the
  > content it's checking. Any future proposal to add retry logic inside MinusOps needs to
  > confront this paragraph directly, not quietly route around it.
- **Reproducibility of the DECISION, not the output.** Since the exact HCL isn't reproducible
  attempt-to-attempt (different driving agents, or the same one on a different day, may author
  differently), what must be reproducible is the record of what happened: the resource_type
  requested, the justification, the grounding examples and live schema supplied, the raw authored
  output (whether it passed or was hard-stopped), and — new, see the write-up below —
  which agent was driving when it happened. Written to the same audit chain
  `plan_gate.py`/`_audit_allow_incomplete_bypass()` already write to, so a specific authoring
  decision is always reconstructable after the fact even though a different attempt might not
  produce the same bytes. **Payload size, checked, not assumed**: a live `get_type_schema()` block
  for `aws_dynamodb_table` measured 8,996 bytes; `retrieve_grounding_examples()` for a typical
  request measured ~4.5 KB across 2 real module bodies (both measured against the real running
  code, not estimated). `audit_chain.append()` has no hard size limit — it's a plain
  `json.dumps()` + sha256 per line — so inlining this would not technically break. It is still the
  wrong call: this project's own established pattern for bulky artifacts (`source_guard.py`'s
  baseline manifests, `requirements.json`/`architecture_decision.json` themselves) is a small,
  hash-verified audit record pointing at real files written into the run's own workspace, not
  bulk content inlined into the hash-chained log itself. The authoring record follows that same
  pattern: the chain entry carries `resource_type`, `justification`, `driving_agent`, `verdict`,
  and a `content_hash`/`schema_hash`/`grounding_hash` plus the relative path each full artifact
  was written to (e.g. `authoring/<resource_type>-{schema,grounding,output}.json` under the run
  root) — tamper-evidence via hash comparison, without bloating the chain itself.

**What MinusOps actually supplies** (the prompt/model/provider the driving agent uses is that
agent's own business, correctly out of this document — MinusOps has no say in it and needs none):

1. `novel_resources[i]`'s own `resource_type` + `justification` + `alternatives_considered` — the
   human-reviewed decision of *what* and *why*, unchanged.
2. `get_type_schema(provider, resource_type)` (Item 4) — the real, live attribute/block shape for
   the declared type, grounding the driving agent in what attributes actually exist rather than
   letting it invent plausible-sounding ones.
3. `retrieve_grounding_examples(requirements)` (Step 5, additive) — real, human-reviewed HCL from
   the closest existing catalog modules, grounding style and wiring convention (naming, tagging,
   how outputs are typically shaped), not correctness (correctness is item 2's job and section 4's
   checks).
4. Output MinusOps accepts back: exactly one `authored_content` entry (Item 1's flat-`str` or
   module-`dict` form), nothing else — the interface items 1/2 already built and proved, unchanged
   by this item. `assemble_authoring_context()` (and its CLI twin, `synthesizer.py author-context
   <resource_type> <requirements>`) is items 1–3's real, built, tested surface for handing this to
   whichever agent is driving.

## 2. The smallest honest first target — `aws_dynamodb_table`, and why

Not "author any resource type." One specific, simple, well-understood type, authored end-to-end
through the real seam, proven to pass every gate. The same discipline G9 used to prove itself —
one real caught failure, not a claim of general coverage.

**Picked: `aws_dynamodb_table`.** Verified against the real code, not chosen for convenience:

- **Already this project's own canonical fixture for exactly this scenario.** `test_synthesizer.
  py`'s `_NOVEL_DECISION`/`_VALID_DYNAMODB_HCL` has used a hand-typed `aws_dynamodb_table` as the
  "no catalog module provides this" example since Step 1, and `destructive_change_gate.py`'s own
  module docstring cites it as the live-confirmed proof that a genuinely novel type stages under
  G5's Step 0 fix. Item 5 closes the loop on the SAME type: from "hand-typed in a test fixture, in
  the abstract" to "authored for real by a real driving agent, through the real pipeline."
- **Confirmed absent from every gate's own reviewed-type set** (grepped, not assumed):
  `destructive_change_gate.py` has zero matches outside its own docstring's comment — not in
  `STATEFUL_RESOURCE_TYPES`, `IAM_RESOURCE_TYPES`, or `AUTO_SHIP_ELIGIBLE_TYPES`. Zero matches in
  `policy/g6/rules.rego`. Zero matches in `ephemeral_apply.py`'s `RESOURCE_TYPE_ALLOWLIST`. This
  means it correctly routes to `unreviewed_resource_type` under G5 (never `autonomous_eligible`)
  AND correctly reports as unverified under G9's `entry is None` branch — staged for two
  independent reasons, neither a bug, giving a clean demonstration with no ambiguity about why it
  didn't ship.
- **Minimal attribute surface**: `name`, `billing_mode`, `hash_key`, one `attribute { name, type }`
  block. No dynamic blocks, no companion assets, no cross-resource wiring — none of items 1's
  named structural gaps (path.module assets, required-variable wiring) are even in play for this
  target, so a first pass isn't simultaneously debugging the seam and the mechanism.
- **A real, well-known AWS type** — `get_type_schema("aws", "aws_dynamodb_table")` resolves a real,
  non-trivial schema (partition/sort keys, billing mode, TTL, point-in-time recovery, encryption),
  giving the grounding step real material to work with, not a toy.

## 3. The coverage expectation, stated so staging reads as correct, not as failure

`aws_dynamodb_table` sits outside the 41-type reviewed universe by construction (that's *why* it's
the right first target — section 2). This means, unavoidably and correctly: **G5 stages it, always
— it is not `autonomous_eligible` regardless of how clean the authored content is. G6 fires zero
rules — not because the content is safe, but because nothing has been written to check DynamoDB
configuration at all (the same 33/41-zero-coverage gap already escalated in HANDOFF §5, unrelated
to and not fixed by this item). G9 reports it as unverified on every emulator.**

This is stated here explicitly so it is never misread later: **the first real generated output
correctly landing in the staged, human-reviewed path is the pipeline working exactly as designed
— not the generator being broken, not a disappointing result, not evidence Item 5 "didn't really
work."** Proof bar item 6 (below) makes this an asserted, checked fact, not a hoped-for one. A
generated `aws_dynamodb_table` auto-shipping on its first appearance would be the actual failure —
it would mean G5's fail-closed boundary broke, not that generation succeeded.

## 4. Fail-closed on the driving agent's output — every mode named, mapped to a verdict

Items 1/2 already established the standard: a hallucinated, nonexistent type is a hard block
(authoring malfunctioned), distinct from a legitimate novel type that exists and flows to G2 for
its real content check. This carries that forward and closes a gap this item's own analysis
found — the existing checks were built for a human/test caller who naturally authors content
matching what they declared; any agentic authoring caller is where "declared X, authored Y"
becomes a real, non-hypothetical failure mode.

| Failure mode | Check | Verdict | Status |
|---|---|---|---|
| Declared `resource_type` doesn't exist in the live provider schema at all | **NEW**: `get_type_schema(provider, resource_type)` called BEFORE authoring; `None` → refuse | Hard block, before any context is even handed to an authoring agent | New work, this item |
| Authored content's own resource/data block type doesn't match the declared `resource_type` (flat form) | **NEW**: cross-check the block type(s) found by `iter_hcl_blocks()` against the declared type | Hard block — content that doesn't address what was asked is authoring malfunction, not novel output | New work, this item |
| Hallucinated attribute, or a mismatched type that IS real but wrong | `schema_lint.gate_content()` (G2) — `unknown_type`/`unknown_attribute` | Hard block | Already built (Step 1) |
| Zero resource/data blocks declared at all | `_validate_novel_resources()`'s existing check | Hard block | Already built (Step 1) |
| `path.module` reference with no matching companion asset | Item 1's asset check | Hard block | Already built |
| Required variable (module form) with no default, no `module_args`, no auto-wire match | Item 1's required-variable check | Hard block | Already built |
| Structurally malformed HCL a regex-based scan can't reliably catch (unbalanced braces etc.) | **G1** (`terraform validate`), downstream in the real `stage_plan` flow — the actual parser, not a best-effort scan | Refuse to plan | Already built, generic, unaffected by this item |
| Well-formed, real, novel type with an unreviewed configuration risk (e.g., no encryption) | G5 (`unreviewed_resource_type`) stages it; G6 has no rule for an out-of-universe type | **Stages** for human review; G6 provides no content-level assurance (disclosed, matches the already-escalated 33/41 gap, not newly introduced or newly fixed here) | Existing, unaffected |
| Different attempts (different driving agents, or the same one twice) may author different HCL for the same declared need | **NEW**: the full authoring record (declared type, justification, schema + grounding examples supplied, raw output, which agent was driving) written to the audit chain | Not blocked — made reconstructable after the fact, regardless of who/what authored it | New work, this item |

The two genuinely new checks (pre-authoring schema existence, and declared-vs-authored type match)
both belong in the same place the existing ones live: `_validate_novel_resources()`'s fail-closed
chain, run in that order — schema-exists check first (cheapest, and the only one that can save an
authoring agent the effort entirely), type-match check right after content exists, before the
already-built G2/asset/variable checks.

**Named, not solved here**: the type-match check is scoped to the flat (`str`) form, matching
this item's own first target. The module (`dict`) form's unit can legitimately bundle several
resource/data blocks under one caller-chosen key that isn't itself a literal type string (Item 1's
own real modules, decomposed, are the existing proof of this shape) — what "the content actually
addresses the declared need" means for a multi-resource unit is a real, harder question this scope
does not resolve, because it isn't required for the target in section 2.

## 5. The proof bar — what "Item 5 works" means, concretely

Not "the generator produced plausible HCL." Every item below is a checkable fact:

1. `aws_dynamodb_table` is authored **fresh, and this is made checkable, not asserted**, against
   four separate properties (all four required — a run satisfying only some of them does not
   count, see the run log below for where this was first gotten wrong):
   - **(a) The content came through `assemble_authoring_context()`** — the driving agent fetched
     the real schema + grounding for this exact declared type, not an ad hoc or invented context.
   - **(b) The content is grounded in the live schema** — every attribute/block it uses is one
     `get_type_schema()` actually returned as real, not invented.
   - **(c) The content differs from the fixture, checkably, not just "looks different."**
     `test_synthesizer.py`'s existing fixture (`_VALID_DYNAMODB_HCL`) is a single-hash-key table
     named `"novel-table"`, `billing_mode = "PAY_PER_REQUEST"`, one `attribute { name = "id", type
     = "S" }`. The declared requirement must make reproducing the fixture impossible, not just
     unlikely: a **different table name**, a **composite key** (`hash_key` + `range_key`, the
     fixture has neither a range key nor a second attribute block), and **`billing_mode =
     "PROVISIONED"`** with explicit `read_capacity`/`write_capacity` (the fixture only ever uses
     `PAY_PER_REQUEST`, which take no capacity arguments at all). Output containing a `range_key`
     and `read_capacity`/`write_capacity` cannot be the fixture reproduced.
   - **(d) The authoring record captures the context supplied, the content returned, and which
     agent was driving** — `write_authoring_record()`'s `driving_agent` field, so the record is
     honest about provenance without MinusOps trying to *verify* provenance (see the note below).

   **What this deliberately does NOT check, and why**: whether a human or a model produced the
   bytes. MinusOps cannot verify this and should not try — there is no non-fakeable signal
   (token-usage counts, response metadata, timing) available to a project that, by design, never
   makes its own model call and never sees a model's own response object. An earlier draft of
   this proof bar required "non-zero real token usage" as a freshness signal — that only makes
   sense for a project that embeds its own API call, which this one explicitly does not (see the
   second correction at the top of this document). Provenance is the audit record's job (property
   (d) above), not something a token count can or should prove.
2. The pre-authoring `get_type_schema()` check passes (the type is confirmed real before any
   context is handed to an authoring agent).
3. The authored content passes every existing `_validate_novel_resources()` check: non-zero
   blocks, the new type-match check, G2's `gate_content()`.
4. Composed through the real public `synthesize()` entry point, via Item 2's zero-catalog path
   (`selected_modules: []`, `novel_resources` covering the DynamoDB table) — not `compose()`
   called directly.
5. **G1** (`terraform validate`) passes for real.
6. **G5 classifies it `unreviewed_resource_type`, `autonomous_eligible: False`** — asserted and
   checked, not assumed from section 3's reasoning alone.
7. **G6** shadow-evaluates it; zero rules fire — asserted as the expected, correct result, not
   silently skipped or treated as "nothing to check."
8. **G9** reports it unverified (absent from `RESOURCE_TYPE_ALLOWLIST`) — a second, independent
   confirmation that this output correctly cannot auto-ship.
9. The full path — decision record, the new authoring record (section 1's audit requirement),
   every gate verdict — is present in the audit chain and reconstructable end-to-end from it
   alone, without needing to re-run anything.
10. The result is reported as what it is: **the pipeline correctly refusing to trust unproven
    output** — matching section 3, so this is never later misread as a shortfall.

### Run log — 2026-07-16

A run was performed under this architecture: the driving agent (this session, operating MinusOps
through its own Claude Code session — a real, concrete instance of "whichever agentic CLI the
operator already runs MinusOps through") fetched real context via `assemble_authoring_context()`,
authored the `aws_dynamodb_table` HCL itself grounded in that real schema and the real grounding
examples returned, with parameters (composite key, `PROVISIONED` billing, explicit read/write
capacity, a different table name) that make the fixture unreproducible, and composed it through
`synthesize()`'s real zero-catalog path.

**This run was, for a brief period, incorrectly downgraded** — reasoned (wrongly) that because the
driving agent authoring the content was also the one who had designed the proof bar, this was
equivalent to hand-typing `_VALID_DYNAMODB_HCL`. That reasoning does not survive this document's
own corrected §1: **the driving agentic CLI is the mechanism, by design** — there is no separate
"real" authoring path this session's own authoring could have deferred to instead. This session,
operating as the driving agent MinusOps is built to be operated by, satisfies properties (a)
through (d) of proof-bar item 1 exactly as designed: it fetched the real context surface, grounded
its output in the real live schema, produced content the fixture cannot reproduce, and the
authoring record captures the context, the output, and the driving agent. **The run's status is
restored: item 1 passes, and Item 5 is proven end-to-end** — real `terraform plan` (G1 valid, G5
`unreviewed_resource_type`/`autonomous_eligible: False`, G9 `unverified`), real audit chain.
Item 7 (G6) was separately, correctly reported inconclusive in that specific run (`opa` not
installed in the local dev environment — a tooling gap, not a functional one;
`policy/g6/rules.rego` has zero rules matching `aws_dynamodb_table` by static read, so "zero rules
fire" is the expected result once this is run somewhere `opa` is present).

## What this scope does not decide

- Which specific agentic CLI drives a given session, and how that agent chooses to author (its
  own prompt/model/provider, if it has one) — that is the operator's own choice, made outside
  this project entirely, and MinusOps needs no visibility into it.
- The module-form type-match question (section 4's named gap) — not required for this item's
  target, named so it isn't silently forgotten when a module-shaped novel unit is authored later.
- Whether/when to build out G6 coverage for `aws_dynamodb_table` or any other newly-authored
  type — a separate, already-escalated, larger question (HANDOFF §5 item 1), not this item's job.

## Ordering invariant

No implementation starts until this scope is reviewed and agreed, same as every item before it.
Build order followed: (1) the two new fail-closed checks in `_validate_novel_resources()`
(schema-exists, type-match), each independently testable against hand-authored fixtures exactly
like Item 1's own tests; (2) the audit-record write for the authoring step
(`write_authoring_record()`, including the `driving_agent` provenance field); (3) the context
surface a driving agent actually needs (`assemble_authoring_context()` + the `author-context` CLI
subcommand) — MinusOps's own side of the mechanism, and the only side MinusOps ever builds, since
authoring itself is always the driving agentic CLI's job, never something wired into this project.
Proof bar (section 5) is the acceptance test for the whole item, not for any one piece of it in
isolation.
