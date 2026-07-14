# G6 scope addition — IAM/KMS/S3 security-CONTENT rules (option (c))

Scope document only. No implementation until this is reviewed and agreed, same discipline as
`docs/g6_scope.md` and every prior phase this session. Posture: compliance-carrying product.

## 0. Why this exists, and why it is not a G9 concern

`docs/phase5_scope.md` section 10 already recorded the routing correction this scope executes:
G9's emulator gauntlet (MiniStack/Floci) found that neither free emulator rejects a malformed
IAM trust-policy principal, a KMS key policy with no root/admin grant, or an S3 bucket policy
naming the wrong bucket. That is a **validity** question (would real AWS accept this at all) —
answerable only by an emulator or real AWS. It says nothing about **safety** (is this config
dangerous even though real AWS would accept it). A perfectly faithful emulator would apply a
wide-open `Principal: "*"` trust policy without complaint, because real AWS does too. Security
*content* review belongs to G6, which already reads the fully **resolved** plan JSON at plan
time — no emulator, no apply, free and deterministic. This scope is that content review, for
exactly the three resource families the G9 finding named: IAM trust relationships, KMS key
policies, S3 bucket policies.

**This does not close, or claim to close, G9's own disclosed gap.** `RESOURCE_TYPE_ALLOWLIST`'s
`negative_fidelity_unverified` block for `aws_iam_role`/`aws_kms_key`/`aws_s3_bucket_policy`
stays exactly as it is. G6 enforcing policy *security* and G9 lacking apply-time *fidelity* for
the same three types are two different, independently-tracked facts — see section 5.

## 1. What already exists, verified against the real file, not assumed

`policy/g6/rules.rego` already has partial coverage of this exact territory, added earlier this
session for a narrower purpose (Databricks cross-account trust). Read directly before scoping
anything new, so this proposal extends real rules rather than duplicating them:

- **SEC-02** (`sec02_findings`) already flags `Resource == "*"` (or `"*" in Resource`) on
  `aws_iam_policy`/`aws_iam_role_policy` (a raw JSON string via `json.unmarshal`) and on
  `data.aws_iam_policy_document` statements (structured, no parsing needed). It does **not**
  check `Action == "*"`.
- **SEC-05** (`sec05_findings`) already flags a missing `sts:ExternalId` condition and a
  wildcard `identifiers = ["*"]` principal — but **only** for cross-account `AssumeRole`
  statements built via `data.aws_iam_policy_document`. It does **not** evaluate
  `aws_iam_role.assume_role_policy` when set directly as a raw JSON string (e.g.
  `jsonencode({...})`), which is how most of this repo's own modules actually write trust
  policies (verified below — grep, not assumed).
- **Nothing today evaluates `aws_kms_key.policy` or `aws_s3_bucket_policy.policy` content at
  all.** SEC-01/COST-01 check that these *sibling resources exist*, never what their policy
  documents actually say.

This addition is therefore two rule **extensions** (SEC-02, SEC-05) and two genuinely **new**
rules (naming them SEC-06, SEC-07 to avoid overloading an ID whose finding shape would
otherwise mix unrelated resource types under one code).

## 2. Verified live against a real plan, not assumed — the shape every rule below depends on

Confirmed via a real `terraform plan`/`show -json` (dummy credentials, no real AWS account),
not read off provider docs:

| Field | Declared type (real schema) | Real plan-JSON shape |
|---|---|---|
| `aws_iam_role.assume_role_policy` | `string` | Resolved as a plain JSON string in `after` when nothing it references is itself unknown — e.g. `'{"Statement":[{"Action":"sts:AssumeRole","Effect":"Allow","Principal":{"AWS":"*"}}],"Version":"2012-10-17"}'`. Same `json.unmarshal`-able shape SEC-02 already uses for `aws_iam_policy.policy`. |
| `aws_kms_key.policy` | `string`, **`computed = true`** | When the module doesn't set `policy` explicitly (real, common pattern — `storage-medallion-s3` does exactly this), plan JSON shows `after.policy: null` and **`after_unknown.policy: true`** — confirmed live. AWS assigns a default key policy at apply time. This is not an edge case; it is the default posture for a huge share of real `aws_kms_key` resources. |
| `aws_s3_bucket_policy.policy` | `string` | **A real, load-bearing surprise, found by testing rather than assumed**: even a policy written as a literal `jsonencode({...})` in HCL comes back with `after_unknown.policy: true` whenever the policy string interpolates an attribute of a bucket created in the *same* plan (e.g. `Resource = "${aws_s3_bucket.b.arn}/*"`) — the bucket's own ARN is itself unknown until apply, which makes the whole assembled JSON string unknown too, even though every literal piece of it is fully known. **This is the majority real-world pattern** (a bucket and its policy created together in one plan) — meaning this rule will emit `field_unresolved`, not a real pass/fail, on most first-time applies. It only evaluates real content against an **already-existing** bucket (a subsequent plan against real state, or a bucket referenced by ARN/name rather than by resource attribute). This must be proven both ways (fresh-create → `field_unresolved`; pre-existing bucket → real content check), not just documented as a theoretical caveat — see the proof bar. |

