# Phase 6 Step 1 scope — the authoring pipeline

Scope document only. No implementation until this is reviewed and agreed. This is Phase 6's Step
1 (`docs/phase6_scope.md`), authorized to be scoped now that Step 0 (G5's fail-closed fix) is
closed and proven. Genuinely new capability — generation-time HCL authoring beyond the fixed
16-module catalog — gets the same scope-first discipline as every prior phase, not less, because
it's the phase that produces the resource types every other gate has to hold the line against.

## 0. The organizing principle this scope is built around

Step 0 closed the one gate that had to be closed before authoring could safely exist: an
unrecognized resource type now stages by default (`docs/g5_autonomy_boundary_scope.md`). Section
4 of this scope's design follows directly from that fix, stated as one governing rule for the
whole authoring era:

**Nothing a generator produces auto-ships on its first real appearance.** A genuinely novel
resource type is, by construction, absent from `AUTO_SHIP_ELIGIBLE_TYPES` (G5), absent from any
named G6 rule, and absent from G9's `RESOURCE_TYPE_ALLOWLIST` — three independent gates, each
already designed (or, for G9, requiring the wiring this scope specifies) to fail closed on
exactly that absence. The first time a type appears, all three correctly block/stage. Only after
a human has reviewed it through all three — schema-verified (G2), security-content-checked (G6,
if it's a policy-bearing or content-risky type), and apply-fidelity-verified against a real
emulator (G9) — does a *subsequent* occurrence of that same type become eligible for anything
resembling autonomous ship. This scope's job is making sure that review path actually exists and
actually runs, not inventing a way around it.

## 1. The research/retrieval step (`docs/phase6_scope.md` section 2.1)

`modules.py:match_modules()`'s keyword-overlap scorer is reused, not rewritten: given a
requirements record, it already returns every catalog module ranked by real, explainable
keyword-phrase overlap (`score`, `matched`). Repurposed as the grounding-retrieval step: for a
requirement that doesn't cleanly match an existing module (a real gap the score itself can
signal — e.g. no result clears `min_score`, or the best match's `matched` phrases are only
generic service-name tokens), the SAME ranked list becomes the *reference corpus* handed to
whatever authors new HCL — real, human-reviewed wiring patterns, attribute shapes, and
cross-module conventions to ground against, not a black-box generation from nothing. No new
retrieval engine needs building; the interface is "return the top-N scored modules and their
real `main.tf` content," which `match_modules()` plus a file read already provide.

### `architecture_decision.json` generalized — the concrete schema change

Today `architecture_decision.validate()` requires `selected_modules` (a non-empty list of
existing catalog IDs) and rejects anything `synthesize()` can't resolve against the registry.
That's necessarily too narrow once a requirement needs a resource type no module provides. New,
additive field, validated with the same rigor `alternatives` already gets (`_valid_alternative`'s
shape, reused, not reinvented):

```python
"novel_resources": [
    {
        "resource_type": "aws_dynamodb_table",
        "justification": "...",              # why this type; why no existing module covers it
        "alternatives_considered": ["...", "..."],  # same bar as the existing `alternatives` field
        "grounding_examples": ["storage-medallion-s3", "..."],  # which retrieved modules informed this
    },
]
```

`validate()` gains: if `novel_resources` is present and non-empty, every entry must have
`resource_type`, `justification`, and at least one `alternatives_considered` entry answered —
literally the same completeness check `_valid_alternative` already performs, applied to a new
field. **This record stays a human-reviewable decision, not a generation trigger** — the
non-negotiable condition `docs/phase6_scope.md` section 2.1 named explicitly: whatever proposes
`novel_resources` (an agent doing research, a human, a hybrid) still produces a record a human
reviews and signs off on *before* `synthesize()` ever calls the authoring step, same as
`selected_modules` already requires today. No change to that discipline, only an extension of
what the record can express.

`synthesizer.synthesize()` needs a real code change to stop treating every requested ID as a
catalog lookup: today `unknown_ids = sorted(requested_ids - chosen_ids)` raises `ValueError` for
anything `select_modules()` doesn't resolve. `novel_resources` entries are legitimately not
catalog IDs and must be split out of that check, routed to the authoring step (section 2)
instead of treated as an error.

## 2. The authoring mechanism (`docs/phase6_scope.md` section 2.2)

This scope does not prescribe *how* new HCL gets authored (a specific model, prompt, or
templating approach is implementation, not scope) — it specifies the **contract** the authoring
step must satisfy, because every gate downstream depends on that contract holding:

1. **Input**: one `novel_resources` entry (resource type + justification + grounding examples
   from section 1) at a time, not a whole-file free-for-all — matching this repo's own
   one-module-one-concern convention (`modules/<id>/main.tf`), so a novel resource's authored
   HCL is reviewable as its own discrete unit, not entangled with the rest of the composition.
2. **Output**: real Terraform HCL text for exactly that resource (and, if genuinely required,
   its minimal directly-supporting resources — e.g. a DynamoDB table's own IAM role, not an
   unrelated second novel resource smuggled in under one justification).
3. **Fail-closed on the authoring step's OWN output, unconditionally, before anything else runs**:
   - Output that doesn't parse as valid HCL at all → hard block, synthesis refuses to proceed.
     Never a partial write, never a silent drop-and-continue with the rest of the composition.
   - A resource/data type that doesn't exist in *any* live provider schema this repo tracks
     (AWS or Databricks) at all — not "unreviewed," genuinely nonexistent, e.g. a typo or a
     hallucinated type name — → hard block. This is stricter than G2's `unknown_type` finding
     (which already exists and already blocks `pin()`); the distinction matters because a
     hallucinated type is a failure of the authoring step itself, not a legitimate novel
     resource needing review.
   - Anything that parses and resolves to a real type flows into section 3 (G2) for the actual
     content-level check — parsing successfully is necessary, never sufficient.

### G2 extended to generation-time output, not just `pin()`

Real, concrete refactor, not a new module: `schema_lint.gate_module(module_id)` today reads
`modules/<module_id>/main.tf` off disk and calls its own internal HCL-scanning pipeline
(`iter_hcl_blocks` → `_scan_body`/`_extract_assigned_values` → `_reduce_full` against the live
schema). The disk-read and the actual linting logic are already two different concerns living in
one function. Split them: a new `gate_content(content, source_label)` becomes the real linting
entry point (everything from `iter_hcl_blocks` onward, unchanged logic), and `gate_module()`
becomes a thin wrapper — read the file, call `gate_content()` — matching the exact pure-function/
enforcing-caller split this session has used everywhere else (`destructive_change_gate.classify()`,
`rego_gate.evaluate()`). The authoring step's output — real HCL text, not yet written to any
`modules/` directory — calls `gate_content()` directly. Same blocking findings
(`unknown_type`, `unknown_attribute`, `deprecated_attribute_in_use`, `type_mismatch`,
`unparseable_reference`), same live-schema-now standard, zero new lint logic to design.

### Provenance via `source_guard`, extended to say what's generated, not just what changed

`source_guard.py`'s hash-baseline mechanism is already file-tree-generic (works on any
directory). The generalization needed is a labeling one, not a mechanical one: a run's manifest
(today, `synthesizer._write_manifest()`'s `minus-generated.json`) records `"modules": [...]` for
composed catalog picks. It needs a parallel `"authored_resources": [{"resource_type": ...,
"decision_source": "novel_resources[N]", "content_hash": ...}]` entry so a later reviewer (or
`module_provenance.verify()`-equivalent check) can tell "this file was copied from a
human-reviewed, pinned module" apart from "this file was generated for this specific run" —
a materially different provenance fact, the same distinction this session's own G5 fix just
established for *why* a type is staged (`reviewed_unsafe_resource_type` vs
`unreviewed_resource_type`) applied here to *where content came from*.

## 3. G9 wired into the real flow (`docs/phase6_scope.md` sections 2.3/4.3) — required, not optional

Confirmed, directly, twice this session (`docs/phase6_scope.md` section 4.3): nothing calls
`ephemeral_apply.py` outside its own CI job. **This is the load-bearing requirement of Step 1,
not a nice-to-have** — a generator that can author a `aws_dynamodb_table` config that plans clean
(G1) and lints clean (G2) has still never been proven to actually *apply* correctly; that is
G9's entire reason to exist, and it currently proves nothing about generated output at all.

Concrete wiring: `plan_gate.py stage_plan()` (or a new stage between `stage_plan` and
`stage_approve` — an interface decision for implementation, not resolved here) gains a real call
to `ephemeral_apply.run_ephemeral_apply(dir_, emulator=...)` whenever the plan's `coverage`
(already computed by `classify_coverage()`) is `"full"` or `"partial"` — i.e., whenever there's
any AWS content to ephemeral-apply at all, generated or composed. This is not conditional on
"does this plan contain a novel type" — every real plan gets the same treatment, matching the
existing pattern that G5/G6 already run on every plan regardless of origin. The design already
handles the novel-type case correctly *by construction*: `RESOURCE_TYPE_ALLOWLIST` blocks on any
unreviewed type (`resource_type_unverified`), so a first-appearance novel type stages here too,
automatically, no new logic needed in `ephemeral_apply.py` itself.

**Real, disclosed cost, not hand-waved**: this makes every real `stage_plan()` call that reaches
this point take as long as a real terraform init/apply/destroy cycle against a live emulator —
minutes, not seconds, a genuine change to the interactive gate's own responsiveness.

**Decision (2026-07-14): synchronous, not async.** `stage_plan()` calls `run_ephemeral_apply()`
and blocks until it returns, the same shape `_g6_shadow_eval()` already uses for G6. Reasoning:
`plan_gate.py` is a CLI/tooling gate invoked from a pipeline step, not a low-latency interactive
UI request path — nothing downstream of it is waiting on a sub-second response.
`ephemeral_apply.py` already works as a blocking call standalone (its own `main()` runs init/
apply/destroy and returns a verdict; no job-store or polling layer exists anywhere in this repo).
Building async now — a job queue, a polling endpoint, a verdict store keyed by request ID — would
be new infrastructure invented for a requirement nobody has yet: no caller today needs a
non-blocking gate, and inventing one speculatively is exactly the "building for a hypothetical
future requirement" this session has repeatedly rejected elsewhere (`docs/
g5_autonomy_boundary_scope.md`'s own heuristic-vs-allowlist call made the same kind of argument).
A multi-minute cost per AWS-touching plan is an acceptable, disclosed price for a governance gate
to pay, not a UX-critical path — if a real need for non-blocking invocation ever materializes,
that is a separate, later, evidence-driven change, not a default assumed in now.

**Named consequence of the current environment (state this plainly, don't let it surprise
anyone)**: no LocalStack token is provisioned this session, and both evaluated free emulators
(MiniStack, Floci — `docs/phase5_scope.md` section 7.5/8.6) already confirmed failing
IAM/KMS/S3 negative-fidelity in this same gauntlet. Wiring G9 into the real flow per this section
means, concretely, today: **G9-in-real-flow is fidelity-limited for IAM/KMS/S3 until a paid
emulator is provisioned; those plans stage rather than auto-ship.** This is correct, intended,
fail-closed behavior — the `opa_not_found`/`terraform_not_found`-style fail-closed path applies
here too — but it is a real, present-tense operational consequence of shipping this wiring today,
not a hypothetical, and must be visible in HANDOFF.md, not left to surprise someone later.

**Emulator choice for this wiring is not this scope's decision to re-litigate**: `docs/
phase5_scope.md` already settled that LocalStack (paid) is the right choice for a trustworthy
fidelity signal, blocked only on a token this session cannot provision. Wiring proceeds against
whichever emulator is actually configured (`ephemeral_apply.py`'s existing `emulator=` parameter
already supports this) — if none is configured, `stage_plan()` must fail closed exactly the way
`opa_not_found`/`terraform_not_found` already do elsewhere in this same file, never silently skip
the check. **A structural consequence worth naming plainly**: every genuinely novel resource type
generation ever produces will need its *own* real fidelity gauntlet (the same both-direction,
per-`(type, emulator)` proof this session ran for the original 41 types) before it can ever leave
`resource_type_unverified` — this is real, recurring verification work Step 1's own existence
creates, not a one-time cost paid by this scope alone.

## 4. G6 coverage for novel types (`docs/phase6_scope.md` section 4.2)

Two distinct real requirements, not one:

### 4.1 A genuinely new resource type gets G6 rule coverage or stays staged — no third option

Mirrors G5's own now-fixed default exactly, applied to G6: before a resource type is ever added
to `AUTO_SHIP_ELIGIBLE_TYPES` (G5), if that type's schema carries a policy-shaped attribute (a
`.policy`/`.assume_role_policy`-style opaque JSON string, or a resource-based-policy pattern —
the same shape `docs/g6_iam_extension_scope.md` already identified for KMS/S3), a corresponding
G6 rule must exist and be shadow-proven (zero false positives) *before* that type's G5 review can
conclude "safe." A type with no policy-shaped content (most compute/networking/scheduling types,
matching this session's own review reasoning in `docs/g5_autonomy_boundary_scope.md` section 3)
has no G6 rule to write and needs none — G6 coverage is required exactly where content risk is
structurally possible, not uniformly for every type regardless of shape.

### 4.2 The 7 config-dependent types already on the safe list — the real, named gap this scope closes

`destructive_change_gate.py`'s `AUTO_SHIP_ELIGIBLE_TYPES` already carries 7 types marked
`# CONFIG-DEPENDENT` (`aws_glue_job`, `aws_kinesisanalyticsv2_application`,
`aws_sfn_state_machine`, `aws_redshiftserverless_workgroup`, `aws_subnet`, `aws_s3_object`, plus
the schema-verified boolean/string flags named there) — reviewed safe *in this repo's current
real configurations*, explicitly flagged for Step-1 re-examination when this scope was written.
G5 gates on **type only**; it structurally cannot see that a generated `aws_subnet` set
`map_public_ip_on_launch = true`, or a generated `aws_s3_object` set `acl = "public-read"` — the
exact same content-blindness `docs/g5_autonomy_boundary_scope.md` section 3 named as the reason
`aws_default_security_group` was excluded, now applying to types that ARE on the safe list.

