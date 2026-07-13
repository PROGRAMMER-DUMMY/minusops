# Phase 5 scope — G9, ephemeral apply

Scope document only. No implementation until this is reviewed and agreed, same discipline as
every prior phase this session.

## 0. Premise check — the original "G9 via LocalStack" naming needs re-confirming, not assumed

The G1–G9 taxonomy named G9 "Ephemeral apply via LocalStack, AWS-only" before this scope was
written. Verified live (not assumed) before drafting anything further: **LocalStack's business
model changed since that name was set, and it changes what "just use LocalStack" actually means
for this repo specifically.**

- LocalStack retired its open-source Community Edition; the free "Hobby" tier now requires
  account registration and is contractually restricted to **non-commercial use**.
- `pyproject.toml` declares `license = { text = "Proprietary" }` — **not** an OSI-approved
  license, so MinusOps does not qualify for LocalStack's free Ultimate-tier-for-open-source
  program (which requires an OSI-approved license, public source, active maintenance).
- The realistic path to legitimate LocalStack CI usage for a proprietary tool is a **paid Base
  plan, $39–45/month minimum** — a real, recurring cost this scope should not paper over or
  assume the user wants to accept.
- In direct response to this same pricing shift, several new, genuinely free, MIT-licensed,
  drop-in-compatible alternatives appeared (same port 4566, same Terraform `endpoints{}`
  pattern, no account/auth token): **MiniStack** (MIT, "free forever," 55–60+ services including
  confirmed S3/IAM/KMS/Glue/Athena/MWAA/Step Functions/Kinesis/CloudWatch) is the most complete
  candidate found; Floci is a comparable, narrower alternative. Both are new (first releases
  March 2026) — genuinely less battle-tested than LocalStack, and neither has been run against
  this repo's actual modules yet.