## 3. Rule-by-rule design

### SEC-02 (extended) — add `Action == "*"` alongside the existing `Resource == "*"` check

Same two code paths already there (`data.aws_iam_policy_document` statements, and
`aws_iam_policy`/`aws_iam_role_policy`'s raw `.policy` string via `json.unmarshal`), same
`--strict-builtin-errors` reliance already documented in the file for malformed JSON. New
finding condition: `stmt.Action == "*"` or `"*" in stmt.Action`, mirroring
`resource_has_wildcard`'s existing shape (`resource_has_wildcard` → generalized to
`field_has_wildcard(stmt, field)` reused for both `Resource` and `Action`).

**Disclosed design judgment, to be settled by shadow-mode evidence, not asserted here**: unlike
`Resource == "*"`, `Action == "*"` on an **identity-based** policy (attached to a specific
role/user — exactly what `aws_iam_policy`/`aws_iam_role_policy` are) is almost always over-broad,
since the principal is already fixed by attachment. This rule fires unconditionally for these
two types, not only when paired with a wildcard principal (identity policies have no
`Principal` field at all — the attached identity *is* the principal). If the 16-module parity
pass surfaces a real, legitimate `Action = "*"` use this session didn't anticipate, that is
exactly what shadow mode exists to catch before it ever blocks anything.

### SEC-05 (extended) — evaluate `aws_iam_role.assume_role_policy` directly, not only
`data.aws_iam_policy_document`

New code path, same two findings SEC-05 already produces (missing `sts:ExternalId`, wildcard
principal), applied to `assume_role_policy` parsed via `json.unmarshal` (same pattern as SEC-02's
managed-resource path) instead of Rego's structured `.statement` field. Scoped identically:
only statements whose `Actions` include `sts:AssumeRole` **and** whose `Principal.AWS` is not
the resource's own account (a **cross-account** trust relationship) are subject to the
missing-`sts:ExternalId` finding — a same-account service principal (e.g.
`glue.amazonaws.com`) legitimately has no external ID to set, and must not be flagged. The
wildcard-principal check (`AWS: "*"` or `AWS: ["*"]`) applies regardless of account, since a
wildcard principal is never a legitimate cross-account **or** same-account trust relationship.

**Real, disclosed scoping decision**: same-account-vs-cross-account detection needs the
account ID `aws_iam_role`'s trust policy is evaluated *against* — this repo's own SEC-05
precedent for `data.databricks_aws_assume_role_policy` sidesteps this by relying on
Databricks' own canonical account ID being externally documented and stable. A hand-rolled
`assume_role_policy` has no such fixed reference. Verify live, before implementing, whether
`data.aws_caller_identity.current.account_id` (already used elsewhere in this repo's modules)
resolves in the same plan and can be compared against `Principal.AWS`, or whether this
comparison is only reliably possible via string-literal account IDs already known in the
statement — do not assume the mechanism works before checking a real plan.

### SEC-06 (new) — KMS key policy wide-open

`aws_kms_key.policy`, `json.unmarshal`, same `--strict-builtin-errors` reliance. Finds any
statement with `Effect: "Allow"`, a wildcard principal (`Principal: "*"` or
`Principal: {AWS: "*"}` / `{AWS: ["*"]}`), and a wildcard or `kms:*`-shaped action — the
combination that grants any AWS principal full key control, not merely a broad-but-scoped
grant. Per section 2's finding, `after_unknown.policy == true` (the unset/computed-default
case) routes to `field_unresolved`, not silently read as "no policy, nothing to check" — an
unset KMS policy is not the same as a verified-safe one; AWS's own generated default happens
to be safe, but this rule cannot assume that without seeing it, so it blocks honestly.

### SEC-07 (new) — S3 bucket policy allows public access