**Concrete requirement, not deferred further**: before generation-time authoring is trusted to
produce novel *configurations* of these types (not just novel resource types), one of two things
must be true for each: (a) a new G6 rule checks the specific risky attribute (a real, scoped
addition — e.g. a new rule flagging `aws_s3_object.acl` set to a public-shaped value, or
`aws_subnet.map_public_ip_on_launch == true`, mirroring SEC-06/SEC-07's own shape exactly), or
(b) the type is moved from `AUTO_SHIP_ELIGIBLE_TYPES` to `REVIEWED_UNSAFE_TYPES` until such a
rule exists, forcing staged review for any plan touching it regardless of configuration. This
scope does not pre-decide which gets (a) vs (b) — that is real per-type review work, same
discipline as Step 0's own 30-type review — but it does require the decision be made and
recorded before authoring can produce novel configurations of any of them, not left implicit.

### Results (2026-07-14)

**Count correction**: `destructive_change_gate.py`'s own `# CONFIG-DEPENDENT` comment (written
at Step 0 close) said "7" types were flagged; the actual set has always had 6
(`aws_glue_job`, `aws_kinesisanalyticsv2_application`, `aws_sfn_state_machine`,
`aws_redshiftserverless_workgroup`, `aws_subnet`, `aws_s3_object`). A real miscount, corrected
in the code comment rather than carried forward silently.