**DECIDED: LocalStack, paid Base plan.** Reframed correctly on review: this is not free-vs-$39/mo,
it's whether G9's fidelity signal stands on a mature, battle-tested emulator or a four-month-old
one whose behavioral fidelity to real AWS is unproven. G9's entire value is faithfully
reproducing real apply-time behavior — an emulator that is subtly too permissive produces false
greens, which under this posture is worse than no gate at all (the gate looks like it verifies
something and doesn't). Building against the newer free option first and letting a fidelity
check decide later was considered and rejected: if the free option fails fidelity on any
security-critical type, all its emulator-specific CI plumbing gets rebuilt for LocalStack
anyway — verification effort spent twice, for a near-certain rather than merely possible payoff.
$39–45/month is accepted as the cost of a trustworthy assurance signal, not deferred.

**A real, structural blocker this creates**: a paid LocalStack account requires a
`LOCALSTACK_AUTH_TOKEN` and a payment method — neither obtainable by an agent. Implementation
below proceeds up to that boundary (the Python module, CI wiring, allowlist, fail-closed
handling, everything testable without a live paid instance) with the exact remaining steps that
need the token called out explicitly, not silently assumed done.

## 1. Structural constraint, verified against GitHub's own docs — G9 is Ubuntu-only

GitHub Actions service containers (and Docker generally) are supported **only on Linux
(Ubuntu) GitHub-hosted runners** — confirmed directly against GitHub's own documentation:
*"If your workflows use Docker container actions, job containers, or service containers, then
you must use a Linux runner... If you are using GitHub-hosted runners, you must use an Ubuntu
runner."* LocalStack's own docs state the same restriction explicitly for Windows. This is not
a gap to apologize for — it matches this repo's own existing `docker` (build-smoke) job, which
already only runs on `ubuntu-latest`, never in the macos/windows legs of the `test` matrix. **G9
runs in its own ubuntu-only CI job, structurally, the same way the existing docker job does** —
the cross-platform `test` matrix never attempts it, and that is by design, not an omission to
justify per-platform later.

## 2. AWS-only / Databricks asymmetry — structural, not a footnote

LocalStack emulates AWS. It cannot stand up a Databricks workspace. A
Databricks-touching change therefore reaches "ephemeral-apply verified" having passed through
**one fewer real gate** than an AWS-only change — the exact asymmetry `destructive_change_gate.py`
(G5) already names structurally via `reduced_assurance` / `databricks_resources` on every plan
touching a `databricks_*` type.

G9 must **compose with, not silently duplicate or override,** that existing signal:

- G9's own verdict carries an explicit `coverage` field distinguishing three real cases, never
  collapsed into one boolean: `"full"` (every resource in the plan is an AWS type G9 actually
  exercised), `"partial"` (a mixed AWS+Databricks plan — G9 ran, but only covers the AWS
  portion), `"none"` (a Databricks-only plan — G9 never ran at all, not "ran and passed").
- A `"partial"` or `"none"` verdict must **never be reported or logged as if it carries the same
  assurance as `"full"`** — the report/audit-chain entry states which resources G9 actually
  exercised, by address, not just a pass/fail bit.
- G5's `reduced_assurance` stays the authoritative "does this need the staged path" signal
  (unchanged, not touched by this phase); G9 adds *why*, concretely, when that flag is set for
  Databricks reasons — the visibility condition 1 asked for, not two gates independently
  guessing at the same fact.

## 3. What G9 actually gates on — distinct from G1–G6, not a slower re-run of them

Static analysis (G1 validate, G2 schema lint, G6 OPA policy) already runs pre-apply and catches
what's derivable from HCL/plan JSON alone. G9's entire reason to exist is the class of failure
that **only surfaces when resources are actually created, in real dependency order, against a
real (emulated) provider**:

1. **Dependency-ordering bugs** — an implicit or missing `depends_on` that plans fine (Terraform's
   graph looks valid) but fails at apply time because a referenced attribute isn't populated yet
   in the order resources actually get created.
2. **Real provider-side validation** — schema-valid HCL the emulated (or real) API itself
   rejects: malformed ARNs, cross-field constraints, resource-specific limits — anything
   `terraform validate`/G1 cannot catch because it only checks Terraform's own type system, not
   the provider's runtime behavior.
3. **Apply-time computed-value resolution** — confirms a module's outputs and interpolations
   resolve to real, sane values once actual IDs exist (not just that they're syntactically
   well-formed at plan time, which G1–G2 already cover).

G9 does **not** re-run SEC-*/COST-* checks (G6's job), destructive-action classification (G5's
job), or schema conformance (G2's job) — a G9 finding is specifically "this failed or produced
something wrong only once real resources existed," and its findings are tagged distinctly so a
report reader never confuses a G9 apply-time failure with a G6 policy finding.

## 4. Fail-closed on the apply result — mapped explicitly, same table shape as G6

| Case | Verdict |
|---|---|
| Emulator (LocalStack) never starts / health check never passes | **BLOCK** — same as G6's `opa_not_found`: a gate that can't run isn't a gate. |
| Apply times out before completing | **BLOCK**, distinctly labeled (`apply_timeout`) — not silently treated as "no findings." |
| Apply partially succeeds (some resources created, then a real failure) | **BLOCK** — a partial apply is evidence of exactly the ordering/validation failure class G9 exists to catch, never read as "mostly fine." |
| Apply result / emulator output unparseable or malformed | **BLOCK** (`apply_result_malformed`) — same "couldn't verify ≠ verified clean" line G6 and G5 already draw. |
| A resource type in the plan has no confirmed emulator coverage (not on the reviewed allowlist, item 5) | **BLOCK for that plan** (`resource_type_unverified`) — never silently attempted against an emulator that might not really support it, and never silently falls through to a real endpoint. |
| Teardown (destroy) itself fails or times out | **BLOCK the run's overall verdict**, surfaced loudly — an ephemeral environment that fails to tear down is a real operational problem (cost, resource leakage), not a footnote. |
| Everything ran, applied, and tore down cleanly | Real verdict: pass/fail per resource-level check, logged with per-address detail. |

## 5. Endpoint isolation — structural, not "we configured it correctly once"

Both the hand-maintained `endpoints{}` block and the official `tflocal` wrapper have a
**documented** gap: a service not explicitly overridden falls through to real AWS. This is not
hypothetical — `tflocal`'s own changelog shows service coverage added incrementally, meaning
"not all services" is an admitted, current limitation of the *official* tool, not just a risk in
a hand-rolled config.

The only structurally safe design: G9 maintains its **own reviewed allowlist** of AWS resource
types confirmed to route correctly to the emulator — the same shape as `destructive_change_
gate.py`'s existing `STATEFUL_RESOURCE_TYPES`/`IAM_RESOURCE_TYPES` (scoped to what this repo's
modules can actually produce, extended deliberately when a new type is introduced, never
guessed). **Every one of the 41 AWS resource types this repo's 16 modules currently declare**
(enumerated directly via `grep -rhoE '^resource "aws_[a-z_0-9]+"' modules/*/main.tf`, not
assumed) must be confirmed on that allowlist — with real emulator coverage checked, not read off
a marketing page — before G9 is trusted to run against a plan containing it. A plan containing
any resource type NOT on the allowlist blocks (per item 4's table), full stop — it never
"probably still worked."

Never applies against a real account, by construction, not by convention: the ephemeral-apply
provider block is generated by G9 itself (dummy credentials, hard-coded emulator endpoint,
`skip_credentials_validation`), never derived from or falling back to whatever ambient AWS
credentials the environment happens to have — the same "dummy-credential real-plan" pattern
already used throughout this session's own verification work, but as the *only* code path G9
ever constructs, not one of several.

## 6. Proof bar

1. **Per-resource-type coverage, verified live, item by item, BOTH DIRECTIONS.** Every one of
   the 41 AWS resource types in the current module catalog, planned and applied against
   LocalStack for real, confirmed to either work or be named as a disclosed gap — not assumed
   from the vendor's own service list, the same "verify against real behavior" standard every
   other phase this session used. "Works" alone is not sufficient proof: an emulator that
   *accepts* something real AWS would *reject* is a false-accept — G9 goes green, a real apply
   later fails, and the gate was worthless for that type. Emulators are commonly too permissive
   (stubbing a response without enforcing the real constraint), which is the fail-open pattern
   one level down, inside the emulator itself, if not checked for directly. For at least the
   security-critical types (`aws_iam_role_policy` and any IAM policy document, `aws_kms_key`/
   its key policy, `aws_s3_bucket_policy`), item 1 requires a **negative fidelity check**: feed
   LocalStack something real AWS is documented to reject (e.g. a policy with a malformed
   principal, an invalid KMS key policy) and confirm LocalStack rejects it too — not just that a
   valid config is accepted. "Coverage" for those types means "enforces the real constraint,"
   not merely "didn't crash." Recorded per type: which were positively verified, which passed
   the negative check, which are disclosed gaps (matches G6/Phase 4's own "no coverage, name it"
   convention rather than silently skipping).
2. **Fail-closed sweep over every row in section 4's table**, each with its own regression test,
   before declaring anything done — not after, same timing discipline as G6/Phase 4.
3. **Prove it runs in CI, on real infrastructure, ubuntu-only**: a real GitHub Actions job that
   starts the emulator, runs a real ephemeral apply against a real module, tears it down, and
   asserts a genuine verdict — not "the job didn't error." Given this session's own repeated
   "correct logic, inert where it ships" pattern (G5 unwired, the Dockerfile checksum, G6 absent
   from CI), this is the single most load-bearing item on this bar, not a formality.
4. **Teardown reliability, stress-tested**: repeated create/destroy cycles (mirroring the
   audit-chain lock's own repeated-stress-run standard) confirming no leaked emulator-side state
   across runs, and that a failed apply still triggers teardown of whatever partially applied.
5. **`coverage` field verified on a real mixed AWS+Databricks plan**: a real composed plan
   touching both an AWS module and `databricks-workspace`, confirming G9's verdict correctly
   reports `"partial"` with the Databricks resources named, never silently reads as `"full"`.

## 7. Scope addition (2026-07-13) — pluggable emulator (LocalStack | MiniStack)

Raised on review, after the build above: make the emulator a user choice rather than
hardcoded to LocalStack. Fits the already-tool-agnostic design (item 0's own framing —
"swapping to paid LocalStack later is a config change, not a rewrite" — cuts both ways) and
matches real user diversity (pay for LocalStack's maturity, or take MiniStack's free, unproven
alternative). The hard requirement, stated explicitly on review and non-negotiable: **the
choice must be visible in the assurance, never a silent config toggle that makes two
different-confidence verdicts look identical.**

### 7.1 Mechanism

`run_ephemeral_apply(dir_, emulator="localstack", ...)` — `emulator` is `"localstack"` or
`"ministack"`, validated against a fixed `SUPPORTED_EMULATORS` set. An unrecognized value
**blocks** (`unsupported_emulator`), not "assume it behaves like one of the known ones" — the
same fail-closed posture as an unrecognized resource type. LocalStack and MiniStack share the
same port (4566) and the same Terraform `endpoints{}` pattern (confirmed for MiniStack this
session — "drop-in replacement for LocalStack," no endpoint reconfiguration needed), so
`_generate_provider_override()` needs no emulator-specific branching *for the endpoint shape*
— but this must be confirmed live per emulator, not assumed identical just because the marketing
copy says so, the same discipline every other "verify against real behavior" item in this
scope already applies.

**MiniStack needs no account or token at all** (confirmed directly earlier this session — no
API key, no auth, dummy credentials only) — unlike LocalStack's paid Base plan. This means
MiniStack's half of section 7.2's fidelity matrix is buildable and provable **right now**,
without waiting on `LOCALSTACK_AUTH_TOKEN` — the token blocks LocalStack's half specifically,
not this whole scope addition.

### 7.2 Per-emulator fidelity matrix — proven, not assumed, for each emulator independently

`RESOURCE_TYPE_ALLOWLIST` restructures from a flat `type -> (verified, security_critical)` map
into a per-emulator shape: for every one of the 41 resource types, a `security_critical` flag
(unchanged) plus a per-emulator record — `{"localstack": {"verified": bool, "negative_fidelity_
verified": bool}, "ministack": {same shape}}`. Every cell starts `False`; nothing here is
assumed complete because a name is now in the table.

Proof-bar item 1 (both directions, including the negative rejects-what-real-AWS-rejects check
on IAM/KMS/S3) runs **per emulator, per type** — the verification workload is not halved by
adding a free option, it's run twice, once per emulator, since a type verified on LocalStack
tells you nothing about whether MiniStack enforces the same real-AWS constraint. The output is
a real capability matrix (resource type × emulator × verified/gap), not a single "G9 works" bit
— this is what makes the user's emulator choice an *informed* one rather than blind trust in
whichever name they picked.

**Negative fidelity is MANDATORY-to-close for security-critical types, best-effort for the
rest — not spread thin equally across all 41.** A rubber-stamp emulator on a Glue job is an
annoyance; a rubber-stamp on an IAM policy is a false-green in the security gate itself. So a
`security_critical=True` type requires **both** `verified=True` **and**
`negative_fidelity_verified=True` on the chosen emulator before it counts as fidelity-proven —
`verified=True` alone is not sufficient for these three types (IAM role policies, KMS key
policies, S3 bucket policies) the way it is for the other 38. A security-critical type with
`verified=True` but `negative_fidelity_verified=False` is **not** a passing state; see 7.4's
table for the exact verdict.

### 7.3 The verdict must name the emulator and its fidelity, every time

Every `run_ephemeral_apply()` result carries `"emulator": "localstack" | "ministack"`. For each
resource type actually exercised in that plan, the verdict states whether that (type, emulator)
pair is `verified` (and, for security-critical types, `negative_fidelity_verified`) — not just a
single aggregate coverage bit. `compose_with_g5()`'s summary states the emulator and names any
unverified-for-this-emulator type explicitly (e.g. "G9 ran on MiniStack; `aws_iam_role_policy`
is fidelity-verified on LocalStack but NOT on MiniStack for this plan"). A MiniStack green and a
LocalStack green must never be presentable as the same evidence — a report reader who only
glances at "G9: PASS" without the emulator/fidelity annotation is exactly the failure mode this
requirement exists to prevent.

### 7.4 Fail-closed, unchanged in spirit, now emulator-aware

Section 4's table gains one more row, and the existing `resource_type_unverified` row becomes
per-emulator rather than global:

| Case | Verdict |
|---|---|
| `emulator` argument is not a recognized value | **BLOCK** (`unsupported_emulator`) — never assumed to behave like a known one. |
| A resource type in the plan is unverified **for the emulator selected** (even if verified for the other one) | **BLOCK** (`resource_type_unverified`, now scoped to `(type, emulator)`, not just `type`) — a type's LocalStack fidelity proof does not transfer to a MiniStack run, or vice versa. |
| A **security-critical** type (`aws_iam_role_policy`, `aws_kms_key`, `aws_s3_bucket_policy`) has `verified=True` but `negative_fidelity_verified=False` on the selected emulator | **BLOCK/reduced-assurance** (`negative_fidelity_unverified`) — distinct from `resource_type_unverified`, and just as blocking. Positive verification alone is never sufficient for these three types; an emulator that accepts everything is a rubber stamp, and a rubber stamp on the security-critical types is a false-green in the gate that exists specifically to catch that. |
| A **non-security-critical** type has `verified=True` but `negative_fidelity_verified=False` | Not a failure — negative fidelity is best-effort for these 38 types, not gating. Recorded in the matrix honestly, but does not block. |

Everything else in the approved scope (sections 1–6) stands unchanged — this addition only
changes how the allowlist is keyed and what the verdict reports, not the fail-closed table's
other rows, the coverage/Databricks-asymmetry design, or the Ubuntu-only CI placement.

## Ordering invariant

Phase 5 is next, unblocked now that the audit-chain lock is closed. Phase 4 stays advisory, G6
stays shadow, catalog teardown (Phase 6) stays last regardless. No implementation starts until
this scope — including the item-0 tool decision and the section-7 emulator-choice addition —
is agreed.
