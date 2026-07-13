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

**Floci added as a third candidate on review, evidence-based, not reputation-based.** Also
free, no token, drop-in port-4566-compatible. Checked directly against its own public issue
tracker (not assumed from its README) before adding it to `SUPPORTED_EMULATORS`:
- Two closed, real bugs directly in G9's target surface: `aws_instance` crashed the AWS
  provider plugin with a nil-pointer panic during the read-after-create step (issue #871,
  closed), and `aws_cognito_user_pool` crashed the same way, with the reporter confirming the
  identical Terraform config worked against real AWS (issue #177, closed) — both are exactly the
  class of apply-time-only failure G9 exists to catch, meaning Floci itself has shipped bugs of
  the kind it's meant to help find elsewhere.
- A separate, independent structural review recorded in issue #28 (from an early user, not this
  session) raised real, specific concerns: no public CI/test gate on pull requests at the time
  ("the README states 408/408 SDK tests passing, but these tests are not in the repository and
  there is no visible regression gate"), an S3 catch-all route that silently hijacked other
  services' endpoints, and several Lambda API gaps that would abort a real Terraform apply.
- Both #871 and #177 are closed (2026-05-29 and 2026-04-03 respectively), and the project has
  shipped ~50 releases since with substantial, apparently real feature work (confirmed via its
  release notes, not just commit counts). Closed-with-time-since is evidence the specific
  reported bugs were addressed; it is **not** evidence the underlying reliability concern (this
  project's own users have flagged it as feeling AI-generated and undertested) no longer
  applies more broadly — the closed-issue count is a starting signal, not a substitute for
  running the same gauntlet against it that LocalStack/MiniStack get.

**Net for all three**: MiniStack shipped a real STS `AssumeRole` validation gap until 2026-06-24
(issue #980 — `AssumeRole`/`AssumeRoleWithWebIdentity` accepted malformed, wrong-service,
wrong-account role ARNs and issued credentials anyway, before that fix landed) — directly
relevant since this repo's Databricks cross-account trust setup is exactly this kind of STS
call. Floci has the two closed apply-crash bugs above. **Every young, free emulator considered
here has real, historical correctness bugs in exactly the surface G9 exists to verify.** This is
the argument *for* running the both-direction negative-fidelity gauntlet as mandatory rather
than diligence theater — it is the only thing that distinguishes "the changelog says fixed" from
"is fixed for the specific types this repo's modules actually produce." No emulator is ranked
by stars, release cadence, or service-count claims; the matrix in 7.2 is the only ranking that
counts, and `SUPPORTED_EMULATORS` becomes `{"localstack", "ministack", "floci"}`.

### 7.2 Per-emulator fidelity matrix — proven, not assumed, for each emulator independently

`RESOURCE_TYPE_ALLOWLIST` restructures from a flat `type -> (verified, security_critical)` map
into a per-emulator shape: for every one of the 41 resource types, a `security_critical` flag
(unchanged) plus one record per entry in `SUPPORTED_EMULATORS` — `{"localstack": {"verified":
bool, "negative_fidelity_verified": bool}, "ministack": {same shape}, "floci": {same shape}}`.
Every cell starts `False`; nothing here is assumed complete because a name is now in the table.

Proof-bar item 1 (both directions, including the negative rejects-what-real-AWS-rejects check
on IAM/KMS/S3) runs **per emulator, per type** — the verification workload is not reduced by
adding free options, it's run once per emulator (three times now, not two), since a type
verified on LocalStack tells you nothing about whether MiniStack or Floci enforces the same
real-AWS constraint. The output is a real capability matrix (resource type × emulator ×
verified/gap), not a single "G9 works" bit — this is what makes the user's emulator choice an
*informed* one rather than blind trust in whichever name they picked, and what lets the matrix
itself — not stars, release cadence, or service-count claims — rank the three candidates.

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

### 7.5 Real gauntlet results (2026-07-13) — MiniStack and Floci, both directions, real CI

Run for real (a temporary scratch CI workflow per emulator, real Docker containers, real
`terraform init/apply`, iterated on and fixed where the harness itself had bugs, then removed):

**A real bug in the test harness itself, found and fixed first**: the initial gauntlet scripts
used `set -uxo pipefail` inside GitHub Actions `run:` steps, on the mistaken assumption this
would let a script continue past a failing `terraform apply` to log the outcome. It does not —
GitHub Actions' bash wrapper invokes `run:` steps as `bash -e {0}`, and `set -uxo pipefail`
inside the script only *adds* `-u`/`-x`/pipefail, it does not clear the already-active `-e`
inherited from that wrapper. This meant any genuinely-failing `terraform apply` (or `aws` CLI
call) killed the whole step immediately, before the script's own result-logging ever ran —
confirmed directly: Floci's real `aws_instance` failure stopped its entire job before the
later IAM/KMS/S3 tests could run at all. Fixed by wrapping each command in an explicit
`if cmd; then RESULT=0; else RESULT=$?; fi` (exempt from `errexit` by bash's own rules) —
after which both gauntlets ran to completion cleanly.

**Security-critical types, both directions, both emulators — the mandatory result:**

| Type | MiniStack positive | MiniStack negative | Floci positive | Floci negative |
|---|---|---|---|---|
| `aws_iam_role` (trust-policy principal ARN) | ✅ applies cleanly | ❌ **accepted** a malformed principal ARN (`arn:aws:iam::12:root`, a 2-digit non-account-ID) real AWS is documented to reject | ✅ applies cleanly | ❌ **accepted** the same malformed ARN |
| `aws_kms_key` (key policy) | ✅ applies cleanly | ❌ **accepted** a key policy with no root/admin grant statement at all — real AWS rejects this with "the new key policy will not allow you to update the key policy in the future" | ✅ applies cleanly | ❌ **accepted** the same policy |
| `aws_s3_bucket_policy` | ✅ applies cleanly | ❌ **accepted** a bucket policy whose `Resource` ARN names a *different* bucket than the one it's attached to — real AWS rejects this as an invalid resource | ✅ applies cleanly | ❌ **accepted** the same mismatched policy |

**Net result: as of this session, NEITHER free emulator passes the mandatory negative-fidelity
bar for any of the three security-critical types this repo's modules actually use.** Per
section 7.4's fail-closed table, this means a real plan touching `aws_iam_role`,
`aws_kms_key`, or `aws_s3_bucket_policy` correctly **BLOCKS** on both MiniStack and Floci today
(`negative_fidelity_unverified`) — this is the gauntlet doing exactly what it exists to do, not
a setback: it caught a genuine, false-green-risk permissiveness gap in both free candidates
before either was trusted, rather than after.

**MiniStack's own historical bug, re-verified — the fix genuinely holds**: issue #980
(`AssumeRole`/`AssumeRoleWithWebIdentity` previously issued credentials for malformed role ARNs
without validating them, directly relevant to this repo's Databricks cross-account STS trust)
was re-tested with a wrong-service-segment ARN (`arn:aws:s3::000000000000:role/...` instead of
`arn:aws:iam::...`) — MiniStack correctly rejected it (`ValidationError: Invalid RoleArn`), and
a positive control (a well-formed role ARN) was correctly accepted. This one specific,
previously-reported gap is confirmed fixed on the current version.

**Floci's own historical bugs, re-tested — the specific crash symptoms are gone, but the
underlying resource types are still not usable, for different reasons**: issue #871
(`aws_instance` crashing the provider plugin with a nil-pointer panic) no longer crashes, but
the instance now goes to a `terminated` state immediately after creation instead of `running`,
which the AWS provider's own wait-for-state logic then fails on — a different, still-real
failure. Issue #177 (`aws_cognito_user_pool` crashing the same way) also no longer crashes, but
now fails with `UnrecognizedClientException: The security token included in the request is
invalid` — an authentication-layer error, not the original panic. **Neither `aws_instance` nor
`aws_cognito_user_pool` is in this repo's real 41-type catalog**, so both findings are
informational (evidence about Floci's overall reliability) rather than blocking for this
repo's specific needs. Issue #28's S3-catch-all-route concern (a two-segment API path like
SNS's being silently hijacked by Floci's S3 controller) did **not** reproduce: `aws_sns_topic`
applied cleanly on Floci in this session's test.

**What this means for the item-0 tool decision, revisited honestly**: the original decision
(LocalStack paid, for a mature fidelity signal) is *reinforced*, not undermined, by this
evidence — both free alternatives just failed the mandatory security bar for real, on the exact
types this repo's modules use most. Whether LocalStack's paid tier would actually pass this same
negative-fidelity gauntlet is still unverified (no token provisioned this session) — that
remains the open question a provisioned account would answer, not assumed to be better just
because it's paid and mature.

## 8. Scope addition (2026-07-13) — sandbox isolation (security finding, mandatory before close)

Raised on review as a security finding, not a fidelity concern: G9's job is to apply
AI-generated infrastructure. That is executing untrusted, machine-generated code, and the
naive design (a sidecar emulator container with the host's Docker socket mounted, run as root)
is a container-escape / arbitrary-host-execution surface — a different, more severe class of
risk than the cost/blast-radius isolation `_generate_provider_override()` already provides
(dummy credentials, never touches real AWS). This section is mandatory-to-close, and now
outranks fidelity on the proof bar per explicit instruction.

### 8.1 A more direct escape vector than the one raised, found while scoping this

The docker-socket-as-root concern is real, but verified research surfaced a **more
fundamental** one that exists independent of any emulator choice or Docker configuration at
all: **Terraform's own `local-exec` and `remote-exec` provisioners execute arbitrary shell
commands directly on whatever machine runs `terraform apply`.** A hostile (or merely buggy)
AI-generated `null_resource` with a `local-exec` provisioner needs no emulator bug, no
docker.sock, and no container escape — it runs on the host the instant `terraform apply`
executes it, because that is literally what the provisioner is designed to do. `ephemeral_
apply.py`'s current design calls `subprocess.run([terraform, ..., "apply", ...])` directly on
the CI runner — meaning **today, before any of this section's isolation work, a generated
`local-exec` provisioner already has unmediated host access**, regardless of which emulator (or
none) is configured.

This changes the isolation boundary's target: it is not sufficient to sandbox "the emulator
container." **The isolation boundary must wrap the entire ephemeral-apply process** — the
`terraform` binary, every provider plugin it launches, and any provisioner it executes — not
merely the Docker container the AWS/emulator provider talks to over HTTP. A design that only
hardens the emulator's container (e.g. gVisor on the LocalStack/MiniStack/Floci container alone)
would leave `local-exec` completely unmitigated, since `terraform apply` itself would still be
running directly on the bare host.

### 8.2 Feasibility, verified live on this repo's real CI, not assumed from secondhand reports

Checked directly (a temporary scratch workflow, pushed, run via `workflow_dispatch`, then
removed) rather than trusted from conflicting online reports about GitHub Actions KVM support:
**`/dev/kvm` exists and `kvm-ok` reports KVM acceleration usable on this repo's real, free-tier
`ubuntu-latest` GitHub-hosted runners, right now** (CPU has `svm` virtualization flags; group
`kvm` on the device). This means a genuine microVM boundary (Firecracker or equivalent) is
feasible on the exact runners this repo's CI already uses, not gated behind GitHub's paid
"larger runners" tier as some secondhand sources claimed — confirmed, not assumed.

### 8.3 Design: the whole apply step runs inside a disposable Firecracker microVM

Given 8.1's finding, the design that actually matches the requirement is a real VM boundary
around the entire ephemeral-apply step, not container-runtime hardening alone (gVisor-style
syscall interception was considered and is a real, well-established technology, but it protects
against a compromised *container* escaping outward — it does nothing for `terraform apply`
running directly on the bare runner and executing a `local-exec` provisioner, which is 8.1's
actual finding). Per run:

1. Boot a disposable, network-isolated microVM (via an established toolkit built on Firecracker
   — e.g. `firecracker-containerd` or Weaveworks `ignite` — not a from-scratch Firecracker
   integration; KVM is confirmed present, so this is an integration/configuration task, not a
   from-scratch VMM build).
2. Inside that microVM: install `terraform`, the chosen emulator (LocalStack/MiniStack/Floci,
   itself run as a Docker container *inside* the VM — its docker.sock, if any, lives inside the
   disposable VM and is destroyed with it, never touches the host), and run the full plan →
   apply → destroy cycle from `ephemeral_apply.py` entirely inside that boundary.
3. Destroy the microVM unconditionally after the run, success or failure — this also
   strengthens proof-bar item 4 (teardown reliability): a resource leaked inside a destroyed VM
   cannot persist, the same way a killed process's advisory lock releases automatically (see
   the audit-chain lock fix), by construction rather than by a `terraform destroy` call that
   could itself fail.
4. The CI job (`.github/workflows/ephemeral-apply.yml`) is responsible for provisioning and
   tearing down the microVM around the existing `ephemeral_apply.py` invocation — this is
   infrastructure the CI job owns, not a code change to `ephemeral_apply.py`'s own control flow
   beyond accepting that it now runs inside a different environment.

**Real engineering cost, disclosed plainly**: this is a genuine build — kernel/rootfs image
selection, VM lifecycle management in a CI step, networking for the emulator port inside the
VM — more than a config change, less than a from-scratch Firecracker integration given an
established toolkit. Scoped here for review before that work starts, same as every other
phase.

### 8.4 Fail-closed addition

| Case | Verdict |
|---|---|
| The microVM fails to boot, or the isolation boundary cannot be established for any reason | **BLOCK** (`isolation_unavailable`) — G9 must never silently fall back to running the apply step unsandboxed. A gate whose isolation can't be confirmed is not a gate; this is the same "can't verify ≠ verified clean" line drawn everywhere else in this scope. |
| The hostile-resource escape test (8.5) fails — a canary escape attempt succeeds | **BLOCK the entire G9 mechanism**, not just that one run — an escape that succeeds once means the isolation boundary itself is not trustworthy, and G9 cannot be closed until it's rebuilt and reproven. |

### 8.5 New proof-bar item — the hostile-resource escape test (mandatory, above fidelity)

A deliberately-hostile fixture: a Terraform configuration containing a `null_resource` with a
`local-exec` provisioner that attempts a concrete, checkable host-escape canary — e.g. writing a
sentinel file to a path outside the microVM (a host-only bind mount never exposed inside the
VM), or attempting to reach a host-only network address/service that only exists outside the
VM boundary. Run this fixture through the full `ephemeral_apply.py` pipeline inside the real
isolation boundary from 8.3, on real CI, and assert the canary **never** appears on the host —
not "the apply step didn't error" (a `local-exec` provisioner succeeding looks identical to a
normal apply from Terraform's own point of view; the proof has to check the host side directly,
not Terraform's exit code). This is the load-bearing proof for this whole section: it is the
only test that actually exercises whether the isolation boundary holds against the exact thing
`terraform apply` is capable of doing on its own, independent of any emulator.

### 8.6 Real proof, run on real CI (2026-07-13) — this is not a design on paper

Built and proven directly against this repo's real `ubuntu-latest` GitHub-hosted runners (a
temporary scratch workflow, pushed, dispatched, iterated on with real evidence, then removed —
same discipline as every other proof this session):

1. **KVM confirmed real, not assumed** (section 8.2's claim, now with the actual command
   output): `/dev/kvm` exists (`crw-rw---- root kvm`), `kvm-ok` reports "KVM acceleration can be
   used", CPU flags include `svm` (AMD-V). This is the free-tier runner this repo's CI already
   uses — no paid "larger runners" tier needed.
2. **Real Firecracker binary + official CI kernel/rootfs artifacts, no third-party wrapper**:
   downloaded the real `firecracker` release binary (v1.16.1) directly from the official GitHub
   releases, and the exact kernel (`vmlinux-6.1.102`) + Ubuntu 22.04 rootfs (`ubuntu-22.04.ext4`)
   + SSH key from Firecracker's own official quickstart CI artifact bucket
   (`s3.amazonaws.com/spec.ccfc.min`) — confirmed the real object keys directly (an initial
   guess at the naming pattern was wrong; the real bucket listing was queried and the correct
   keys used).
3. **Real, successful boot, confirmed as a genuinely separate machine**: booted via the
   documented Firecracker REST API over a Unix socket (kernel/rootfs/network-interface
   configuration, then `InstanceStart`), SSH'd in over a tap-device network interface, and
   confirmed the guest has its OWN hostname (`ubuntu-fc-uvm`), OWN kernel
   (`Linux ubuntu-fc-uvm 6.1.102 ...`), OWN OS (`Ubuntu 22.04.5 LTS`) — verifiably distinct from
   the host runner's own kernel (`6.17.0-1018-azure`) and hostname. Boot-to-SSH-ready took
   roughly 7 seconds.
4. **A real gap found, not assumed away, then fixed and re-proven closed**: the first hostile-
   escape attempt (a simulated `local-exec`-style command run over the guest's SSH session,
   standing in for what a hostile provisioner would execute) tried two things: (a) read a
   host-only canary file, (b) connect to a TCP listener bound to the host's own tap-gateway
   address (172.16.0.1:9999). **(a) was blocked from the very first run** — the guest's root
   filesystem is a wholly separate disk image with no bind mounts, so a host-only file
   is simply invisible (`No such file or directory`), and `ls -la /` from inside the guest
   shows a normal, minimal guest-only filesystem. **(b) SUCCEEDED on the first run** — the
   guest reached the host's listener and received `HOST_REACHED_FROM_GUEST` back. Root cause:
   the NAT/forwarding rules set up to let the guest reach the internet only governed the
   `FORWARD` chain (traffic passing *through* the host to elsewhere); nothing governed the
   `INPUT` chain (traffic destined *for* the host's own services), so the guest could talk
   directly to whatever the host had listening on the tap-gateway IP. **Fixed**: `iptables -A
   INPUT -i tap0 -j DROP` (with `ESTABLISHED,RELATED` explicitly allowed first, so the host's
   own outbound SSH-into-guest management traffic still gets its return packets). **Re-run
   after the fix**: the same connection attempt from inside the guest now times out
   (`EXIT=124`, no `HOST_REACHED_FROM_GUEST` anywhere in the output) — the path is closed.
5. **What this proves, and what it doesn't yet**: this is real, working proof that (a) a
   genuine microVM boundary is buildable on this repo's actual CI infrastructure without a paid
   tier, (b) the specific `local-exec`-shaped host-escape vector this session identified as more
   fundamental than the docker.sock concern can be closed, and (c) finding a real gap by testing
   rather than assuming is exactly what this proof-bar item is for — a naive first attempt at
   this design was NOT secure, and shipping the naive version untested would have been a real
   vulnerability. What this does **not** yet prove: the fully-wired `ephemeral_apply.py`
   pipeline (terraform + a real emulator container) running inside this exact microVM boundary
   end to end — the proof above uses a bare Ubuntu guest and an SSH-driven command to stand in
   for what a hostile provisioner would attempt, not a full G9 run. Wiring `ephemeral_apply.py`
   itself to execute inside this boundary (installing terraform + the chosen emulator inside
   the guest's rootfs or via a startup script, then running the real plan/apply/destroy cycle
   over the same SSH channel) is real, remaining implementation work — scoped and proven
   feasible here, not yet built as the shipped mechanism.

### 8.7 The shipped mechanism itself, wired and proven on real CI (2026-07-13) — G9 closes

Section 8.6 proved the isolation boundary was buildable but explicitly flagged what remained:
wiring the real `ephemeral_apply.py` pipeline (not a bare SSH-driven stand-in) to execute inside
it. That work is now done, real, and green on this repo's actual CI — `.github/workflows/
ephemeral-apply.yml`'s `isolation-boundary` job, running on every push, no secrets required
(MiniStack, free — the isolation boundary is emulator-agnostic by design, so proving it holds
does not wait on a LocalStack token).

**What the job actually does, end to end, on real infrastructure:**
1. Boots the same proven Firecracker microVM (KVM, pinned+checksummed v1.16.1 binary, official
   kernel/rootfs artifacts, the INPUT-chain DROP rule baked in from the start this time).
2. Provisions the guest **entirely inside itself** — docker, terraform, python, and this
   repo's own package, installed and running only inside the disposable VM, never on the host.
3. Runs the real `run_ephemeral_apply()` — the actual shipped function, imported and called
   directly, not a re-implementation — against a real MiniStack container (`--network host`
   inside the guest) for a plan containing `aws_s3_bucket.smoke`: real `terraform init/plan/
   apply/destroy`, a real verdict (`evaluation_failed: false`, `aws_resources_applied:
   ["aws_s3_bucket.smoke"]`, `emulator: "ministack"`).
4. Runs the **hostile-escape fixture** through that exact same real pipeline: `aws_s3_bucket.
   hostile` carrying a `local-exec` provisioner attempting the proven escape vector (a raw TCP
   connection to the host's own tap-gateway address, 172.16.0.1:9999). The provisioner executes
   for real, as part of a real `terraform apply` Terraform itself considers successful
   (`evaluation_failed: false` — a provisioner's own exit status doesn't fail the apply, exactly
   as real Terraform behaves).
5. **The load-bearing check runs on the HOST side**, independent of anything Terraform or
   `ephemeral_apply.py` itself reports: a canary listener armed on the host before the hostile
   fixture runs, checked after — `grep -q "HOST_REACHED_FROM_GUEST"` against the listener's own
   log. Real result: **no match** — the canary was never reached. The isolation boundary held
   against the exact thing a hostile or merely-buggy generated `local-exec` provisioner is
   capable of doing, exercised through the real, shipped G9 mechanism, not a stand-in.

**Five more real bugs found and fixed getting here, each confirmed via actual CI failures, not
anticipated in advance** — the pattern holds from every other proof this session: the design was
sound, but real infrastructure surfaces real gaps a design review can't:
- A checksum-filename mismatch in the pinned Firecracker download (fixed: name the local file
  to match what the `.sha256.txt` actually references).
- No DNS resolver configured in the guest — NAT routes IPs, not hostnames (fixed: `/etc/resolv.
  conf` pointed at a public resolver).
- **The real, load-bearing one**: every "grow the disk" fix (resize2fs in place — rejected, it
  corrupted the guest filesystem; a redirected scratch disk; a bigger root) kept failing at
  increasing sizes, because the actual bug was that Firecracker's own downloaded artifacts
  (kernel, rootfs, the multi-GB scratch/root images) lived inside `$GITHUB_WORKSPACE` and were
  being swept up by the "copy the repo into the guest" step's own `tar` command — each fix made
  the accidentally-copied file bigger, not the problem smaller. Fixed by moving every Firecracker
  artifact into `$RUNNER_TEMP/fc`, structurally outside the checked-out repo.
- Firecracker's own default machine config (1 vCPU / 128MiB RAM, never explicitly set before)
  silently let the guest go unresponsive mid-`apt-get`; fixed with an explicit `/machine-config`
  PUT (2 vCPU / 2048MiB).
- The quickstart rootfs has no dpkg database at all (a bare SSH-probe artifact, not a
  provisioned OS) and this kernel lacks nftables support (`iptables` defaults to the `nft`
  backend, which fails outright) — fixed by seeding an empty dpkg status file and switching to
  the legacy `iptables` backend. Docker's own bridge-networking path needed a further `iptables
  raw`-table rule this kernel doesn't support either; sidestepped with `--network host` for the
  single-tenant emulator container, which needs no bridge/NAT path at all.

**G9 (Phase 5) is now genuinely closed.** Both proof-bar items this session's own instructions
ranked above fidelity are real, not designed-on-paper: the isolation boundary is proven, and it
is now the actual shipped mechanism, not a standalone probe standing in for it. LocalStack's own
fidelity column stays honestly unverified pending a provisioned paid account — a real, disclosed,
separately-tracked gap (item 0), not a blocker to closing the isolation requirement itself.

## 9. Design option, not required now — hybrid gating (flagged for later consideration)

Raised on review as worth scoping, not building: G9 does not have to run every resource type
through one general-purpose emulator. A layered design was suggested:
- `terraform validate` (free, local, no emulator, already G1) as the always-on first gate —
  unchanged, already exists.
- The general emulator (LocalStack/MiniStack/Floci, per section 7's matrix) for resource types
  that genuinely need a real apply cycle to catch ordering/validation/computed-value bugs.
- Service-specific, higher-fidelity emulators (e.g. ElasticMQ for SQS, DynamoDB Local for
  DynamoDB) for the specific services where general-purpose AWS emulators are documented to be
  weakest, if this repo's module catalog ever grows to include those services (it currently does
  not — no SQS, no DynamoDB in any of the 41 real resource types).

Not required for G9's close — flagged here so it isn't lost, and because it composes naturally
with section 7's per-`(type, emulator)` matrix (a service-specific emulator is just another
column). Revisit if/when this repo's catalog grows into services those tools cover better than
LocalStack/MiniStack/Floci do.

## 10. Correction (2026-07-13) — IAM/KMS/S3 security enforcement belongs in G6, not the emulator

Raised on review of section 7.5's negative-fidelity finding: **the emulator gauntlet was answering
the wrong question for these three types.** Emulator fidelity checks *validity* — would real AWS
accept this config at all — not *safety* — is this config dangerous. A perfect, 100%-faithful
emulator would apply a wide-open, `Principal: "*"` IAM trust policy without complaint, because
real AWS *does* accept it. Negative fidelity on `aws_iam_role`/`aws_kms_key`/`aws_s3_bucket_policy`
was never going to close this gap, no matter which emulator eventually passes it — it was testing
the wrong layer for the actual risk.

**The correct owner is G6** (`policy/g6/rules.rego`, OPA over plan JSON) — it already reads the
*resolved* policy content at plan time, for free, deterministically, with no emulator involved at
all. This is a routing correction, not new scope: G9 keeps its own job (apply-time-only failures —
ordering, provider-side validation, computed-value resolution, per section 3) and explicitly does
**not** own IAM/KMS/S3 *security content* — that responsibility moves to the gate that can actually
enforce it.

**Queued, in order, after the section-8 isolation boundary is wired into the shipped
`ephemeral_apply.py` mechanism (G9's own close condition, unaffected by this section):**

1. Extend G6's SEC-* Rego to hard-enforce the security properties an emulator cannot: no
   `Principal: "*"`, no `Action: "*"` on sensitive resources, KMS key policies that aren't
   wide-open, S3 bucket policies that deny public access, IAM trust relationships properly scoped.
   These are security-*content* checks over plan JSON — free, deterministic, no emulator required.
2. Shadow-mode-then-flip, identical to G6's own existing migration pattern (see the Phase 3
   HANDOFF entry): the new rules run logging-only first, proven against all 16 real modules in
   both directions (fires on a real constructed violation, stays silent on a real clean policy,
   zero false positives) — only then does enforcement flip on. No enforcing on day one.
3. Disclose the residual boundary honestly, in `HANDOFF.md`, as the G9/G6 coverage split: **G6
   enforces IAM/KMS/S3 policy *security* statically; no emulator, and nothing else in CI, verifies
   real apply-time IAM *interaction* fidelity for those three types.** `RESOURCE_TYPE_ALLOWLIST`'s
   `negative_fidelity_unverified` block for these types stays exactly as-is — G6 taking ownership
   of the *security* question does not answer or close the *fidelity* question G9 already
   disclosed. The two gaps are distinct and both stay named, not collapsed into "G6 handles it now."

This makes the disclosed option genuinely real: IAM/KMS/S3 are not dropped from governance, their
*security* enforcement moves to the layer built to enforce it, while G9 keeps disclosing — rather
than quietly implying closed — the apply-time fidelity question that belongs to it and remains
open pending a provisioned LocalStack account.

## Ordering invariant

Phase 5 is next, unblocked now that the audit-chain lock is closed. Phase 4 stays advisory, G6
stays shadow, catalog teardown (Phase 6) stays last regardless. No implementation starts until
this scope — including the item-0 tool decision, the section-7 emulator-choice/Floci addition,
and the section-8 sandbox-isolation requirement — is agreed. **Section 8 (isolation) is now the
most load-bearing item on the entire proof bar, above fidelity** — G9 does not close without a
real, tested isolation boundary, regardless of how complete the fidelity matrix is.

**Status (2026-07-13): Phase 5 (G9) CLOSED.** Section 8.7 is the real, on-CI proof that the
isolation boundary is not just buildable but is the actual shipped mechanism — the hostile-escape
canary was checked against a real run of `ephemeral_apply.py` itself, inside the microVM, and
held. Section 10's correction stands alongside this, not against it: G9 closing means its own
proof bar (apply-time-only failures, isolation) is satisfied; it does not mean IAM/KMS/S3
*security* is solved by G9 — that's G6's queued work, tracked separately. LocalStack's fidelity
column (item 0, section 7) remains open pending a provisioned account — disclosed, not blocking.