Each of the 6 got its own reasoned disposition:

- **(a) new G6 rule, stays eligible** — `aws_redshiftserverless_workgroup` (SEC-08,
  `publicly_accessible == true`), `aws_subnet` (SEC-09, `map_public_ip_on_launch == true`),
  `aws_s3_object` (SEC-10, `acl` in a public-shaped canned-ACL set, fail-closed to
  `field_unresolved` when `acl` is unset — verified live that this field is schema `computed`,
  so an omitted value is `after_unknown`, the same shape SEC-06/SEC-07 already established for
  `aws_kms_key.policy`/`aws_s3_bucket_policy.policy`). All three rules are shadow-only, same as
  every G6 rule, and added to `G6_RULE_IDS` in `plan_gate.py` so they are not silently dropped
  from the divergence report/audit chain — the exact bug class the `G6_RULE_IDS` standing
  checklist item exists to prevent.
- **(b) no G6 rule, stays eligible on a reasoned exception** — `aws_glue_job`,
  `aws_kinesisanalyticsv2_application`, `aws_sfn_state_machine`. Each carries an arbitrary
  executable payload; the risk is in what that payload does at runtime, not in any single
  plan-time attribute a Rego rule could pattern-match the way SEC-08/09/10 do. The real
  privilege boundary is the IAM role each one assumes — a genuinely new role is itself a
  separate `aws_iam_role`/`aws_iam_policy` resource, independently caught by SEC-02/SEC-05 if
  newly authored. None were moved to `REVIEWED_UNSAFE_TYPES`: staging every future occurrence
  of an already-reviewed-safe type shape buys nothing against a risk this gate structurally
  cannot see any better staged than un-staged.

