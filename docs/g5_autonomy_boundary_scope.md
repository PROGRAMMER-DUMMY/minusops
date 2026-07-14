# G5 scope — invert the autonomy boundary from fail-open to fail-closed on unknown resource type

Scope document only. No implementation until this is reviewed and agreed. This is Phase 6's
Step 0 — a **self-contained fix to the existing, already-enforced G5 gate**, closed and proven on
its own, entirely independent of any generation-pipeline work, before Step 1 of `docs/
phase6_scope.md` starts. Posture: compliance-carrying product; this is the gate standing
directly between "generated" and "auto-shipped to real AWS," so it gets the same no-rushing
discipline every prior phase's tool/design decision got.

## 0. The finding this fixes, restated precisely (not re-argued — already ratified)

`destructive_change_gate.classify()` gates `autonomous_eligible` on **allowlist-of-danger**
membership: `rtype in STATEFUL_RESOURCE_TYPES` (11 named types) or `rtype in IAM_RESOURCE_TYPES`
(2 named types). Anything create-only and **not** in either set passes with zero findings. Both
sets are explicitly scoped, per the module's own docstring, to "what MinusOps' own 16 modules can
actually produce today" — fail-**open** on any resource type outside that known set. This is the
one gate that decides real-AWS auto-ship, and it is the opposite fail-closed posture every other
gate in this stack (G2, G6's `field_unresolved`, G9's allowlist) already uses successfully.

## 1. The design decision — two options, evaluated against real evidence, not asserted

### Option (a): invert to an allowlist of types *confirmed safe* to auto-ship