`aws_s3_bucket_policy.policy`, same `json.unmarshal` pattern. Finds any `Effect: "Allow"`
statement with a wildcard principal and no matching explicit `Deny` covering the same
action/resource shape — modeled on AWS's own public-access-block guidance (a bucket policy
should never itself grant public access; the account/bucket-level Block Public Access
settings are the intended control, and SEC-01 already checks that sibling exists). Given
section 2's finding that `after_unknown.policy` is `true` for the large majority of real
bucket+policy-created-together plans, this rule will predominantly emit `field_unresolved` on
a fresh apply — genuine, disclosed, not a bug to paper over. It produces a real content
verdict once the bucket already exists in state (a re-plan, or a policy referencing an
existing bucket by literal ARN/name rather than by resource attribute).

### Shared helper, used by SEC-05 (extended)/SEC-06/SEC-07

A single `is_wildcard_principal(principal)` helper, handling both real AWS policy shapes:
bare string (`"Principal": "*"`) and object form (`"Principal": {"AWS": "*"}` /
`{"AWS": ["*"]}`) — written once, reused three times, rather than three near-duplicate
implementations drifting apart. Same discipline as the existing `has_wildcard_principal`/
`has_aws_principal` helpers SEC-05 already has for the structured-statement case.

## 4. Shadow-mode plan — same discipline as G6's original migration, no exception

`rego_gate.evaluate()` is **already** the sole call site (`plan_gate.py:310`), already
non-enforcing (`BLOCKING_PREFIXES` / the regex path in `optimize_analyzer.py` is still the only
thing that blocks in production mode, for every existing G6 rule, not only these new ones).
Adding SEC-06/SEC-07 and extending SEC-02/SEC-05 in `rules.rego`'s `findings` aggregate means
they start flowing through that exact same shadow-only path with **no new wiring code** —
which also means there is no separate "flip" switch for just this extension; the eventual
enforcement flip is a single, already-deferred decision covering all of G6's Rego findings at
once (`docs/g6_scope.md` section 2, still open — item 3 of that scope's proof bar). This
addition does not bring that flip closer on its own; it adds more shadow evidence to the same
pile.

Required before this addition's own rules are considered proven (independent of the overall
G6 flip decision):
1. Both directions, per rule, against **real, constructed fixtures** — a genuine violation
   fires the finding; a genuinely correct, scoped policy produces zero findings for that rule.
2. **Zero false positives across all 16 real modules**, real `terraform plan` + `show -json`
   per module (dummy credentials), for every rule whose target type a module actually declares.
   Real counts, not assumed (grepped directly, not guessed): 9 modules declare `aws_iam_role`/
   `aws_iam_role_policy` (`compaction-glue`, `compute-emr-serverless`, `compute-glue-etl`,
   `databricks-workspace`, `dq-great-expectations`, `ingest-firehose`, `orchestrator-mwaa`,
   `orchestrator-stepfunctions`, `speed-layer-kinesis`); 1 declares `aws_kms_key`
   (`storage-medallion-s3`); 1 declares `aws_s3_bucket_policy` (`databricks-workspace`); **none**
   declare a standalone `aws_iam_policy`, so that half of SEC-02's extension is real code with
   currently-vacuous coverage in this repo's own catalog, same disclosed shape as the five
   modules Phase 3's parity pass already found vacuous for other rules — not silently hidden.
3. Every finding (or `field_unresolved`) produced against the real 16-module baseline is read
   and explained individually before being called clean — a real finding on a real module means
   either the module has a genuine, fixable issue, or the rule has a bug. Neither gets waved
   through as "probably fine."

## 5. Fail-closed table addition (extends `docs/g6_scope.md` section 3, same shape)

| Case | Verdict |
|---|---|
| `aws_iam_role.assume_role_policy` is unresolved at plan time (`after_unknown.assume_role_policy == true`) | **BLOCK** (`field_unresolved`) via `finding_unresolved`, same as every other presence-based G6 rule — an interpolated trust policy referencing a not-yet-created resource is a real, if less common, case for this field. |
| `aws_kms_key.policy` is unresolved (`after_unknown.policy == true`) — **the common case for an unset/default policy**, confirmed live | **BLOCK** (`field_unresolved`) — an unset KMS policy is not verified-safe just because AWS's default happens to be reasonable; this rule cannot see the default's content at plan time and must not assume it. |
| `aws_s3_bucket_policy.policy` is unresolved (`after_unknown.policy == true`) — **the majority case when the bucket is created in the same plan**, confirmed live | **BLOCK** (`field_unresolved`) — disclosed as the dominant real-world outcome for this rule on a fresh apply, not a rare edge case swept into the same generic row as everything else. |
| Malformed/non-JSON `policy` or `assume_role_policy` string | Already covered by `--strict-builtin-errors` (documented in `rules.rego` for SEC-02); a `json.unmarshal` failure surfaces as a real `opa eval` error → `opa_eval_failed`, never a silently-dropped match. |
| A tracked type has zero instances in the plan | Not a failure — matches every existing G6 rule's own "nothing there to check" distinction. |