Full reasoning recorded in `core/governance/destructive_change_gate.py`'s
`AUTO_SHIP_ELIGIBLE_TYPES` docstring, same standard as the `aws_default_security_group`
exclusion. SEC-08/09/10 unit tests (`tests/test_rego_gate.py`) proven green against a real
`opa` binary — both-direction coverage per rule (flags the dangerous value, clean on the safe/
default value, and, for SEC-10 only, routes the schema-computed unset case to
`field_unresolved`).

## 5. Proof bar

1. **Section 1's schema change**: `architecture_decision.validate()`'s new `novel_resources`
   completeness check, both directions — a complete entry passes, an incomplete one (missing
   `justification` or `alternatives_considered`) fails exactly like an incomplete `alternatives`
   entry does today.
2. **Section 2's fail-closed sweep**: a deliberately malformed authoring-step output (unparseable
   HCL, a hallucinated nonexistent type) must block synthesis outright — proven the same way
   every other fail-closed sweep this session has proven, with a real constructed fixture, not
   asserted.
3. **`schema_lint.gate_content()` parity**: the refactored function, called directly against real
   HCL text (not a `modules/` file), must produce byte-identical findings to `gate_module()`
   calling it internally for every one of the 16 real modules — a real regression proof that the
   split didn't change behavior, run before this closes.