`classify()` stops asking "is this type in a small known-dangerous list" and starts asking "is
this type in a reviewed, deliberately-extended known-*safe* list" — the exact shape
`ephemeral_apply.py`'s `RESOURCE_TYPE_ALLOWLIST` already uses for G9, proven twice this session
(the base allowlist, and this session's own per-`(type, emulator)` extension). A type not on the
list — because it's dangerous, or because nobody has reviewed it yet — is staged. No guessing
required in either direction: membership is a deliberate, reviewed fact, not an inference.

### Option (b): keep a known-dangerous list, add a live-schema/heuristic detector for the rest

Supplement today's explicit lists with something that inspects a resource type's live provider
schema (or its name) for signals suggesting statefulness or sensitivity — e.g., "the type name
contains `bucket`/`table`/`cluster`/`queue`", or "the schema has an attribute suggesting
persistent storage" — and route anything the heuristic flags to staged, everything else to
auto-ship.

### Recommendation: (a). Evaluated against a real case this session's own cross-reference found, not a hypothetical

Cross-referencing today's real 41-type catalog (`ephemeral_apply.py`'s `RESOURCE_TYPE_ALLOWLIST`,
re-enumerated directly for this scope, not from memory) against `STATEFUL_RESOURCE_TYPES`/
`IAM_RESOURCE_TYPES` surfaces a genuinely useful data point: **`aws_s3_bucket_policy` is not in
either set today.** A plan creating *only* an `aws_s3_bucket_policy` (attached to an
already-existing bucket, no `aws_s3_bucket` in the same plan) is, right now, `autonomous_eligible`
if create-only — the exact resource type this session's own G6 extension (SEC-07) found can grant
public access via a bare `Principal: "*"` `Allow` statement, with no data-holding attribute of its
own to trip a naive "does it look stateful" classifier. **A heuristic keyed on "does this look
like it holds data" would very plausibly miss this exact type** — its name contains no
data-suggestive keyword, and its schema is a single opaque `policy` JSON string, not a
recognizable stateful shape. An explicit reviewed allowlist catches it trivially: nobody has
reviewed `aws_s3_bucket_policy` as safe (its own G6 rule exists *because* it isn't), so under (a)
it simply isn't on the list, no heuristic required, no guessing to get right. This is exactly the
kind of case a heuristic is structurally worse at than a deliberate review, and it's real, not
constructed for the argument — found by the same cross-reference exercise done to write section 3.

Also weighed and rejected: a heuristic is, definitionally, an educated guess about danger — the
same shape this session has rejected everywhere else it appeared (G2 checks the live schema
directly rather than guessing whether an attribute "looks" deprecated; G9 requires a live,
proven-against-real-behavior check rather than assuming vendor claims). A heuristic can also be
wrong in the *other* direction — over-flagging a genuinely safe type whose name happens to match
a keyword (`aws_route_table` contains "table" but holds no data) — trading the current false-safe
problem for a new false-dangerous one, adding friction without adding real assurance. Option (a)
has no failure mode shaped like either of these: a type is safe because a human said so, in
writing, reviewed — or it stages.

## 2. Mechanism — two layers, not a wholesale rewrite

Keep `STATEFUL_RESOURCE_TYPES`/`IAM_RESOURCE_TYPES` exactly as they are today — they remain
useful, accurate **annotation** (a staged finding should still say *why*: "this is stateful," "this
is IAM," not just "unreviewed"). Add a new, reviewed allowlist that actually gates the decision:

```python
AUTO_SHIP_ELIGIBLE_TYPES = frozenset({
    # every AWS resource type this repo's real catalog produces, explicitly reviewed and
    # confirmed to carry no meaningful data-loss, privilege-escalation, or access-control-
    # content risk on its own -- extended only by deliberate review, same discipline as
    # ephemeral_apply.py's RESOURCE_TYPE_ALLOWLIST. NOT a mechanical "everything not already
    # flagged" migration -- see section 3 for the two real exceptions this review found.
    ...
})
```

`classify()`'s gating condition changes from *"not stateful and not IAM"* to *"on the reviewed
safe list"* — `autonomous_eligible = not findings and not databricks_resources` stays the same
shape, but a type not on `AUTO_SHIP_ELIGIBLE_TYPES` now produces a finding
(`reason: "unreviewed_resource_type"`), distinct from `"stateful_resource_type"`/
`"iam_resource_type"` — a reviewer can tell "known-dangerous" from "simply never reviewed" apart
in the audit output, which matters once generation starts producing genuinely novel types the
gate has never seen. The `STATEFUL_RESOURCE_TYPES`/`IAM_RESOURCE_TYPES` checks stay first (more
specific, more informative reason), falling through to the new allowlist check only for a type
that's neither.

## 3. The proposed initial seed list — every one of today's 41 real types, reviewed, not migrated wholesale

Of the 41 AWS resource types in the real catalog (re-enumerated directly from `ephemeral_apply.
py`'s `RESOURCE_TYPE_ALLOWLIST` for this scope), 9 are already `STATEFUL_RESOURCE_TYPES`/
`IAM_RESOURCE_TYPES` members and stay staged unchanged (`aws_s3_bucket`, `aws_kms_key`,
`aws_redshiftserverless_namespace`, `aws_glue_catalog_table`, `aws_kinesis_stream`,
`aws_kinesis_firehose_delivery_stream`, `aws_mwaa_environment`, `aws_iam_role`,
`aws_iam_role_policy`). Of the remaining 32, this scope proposes 30 for the new
`AUTO_SHIP_ELIGIBLE_TYPES` seed list and flags 2 for explicit exclusion, not a rubber-stamp
migration of "everything not currently flagged":

**Proposed safe (30)**: `aws_athena_workgroup`, `aws_budgets_budget`, `aws_cloudwatch_event_rule`,
`aws_cloudwatch_event_target`, `aws_cloudwatch_metric_alarm`, `aws_eip`,
`aws_emrserverless_application`, `aws_glue_catalog_database`, `aws_glue_job`, `aws_glue_registry`,
`aws_glue_schema`, `aws_glue_trigger`, `aws_internet_gateway`, `aws_kinesisanalyticsv2_application`,
`aws_kms_alias`, `aws_nat_gateway`, `aws_redshiftserverless_workgroup`, `aws_route_table_association`,
`aws_s3_bucket_lifecycle_configuration`, `aws_s3_bucket_public_access_block`,
`aws_s3_bucket_server_side_encryption_configuration`, `aws_s3_bucket_versioning`, `aws_s3_object`,
`aws_sfn_state_machine`, `aws_sns_topic`, `aws_sns_topic_subscription`, `aws_subnet`, `aws_vpc`,
`aws_vpc_endpoint`, `aws_default_security_group` *(see caveat below — proposed safe, not obvious)*.

Reasoning for the class as a whole: either a pure configuration/scheduling resource with no data
or access-control content of its own (`aws_athena_workgroup`, `aws_glue_job/registry/schema/
trigger`, `aws_cloudwatch_*`, `aws_budgets_budget`, `aws_sfn_state_machine`), a networking
primitive whose own risk is realized only through a *separate*, separately-classified resource
(`aws_vpc`/`aws_subnet`/`aws_internet_gateway`/`aws_nat_gateway`/`aws_eip`/
`aws_route_table_association`/`aws_vpc_endpoint` — a route or an endpoint does not itself grant
or deny access without an accompanying security-group/policy resource), or a resource whose
*presence* is itself hardening rather than risk (`aws_s3_bucket_public_access_block`,
`aws_s3_bucket_server_side_encryption_configuration`, `aws_s3_bucket_versioning`).

**Proposed EXCLUDED, not safe (2)** — the real, useful output of doing this review type-by-type
instead of migrating the "not currently flagged" list wholesale:

- **`aws_s3_bucket_policy`** — section 1's own finding. A resource-based policy document whose
  *content* (not its schema shape) determines whether it grants public access — the same content
  risk this session's SEC-07 rule exists to catch, and G6 is shadow-only, so G5 is the only thing
  that could actually stage this today. Recommended: **stays off the safe list**, findings reason
  `unreviewed_resource_type` (or a new, more specific `policy_content_risk` reason — a naming
  decision for implementation, not this scope).
- **`aws_default_security_group`** *(judgment call, flagged not resolved)* — its own content
  (ingress/egress CIDR ranges) carries the same class of risk as an IAM/KMS/S3 policy's content
  (a `0.0.0.0/0` rule is the network-layer equivalent of `Principal: "*"`), even though managing
  the *default* security group is also a common, real hardening pattern (locking it down to zero
  rules). This scope does not resolve which behavior dominates in practice for this repo's
  catalog — flagged explicitly for review during implementation rather than silently defaulted
  either way.

This 30/2 split (not 32/0) is the actual deliverable of doing the review for real rather than
asserting the migration is safe by construction.

## 4. Regression proof, both directions — the exact test design, not just the requirement

1. **Nothing that should auto-ship today regresses.** Extend the existing real 16-module baseline
   (`tests/test_destructive_change_gate.py::test_every_current_module_plans_as_create_only`,
   already proven 16/16 create-only against real Terraform) with a new assertion: for every real
   plan in that baseline, `classify()`'s result after this change must be
   `autonomous_eligible == True` wherever it was `True` before — a direct before/after comparison
   against the real, current plans this repo's real modules produce, not a synthetic fixture.
2. **The fix actually closes the gap — proven by a test that fails today and passes after.** A
   new, standalone test constructs a plan with a single resource of a type confirmed absent from
   both `STATEFUL_RESOURCE_TYPES`/`IAM_RESOURCE_TYPES` **and** (once written) absent from
   `AUTO_SHIP_ELIGIBLE_TYPES` — e.g. `aws_dynamodb_table` or `aws_rds_cluster`, real, genuinely
   stateful AWS resource types this repo's catalog has never declared — `actions == ["create"]`.
   Run against **today's unmodified `classify()`** first and confirm it is currently
   `autonomous_eligible == True` (the failing-before state, proving the gap is real, not assumed)
   — then confirm it is `False` with `reason == "unreviewed_resource_type"` after the fix. Both
   assertions live in the same permanent regression test, not a throwaway probe: the "before"
   assertion stays in the suite (skipped or inverted once the type is never going to be added,
   or kept as a documented historical proof — implementation detail) so the fix cannot silently
   regress back to fail-open later without a test noticing.
3. **A genuinely novel type test, one layer further** — a plan containing a resource type that
   does not exist in *any* of today's three sets at all (not stateful, not IAM, not yet reviewed
   safe) must stage, confirming the *default* for an unrecognized type is fail-closed, not merely
   that specific known-dangerous examples are covered.

## 5. Fail-closed table addition (extends the existing sweep, same shape)

| Case | Verdict |
|---|---|
| Resource type is in `STATEFUL_RESOURCE_TYPES` or `IAM_RESOURCE_TYPES` | STAGED, `reason` names which (unchanged from today). |
| Resource type is not in either of the above, and not in `AUTO_SHIP_ELIGIBLE_TYPES` | **STAGED** (new), `reason = "unreviewed_resource_type"` — the fix itself. |
| Resource type is in `AUTO_SHIP_ELIGIBLE_TYPES` and action is exactly `["create"]` | Eligible (unchanged mechanism, now gated by an inverted condition). |
| Resource type field missing/non-string/malformed | Already fail-closed (Probe A, 2026-07-10) — unaffected, unchanged. |

## 6. Proof bar

1. Section 4's three-part regression proof, all real, none synthetic-only for the baseline half.
2. Section 3's 30/2 seed-list review stays reviewable in the diff — every entry is a deliberate
   line, not a generated migration, so a reviewer can check the reasoning against the list
   directly, the same standard `RESOURCE_TYPE_ALLOWLIST` already set.
3. `aws_default_security_group`'s disposition is explicitly decided (not left ambiguous) before
   this closes — resolved one way or the other, with the reasoning recorded, not silently
   defaulted by whichever way the implementation happened to fall.
4. Real `terraform plan` confirmation (not assumed from the schema alone) that the two
   synthetic novel types used in section 4's tests genuinely aren't declared anywhere in this
   repo's real 16-module catalog, so the "before" state genuinely represents today's fail-open
   gap and not a fixture that happens to collide with something already covered.

## 7. Results (2026-07-14) — built, proven, both directions, three real bugs caught by the proof itself

### 7.1 `aws_default_security_group` — decided, not defaulted

**Excluded.** Confirmed live against this repo's own `modules/networking-vpc/main.tf` before
deciding: even this repo's correctly-configured usage sets
`egress { cidr_blocks = ["0.0.0.0/0"] }` — an unrestricted CIDR block is present in the type's
real, intended configuration here, not a hypothetical misconfiguration. This *strengthens* the
exclusion rather than complicating it: the classifier reads only resource type and action, never
rule content, so it cannot distinguish this repo's own safe pattern (self-referencing ingress,
open egress) from a hypothetical future occurrence that opens ingress to `0.0.0.0/0` — the exact
content-blindness section 1 named as heuristics' structural weakness applies to this classifier
itself for this one type. Staged, reason `reviewed_unsafe_resource_type` (see 7.2).

### 7.2 A real code improvement the build itself surfaced: `reviewed_unsafe_resource_type`

Implementing section 3's two exclusions (`aws_s3_bucket_policy`, `aws_default_security_group`)
against the real 16-module baseline immediately produced a real, confirmed finding for
`databricks-workspace`'s own `aws_s3_bucket_policy.root_storage_bucket` — correct behavior, but
tagged identically (`unreviewed_resource_type`) to a genuinely-never-reviewed type, collapsing
"reviewed and rejected" into "nobody looked at this," the exact distinction section 2 called for.
Fixed with a third set, `REVIEWED_UNSAFE_TYPES`, and its own reason,
`reviewed_unsafe_resource_type` — an audit-chain reader can now tell "known-dangerous"
(`stateful_resource_type`/`iam_resource_type`), "reviewed, rejected" (`reviewed_unsafe_
resource_type`), and "never reviewed" (`unreviewed_resource_type`) apart, three distinct facts
instead of two.

### 7.3 Two more real bugs, both caught by the regression proof itself, neither hypothetical

- **`random_id` regression**: this repo's own pre-existing action-shape tests
  (`test_real_create_only_plan_is_autonomous_eligible` et al.) use `hashicorp/random`'s
  `random_id` as a zero-cloud-footprint stand-in for create/delete/replace testing. Never
  reviewed (it isn't a cloud resource), it fell through to `unreviewed_resource_type` and broke
  an existing, previously-green test on the very first run. Reviewed and added to
  `AUTO_SHIP_ELIGIBLE_TYPES` with explicit reasoning (genuinely zero cloud footprint, a test
  utility, not a real-world safety judgment).
- **`aws_route_table` omission**: section 3's own review classified `aws_route_table` as safe
  ("a networking primitive whose own risk is realized only through a separate, separately-
  classified resource"), but the actual `AUTO_SHIP_ELIGIBLE_TYPES` frozenset only got
  `aws_route_table_association` — a real transcription gap between the review and the code,
  caught immediately by `networking-vpc`'s own real baseline plan failing the regression test.
  Fixed; re-verified against a full re-cross-reference of all 41 real AWS types against the four
  classification sets (`STATEFUL_RESOURCE_TYPES ∪ IAM_RESOURCE_TYPES ∪ REVIEWED_UNSAFE_TYPES ∪
  AUTO_SHIP_ELIGIBLE_TYPES`) confirming zero gaps remain.
- **Databricks double-flagging (a scope-boundary bug, not a safety bug)**: `databricks-
  workspace`'s own `databricks_mws_credentials` — a real Databricks type absent from
  `STATEFUL_RESOURCE_TYPES` — fell through to `unreviewed_resource_type` on the first full
  16-module run. This scope was explicitly AWS-only (section 1 evaluates AWS types only, matching
  G9's own AWS-only boundary); Databricks types are already, unconditionally, never
  autonomous-eligible via the pre-existing `databricks_resources`/`reduced_assurance` mechanism,
  regardless of this fix. Fixed by skipping the new checks entirely for any `databricks_*`
  prefixed type — not silently declaring Databricks resource-type review done by an AWS-only fix.

### 7.4 Both-direction proof, complete

- **The fix closes the gap**: `aws_dynamodb_table` and `aws_secretsmanager_secret` (confirmed
  absent from the real catalog by direct grep, neither ever declared anywhere in
  `modules/*/main.tf`) — both `autonomous_eligible=True` on the unmodified classifier (the
  `aws_dynamodb_table` case captured as real, executed evidence before any code changed), both
  `False` with reason `unreviewed_resource_type` after.
- **Nothing that should auto-ship today regresses**: the real 16-module baseline
  (`test_every_current_module_plans_as_create_only`), extended with an assertion that no real
  module's real plan produces an `unreviewed_resource_type` finding, run against real Terraform
  for all 16 modules (in batches, due to this session's own environment constraints, not a
  scoping shortcut) — clean, after the three fixes above. `test_destructive_change_gate.py`:
  42 tests total (26 fast + 16 real-module), all passing. `test_plan_gate.py` (33 tests,
  downstream consumer of `classify()`) unaffected, all passing.

Net: the fix works exactly as designed, and the process of proving it — not the design itself —
is what surfaced three real, concrete bugs (one test-fixture regression, one transcription gap,
one scope-boundary leak) before any of them could ship. Exactly the discipline this whole
session has run on since Phase 1.

## Ordering invariant

This closes standalone, proven against the current 16-module catalog, **before** `docs/
phase6_scope.md` Step 1 (the authoring pipeline) starts — not in parallel, not bundled into it.
Phase 6's own key-question finding named this as the load-bearing prerequisite; this scope is
that prerequisite, scoped on its own terms. No implementation starts until this is reviewed and
agreed. G6's shadow-only status and enforcement-flip decision are unaffected and unchanged by
this work — this fixes G5 specifically, not G6's separate open questions.