## 6. Disclosure required in HANDOFF before this is considered done (condition, not optional)

Stated explicitly, not implied: **G6 (once its Rego findings are eventually flipped to
enforcing — still a separate, undecided step per `docs/g6_scope.md`) enforces IAM/KMS/S3 policy
*security content* statically, over resolved plan JSON.** It does **not** verify apply-time IAM
*interaction* — whether a principal ARN is well-formed, whether a role can actually be assumed,
whether resource creation ordering matters for a policy's own references. That remains G9's
disclosed, open gap (`negative_fidelity_unverified` for these same three types, pending a
provisioned LocalStack account). A reader of HANDOFF must come away with both facts, never one
implying the other is closed.

## 7. Results (2026-07-14) — real, built to this scope, shadow-proven, NOT enforcing

Implemented exactly as scoped: `policy/g6/rules.rego` gained the SEC-02/SEC-05 extensions and
the new SEC-06/SEC-07 rules, `tests/test_rego_gate.py` gained 15 new tests (50 total, all
passing against the real `opa` binary), and a real bug found while proving this — not while
designing it — was fixed in `core/governance/plan_gate.py`. **This is proven-in-shadow, not
enforcing** — see section 4 and the note at the end of this section; the two are being kept
deliberately separate, per explicit review instruction.

### 7.1 The two verify-first items, resolved with real evidence, not assumption