4. **G9 wiring proof**: a real `stage_plan()` call, with a real (free) emulator configured,
   actually invokes `ephemeral_apply.py` and its verdict is visible in the same output/audit
   trail G5/G6 already use — proven against a real plan, not asserted from reading the diff.
5. **The config-dependent types' disposition is explicitly decided**, per type, with reasoning
   recorded — same standard `aws_default_security_group`'s exclusion was held to in Step 0, not
   silently left `# CONFIG-DEPENDENT` forever. **CLOSED, see section 4.2 Results.**
6. **G9 must be shown to CATCH, not just RUN** (added on review, not in the original scope):
   proof-bar item 4 above only proves the wiring exists — that the pipe carries a verdict, not
   that the verdict is ever load-bearing. Required: at least one real case where a plan passes
   G1 (`terraform validate`) and G2 (schema lint) cleanly but FAILS at G9 — a real apply-time
   failure the static gates structurally cannot see — same standard G9's own hostile-escape
   proof was already held to (`docs/phase5_scope.md`): prove it catches the thing it exists
   for, not merely that it runs.

## Results (2026-07-14) — all six proof-bar items closed

1. **`novel_resources` completeness, both directions**: `tests/test_architecture_decision.py`
   — a complete entry validates, an incomplete one (blank `justification`, empty
   `alternatives_considered`) fails with the same shape as an incomplete `alternatives` entry.
   `test_architecture_decision_with_no_novel_resources_still_validates` proves backward
   compatibility (every pre-existing record, with no `novel_resources` key at all, is
   unaffected).
