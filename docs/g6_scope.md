# G6 scope — SEC-*/COST-* rules over plan JSON via OPA/Rego

Scope document only. No implementation until this is reviewed and agreed, per the same
discipline G2's scope went through. Posture: compliance-carrying product — the proof bar
below reflects that.

## Where this replaces, and what it doesn't

Today, `core/reporting/optimize_analyzer.py`'s `scan_hcl_files()` is invoked as a subprocess
from `plan_gate.py`'s `verify()` (`plan_gate.py:492-497`), given `--source-dir <dir>` — it
regex-scans the raw `.tf` **source text**, never the plan. G6 moves the SEC-*/COST-* subset
of these rules to Rego rules evaluated against `terraform show -json` **plan output**
instead. The DATA-*/OBS-* rules already in `optimize_analyzer.py` (DATA-01/02/03, OBS-01) are
**not** in scope for this migration — they're performance/observability heuristics, not
security/cost gates, and the user's ask was specifically SEC-*/COST-*. They stay exactly
where they are, regex-over-HCL, untouched.

## 1. Rule-by-rule migration map

Every current SEC-*/COST-* rule, mapped from its regex-over-HCL form to what it becomes over
plan JSON:

| Rule | Current check (regex over HCL text) | Plan-JSON form |
|---|---|---|
| **SEC-01** S3 Public Access Block Missing | An `aws_s3_bucket` resource with no sibling `aws_s3_bucket_public_access_block` referencing it | Same shape, over `resource_changes`: for every planned `aws_s3_bucket`, confirm a planned `aws_s3_bucket_public_access_block` exists whose `bucket` input resolves to that bucket's address/id |
| **COST-01** S3 Missing Lifecycle Policy | Same pattern, `aws_s3_bucket_lifecycle_configuration` | Same shape as SEC-01, different sibling type |
| **SEC-03** Unencrypted Redshift Cluster | `encrypted = true` not found in the resource's HCL body | Plan JSON's resolved `values.encrypted` (or `after.encrypted`) on the `aws_redshift_cluster` change — a real boolean value, not a text-presence check; strictly more precise (catches `encrypted = var.x` where `var.x` resolves false, which the current regex cannot) |
| **SEC-04** Unencrypted MSK Cluster | `"encryption_info"` substring absent from body | `after.encryption_info` presence/shape on the `aws_msk_cluster` change |
| **COST-02** Databricks Cluster Missing Auto-Termination | `autotermination_minutes = <int>` regex | `after.autotermination_minutes` on the `databricks_cluster` change |
| **COST-03** EMR Lacks Spot Pricing | `bid_price =` regex | `after.bid_price` (or the equivalent nested instance-fleet field — verify exact plan-JSON shape live before implementing, don't assume it mirrors the HCL argument name 1:1) |
| **SEC-05a** Databricks trust policy missing external_id | `external_id =` argument-presence regex on the **data source's HCL block** | **Different data source than the others**: `external_id` is a data-source **input**, resolved into `resource_changes` for `data.databricks_aws_assume_role_policy` the same as any other data source's config. Straightforward input-presence check on plan JSON, same precision as today. |
| **SEC-05b** Hand-rolled cross-account trust policy missing `sts:ExternalId` | Regex over the **generated JSON string** inside a `data.aws_iam_policy_document` HCL body | **Real behavior change worth flagging explicitly, not smoothing over**: `aws_iam_policy_document`'s `.json` output is a data source computed at plan time — real plan JSON captures the **provider-resolved** JSON string, not just the HCL author's literal text. This means Rego evaluation can see the actual assembled policy (including any variable interpolation resolved), where the current regex only sees literal source text. This is very likely a **strict improvement** (fewer false negatives — a `sts:ExternalId` built from a variable would be invisible to the current regex but visible in resolved plan JSON) but it changes what the rule can catch. Must be verified empirically during implementation, and any NEW finding this surfaces on the real 16 modules (that the old rule didn't) must be understood and explained before parity is declared, not waved through as a coincidence. |
| **SEC-05c** Wildcard principal (`identifiers = ["*"]`) | Regex on HCL body | Same resolved-JSON reasoning as SEC-05b |
| **SEC-02** Wildcard IAM Resource | Global regex over the **whole concatenated file**, not per-resource | Plan JSON's `aws_iam_policy`/`aws_iam_role_policy`/`aws_iam_policy_document` changes' resolved `policy`/`json` fields, checked per-resource instead of whole-config — this is also a precision **improvement**: today a wildcard buried in one resource can't be attributed back to which resource it came from; plan JSON can name it exactly. |

Two rules (SEC-05b/c and SEC-02) get a genuine improvement in what they can see, not just a
mechanical port. That has to be called out and verified, not treated as risk-free parity —
see the proof bar.

## 2. Shadow-mode-then-retire plan

Same precedent G5 already established (`classify()` wired into `stage_plan`/`stage_apply` in
shadow mode — printed + audited on every plan, unconditionally, before enforcement was ever
flipped on):

1. Add the Rego evaluation as a **second, parallel** call inside `plan_gate.py`'s `verify()`,
   alongside the existing `optimize_analyzer.py` subprocess call — not replacing it yet.
2. Every real `verify()` run logs **both** verdicts to the audit chain: the existing
   regex-based findings and the new Rego-based findings, tagged so they're distinguishable in
   `.agents/logs/audit.jsonl`.
3. A **divergence report**: for every plan where the two disagree (a finding one produces
   that the other doesn't, for the same resource/rule), log it explicitly — this is the
   mechanism that catches the SEC-05b/SEC-02 "real improvement" cases above, and would also
   catch a genuine bug in the new Rego rules if one produces a finding the old rule correctly
   never had.
4. **Never both enforcing at once.** The existing regex path stays the actual gate
   (`BLOCKING_PREFIXES = ("SEC-",)` still governs real enforcement) until parity is proven
   across all 16 modules and every divergence is explained (either "this is the resolved-JSON
   improvement, expected" or "this is a Rego bug, fixed").
5. Only after that: retire the SEC-*/COST-* portion of `optimize_analyzer.py`'s regex rules
   (DATA-*/OBS-* stay), flip Rego to be the enforcing path.

## 3. Fail-closed on OPA input

Same standard as G2 and G5 (a systematic sweep closed six gaps in G5, three more in G2's own
classifier) — the exact place this bites next, per your own framing, is OPA's input parsing.
Every degradation case, mapped to a verdict:

| Case | Behavior |
|---|---|
| `opa` binary not found on PATH | **BLOCK** — same as G2's `schema_fetch_failed`: a gate that can't run isn't a gate. No silent skip. |
| `terraform show -json` fails, or the plan file doesn't exist | **BLOCK** — nothing to evaluate is not the same as "nothing wrong." |
| Plan JSON parses but is missing `resource_changes` entirely, or it's present but not a list | **BLOCK** — mirrors G2's `schema_malformed` check exactly (learned the hard way there: check the field is the right *type*, not just present). |
| `opa eval` itself returns a non-zero exit / malformed output (bad Rego syntax, a runtime error inside a rule) | **BLOCK** — an OPA evaluation error is not "no findings," it's "couldn't determine," and must not be read as clean. |
| A specific resource type this rule cares about (e.g. `aws_redshift_cluster`) has zero instances in `resource_changes` | **Not a failure** — nothing to check, matches G2's own "no types used = no findings" distinction. The dividing line is the same one G2 already drew: "genuinely nothing there" vs. "something's there and we can't verify it" are different outcomes, and only the second blocks. |
| A resource of a tracked type exists but a field the rule expects (e.g. `after.encrypted`) is absent from its plan JSON (a schema shift, or the field is legitimately unknown-until-apply) | **BLOCK**, distinctly labeled (e.g. `field_unresolved`) — an unknown-at-plan-time computed value is a real, different case from "field doesn't exist because it's not applicable," and collapsing them would either over-block harmless cases or silently pass a genuinely unverifiable one. |

**Made explicit, not left as one generic row — this is the exact shape where the sixth-instance
fail-open pattern hides**: every presence-based rule in §1 (SEC-01/03/04, COST-01/02/03) reads a
scalar or existence field off `change.after`. Real Terraform plan JSON carries a parallel
structure, `change.after_unknown`, mirroring `after`'s shape with a `true`/`false` at each leaf
marking whether that specific value is knowable yet or only resolves at apply. A rule that reads
only `after.<field>` and treats "absent" and "unknown" the same way IS the fail-open case: an
unknown `encrypted` would read as falsy/absent and silently pass SEC-03, exactly the pattern
that's recurred six times this session. **Every presence-based rule must check `after_unknown.
<field>` first**: `true` there routes to BLOCK (`field_unresolved`) unconditionally, before ever
consulting `after.<field>`'s actual value — unknown is never treated as "false" or "absent" by
any rule in this migration. The exact JSON shape of `after_unknown` (verified live, not assumed,
before implementation) determines how each rule's Rego reads it, but the BLOCK-on-unknown
verdict itself is not implementation-dependent and applies uniformly across all six rules.

## 4. Proof bar

1. **Parity, all 16 modules.** Real `terraform plan` + `show -json` for every current module,
   Rego verdicts compared against `optimize_analyzer.py`'s current verdicts on the same HCL.
   Every difference explained — either a documented resolved-JSON improvement (SEC-05b/c,
   SEC-02) or a bug, never left as an unexplained mismatch.
2. **Fail-closed sweep**, Probe-A style, over every case in §3's table, each with its own
   regression test, run before Phase 3 is called done — not after, the way G5's sweep landed
   one day later than it should have.
3. **Shadow-mode divergence log reviewed** across enough real runs to be confident retiring
   the old path doesn't regress anything — not just the one-time parity check in (1), the
   actual logged divergence stream from real `verify()` calls during the shadow period.
4. **`opa` binary availability** verified the same way `terraform`'s is (`toolpath.find_tool`
   pattern) — confirm this repo's CI images and the Dockerfile can actually get `opa` before
   assuming the gate can run there at all.
5. **`after_unknown` verified against a real plan, not memory.** Same standard as G5's
   verify-replace-representation-in-real-terraform-plan-JSON check: capture a real
   `terraform show -json` for a module/attribute combination that genuinely has an
   unknown-until-apply computed value (e.g. an attribute whose value depends on another
   resource not yet created), confirm the exact `after_unknown` shape Terraform actually
   emits for it — not assumed from documentation — and write a test asserting that shape
   routes to BLOCK (`field_unresolved`) for at least one of the presence-based rules
   (SEC-01/03/04, COST-01/02/03). This is not satisfied by the parity check in item 1 alone,
   since none of the 16 current modules may happen to produce a genuinely unknown value on
   their own baseline plan — if none do, this item requires deliberately constructing a
   fixture that does, the same way G2's proof bar constructed a fixture regressing to
   `data.aws_region.current.name` rather than waiting for a module to happen to hit it.

## Ordering invariant

G6 is Phase 3. G2 (Phase 2) closes first. Catalog teardown (Phase 6) stays last regardless.
No implementation starts until this scope is agreed.