1. **Same-account-vs-cross-account detection**: confirmed live, exactly as flagged as a risk —
   `data.aws_caller_identity.current` is a genuine STS API call that fails outright
   (`InvalidClientTokenId`) under the dummy credentials this repo's own real-plan testing uses,
   even with `skip_credentials_validation`/`skip_requesting_account_id` set (those affect the
   *provider's* own init, not an explicit data source read). Confirmed via a real `terraform
   plan`: a role depending on that data source is dropped from `resource_changes` entirely when
   the read fails, while an independent role with no such dependency plans fine. **Design
   decision made from this evidence**: SEC-05's extension does not attempt account comparison at
   all — it falls back to literal-ARN matching (any 12-digit-account-shaped ARN is treated as
   external), the documented fallback section 3 named as the alternative if this happened.
2. **SEC-02's unconditional `Action == "*"` fire on identity policies**: the 16-module parity
   pass (7.2 below) is the actual test of this, per the review instruction ("let shadow evidence
   on the 16 modules confirm no legitimate use before it's ever enforced") — result: zero
   real modules tripped it. Confirmed, not merely asserted.

### 7.2 Zero-FP proof across the real 16-module catalog, per-type where declared

Real `terraform plan` + `show -json`, dummy AWS/Databricks credentials (no `mock_provider`,
no `terraform test` — the same real-provider method the original Phase 3 parity pass used, since
`mock_provider` supplies synthetic computed values that would misrepresent the exact
`after_unknown` behavior this extension's design depends on). Two real bugs in the test harness
itself found and fixed before trusting any result, same discipline as every other proof this
session: `data.aws_caller_identity` blocks needed removing outright, not just their attribute
references patched (Terraform reads every declared data source at plan time regardless of
whether its output is used); the Databricks provider was being required unconditionally for
every module, breaking `storage-medallion-s3` (which needs no Databricks provider at all) on an
unrelated registry timeout.

| Module | Types declared | Result |
|---|---|---|
| `compaction-glue`, `compute-emr-serverless`, `compute-glue-etl`, `dq-great-expectations`, `ingest-firehose`, `orchestrator-mwaa`, `speed-layer-kinesis` | `aws_iam_role`/`aws_iam_role_policy` | Zero findings on any extended/new rule — clean. |
| `databricks-workspace` | `aws_iam_role`/`aws_iam_role_policy`, `aws_s3_bucket_policy` | One SEC-02 finding — confirmed the **pre-existing** `Resource == "*"` finding already known and explained since Phase 3 (verified by its description text, not the new `Action == "*"` wording), not a new false positive. One SEC-07 `field_unresolved` on `aws_s3_bucket_policy.root_storage_bucket` — the predicted dominant outcome for a bucket+policy created together, confirmed real, not a false positive (a `field_unresolved` is an honest "can't verify," not a violation claim). |
| `storage-medallion-s3` | `aws_kms_key` | One SEC-06 `field_unresolved` on `aws_kms_key.lake` — the section 2 finding confirmed for real: this module doesn't set `policy` explicitly, so it's genuinely unknown until apply, exactly as predicted before any code was written. |
| `orchestrator-stepfunctions` | `aws_iam_role`/`aws_iam_role_policy` | **Unverified, same pre-existing disclosed gap as Phase 3** — `aws_sfn_state_machine` triggers a real `ValidateStateMachineDefinition` AWS API call at plan time that dummy credentials can't satisfy, so this module cannot be planned standalone at all. Not a new gap this extension introduced; carried forward unchanged. |
| (remaining 5 modules) | none of `aws_iam_role`/`aws_kms_key`/`aws_s3_bucket_policy` | Vacuous — nothing for these rules to check, same disclosed shape as Phase 3's own five vacuous-coverage modules. |

**Net: zero new false positives across every module that could be planned.** Every finding that
fired is either the already-known, pre-existing SEC-02 case, or a `field_unresolved` exactly
matching this scope's own predicted dominant real-world outcome — not a single incorrectly-flagged
clean configuration.

### 7.3 SEC-07 proven both ways, per explicit review instruction — real integration test, not just unit fixtures

`tests/test_rego_gate.py::test_real_plan_s3_bucket_policy_both_ways_fresh_create_vs_preexisting_bucket`
plans both shapes in the same real Terraform run: a bucket and its policy created together
(policy interpolates the bucket's own ARN) alongside a policy written against a bucket referenced
by a literal, fixed name (the "already exists / referenced by convention" pattern). Confirmed
live: the fresh-create policy resolves `after_unknown.policy = true` (routes to
`field_unresolved`); the literal-name policy resolves fully and produces a real `standard`
finding (a deliberate public-Allow statement, proving the "real verdict" path genuinely fires,
not just "didn't block"). Both assertions pass against real Terraform, not a hand-built shape.

### 7.4 A real bug found running this, not designing it — `plan_gate.py`'s `G6_RULE_IDS`

`_g6_shadow_eval()`'s divergence computation only ever iterates a fixed tuple, `G6_RULE_IDS`.
Adding SEC-06/SEC-07 to `rules.rego` without adding them to that tuple would have meant: a real,
confirmed SEC-06/SEC-07 **violation** (not the uncertain `field_unresolved` case) would be
computed, silently absent from the divergence report, and absent from the audit chain
entirely — while the *uncertain* case (`field_unresolved`, logged via a separate, unfiltered
list) would still have surfaced. That is exactly backwards from what shadow mode exists to
guarantee: the confirmed-dangerous case invisible, the merely-uncertain one visible. Found by
tracing the real code path while building the parity harness, fixed on the spot (`G6_RULE_IDS`
now includes `"SEC-06"`, `"SEC-07"`), and locked in with a new regression test
(`tests/test_plan_gate.py::test_g6_shadow_surfaces_a_real_sec06_finding_not_just_field_unresolved`)
that constructs a real wide-open KMS policy and asserts it appears in the divergence report.

### 7.5 What this is, and what it deliberately is not

**Proven in shadow. Not enforcing.** `rego_gate.evaluate()` was already the sole, already-
shadow-only call site before this addition (`plan_gate.py:310`) — these new/extended rules flow
into the exact same non-blocking path automatically, no new wiring, no new flag. The all-of-G6
enforcement flip (`docs/g6_scope.md` section 2, item 3 of that scope's own proof bar) remains a
single, separate, still-open decision covering every G6 rule at once — this addition adds more
shadow evidence to that same pile; it does not bring the flip closer or decide it, and closing
this task must never be read as "G6 now enforces IAM/KMS/S3 content."

### 7.6 Disclosure (section 6's own hard done-condition) — recorded in `HANDOFF.md`

Both facts stated together, neither implying the other: G6 (once eventually flipped to
enforcing — still undecided) would enforce IAM/KMS/S3 policy *security content*, statically,
over resolved plan JSON. It does **not** verify apply-time IAM *interaction* — ARN validity,
assumability, resource-creation-ordering effects on a policy's own references — which remains
G9's own disclosed, open gap (`negative_fidelity_unverified` for these same three types, pending
a provisioned LocalStack account). See `HANDOFF.md`'s corresponding entry.

## Ordering invariant

Queued after Phase 5 (G9)'s isolation-boundary close, which is done. G6's **existing** rules
(SEC-01 through SEC-05 as they stood before this addition, COST-01/02/03) stay shadow-only,
unflipped, exactly as before — this addition does not change that. Phase 6 (catalog teardown)
stays last, untouched, not started. No implementation starts until this scope is agreed.