2. **Fail-closed sweep on the authoring step's own output**: `tests/test_synthesizer.py` —
   `test_synthesize_refuses_novel_resource_with_no_matching_authored_content` (missing
   authored_content), `test_synthesize_refuses_authored_content_with_no_declared_blocks` (empty/
   garbage output), `test_synthesize_refuses_authored_content_with_hallucinated_type` (a real,
   nonexistent type, caught by G2's own `unknown_type` finding) — all three block *before* any
   run workspace is created, proven with real constructed fixtures.
3. **`gate_content()`/`gate_module()` parity**: `test_gate_content_is_byte_identical_to_gate_
   module_for_every_real_module`, parametrized over all 16 real modules, live-schema-verified
   (`opa`/real `terraform providers schema -json`) — byte-identical `{blocking, findings,
   warnings, schema_hash}` for every one, both call paths.
4. **G9 wiring proof**: `tests/test_plan_gate.py`'s new suite drives the real `stage_plan()` ->
   `stage_approve()` -> `stage_apply(mode="auto-approve")` flow with a G5-eligible plan
   (`aws_athena_workgroup`, isolating this check from G5's own) — the G9 verdict is computed at
   plan time, stored in the pending record, carried through approval, and read at apply time,
   visible in the audit trail (`reason="g9_not_clean"` on refusal).
5. **Config-dependent types' disposition**: closed, see section 4.2 Results above — the real
   count was 6, not 7 (a genuine miscount in the original scope prose, corrected); 3 got new G6
   rules (SEC-08/09/10), 3 stay eligible on a recorded, reasoned exception (arbitrary-code-
   execution risk gated by IAM role review, not by any plan-time-checkable attribute).
6. **G9 catches something real** (the hardest bar, added on review): real, executed evidence —
   not asserted — gathered against a real MiniStack container (Docker Desktop started locally,
   `ministackorg/ministack` run and health-checked). Fixture: `aws_s3_bucket.real` (created) +
   `aws_s3_object.orphan` whose `bucket` argument is a hardcoded, never-created bucket name (no
   Terraform reference between them at all — a missing-`depends_on`-shaped bug). Captured
   verbatim: `terraform validate` → `Success! The configuration is valid.` (G1 clean);
   `schema_lint.gate_content()` → `{"blocking": false, "findings": [], "warnings": []}` (G2
   clean, zero findings — G2 cannot see a hardcoded string is a bucket that doesn't exist, only
   that it's a validly-typed string); `ephemeral_apply.run_ephemeral_apply(emulator="ministack")`
   → `{"evaluation_failed": true, "reason": "apply_partial_failure", "detail":
   "succeeded=['aws_s3_bucket.real'] errored=['aws_s3_object.orphan']", "coverage": "full",
   "emulator": "ministack"}`. Committed as a permanent CI regression job
   (`g9-catches-a-real-failure`, `.github/workflows/ephemeral-apply.yml`), re-verified on every
   push, not a one-time local claim.

**A real bug found while wiring this in, not assumed away**: `ephemeral_apply.classify_
coverage()`'s "aws" bucket was defined as "not Databricks," not "actually `aws_`-prefixed" —
`terraform_data` (zero-cloud-footprint, `tests/test_gate_e2e.py`'s own real auto-approve
fixture, chosen specifically because it has no cloud footprint) fell into that bucket and was
misclassified `"full"` AWS coverage, which made G9 attempt a real ephemeral-apply cycle for a
plan with no AWS content at all — caught immediately (first wiring attempt broke the real e2e
test), fixed to check the real `aws_` prefix, regression-tested
(`test_classify_coverage_none_for_provider_neutral_test_utility_types`,
`test_classify_coverage_full_ignores_a_mixed_in_test_utility_type`).

**The real, present-tense consequence, stated plainly (not left to surprise anyone)**:
`MINUS_G9_EMULATOR` is unset in every real environment this repo runs in by default — no
`LOCALSTACK_AUTH_TOKEN` is provisioned, and both free emulators (MiniStack, Floci) already
failed IAM/KMS/S3 negative-fidelity this session. **G9-in-real-flow is fidelity-limited for
IAM/KMS/S3 until a paid emulator is provisioned; those plans stage rather than auto-ship** —
every `auto-approve` apply touching AWS content is refused today, forcing gatekeeper/staged
human review. Correct, disclosed, fail-closed behavior, not a bug — but a real operational cost
of this wiring existing, logged in `HANDOFF.md`, not buried in a diff.

**Phase 6 Step 1 is BUILT and PROVEN.** Every affected test file was enumerated and run locally
before push (the standing checklist item from Step 0, applied to itself): `test_rego_gate.py`
(59), `test_plan_gate.py` (37), `test_architecture_decision.py` (8), `test_schema_lint.py` (all,
including the new parity parametrization), `test_synthesizer.py` (21), `test_ephemeral_apply.py`
(all, including 3 new `classify_coverage` tests), `test_gate_e2e.py`, `test_credentials.py`,
`test_reporter.py`, `test_module_provenance.py`, `test_dashboard.py`, `test_intent_assertions.py`,
`test_schema_watch.py` — all green locally. Teardown (`docs/phase6_scope.md` Step 5) stays last,
gated on the regression-baseline proof bar, reachable only now that Step 1 is closed. G6's
shadow-only status and its own separate, still-open enforcement-flip decision are unaffected.

## Ordering invariant

Step 1 does not start implementation until this scope is reviewed and agreed. Step 0 (G5's
fail-closed fix) stays closed and unaffected. G6's shadow-only status and its own separate,
still-open enforcement-flip decision are unaffected by this scope. Teardown (`docs/
phase6_scope.md` section 5, "Step 5") stays last, gated on the regression-baseline proof bar,
reachable only after Step 1 is built AND proven — this scope is Step 1's design, not its
completion.
