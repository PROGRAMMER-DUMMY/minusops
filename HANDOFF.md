# Handoff — Data-Pipeline Specialization

> **2026-07-09: Databricks-on-AWS build (Phases 0–2b) done — blocked on external input, not
> more code.** Terraform MCP wired (docs/schema lookups, `docker hashicorp/terraform-mcp-server`);
> AWS MCP deliberately deferred (needs real credentials to even connect — see
> `docs/project_plan.md` Phase E addendum). `networking-vpc` module (customer-managed VPC, closes
> the old MWAA-scratch gap for real). `databricks-workspace` module: classic workspace, trust +
> permissions policy both Databricks-canonical (not hand-rolled), `SEC-05` verifies the trust
> policy, DBU cost honestly reported as unresolved/not-priced. Phase 2b: optional named
> `databricks_catalog` + `databricks_sql_endpoint`, both off by default at the module **and**
> synthesizer level (confirmed by test + a fresh `compose()` render, not assumed) — an arbitrary
> `minus generate` never emits a billable warehouse or an unrequested catalog. 16 modules total,
> 328 tests passing, everything pinned via `minus-update-module`.
>
> **Nothing left to build speculatively.** Three things remain, all blocked on a real-world input
> MinusOps can't manufacture: (1) the live create+destroy test (§6 item 10) needs a real account
> to spend on — also the first real exercise of the warehouse/catalog `provider_config` pattern;
> (2) Phase 3 (multi-tenant platform layer) is deferred until a real second tenant exists — see
> the Phase E addendum's fast-follow decision; (3) Autoresearch's actual Databricks connection is
> gated on its own BAA + de-identification work, entirely outside MinusOps' repo. Do not scope
> new modules against any of these until the blocking input actually arrives — that would repeat
> the speculative-infrastructure pattern already rejected this session for landing-zone,
> full-PrivateLink, and AWS MCP Server.
>
> **Follow-up (same day): Terraform MCP wiring made agent-neutral.** `mcp/terraform-mcp.json` is
> now the single canonical, client-neutral definition (pinned image, `registry`-only toolset, no
> credentials, stdio default) — verified byte-for-byte identical to the live Claude Code
> registration (`claude mcp get terraform`), so it's a provable source of truth, not a doc that
> merely resembles what's running. `mcp/README.md` gives per-agent registration derived from that
> one file: Claude Code, Codex, the generic `mcpServers` JSON shape (Cline/Continue/Cursor/
> Windsurf), Goose, Google Antigravity CLI ("Agy CLI"); Aider's native MCP support status is
> honestly flagged as unsettled rather than given a fake-confident snippet. No server, module, or
> existing registration changed — pure additive documentation/config. This closes out the
> Databricks-on-AWS build's tooling side.
>
> **This is a deliberate stop, not a pause for input.** Phases 0–2b plus MCP portability are done
> and verified. There is no next module to scope from here without re-entering the
> speculative-infrastructure pattern already rejected this session — the three items above are the
> entire remaining surface, and all three wait on an external party, not on more code from this
> repo. Do not open a new phase, module, or tooling task until one of them actually lands.
>
> **Follow-up (same day): CI provider schema-diff watch (`minus-schema-watch`).** An external
> "enterprise expansion" doc proposed 5 phases (self-updating schema watch, hardening the 16
> modules, then three tiers of new AWS/Databricks provisioning breadth); negotiated down to
> exactly the first piece — `core/generation/schema_watch.py` fetches a real
> `terraform providers schema -json` for aws + databricks, reduces it to the resource/data types
> MinusOps' modules actually reference, and diffs against the last committed
> `recent-changes/<provider>/schema-snapshot.json`, opening a `finding` on a used type being
> removed, its schema `version` bumping, or a newly `deprecated` attribute — informational-only
> notes for anything else. Wired additively into `module_provenance.py` (optional `schema_hash`
> on `pin()`, an `upgrades/<id>-v<n>.json` report on a real re-pin) and `coverage_audit.py` (a
> read-only `schema_watch_status` field, zero change to the `unresolved`/blocking classification).
> `.github/workflows/schema-watch.yml` runs it daily + on manual dispatch and opens a PR with any
> changes — never pushes to main. All of this is pure tooling: it provisions nothing, for any
> tenant, so it doesn't reopen the provisioning stop above. The provisioning stop (Phases 1–4 of
> that source doc: module hardening, and all new AWS/Databricks modules) still holds — it was
> explicitly held out of this round and stays parked until a real driving need shows up.
>
> Live-verified before writing any extraction code (never trusted from the source doc's prose):
> real `terraform providers schema -json` output uses attribute-level `deprecated` +
> `deprecation_message` fields (recursively nested under `block_types`, not a resource-level
> flag) and a per-resource `version` integer. First real run resolved `aws 6.54.0` /
> `databricks 1.121.0` and seeded clean baseline snapshots (45 AWS types, 11 Databricks types —
> matches the full real module tree, not a fixture). Full suite green: 364 tests (36 net new —
> a real bug caught along the way: the version-constraint parser initially matched Terraform's
> own `required_version` instead of the provider block's `version`, caught by a unit test before
> it ever ran for real).
>
> **Two closing confirmations requested and completed:**
>
> 1. **Fail-loud path, not just the clean baseline.** The three finding kinds (`removed`,
>    `schema_version_bump`, `deprecated`) were each already unit-tested against the pure diff
>    function, and one kind end-to-end through real `run_provider()` file I/O — but no test
>    exercised a *real* diff all the way through `main()`'s exit code (`test_cli_run_exits_1_on_findings`
>    stubbed `run_provider` itself, decoupling it from the real diff). Closed with one more test,
>    `test_main_exits_1_from_a_real_diff_not_a_stubbed_run_provider`
>    (`tests/test_schema_watch.py`): calls `main()` twice with only the network fetch stubbed —
>    first run seeds a clean baseline (exit 0), second run's real diff logic detects a real
>    version bump and `main()` exits 1. 365 tests total, all green.
> 2. **Cron/PR mechanics — genuinely unproven, and correctly deferred, not skipped.** Attempted a
>    live `workflow_dispatch` test via an isolated git worktree + throwaway probe workflow
>    (deleted branch `chore/probe-schema-watch-pr-mechanics`, cleaned up, no trace left on
>    GitHub). Hit a real GitHub platform constraint before it could run: a workflow is only
>    dispatchable (by schedule or manually) once it exists on the repo's **default branch**
>    (`main`) — pushing it to any other branch first isn't enough, confirmed via
>    `gh workflow run` returning 404. This applies to the real `schema-watch.yml` too, not just
>    the probe. **Deployment prerequisite, recorded here so it isn't rediscovered under
>    pressure:** `schema-watch.yml` will not fire — on its daily cron or on manual dispatch —
>    until it is merged to `main`. Given the subpackage restructure it depends on
>    (`core/generation/`, `core/cost/`, etc.) is itself still entirely uncommitted and unpushed,
>    testing PR-open permissions in isolation now would mean touching `main` with a throwaway
>    add-then-revert ahead of the real thing being staged — the same speculative-work-ahead-of-need
>    pattern this session has avoided throughout. Correct sequencing instead: the real workflow's
>    **first dispatch run, at actual deploy time**, is what proves `pull-requests: write` +
>    `GITHUB_TOKEN` actually opens a PR — same permission surface, real workflow, no rehearsal
>    commit needed.
>
> **Follow-up (2026-07-10): REPO_ROOT wheel bug fixed, two cheap deploy mitigations folded in.**
> A post-close review surfaced a real correctness bug in Phase 0's own tooling: `recent-changes/`,
> `upgrades/`, and the audit log all resolved off `module_registry.REPO_ROOT` — naked dirname
> math off `modules.py`'s own file location, correct for a source checkout, wrong for an
> installed wheel (would resolve inside `site-packages`). `MODULES_DIR` already had a wheel-aware
> fallback chain (env var → cwd → `REPO_ROOT` → sysconfig data/purelib); these governance-output
> paths had none — for a tool whose entire value is a trustworthy audit trail, writing it to a
> nondeterministic, likely-unwritable location is a correctness bug in the thing MinusOps exists
> to guarantee, not a cosmetic one. **Fixed**: `modules.output_root()` (new function, same file)
> gives every governance-output consumer (`module_provenance.py`'s `upgrades/`,
> `schema_watch.py`'s `recent-changes/` + audit log, `coverage_audit.py`'s schema-watch lookup) a
> real fallback chain — explicit `MINUSOPS_OUTPUT_DIR` override → cwd if it looks like a checkout
> → `REPO_ROOT` if *it* looks like a checkout → a guaranteed-writable `~/.minusops/` — and never
> resolves into an installed package. Proven two ways: `tests/test_modules.py` has a test that
> simulates the exact wheel-install failure mode (fake site-packages `REPO_ROOT`, fake unrelated
> cwd) and asserts the result is neither of those, and `ci.yml`'s wheel-smoke job now actually
> invokes `minus-update-module pin` and asserts `output_root()` doesn't resolve inside the
> installed venv — so this exact bug can't ship clean again. 369 tests total, all green.
>
> Two cheap mitigations folded in alongside, not new work: (1) `schema-watch.yml` now caches
> Terraform provider plugins via `actions/cache` + `TF_PLUGIN_CACHE_DIR`, so the daily cron
> doesn't re-download both providers from scratch every run. (2) **Deploy-time reminder, added
> to the prerequisite above:** before trusting the first real cron run, confirm repo Settings →
> Actions → "Allow GitHub Actions to create pull requests" is **ON**. If it's off, the PR step
> fails loud (the job goes red, per the design above) rather than silently — but a red *scheduled*
> job's first run is easy to write off as transient noise rather than a real permission gap. Check
> the setting before the first run, don't diagnose it after the fact.
>
> Left correctly held, not touched: Databricks-never-hit-a-real-account, the metastore #3396
> teardown-residue risk, the `dag_s3_bucket_arn` REVIEW gap, the `provider_config`
> account-vs-workspace doc contradiction, and cross-platform CI (this session's tests all ran
> locally on Windows only — the real ubuntu/macos/windows matrix has never seen any of this
> code). All five resolve only on a real apply or a real CI matrix run; no amount of local work
> closes them, so none were touched.
>
> **Follow-up (2026-07-10): external competitor/standards calibration → one real bug fixed, one
> decision correctly parked, a disclosure set logged with citations.** A self-assessment
> questionnaire (repo-grounded: RBAC model, audit-chain mechanics, drift detection, rollback,
> Databricks provider quirks — see §6 for the full findings) was checked against HCP Terraform,
> Terraform Enterprise, Spacelift, env0, Atlantis, and SOC 2/ISO 27002 practice. Verdict split
> three ways, each treated differently on purpose:
>
> 1. **Attempted fix — a real bug, not a compliance nicety, but the fix is UNPROVEN, not closed.**
>    `audit_chain.append()`'s read-then-write was unlocked: concurrent writers could read the
>    same `prev_hash` and both append, forking the chain. Proven empirically *before* fixing,
>    not just theorized — 8 threads × 15 concurrent appends against the unlocked code produced
>    **115 lines instead of 120** (silent write loss) plus chain-verify failures. Fixed with a
>    stdlib-only, cross-platform sidecar-file lock (`os.O_CREAT | os.O_EXCL`, atomic on POSIX
>    and Windows alike) — no third-party dependency added to the one component whose entire
>    value is trustworthiness. A second test covers the crashed-writer case: a stale `.lock`
>    file left behind times out cleanly (`TimeoutError`) instead of hanging forever.
>    **2026-07-10, later same day — reclassified by an independent audit: the concurrency test
>    is FLAKY, not green.** Re-run 6 times in a row on this machine: 5 passed, 1 failed with
>    `PermissionError(13)` on lock-file cleanup (`os.remove` racing another thread's
>    `os.open(..., O_CREAT|O_EXCL)`). This means the "120/120, all green" claim originally made
>    here was itself a **false green** — the exact correlated-failure mode (a test that looks
>    like validation but is actually intermittent) this whole pivot exists to catch, now found
>    in the project's own governance-test infrastructure. **Status: UNPROVEN/flaky, not fixed.**
>    Deliberately not re-investigated yet — its fix-urgency is positioning-dependent (see the
>    open positioning question at the end of this entry) and single-machine testing can't tell
>    Windows-only quirk from a real cross-platform race; the actual CI matrix (once triggered)
>    is what will answer that. §6 item 11 updated accordingly. 371 tests total — passing on
>    THIS run, not guaranteed on the next.
> 2. **Parked, not guessed.** The approver allowlist defaulting to `"open"` (everyone authorized)
>    unless explicitly configured is a real SOC 2 CC8.1 finding *if* MinusOps is meant to carry a
>    compliance claim — but `authz.py` documents it as an intentional single-operator-dev default,
>    and nothing in this repo's own docs settles whether MinusOps is a product, a governed
>    platform, or a reference implementation. That's a positioning call, not a code question — it
>    stays open pending that decision, tagged explicitly in §6 item 1, not silently resolved either
>    direction.
> 3. **Disclosed with citations, deliberately not fixed.** No live drift detection (§6 item 12 —
>    behind paid-competitor table stakes, not a SOC 2 requirement, a disclosure gap not a
>    correctness one) and the Databricks provider/teardown ecosystem issues (§6 item 10, extended
>    with the upstream PR/issue trail) are real but external — no amount of MinusOps code closes
>    them without a real account or an upstream provider fix.
>
> **The positioning decision is intentionally not made here** — it's the one open item that
> depends on a call only the project owner can make, not on more research or more code.

> **Follow-up (2026-07-10, later): generation-time-authoring pivot, Phase 1 (G5 destructive-change
> gate) — real 16/16 baseline, real enforcement wired, one more false-green caught and fixed.**
> An independent audit of the in-progress pivot (frozen 16-module catalog → generation-time,
> research-grounded authoring, a 9-gate G1–G9 stack) rated everything against real evidence, not
> prose — and caught two real overstatements from earlier the same day: the tfsec→Trivy swap
> (G7) had been described as basically done and was never executed (`EXTERNAL_SCANNERS =
> ("checkov", "tfsec")` unchanged — **still NOT STARTED, low priority, do later**), and G5's
> classifier (`core/governance/destructive_change_gate.py`) was "built" while governing nothing —
> `plan_gate.py` never imported or called it. Both closed out honestly this round:
>
> 1. **16/16 create-only baseline, closed for real.** The prior 15/16 baseline's one failure
>    (`orchestrator-mwaa`) was a test-fixture placeholder gap (`dag_s3_bucket_arn` needs an
>    ARN-shaped string, a real AWS-provider attribute-level check, not a Terraform type-system
>    one) — confirmed unrelated to the actual `dag_s3_bucket_arn` synthesizer-wiring gap (§6 item
>    9, a different code path entirely, never exercised by this standalone-module test) before
>    touching anything. Fixed with a narrow, name-scoped placeholder override (`*_arn`-suffixed
>    variables only). 16/16 real modules now classify create-only.
> 2. **classify() wired into `plan_gate.py` for real — shadow, then enforce.** `stage_plan` and
>    `stage_apply` now call the classifier on every real plan and print+audit the verdict
>    unconditionally (shadow visibility, all modes). Enforcement reuses the *existing*
>    gatekeeper/auto-approve distinction rather than inventing a new concept: `mode=
>    "auto-approve"` (no human ever reviews the plan) now refuses to apply anything that isn't
>    autonomous-eligible, with no bypass flag, in dev or production alike; `mode="gatekeeper"`
>    (a human already reviews and approves) is never blocked by this check — that IS the
>    staged/guarded path the classifier routes non-eligible plans to. Proven against real
>    Terraform state, not mocks: `tests/test_gate_e2e.py`'s two new tests (a) create a real local
>    resource, attempt to destroy it via `--mode auto-approve`, confirm the apply is refused AND
>    the resource is provably still there afterward, then confirm the *same* destructive plan
>    succeeds via `--mode gatekeeper` (the staged path genuinely still works, not just blocked
>    outright), and (b) confirm a genuinely create-only plan sails through `--mode auto-approve`
>    end to end with the resource actually created — enforcement doesn't block indiscriminately.
> 3. **A third real bug found by cross-checking against the existing test suite, fixed on the
>    spot.** `destructive_change_gate.classify()`'s original `mode == "managed"` allowlist would
>    fail *open* on malformed input: a plan entry with a missing/unrecognized `mode` field would
>    be silently excluded from classification entirely, even if it were a genuine destructive
>    change — found because `tests/test_plan_gate.py`'s older `PLAN_A`/`PLAN_B` fixtures (which
>    predate this module) never set `mode` at all. Real `terraform show -json` always sets `mode`
>    explicitly, so this changed no real-world behavior — but it closes a latent fail-open gap in
>    the classifier itself. Fixed by denylisting `mode == "data"` instead of allowlisting
>    `mode == "managed"`: only a confirmed non-mutating read is excluded now; anything else
>    (including a missing field) stays in scope. New regression test locks this down.
>
> Phase 1 (G5) is now materially closed: real baseline, real wiring, real enforcement, real
> proof against actual Terraform state. **Explicitly not done, and not attempted this round per
> instruction:** committing/pushing this work or triggering the real CI matrix — held pending a
> separate go-ahead, since it's a repo-visible, harder-to-reverse action distinct from local file
> edits. Phase 2 (G2, the pre-write schema linter) has not been started — brought back for review
> before starting it, per standing instruction.
>
> **Follow-up (2026-07-10, later): two more independent-audit probes, a fail-closed sweep, a real
> pre-push scan, and Phase 1 formally stamped closed.**
>
> **Probe A — sibling fail-opens in `classify()`.** The mode-field fix above was one gap found by
> accident; a systematic sweep of every field `classify()` reads found five more of the same
> shape (three silent fail-opens, three crashes-instead-of-fail-closed), empirically probed
> before and after fixing, none assumed. All six now fail closed through one `_fail_closed()`
> helper; 14 new regression tests lock every field down individually, plus two sanity checks
> confirming fail-closed didn't become fail-always (a genuine no-op plan and a genuine
> create-only plan both still classify eligible).
>
> **Probe B — is `stage_apply` really the only door?** Traced every subprocess/CLI/dispatcher
> path that could reach a real `terraform apply`. Confirmed: `_apply_with_json_capture` (the one
> literal apply invocation, `plan_gate.py:177`) has exactly one production caller, `stage_apply`;
> `stage_apply` has exactly one production call path (`stage_run`, reached identically via the
> CLI `apply` stage and via `dispatcher.py`'s DEPLOY intent re-invoking `plan_gate.py run` as a
> subprocess — same gated path, different invocation surface, not a bypass). `modules.py` has
> zero subprocess calls. `minusctl.py`/`dashboard_app.py` only ever print `plan_gate.py`
> instructions, never shell out. No bypass found.
>
> **Pre-push scan (repo is public — this mattered).** Confirmed via `git check-ignore` against
> real files, not `.gitignore`'s stated intent: every real `runs/*/terraform.tfstate` from the
> July 2–5 applies, every `.terraform/`, and the real audit chain (`.agents/logs/audit.jsonl`,
> holding real approver identities) are genuinely excluded. Secret-pattern scan (AWS keys,
> Databricks dapi tokens, private key blocks, real emails) across every file `git add -A` would
> stage: clean. One `arn:aws:iam::414351767826:root` found in a SEC-05 test fixture
> (`tests/test_optimize_analyzer.py`) — verified via Databricks' own official docs
> ([credentials](https://docs.databricks.com/en/administration-guide/account-settings-e2/credentials.html),
> [cross-account permissions](https://docs.databricks.com/aws/en/admin/cloud-configurations/aws/permissions))
> that this is Databricks Inc.'s own public AWS account ID — the canonical value every
> customer's cross-account trust policy is supposed to reference, not a private identifier, and
> confirmed absent from every real applied state in this repo. **Left as-is, deliberately**:
> scrubbing it would make the SEC-05 fixture verify a fiction instead of the real account real
> trust policies actually reference.
>
> **A real averted exposure, not just hygiene.** `.gitignore`'s `.agents/logs/` and
> `.agents/reports/` patterns turned out to be anchored to the repo root only — git's own glob
> rule for a pattern containing a middle slash, confirmed by direct empirical probe (an
> unprefixed pattern did not catch a nested `core/.agents/logs/`, while `runs/`/`artifacts/`,
> trailing-slash-only patterns, correctly matched at any depth). Eleven different files across
> `core/` resolve their audit/log directory via `os.path.join(os.getcwd(), ".agents", "logs")` —
> not a fixed repo-root anchor — so any invocation from a non-root working directory could have
> written the real audit chain to a tracked, non-ignored path on a now-public repo. Checked
> whether this had actually happened: it hadn't (the only stray nested `.agents/` content found,
> `core/.agents/cache/`, held only public AWS Price List dimension names, confirmed non-sensitive)
> — but the gap was real and would not have stayed lucky forever. Fixed to `**/.agents/logs/` and
> `**/.agents/reports/` (plus a new `**/.agents/cache/` entry and `pt_*.log` for the two hygiene
> items also found), verified against a corrected empirical probe (the first probe attempt used
> the wrong directory name and gave a false "still broken" reading — caught and redone correctly
> before trusting it).
>
> **Phase 1 formally stamped CLOSED.** Read this precisely, not as "suite green": **394/395
> tests.** The one failure is `test_append_is_safe_under_concurrent_writers` — the pre-existing,
> already-tracked Windows audit-chain lock flake (§6 item 11), UNPROVEN/flaky, living in a
> different subsystem than anything Phase 1 touched, diagnosed via its actual traceback (not
> re-run to chase a clean pass — re-running a flaky test until it passes is the exact false-green
> pattern this whole pivot exists to catch). **Phase 1's own surface is clean**: the 16/16
> create-only baseline, the fail-closed input sweep (Probe A), real e2e enforcement against
> actual Terraform state, and `stage_apply` confirmed as the single door with no bypass (Probe
> B). The audit-chain lock's fix-urgency stays positioning-dependent, untouched this round, per
> standing instruction.

> **Follow-up (2026-07-11): the G1–G9 gate taxonomy, recorded for the first time.** This
> numbering has been used throughout the generation-time-authoring pivot but was defined in a
> working session and never actually written down here — pure tribal knowledge until now.
> Recorded once so the two framings (gate number vs. phase number) stop drifting apart.
>
> | Gate | What it does | Status |
> |---|---|---|
> | G1 | fmt + validate | **DONE** (production path) |
> | G2 | Pre-write schema linter | **IN PROGRESS** (this session — see `docs/g2_scope.md`) |
> | G3 | Test mechanism + auto-generated assertions | **PARTIAL** — the mechanism exists (`terraform test`, real e2e proofs throughout this repo); auto-generating the assertions themselves is Phase 4, not built |
> | G4 | Plan-JSON parsing | **PARTIAL/proven for existing paths** — feeds G5 directly, not a standalone deliverable |
> | G5 | Destructive-change gate | **DONE, enforced** (Phase 1, closed — see above) |
> | G6 | SEC/COST rules → OPA/Rego over plan JSON | **NOT STARTED** (Phase 3) |
> | G7 | Checkov + Trivy (the tfsec→Trivy swap) | **NOT STARTED**, low priority (`EXTERNAL_SCANNERS` still `("checkov", "tfsec")`, unchanged) |
> | G8 | BCM live-API cost forecast (not Infracost) | **HAVE as architecture, unexercised** without a real account to forecast against |
> | G9 | Ephemeral apply via LocalStack, AWS-only | **NOT STARTED** (Phase 5) |
>
> **Six-phase mapping**, so "Phase N" and "G-number" resolve to the same thing every time:
>
> | Phase | Maps to |
> |---|---|
> | Phase 1 | G5 — **done** |
> | Phase 2 | G2 — **in progress** |
> | Phase 3 | G6 (OPA/Rego) |
> | Phase 4 | Intent-spec + G3 auto-generated assertions |
> | Phase 5 | G9 (LocalStack ephemeral apply) |
> | Phase 6 | Generation pipeline / catalog teardown — **last**, regardless of gate progress |
>
> **Four phases remain after G2** (3 through 6). Standing disclosed limitations —
> Databricks-no-real-apply and G9/AWS-only ephemeral-apply coverage — stay disclosed under the
> compliance-carrying-product posture (2026-07-10) until actually closed, not quietly dropped
> once they stop being the newest news.

> **Follow-up (2026-07-11/12): Phase 2 (G2, pre-write schema linter) built, dogfooded, closed.**
> `core/generation/schema_lint.py` gates `module_provenance.py`'s `pin` CLI action (the pure
> `pin()` function stays untouched/offline-testable; the live, blocking check lives in `main()`'s
> `pin` subcommand — same pure-classifier/enforcing-caller split G5 already established) against
> the real, live provider schema, on every call, no diff, no first-run pass, no missing-baseline
> skip. Proof bar: 73 unit tests (schema_lint's own), a fail-closed sweep covering unreadable
> module files, malformed/non-dict schema shapes at every level, and a corrupted previous
> provenance record (all BLOCK or gracefully no-op, never crash) — and the real integration
> proof this whole pivot exists for: a fixture regressing to `data.aws_region.current.name`
> (the pre-v6 form) correctly HARD-FAILs as deprecated against the real, live AWS provider
> (6.54.0), and a second real proof against Databricks (`databricks_mws_credentials.account_id`,
> a real live deprecation this repo's own module already knew to avoid by comment) confirms G2's
> fetch/reduce machinery works on both tracked providers, not just AWS — proven, not disclosed.
>
> **Dogfooding against the real 16-module catalog (not synthetic fixtures) found and fixed four
> real bugs before they could ever ship as false positives or false negatives:**
> 1. Schema-attribute recursion stopped one level deep — missed `statement.principals.type`
>    (`aws_iam_policy_document`) and three-deep `rule.apply_server_side_encryption_by_default.
>    sse_algorithm`. Fixed to walk block_types to arbitrary depth, matching schema_watch.py's
>    own `_deprecated_attrs` recursion.
> 2. `event_pattern = jsonencode({...})` spanning multiple lines leaked its JSON payload's own
>    keys (`source`, `detail`) as if they were sibling top-level Terraform attributes — the
>    multi-line-literal fold only triggered when the RHS *started* with a bracket, and
>    `jsonencode(` starts with a letter.
> 3. **A real infinite loop**, not just a slow case: `filter {}` (a valid, real, empty nested
>    Terraform block — `aws_s3_bucket_lifecycle_configuration`'s rule with no filter criteria)
>    advanced the line index to its own current line instead of the next one when the block
>    opened and closed on the same physical line, re-entering forever. Reproduced directly (hung
>    6+ hours before diagnosis), fixed, and locked down with its own regression test.
> 4. Index/splat access (`databricks_metastore.this[0].id` — wiring an optional, count-based
>    resource's output elsewhere, a real and common pattern) was originally treated as always
>    unparseable/blocking. Corrected: what's inside the brackets only selects *which instance*,
>    never *which attribute* — the attribute name after the bracket is exactly as statically
>    knowable as without the index, so it now resolves normally. The genuinely unresolvable case
>    (kept, and still proven as the required unparseable/BLOCK example) is a `dynamic` block,
>    whose emitted attributes depend on evaluating its own `for_each`.
>
> Final dogfood state: **15 of 16 real modules clean.** The one exception,
> `table-format-iceberg`, correctly and deliberately BLOCKS — it uses a real
> `dynamic "columns" { for_each = var.columns ... }` block, which is genuinely unresolvable
> statically and is exactly the agreed hard-fail case, not a bug. It needs restructuring (or an
> explicit reviewed exception) before it can be re-pinned under G2 — a real, disclosed
> consequence of the agreed design, not smoothed over.
>
> **A real infrastructure incident, found and fixed along the way, not swept under the rug:**
> the disk filled to 100% (1.7GB free out of 361GB) mid-session, crashing an unrelated
> full-suite run with a genuine `OSError: No space left on device` — traced to pytest's own tmp
> directory (`AppData\Local\Temp\pytest-of-shubh\`) growing to **65GB** from every real-terraform
> test re-downloading the same provider binaries into a fresh `tmp_path` every run, session after
> session, with no shared cache. Cleared (confirmed no pytest process running first), and fixed
> at the root: `tests/conftest.py` now sets `TF_PLUGIN_CACHE_DIR` via `os.environ.setdefault`
> (never overrides an operator's or CI's own setting) to a stable, gitignored
> `.agents/tf-plugin-cache/`; `ci.yml`'s `test` job gained the matching `actions/cache` step
> (mirroring the pattern `schema-watch.yml` already used). This is the same "confirmed the
> failure was environmental, not code" discipline as the earlier Terraform-version diagnosis —
> the stale clean-suite signal from before the crash was explicitly not trusted; the suite was
> re-run **foreground, to a real exit 0** after the fix (**482 passed, 0 failed** — the +9 over
> the previous 473 is exactly the fail-closed sweep's own new regression tests).
>
> **Phase 2 (G2) is closed.** `docs/g6_scope.md` (Phase 3 / G6 — SEC-*/COST-* rules migrating to
> OPA/Rego over plan JSON) is drafted and awaiting review; no implementation has started.
> Two small, decoupled CI-hygiene fixes landed in their own commits: the wheel-build job now
> installs `setuptools`/`wheel` explicitly for its `--no-isolation` build, and the Dockerfile's
> Terraform/AWS-CLI download gained `curl --retry` (testing the transient-download theory).
>
> **Correction, caught before it shipped as a false claim**: an earlier draft of this entry
> asserted the SHA256SUMS checksum step was silently no-op'ing when `grep` matched zero lines
> (piped straight into `sha256sum -c -`). Verified directly rather than left as an assumption:
> `sha256sum -c` on empty input already fails loudly (exit 1, "no properly formatted checksum
> lines found"), and combined with the script's `set -e`, the original code already aborted
> correctly on a zero-match grep. Not a 4th fail-open-ingestion instance — the checksum change
> that shipped is a legibility improvement (an explicit `test -s` before running sha256sum),
> not a security fix. Recorded here so the corrected version is what's on record, not the
> wrong first draft.

> **Follow-up (2026-07-12): CI reds closed — 8/8 green, first fully clean run this session —
> and two findings worth carrying forward.**
>
> **The wheel-packaging bug and the Dockerfile checksum bug are both closed**, in their own
> commits (`fix(packaging): databricks-workspace and networking-vpc missing from wheel`,
> `fix(docker): checksum was never functional, not merely misnamed`). Both real, both found
> only because a real CI run — not local dogfooding, not a self-audit — forced them out.
>
> **Finding worth its own line, not folded into "fixed two bugs": both shipped-artifact
> defects this session were pre-existing, silent, and already in the distributed product**,
> not newly introduced regressions. `databricks-workspace`/`networking-vpc` were absent from
> every wheel a real installer would have gotten since the day they were added — silent
> because the wheel-build job never got far enough (blocked earlier on the setuptools issue)
> to exercise the assertion that would have caught it. The Dockerfile's checksum step had
> **never once actually verified anything** since the file was first written — a filename
> mismatch made `sha256sum -c` fail closed on every single build with "FAILED open or read",
> which is why the image never shipped broken — but the *verification itself* had zero
> real-world coverage the entire time, not a narrow edge case. **The common thread: this
> session's gates (G2, G5, the fail-closed sweeps) were all under active scrutiny; the defects
> that actually reached the distributed artifact were sitting in the un-scrutinized
> packaging/supply-chain path** — `pyproject.toml`'s data-files list and the Dockerfile's own
> integrity check, neither of which any gate this session built was watching. Sixth instance
> of "a verifier that passes without verifying" this session (G5's classify(), G2's extractor,
> the schema-lint fail-closed sweep, the checksum no-op non-fix, the wheel omission, and now
> this) — and the first of the six that had never verified at all, rather than regressing from
> a working state.
>
> **Audit-chain lock (§6 item 11): now 4 clean Windows CI runs in a row**, zero failures.
> Still formally UNPROVEN — absence of a race across 4 runs is not proof the race is fixed —
> but the framing shifts: the evidence increasingly points to a **local Windows environment
> artifact** in how this was originally observed (this session's own machine/setup), not a
> **cross-platform defect** the CI matrix would independently reproduce. When this is next
> scheduled (still gated on positioning, still before any external reliance, still not now),
> the task is "reproduce locally, confirm it's environmental" — not "hunt a cross-platform
> race that 4 clean CI runs increasingly suggest doesn't exist on real CI hardware."
>
> **Correction (2026-07-12, later): the "4 clean runs" streak above is broken — do not carry the
> "likely environmental" framing forward as settled.** The CI run for the Phase 4 push (below)
> hit `test_append_is_safe_under_concurrent_writers`'s known `PermissionError(13, 'Permission
> denied')` on **both** windows-latest jobs in the same run (not one of two, both) — the
> strongest real-CI-hardware recurrence observed so far, not weaker evidence. Unrelated to
> Phase 3/4's own code (only this one pre-existing test failed; everything else, including every
> new Phase 4/G6 file, passed clean on all 4 non-Windows jobs plus wheel/docker). Not
> investigated further here — still gated on the same positioning decision, still not now — but
> the record needs to say "recurred, strongly, on real CI" rather than let the older "likely
> environmental" entry stand uncorrected.

> **Follow-up (2026-07-12): Phase 3 (G6) implemented to the approved `docs/g6_scope.md`,
> shadow mode only — evidence gathered against the proof bar, not yet closed.**
>
> `policy/g6/rules.rego` (all 8 rule IDs), `core/governance/rego_gate.py` (fail-closed OPA
> wrapper), wired into `plan_gate.py`'s `stage_plan()` alongside the existing regex scan.
> **Shadow only**: never blocks, never enforces — `BLOCKING_PREFIXES`/real enforcement is
> unchanged, exactly per the approved scope's condition to not retire the regex path or flip
> enforcement before this review.
>
> **Fail-closed sweep (proof-bar item 2), done before declaring anything closed, not after**:
> 34 tests in `tests/test_rego_gate.py` cover every case in the scope doc's §3 table, including
> a deliberate negative control proving `--strict-builtin-errors` is load-bearing (the exact
> same malformed-JSON input silently produces zero findings without the flag, hard-fails with
> it) — SEC-02's `json.unmarshal` risk is real, not decorative.
>
> **A real correction found by the sweep itself, not assumed correct from the scope doc's own
> table**: `resource_changes` being entirely ABSENT from plan JSON (not an empty list) is the
> normal shape for a data-source-only or genuine no-op plan — confirmed twice live against real
> `terraform show -json`. The original design blocked on this as `plan_malformed`, over-blocking
> a common, legitimate case. Fixed: only a *present-but-wrong-typed* `resource_changes` blocks
> now; absent means "nothing managed to check," not malformed.
>
> **Item 5 (unknown-value proof, proof-bar)**: a real, constructed plan — `aws_redshift_cluster.
> encrypted` derived from `length(aws_kms_key.k.key_id) > 0` on a KMS key created in the same
> plan — confirmed live (`after.encrypted: null`, `after_unknown.encrypted: true`) and asserted
> to route to BLOCK (`field_unresolved`), not a silent pass. Permanent regression test, not a
> one-off.
>
> **16-module parity pass (proof-bar item 1): 15/16 verified, NOT 16/16.** Of 16 modules, 11
> declare a G6-relevant type (the other 5 — `consumption-redshift-serverless`,
> `governance-observability`, `networking-vpc`, `schema-registry-glue`, `table-format-iceberg` —
> declare none, vacuous parity, matching the already-known zero-real-coverage finding for
> SEC-03/04/COST-02/03). Real `terraform plan` + `show -json` per module (dummy AWS/Databricks
> credentials, `aws_caller_identity` textually patched to a placeholder in 3 modules since it's
> used only for bucket-name uniqueness, irrelevant to every G6 rule's content — disclosed, not
> silent). Results:
> - 8 modules (`compaction-glue`, `compute-emr-serverless`, `compute-glue-etl`,
>   `dq-great-expectations`, `ingest-firehose`, `orchestrator-mwaa`, `speed-layer-kinesis`,
>   `query-athena`) plan clean, parity confirmed (zero findings both sides).
> - **`orchestrator-stepfunctions`: G6 behavior UNVERIFIED on this module, pending real
>   credentials — not folded into "parity done."** `aws_sfn_state_machine` triggers a real
>   AWS-side `ValidateStateMachineDefinition` API call at plan time that dummy credentials can't
>   satisfy, so this module could not be planned standalone at all. Logged in the same
>   disclosed-gap category as the Databricks live-apply item (§6 item 10) — a real, named,
>   carried-forward gap, not a passed check. **15 of 16 modules verified, 1 of 16 unverified.**
> - **`storage-medallion-s3`: a real Rego bug, found and fixed.** Rego false-positived SEC-01/
>   COST-01 on all three `for_each`-indexed buckets despite the module having genuinely correct
>   `aws_s3_bucket_public_access_block`/`aws_s3_bucket_lifecycle_configuration` siblings. Root
>   cause, confirmed against the real plan's `configuration` block: a `for_each` sibling's
>   `bucket = each.value.id` never resolves to the bucket's address inside `expressions.bucket.
>   references` (only the symbolic `each.value`); the real reference lives in a separate
>   `for_each_expression.references` field the code never read, and the expanded instance
>   address (`aws_s3_bucket.zone["bronze"]`) was never stripped to its base form before
>   comparing. Fixed in `policy/g6/rules.rego`, re-verified clean against the real plan,
>   locked down with a permanent regression test.
> - `databricks-workspace`: one true positive both sides (COST-01, a genuinely missing
>   lifecycle sibling on `aws_s3_bucket.root_storage_bucket`) and one genuine Rego-only finding
>   (SEC-02 on `aws_iam_role_policy.cross_account_role` — a real `Resource: "*"` statement in
>   Databricks' own required AWS cross-account policy, invisible to the old single global regex
>   since it can't attribute per-resource or see resolved JSON). Confirmed genuine, not a bug,
>   by reading the real resolved policy — matches the scope doc's anticipated SEC-02
>   resolved-JSON improvement exactly.
>
> **Follow-up (same day): Gap 1 (opa availability, proof-bar item 4) closed.** `opa` was
> genuinely absent from every CI workflow and the Dockerfile — confirmed the gate was inert
> there (`opa_not_found`) before fixing it, not assumed. Fixed with the identical discipline as
> the Terraform checksum fix: pinned version (1.18.2), verified against OPA's own real
> per-binary `.sha256` (confirmed live — `<hash>  <filename>` format, `sha256sum -c` native),
> fails loud on a mismatch or unsupported OS/arch, no silently-absent gate.
> - `.github/workflows/ci.yml`: a cross-platform (`uname`-dispatched) install step in the `test`
>   matrix job, followed by a dedicated proof step that runs `opa version` and calls
>   `rego_gate.evaluate()` against a real fixture, asserting `evaluation_failed is False` and
>   the expected SEC-01/COST-01 findings — not "the step didn't error," an actual verdict
>   assertion, same standard as "the checksum prints OK." Verified locally before pushing: the
>   positive case (`sha256sum -c` → `OK` against the live release) and the negative case (hash
>   corrupted → `FAILED`, script aborts under `set -euxo pipefail`) both proven directly: with
>   `opa` unavailable, `rego_gate.evaluate()` genuinely returns `opa_not_found` and the assertion
>   catches it — this is a real trap, not decorative.
> - `Dockerfile`: the same pinned+verified install added to the existing checksummed-download
>   block, `opa version` added to the build-time verification line alongside `terraform
>   version`/`aws --version`. Docker itself could not be run in this local session (no daemon
>   available) — the download+checksum logic was verified directly on the host with the exact
>   same commands (real `OK` on the real release, real `FAILED`+abort on a corrupted hash); the
>   actual image build is proven by the real CI `docker` job once pushed, not assumed from the
>   host-level check alone.
>
> **Proof-bar item 3 (shadow-mode divergence log reviewed across real runs)** stays open,
> correctly downstream of item 4: now that opa can actually run in CI, real `stage_plan()` calls
> will start accumulating genuine divergence entries. This item is satisfied by accumulating
> enough real runs to trust the retirement decision, not by a one-time check — stays open until
> that real stream exists, deliberately not rushed.
>
> **Not yet committed.** Per the approved scope's own condition, this is evidence for review —
> the regex path stays untouched and enforcing, G6 stays shadow-only, until this is reviewed.
>
> **Phase 3 (G6) CLOSED (same day)** — proof-bar item 4 (opa availability) fixed and proven on
> real CI across all 3 platforms (pinned + checksum-verified opa install, a dedicated CI step
> asserting a real `rego_gate.evaluate()` verdict, not just "opa version didn't error"). A real
> Windows-only bug surfaced by that same CI run, not before: `opa version` worked inside the
> install step but a POSIX-style path written to `$GITHUB_PATH` didn't translate for the next
> step's Python subprocess (`shutil.which` came back empty) — fixed with `cygpath -w`, verified
> on real Windows runners after the fix. G6 stays **shadow mode**: regex path is still the sole
> enforcer, nothing retired, nothing flipped. Item 3 (divergence log across real runs) stays
> open, now achievable since opa actually runs in CI.

> **Follow-up (same day): Phase 4 (G3/G4 — intent-spec + auto-generated assertions) built to the
> approved `docs/phase4_scope.md`, advisory-only from day one, per its own explicit condition.**
>
> **G4 consolidation (condition 1): migrate-now, not tracked-follow-up.** `core/governance/
> plan_reader.py` is the new shared, fail-closed Python-side plan-JSON reader; both pure-Python
> consumers (`destructive_change_gate.py`'s `classify()`, `architecture_model.py`'s
> `extract_resources()`/`module_dependencies()`) were migrated onto it in this same pass, each
> re-verified with its own full existing test suite (byte-for-byte unchanged behavior — G5's
> full 16-module baseline + fail-closed sweep, `test_architecture_model.py`, both clean).
> `rego_gate.py`/`policy/g6/rules.rego`'s Rego-side logic is **excluded, disclosed as a hard
> language boundary** (Rego cannot import a Python module), not a deferred migration — the
> shared reader ends up with 3 real consumers (the two migrated sites plus Phase 4's own new
> module), not "1 shared + 3 legacy."
>
> **Real bug caught before anything shipped, not during the sweep**: `_check_scoped_iam`'s first
> draft silently fell through to "satisfied" when an IAM policy's content was genuinely unknown
> until apply (`after_unknown.policy == True`, confirmed live against the demo blueprint's own
> generated Terraform, since its policies reference not-yet-created bucket ARNs) — the exact
> silent-unknown-passes-as-clean pattern this whole session exists to catch, found in this
> phase's own first draft before it was ever tested, not by the sweep. Fixed with a third,
> distinct finding kind (`control_unresolved`, mirroring G6's `field_unresolved`) that must never
> silently drop to "no finding."
>
> **Control-mapping table (condition 2), proven both directions for every one of the 6 mapped
> controls** (`tests/test_intent_assertions.py`, 33 tests): a clean-case fixture proving the
> check passes when satisfied, and a deliberately-broken fixture proving it fires when violated.
> The demo blueprint's own real generated Terraform (real `terraform plan`, dummy AWS
> credentials) supplied genuine, non-hypothetical evidence for most of these — **two real,
> previously invisible gaps surfaced**: the blueprint claims "CloudWatch alarms and log
> retention" but generates an alarm with no log_group at all, and claims "Budget and anomaly
> detection hooks" but generates only the budget, no `aws_ce_anomaly_*` resource. The 7th
> control ("Terraform plan hash approval before apply") is a process-level claim, not a
> plan-JSON property — correctly and loudly logged as `control_unmapped`, never silently passed.
>
> **Mock-harness shape question (condition 3), verified live, not assumed**: the existing
> 16-module `terraform test`/mock_provider baseline harness plans each module **standalone**, no
> `module.` wrapper at all — the wrong shape for a module-presence check entirely. Confirmed via
> a real `synthesizer.compose()` composition (dummy AWS credentials) that a genuine multi-module
> plan carries `module.<label>.*` addresses (hyphens become underscores) with a direct
> `module_address` field — module-presence proof (`test_real_composed_plan_module_presence_
> across_catalog`) uses a real 3-module composition (storage-medallion-s3, compaction-glue,
> query-athena), not the mock harness.
>
> **Fail-closed sweep (condition 4), before close**: the sweep itself caught that `check_
> controls`/`check_numerics` had no malformed-plan guard at all (only `check_module_presence`
> did) — a wrong-typed `resource_changes` would silently read as an empty plan rather than
> blocking the assertion pass. Fixed with a shared `_plan_malformed_finding()` guard used by
> every check function and by `evaluate()` itself (which checks plan validity once upfront,
> avoiding the same evaluation_failed finding three times over).
>
> **Advisory-only, non-blocking (condition 5) — confirmed, not just designed that way.**
> `check_module_presence` + `check_numerics` are wired into `plan_gate.py`'s `stage_plan()`
> (real production path); `check_controls` is fully built and tested but **deliberately NOT
> wired into `demo.py`'s blueprint path** — a real limitation discovered while wiring, not
> papered over: `demo.py`'s `synthetic_plan()` has no `configuration` key at all, so the two
> checks needing sibling-reference tracing would false-positive on every demo run regardless of
> real correctness. Full end-to-end smoke test: a real composed plan with a deliberately
> mismatched `architecture_decision.json` (one selected module never composed) and an
> unsatisfied budget declaration — both real findings fired correctly, logged at the top level
> of the audit-chain entry (`intent_assertions`), and `stage_plan()` still returned `True` —
> confirmed nothing blocks.
>
> Not yet committed at the time this entry was written — same review-before-close discipline as
> every prior phase this session.
>
> **Two tracked follow-ups from Phase 4's own findings, not dropped:**
> 1. **False-claim blueprint controls (§6, new item below)**: the demo blueprint's `controls[]`
>    claims "CloudWatch alarms and log retention" and "Budget and anomaly detection hooks," but
>    the generated Terraform only builds half of each pair (no log_group; no `aws_ce_anomaly_*`).
>    A **Phase 6 generation-fidelity input** — fix generation to honor the control, or remove the
>    control from the blueprint. Do not leave "we claim it, we don't build it" standing.
> 2. **Audit-chain lock (§6 item 11)** — see the follow-up entry immediately below: reclassified
>    from "probably a local artifact" to a confirmed cross-platform Windows race (both
>    windows-latest CI jobs hit it in the same run), root-caused, and fixed. This JUMPED AHEAD of
>    Phase 5 — a confirmed defect in a compliance product's tamper-evidence layer outranks
>    building the next gate.

> **Follow-up (2026-07-13): audit-chain lock (§6 item 11) — root-caused and fixed for real, not
> re-guessed.** Full writeup: `docs/audit_chain_lock_fix_scope.md`; final status in §6 item 11
> itself. Summary: `os.open(O_CREAT|O_EXCL)` racing a concurrent `os.remove()` of the same lock
> filename returns `PermissionError(13)` on Windows instead of `FileExistsError` (NTFS has no
> POSIX-equivalent atomic-unlink-while-open guarantee) — reproduced directly (852/4800 cycles in
> a tight repro) before writing any fix. Fixed by removing the delete-recreate cycle entirely:
> the lock sidecar is created once, never deleted; acquire/release toggle an OS-native advisory
> region lock (`fcntl.flock`/`msvcrt.locking`) instead. A broad `except PermissionError` was
> explicitly rejected — would trade a fail-loud crash for a fail-open hang on a genuine
> permission denial. Proof before closing: the same 4800-cycle repro now shows zero exceptions;
> 25+10 consecutive clean runs of the real test; a new timestamp-interval test proving actual
> thread serialization (zero overlap), not just "no exception raised"; a negative control
> proving a genuinely un-openable path still fails in <1s, not after a 10s hang; the
> crashed-writer test rewritten to hold a real live lock (a bare stale file is now confirmed to
> need no manual cleanup — the OS releases advisory locks automatically on process death).

> **Follow-up (2026-07-13): Phase 5 (G9, ephemeral apply) built to the approved
> `docs/phase5_scope.md`, up to a real, disclosed blocker — not fully closeable this session.**
>
> **Item 0 (tool decision) resolved: LocalStack, paid Base plan** — reframed correctly on
> review as a fidelity question (a subtly-too-permissive free/new emulator produces false
> greens, worse than no gate), not a cost one. This creates a real, structural blocker: a paid
> LocalStack account needs a `LOCALSTACK_AUTH_TOKEN` and a payment method, neither obtainable by
> an agent. Everything buildable/testable without a live paid instance was built and proven;
> everything that genuinely needs one is named explicitly, not silently skipped.
>
> **Built and tested**: `core/governance/ephemeral_apply.py` — resource-type allowlist (all 41
> real AWS types this repo's modules declare, every one honestly `unverified` right now, same
> shape as `destructive_change_gate.py`'s `STATEFUL_RESOURCE_TYPES`), the dummy-credential-only
> LocalStack provider-override generator (the sole credential path, never ambient), the full
> fail-closed apply/destroy orchestration (33 tests, `tests/test_ephemeral_apply.py`), and a
> `coverage` (full/partial/none) classifier composing visibly with G5's `reduced_assurance`
> (`compose_with_g5()` — a "none"/"partial" verdict is structurally incapable of reading as
> "passed").
>
> **A real bug caught before this shipped, not during the sweep**: the first draft ran the
> read-only classification plan *before* writing the LocalStack provider override, meaning that
> first `terraform plan` call was not isolated from ambient credentials at all — exactly the
> violation condition 5 (structural endpoint isolation) exists to prevent, even though `plan`
> itself never mutates anything. Caught by an actual end-to-end smoke test that produced a
> confusing `teardown_failed` verdict traced back to an orphaned provider-plugin process holding
> the state lock. Fixed: the override is now written first, before any terraform command runs.
>
> **Real, live proof obtained without needing a LocalStack account at all**: the "emulator
> unreachable" fail-closed path (proof-bar item, section 4's table) doesn't need a paid or even
> a running LocalStack to prove — a genuinely unreachable endpoint is real regardless. Confirmed
> live: a real `terraform apply` against `http://localhost:4566` with nothing listening blocks
> in bounded time (`apply_timeout`), never hangs forever, never silently proceeds. Also verified
> live (not assumed): the real `terraform apply -json` event shape (`apply_start`/
> `apply_complete`/`apply_errored`), including a genuinely-observed case where a crashed provider
> plugin dumps a non-JSON Go panic stack trace into the output stream — locked in as the
> `apply_result_malformed` regression test.
>
> **Blocked on the token, named explicitly, not silently assumed done**:
> - Proof-bar item 1 (per-resource-type coverage, all 41 types, both directions including the
>   negative-fidelity check on IAM/KMS/S3 policies) — cannot run without a live paid instance.
>   `RESOURCE_TYPE_ALLOWLIST` stays honestly all-`False` until this happens for real.
> - Proof-bar item 3 (real CI run) — `.github/workflows/ephemeral-apply.yml` is built (Ubuntu-
>   only, matches the `docker` job's own placement; official `LocalStack/setup-localstack`
>   action) but skips loudly (`::warning::`, not a silent pass) when `LOCALSTACK_AUTH_TOKEN` is
>   absent, which it is until the user configures it as a repo secret.
> - Proof-bar item 4 (teardown reliability stress test) and item 5 (`coverage` field on a real
>   mixed AWS+Databricks plan applied against LocalStack) — both need the same live instance.
>
> **Not closed.** Per the approved scope's own condition, Phase 5 stays open until the token is
> provisioned and the remaining proof-bar items actually run for real — this is evidence of
> what's built and correctly gated, not a claim that G9 is verified.
>
> **Follow-up (same day): a real CI regression caught and fixed immediately, not left standing.**
> The push above broke both windows-latest jobs for real — not the pre-existing audit-chain
> flake, a genuinely new one: the real unreachable-endpoint test leaves an orphaned
> `terraform-provider-aws` process (Python's subprocess timeout kills the direct `terraform.exe`
> child but not its own provider-plugin grandchild on Windows), which holds a Windows-only file
> lock on the *shared* `TF_PLUGIN_CACHE_DIR`, breaking unrelated tests
> (`test_rego_gate.py`/`test_schema_lint.py`/`test_schema_watch.py`) in the same job.
> macos-latest and ubuntu-latest both ran the same test clean in the same run — POSIX's
> lock/unlink semantics for a dead-but-not-yet-reaped process don't have this gap. Fixed by
> skipping that one subprocess-heavy real test on `win32` specifically (not Linux/macOS) — G9
> is structurally Ubuntu-only anyway, so this is a consistent scope narrowing, not a workaround.
> Confirmed on real CI: all 8 jobs green after the fix, both previously-failing Windows jobs
> included.
>
> **Pattern flag, not just a one-off**: this is the SECOND Windows file-lock-on-a-shared-resource
> bug this session, not an isolated fluke. The first was the audit-chain lock (§6 item 11):
> `os.remove()` racing a concurrent `os.open(O_CREAT|O_EXCL)` on the same lock filename. This one:
> a killed process's surviving grandchild holding a lock on a shared cached file. Different
> mechanism, same root shape — Windows' file-handle/lock semantics under concurrency (whether
> from threads racing a lock file, or a subprocess tree outliving its parent) do not behave like
> POSIX's, and this repo's test suite spawns real subprocesses and shares real files (the
> plugin cache, lock sidecars) constantly. **Standing check for every future test that spawns a
> subprocess or touches a shared file: does a timeout/kill path leave anything alive holding a
> handle on something another test also touches?** Worth a real audit of this pattern specifically
> if a third instance shows up, rather than fixing each occurrence as a one-off surprise.

> **Follow-up (2026-07-13): Phase 5 (G9) advanced on two fronts run in parallel — sandbox
> isolation (security floor, settled first) and emulator fidelity (optimization, inside that
> sandbox) — plus G7 (tfsec→Trivy) and Phase 4's HANDOFF debt cleared alongside, per the user's
> explicit "parallelize what's decoupled" instruction. Full detail: `docs/phase5_scope.md`
> sections 7–9 (pushed and re-pushed as real evidence accumulated, not drafted once and left).
>
> **Decision 1 (isolation) — real proof, not a design on paper.** G9 executes AI-generated
> infrastructure; both free emulators get real-Docker fidelity via the host's own Docker socket
> mounted as root, and — a more fundamental vector found while scoping this, independent of any
> emulator — Terraform's own `local-exec`/`remote-exec` provisioners already execute arbitrary
> shell commands directly on whatever runs `terraform apply`, meaning the isolation boundary has
> to wrap the *whole* apply process, not just the emulator's container. Verified live (a
> temporary scratch CI workflow, pushed/iterated/removed): `/dev/kvm` is genuinely available on
> this repo's real, free-tier `ubuntu-latest` runners (not gated behind a paid tier, contrary to
> some secondhand reports); a real Firecracker microVM boots from the official release binary +
> official CI kernel/rootfs artifacts and is confirmed as a genuinely separate machine (own
> kernel, own hostname, ~7s to SSH-ready). **A real escape gap was found on the first hostile-
> escape attempt, not assumed safe**: filesystem isolation was total from the start, but the
> guest could reach a TCP listener bound to the host's own tap-gateway address — the NAT/forward
> rules governed traffic *through* the host, not traffic *to* the host itself. Fixed
> (`iptables -A INPUT -i tap0 -j DROP`, with `ESTABLISHED,RELATED` allowed first so host-
> initiated management traffic still works) and re-verified closed (the same attempt now times
> out, no reachability). This proves the design is buildable and provably closeable on real
> infrastructure; wiring the full `ephemeral_apply.py` pipeline to actually run inside this
> boundary is real, disclosed remaining work, not yet built.
>
> **Decision 2 (fidelity) — real gauntlet, both directions, both free emulators, real CI.**
> MiniStack and Floci both need no account/token, so both were run for real (real Docker
> containers, real `terraform apply`). A real bug in the gauntlet scripts themselves was found
> and fixed first: `set -uxo pipefail` does not clear GitHub Actions' own inherited `-e`, so a
> genuinely-failing apply killed the whole job before result-logging ran (fixed with explicit
> `if/else` around each command, exempt from `errexit`). **Mandatory finding: neither MiniStack
> nor Floci currently passes negative fidelity for any of the three security-critical types this
> repo's modules use** — `aws_iam_role` (malformed trust-policy principal ARN), `aws_kms_key`
> (key policy missing any root/admin grant), and `aws_s3_bucket_policy` (policy naming the wrong
> bucket's ARN) are all **incorrectly accepted** by both emulators, when real AWS is documented
> to reject each. Per G9's own fail-closed design, a plan touching any of these three types now
> correctly BLOCKS (`negative_fidelity_unverified`) on both emulators — the gauntlet did exactly
> what it exists to do, catching a real false-green risk before either emulator was trusted, not
> a setback. Separately: MiniStack's own historical STS ARN-validation bug (#980) is confirmed
> genuinely fixed on the current version (directly relevant to this repo's Databricks cross-
> account trust); Floci's two historical crash bugs (#871 `aws_instance`, #177
> `aws_cognito_user_pool`) no longer crash but the underlying resource types still fail
> differently today — informational only, since neither type is in this repo's real catalog.
> **This reinforces, not undermines, the earlier LocalStack-paid tool decision** — both free
> alternatives just failed the mandatory bar for real; whether LocalStack's paid tier would pass
> is still the open, unanswered question a provisioned account would resolve.
>
> `core/governance/ephemeral_apply.py` now supports a pluggable `emulator=` parameter
> (`localstack`/`ministack`/`floci`, `SUPPORTED_EMULATORS`), and `RESOURCE_TYPE_ALLOWLIST`
> restructured to a real per-`(type, emulator)` matrix carrying the results above — LocalStack's
> own column stays honestly unverified (no token provisioned). New fail-closed cases
> (`unsupported_emulator`, `negative_fidelity_unverified`) each have a real regression test.
>
> **G7 (tfsec → Trivy) closed in parallel**, self-contained, different files: tfsec was archived
> upstream in Trivy's favor, so `optimize_analyzer.py`'s external-scanner path would have
> silently stopped receiving new checks — the same "quietly stops verifying" shape this session
> keeps finding, just for a scanner. Trivy's real JSON shape (`Results[].Misconfigurations[]`)
> was verified live against a real module in this repo (not assumed from docs), including the
> real, confirmed behavior that `trivy config` exits non-zero (32) on genuine findings — its
> normal "findings present" signal, correctly not treated as a scanner failure.
>
> Not closed: Phase 5 (G9) stays open. The isolation boundary is proven feasible but not yet
> wired into the shipped pipeline; the LocalStack column of the fidelity matrix stays unverified
> pending a provisioned account; both are real, named, remaining work, not silently assumed done.

> **Follow-up (2026-07-13): Phase 5 (G9) — isolation boundary wired into the shipped mechanism
> for real, on real CI. G9 CLOSED.** Full detail: `docs/phase5_scope.md` section 8.7.
>
> **A routing correction, settled before the wiring work started, not after**: the section 7.5
> negative-fidelity finding (neither free emulator rejects malformed IAM/KMS/S3 policies real AWS
> rejects) was being answered at the wrong layer. An emulator checks *validity* (would real AWS
> accept this) — never *safety* (is this dangerous). A perfectly faithful emulator would apply a
> wide-open `Principal: "*"` policy without complaint, because real AWS does too. That security
> question belongs to G6 (OPA over plan JSON, which already reads the resolved policy for free,
> no emulator needed) — queued as G6's next real work (extend SEC-* Rego to hard-enforce IAM/KMS/
> S3 content, shadow-then-flip against all 16 modules, zero false positives both directions before
> enforcing), explicitly ordered *after* this isolation-wiring close, not instead of it. This does
> not touch `RESOURCE_TYPE_ALLOWLIST`'s `negative_fidelity_unverified` block, which stays exactly
> as-is — G6 taking the security question doesn't answer G9's own disclosed apply-time-fidelity
> gap; the two stay named separately. `docs/phase5_scope.md` section 10 has the full writeup.
>
> **The wiring itself, real and green**: `.github/workflows/ephemeral-apply.yml` gained an
> `isolation-boundary` job that boots the same proven Firecracker microVM (section 8.6), installs
> docker/terraform/python **entirely inside the guest**, and calls the actual shipped
> `run_ephemeral_apply()` — not a re-implementation — against a real MiniStack container for a
> normal fixture (real `evaluation_failed: false`, real `aws_resources_applied`) and against a
> **hostile fixture**: a real, allowlisted resource carrying a `local-exec` provisioner that
> attempts the proven escape vector (a raw connection to the host's own tap-gateway address). The
> load-bearing check runs on the **host** side, independent of Terraform's own exit code: a canary
> listener armed before the hostile run, grepped after. Real result: **not reached**. This is the
> first time the escape test has run against the real pipeline instead of a bare SSH-driven stand-
> in — proof-bar item 8.5 is now satisfied for the shipped mechanism, not just the design.
>
> **Five more real bugs, each found by an actual CI failure, not anticipated**: a checksum-
> filename mismatch on the pinned Firecracker download; no DNS resolver in the guest (NAT routes
> IPs, not names); Firecracker's own 128MiB default RAM silently hanging the guest mid-`apt-get`
> (fixed with an explicit 2 vCPU/2048MiB `/machine-config`); the quickstart rootfs having no dpkg
> database at all plus lacking nftables support (fixed: seed an empty dpkg status, switch
> `iptables` to the legacy backend); and Docker's own bridge-networking path needing an `iptables
> raw`-table rule this kernel doesn't support either (fixed: `--network host`, since this
> single-tenant guest needs no bridge/NAT path at all). **The one worth flagging on its own**:
> every "grow the disk" fix (in-place resize2fs — rejected, it corrupted the guest filesystem; a
> redirected scratch disk; a bigger root, three separate attempts) kept failing at ever-larger
> sizes because the actual bug had nothing to do with size — Firecracker's own downloaded
> artifacts lived inside `$GITHUB_WORKSPACE` and were being swept into the guest by the "copy the
> repo" step's own `tar` command; each size increase just grew the accidentally-copied file.
> Fixed by moving every Firecracker artifact to `$RUNNER_TEMP/fc`, structurally outside the
> checked-out repo — the actual fix was isolation of build artifacts, not more disk.
>
> Also fixed in the same pass: `run_ephemeral_apply()` now names its `emulator` in every returned
> verdict (section 7.3's own requirement, previously unimplemented — a real gap, not a rewrite),
> and the pre-existing LocalStack smoke test's allowlist-patch line was updated for the `_entry()`
> dict shape (would have crashed the moment that job next ran for real). New regression test
> (`test_every_verdict_names_its_emulator`) locks the emulator-naming fix down; 37/37 tests green.
>
> **Phase 5 (G9) is now genuinely CLOSED**: both proof-bar items ranked above fidelity — the
> isolation boundary, and its wiring into the actual shipped mechanism — are real, not designed on
> paper. Still honestly open, disclosed, not silently dropped: LocalStack's own fidelity column
> (item 0) pending a provisioned paid account, and the G6 IAM/KMS/S3 security-enforcement work
> queued right behind this close.

> **Follow-up (2026-07-14): G6 extended for IAM/KMS/S3 security CONTENT (option (c)) — built to
> `docs/g6_iam_extension_scope.md`, proven in shadow with zero false positives, NOT enforcing.**
>
> **The routing correction this executes**: the G9 emulator gauntlet's negative-fidelity finding
> (neither free emulator rejects a malformed IAM/KMS/S3 policy real AWS would) was answering a
> *validity* question, never a *safety* one — a perfectly faithful emulator would apply a
> wide-open `Principal: "*"` policy without complaint, because real AWS does too. That security
> question belongs to G6, which reads the fully resolved plan JSON for free, no emulator needed.
>
> **What was built**: `policy/g6/rules.rego` — SEC-02 extended to also flag `Action == "*"` (not
> only the pre-existing `Resource == "*"`); SEC-05 extended to evaluate `aws_iam_role.assume_
> role_policy` set directly as raw JSON (most of this repo's own modules write trust policies
> this way, not via `data.aws_iam_policy_document`, confirmed by grep before scoping); two new
> rules, SEC-06 (KMS key policy wide open) and SEC-07 (S3 bucket policy allows public access).
>
> **The two verify-first items review required before coding, resolved with real evidence**:
> (1) same-account-vs-cross-account detection for SEC-05 was considered via `data.aws_caller_
> identity.current.account_id` and confirmed, live, to fail — a genuine STS call that errors
> under this repo's own dummy-credential testing (and would couple every real customer's plan to
> a live STS call succeeding just to run a governance check) — so SEC-05 falls back to literal-
> ARN matching instead, the documented fallback. (2) SEC-02's unconditional `Action == "*"` fire
> on identity policies was left for the 16-module parity pass to confirm, not asserted safe in
> advance — zero real modules tripped it.
>
> **The load-bearing empirical finding, confirmed exactly as predicted before any code was
> written** (`docs/g6_iam_extension_scope.md` section 2): a real `terraform plan` showed
> `aws_kms_key.policy` (schema `computed = true`; `storage-medallion-s3` doesn't set it, the
> common real pattern) and `aws_s3_bucket_policy.policy` (whenever it interpolates its own
> bucket's ARN — the majority real pattern for a bucket+policy created together) both resolve
> as `after_unknown: true`. Routing unknown → BLOCK (`field_unresolved`) is the only correct
> answer, and this session could only know it was the *dominant* real-world outcome — not a rare
> edge case — by testing a real plan, not from provider docs. Confirmed against the real
> 16-module catalog: `storage-medallion-s3` produced exactly the predicted SEC-06
> `field_unresolved`; `databricks-workspace` produced exactly the predicted SEC-07
> `field_unresolved` on its bucket+policy pair created together. SEC-07 proven **both ways** in
> one real integration test, per explicit review instruction: a fresh-create policy → `field_
> unresolved`; a policy against a bucket referenced by literal name (already-exists pattern) →
> a real `standard` finding, proving the "real verdict" path genuinely fires, not just "didn't
> block."
>
> **Zero-FP proof, all 16 real modules, per-type where declared**: 7 of 9 `aws_iam_role`-
> declaring modules planned clean with zero findings on any extended/new rule.
> `databricks-workspace`'s one SEC-02 finding is confirmed the **pre-existing**, already-known
> `Resource == "*"` finding from Phase 3 (verified by its exact description text), not a new
> false positive from this extension. `orchestrator-stepfunctions` stays **unverified**, same
> pre-existing disclosed gap as Phase 3 (`aws_sfn_state_machine` triggers a real AWS validation
> API call at plan time dummy credentials can't satisfy) — not a new gap this extension caused.
>
> **A real bug found running this, not designing it, fixed on the spot**: `plan_gate.py`'s
> `G6_RULE_IDS` is a fixed tuple `_g6_shadow_eval()`'s divergence computation iterates over.
> Leaving SEC-06/SEC-07 out of it (an easy thing to forget when adding a rule) would have meant a
> real, confirmed violation for either rule was silently absent from both the divergence report
> and the audit chain — while the *uncertain* `field_unresolved` case (a separate, unfiltered
> list) would still have surfaced. Exactly backwards from what shadow mode exists to guarantee:
> the confirmed-dangerous case invisible, the merely-uncertain one visible. Fixed (`G6_RULE_IDS`
> now includes both), locked in with a new regression test that constructs a real wide-open KMS
> policy and asserts it actually appears in the divergence report.
>
> **Proven in shadow. NOT enforcing — kept deliberately separate, per explicit review
> instruction.** `rego_gate.evaluate()` was already the sole, already-shadow-only call site
> before this addition; these rules flow into the exact same non-blocking path automatically, no
> new wiring. The all-of-G6 enforcement flip (`docs/g6_scope.md`'s own still-open item 3) remains
> a single, separate decision covering every G6 rule at once — this closes as "proven-in-shadow
> with zero false positives," not "G6 now enforces."
>
> **The disclosure this closure's own done-condition requires, stated as two separate facts,
> neither implying the other**: G6 (once eventually flipped to enforcing — still undecided)
> would enforce IAM/KMS/S3 policy *security content*, statically, over resolved plan JSON. It
> does **not** verify apply-time IAM *interaction* — ARN validity, assumability, resource-
> creation-ordering effects on a policy's own references — which remains G9's own disclosed,
> open gap (`negative_fidelity_unverified` for these same three types, pending a provisioned
> LocalStack account). Neither fact closes the other.
>
> 65 tests in `tests/test_rego_gate.py` (50) + `tests/test_plan_gate.py` (33, includes the new
> `G6_RULE_IDS` regression) combined with the pre-existing `test_intent_assertions.py` (31) and
> `test_destructive_change_gate.py` (36) all pass locally against the real `opa`/`terraform`
> binaries. **Phase 6 (catalog teardown) still not started, per standing instruction.**
>
> **Pattern flag, worst variant yet, not just a one-off (independent review, 2026-07-14).** The
> `G6_RULE_IDS` bug above is the same "a verifier that passes without verifying" shape this
> session has hit five times before (G5's `classify()`, G2's extractor, the schema-lint
> fail-closed sweep, the Dockerfile checksum non-fix, the wheel-packaging omission) — but this is
> the sharpest version: the rule did not fail to fire, and it did not fail to detect a real
> violation. **It fired correctly, produced a correct finding, and that finding was then
> silently discarded before it ever reached the divergence report or the audit chain** — a
> verifier succeeding and its result being thrown away one hop downstream, not a verifier that
> never ran. **Standing checklist item, binding on every future G6 rule addition, not advisory**:
> before considering any new rule ID done, trace it all the way to the audit chain — confirm a
> constructed real violation for that rule ID actually appears in `_g6_shadow_eval()`'s
> `divergence` output (or whatever the equivalent reporting path is at the time), not just that
> `rego_gate.evaluate()` returns the finding. "The rule fires" and "the finding is recorded" are
> two different claims, and this session now has direct proof they can silently diverge.
>
> **Known recurring session hazard, flagged so it isn't rediscovered as a surprise**: this
> repo's real-terraform-heavy testing (this session's own parity passes, the `pytest-of-shubh`
> tmp tree, and Terraform's own per-invocation provider-binary extraction into the OS Temp root)
> has now filled the local Windows dev machine's C: drive to **0 bytes free** at least twice this
> session — once documented in the audit-chain-lock era (`pytest-of-shubh` alone reaching 65GB),
> once during this G6 extension (a combination of `pytest-of-shubh` regrowth plus 400+ stray
> `terraform-provider*` binaries directly in Temp root, ~46GB, plus this session's own ad hoc
> parity-test scratch directories bypassing the shared plugin cache). `tests/conftest.py`'s
> `TF_PLUGIN_CACHE_DIR` mitigation only caches the *downloaded* provider package — it does not
> stop Terraform's own per-run extraction of the provider binary into Temp root. **Before any
> future terraform-plan-heavy session (a new gauntlet, a new parity pass, a new module sweep):
> check `df -h` first**, and if low, clear `pytest-of-shubh` and stray `terraform-provider*`
> files in Temp root — both confirmed safe, disposable, and the actual recurring culprits, not a
> one-off.

> **Follow-up (2026-07-14): two decisions explicitly recorded as open, not Phase 6, not left to
> drift.**
>
> 1. **The all-of-G6 enforcement flip.** Every G6 rule — the original SEC-01/COST-01/SEC-03/
>    SEC-04/COST-02/COST-03/SEC-02/SEC-05 set from Phase 3, and this round's SEC-02/SEC-05
>    extensions plus SEC-06/SEC-07 — stays shadow-only. Flipping any of it to enforcing is a
>    single, still-undecided, later decision (`docs/g6_scope.md` section 2, item 3 of that
>    scope's own proof bar) that needs the accumulated real shadow-divergence log as its
>    evidence base, not a per-rule decision made piecemeal as rules are added.
> 2. **G9's LocalStack fidelity column, and the IAM/KMS/S3 apply-fidelity gap specifically.**
>    `RESOURCE_TYPE_ALLOWLIST`'s LocalStack entries stay unverified; `aws_iam_role`/`aws_kms_key`/
>    `aws_s3_bucket_policy` stay `negative_fidelity_unverified` on both free emulators. This is a
>    **permanent disclosed limitation unless the paid-LocalStack posture changes** — resolvable
>    only by provisioning the paid account this session deliberately chose not to buy (`docs/
>    phase5_scope.md` item 0), not by any further code in this repo.

> **Follow-up (2026-07-14): Phase 6 scoped — the generation pipeline + conditional catalog
> teardown — and its own key question answered honestly, not glossed over. Full detail: `docs/
> phase6_scope.md`.**
>
> Read directly against `synthesizer.py`/`modules.py`/`module_provenance.py`/`architecture_
> decision.py`, not the original plan from memory: **today's "generation" is selection and
> verbatim copy of pre-vetted, human-authored modules, not authoring.** `compose()` does
> `shutil.copytree` on already-pinned files; no HCL is generated anywhere in the current path.
> `match_modules()`'s keyword scorer is real, reusable code, but as a final-selection mechanism
> today, not a generative one — it's the thing that gets repurposed into retrieval-for-grounding
> once real authoring exists, not discarded.
>
> **The key question — are the built gates (G1/G2/G5/G6/G9 + advisory Phase 4) sufficient for
> the autonomy boundary once generation moves past the fixed catalog — got a real answer: no,
> and the reason is concrete, not a hedge.** `destructive_change_gate.classify()`'s autonomy
> boundary is **fail-open by construction**: a resource type absent from `STATEFUL_RESOURCE_
> TYPES`/`IAM_RESOURCE_TYPES` (explicitly scoped, per the module's own prior docstring, to "what
> MinusOps' own 16 modules can actually produce today") produces zero findings and auto-ships if
> create-only. Since generation-time authoring's entire purpose is producing resource types
> outside today's 41, this is the one gate standing between "generated" and "auto-shipped to
> real AWS," and it would silently wave through the first novel stateful/sensitive resource type
> it met. G6 compounds this (rules enumerated per named resource type, and shadow-only
> regardless of coverage). G9 is fail-closed and well-designed but confirmed, by direct grep,
> disconnected from the real flow entirely — nothing calls `ephemeral_apply.py` outside its own
> CI job. Phase 4 and G1 are not safety gates and were never meant to be (intent-fidelity and
> type-system validity, respectively). **This finding was ratified as the single most important
> finding of the project** — the ordering invariant that kept catalog teardown last caught this
> on paper instead of in production, exactly what it was for.
>
> Phase 6 was split on review: the G5 fail-open fix becomes its own standalone prerequisite
> ("Step 0"), closed and proven before any authoring work starts — see the next entry. Teardown
> stays the literal last step, gated on a concrete regression-baseline proof bar (`docs/
> phase6_scope.md` section 5), not "the new pipeline seems to work."

> **Follow-up (2026-07-14): G5 Step 0 — the autonomy boundary inverted from fail-open to
> fail-closed on unknown resource type — scoped, built, and proven standalone against the real
> 16-module catalog. Full detail: `docs/g5_autonomy_boundary_scope.md`.**
>
> **Design decision, evaluated against real evidence, not asserted**: a reviewed allowlist of
> types *confirmed safe* to auto-ship (`AUTO_SHIP_ELIGIBLE_TYPES`, the same shape as
> `ephemeral_apply.py`'s `RESOURCE_TYPE_ALLOWLIST`), not a shape/name heuristic. Settled by a
> real case this session's own cross-reference found, not a hypothetical: `aws_s3_bucket_policy`
> was in neither `STATEFUL_RESOURCE_TYPES` nor `IAM_RESOURCE_TYPES` — its schema is a single
> opaque policy string with no stateful shape at all, yet its *content* is exactly what this
> session's own G6 SEC-07 rule exists to catch (a bare `Principal: "*"` grants public access). A
> heuristic keyed on type name/schema shape would very plausibly miss it; an explicit review
> does not, because nobody had reviewed it as safe.
>
> **The real, deliberate 30/2-then-31/2 review, not a wholesale migration**: of the 41 real AWS
> resource types, 9 were already correctly staged (`STATEFUL_RESOURCE_TYPES`/`IAM_RESOURCE_
> TYPES`, unchanged). Of the remaining 32, two were explicitly excluded with recorded reasoning
> rather than migrated in: `aws_s3_bucket_policy` (above), and **`aws_default_security_group`**
> — decided, not defaulted, per explicit instruction: confirmed live against this repo's own
> `modules/networking-vpc/main.tf` that even the *correct*, intended configuration here sets
> `egress { cidr_blocks = ["0.0.0.0/0"] }` — an unrestricted CIDR block present in real, sanctioned
> usage, not a hypothetical misconfiguration, and the classifier (type + action only) has no way
> to distinguish that from a hypothetical wide-open *ingress* rule. Asymmetric downside decided
> it: staging a genuine hardening change costs one glance; auto-shipping the one that opens
> inbound to the world is the exact failure this fix exists to prevent. A new, distinct reason,
> `reviewed_unsafe_resource_type`, was added (a real code improvement the build itself
> surfaced) so an audit-chain reader can tell "reviewed and rejected" apart from "never
> reviewed" (`unreviewed_resource_type`) and from the pre-existing known-dangerous categories.
>
> **Config-dependent entries flagged for Phase 6 Step 1, not resolved now, as requested**: seven
> types (`aws_glue_job`, `aws_kinesisanalyticsv2_application`, `aws_sfn_state_machine` — each
> carries executable logic of its own; `aws_redshiftserverless_workgroup.publicly_accessible`,
> `aws_subnet.map_public_ip_on_launch`, `aws_s3_object.acl` — each a real, schema-verified
> attribute that flips public exposure without changing resource type) are reviewed safe *in
> this repo's current real configurations*, not safe *by type* independent of configuration —
> marked inline in `destructive_change_gate.py` as `# CONFIG-DEPENDENT` for re-examination once
> generation can produce novel configurations of these same types.
>
> **Three real bugs, all caught by the proof itself, not the design** (the exact discipline this
> whole session runs on): (1) `random_id` — this repo's own pre-existing action-shape tests use
> it as a zero-cloud-footprint stand-in; never reviewed, it broke an existing green test on the
> first run, fixed by reviewing and adding it with explicit test-utility reasoning. (2)
> `aws_route_table` — reviewed safe in the scope doc's own written reasoning, but only `aws_
> route_table_association` made it into the actual frozenset; a real transcription gap, caught
> immediately by `networking-vpc`'s own real baseline plan failing the new regression test, not
> a hypothetical. (3) Databricks double-flagging — `databricks_mws_credentials` (absent from
> `STATEFUL_RESOURCE_TYPES`) fell through to `unreviewed_resource_type` on the first full run;
> this scope was explicitly AWS-only (matching G9's own AWS-only boundary), and every
> `databricks_*` type is already, unconditionally, never autonomous-eligible via the pre-existing
> `reduced_assurance` mechanism — fixed by skipping the new checks for any Databricks type
> entirely, not silently declaring Databricks resource-type review done by an AWS-only fix.
>
> **Both-direction proof, complete and real**: `aws_dynamodb_table` (genuinely stateful, never
> declared anywhere in this repo's real catalog, confirmed by direct grep) classified
> `autonomous_eligible=True` on the *unmodified* classifier — captured as real, executed
> evidence before any code changed, not just argued — and `False` with reason
> `unreviewed_resource_type` after. A second, different novel type (`aws_secretsmanager_secret`)
> confirms this is a real default, not a special case for one hardcoded example. The real
> 16-module baseline (`test_every_current_module_plans_as_create_only`), extended with an
> assertion that no real module's real plan produces an `unreviewed_resource_type` finding, ran
> clean for all 16 modules after the three fixes above (in batches, due to this session's own
> environment constraints — see the disk-hazard note below, not a scoping shortcut).
> `test_destructive_change_gate.py`: 42 tests total, all passing; `test_plan_gate.py` (33,
> downstream consumer of `classify()`) unaffected, all passing.
>
> **G5 Step 0 is CLOSED.** Phase 6 Step 1 (the authoring pipeline) does not start until this
> entry's own reviewer sign-off; this fix does not touch G6's shadow status or its own separate,
> still-open enforcement-flip decision.
>
> **Correction, caught by real CI, not by this session's own local testing — a fourth real bug,
> and a real process gap, not swept under the "three bugs" count above.** The first push of this
> fix went green locally (`test_destructive_change_gate.py`, `test_plan_gate.py`) but failed on
> **every one of the 6 real CI test-matrix jobs** (ubuntu/macos/windows × py3.10/3.12):
> `tests/test_gate_e2e.py`'s real end-to-end auto-approve apply test uses `terraform_data`
> (built into Terraform core, zero cloud footprint) as its fixture — never reviewed, exactly the
> same shape as the `random_id` regression already fixed, but in a test file this session never
> ran locally before pushing. Fixed the same way (reviewed, added, reasoning recorded), and
> closed the actual gap that let it happen: every test file in this repo that imports
> `plan_gate`/`destructive_change_gate` (`test_gate_e2e.py`, `test_credentials.py`, `test_
> reporter.py`, `test_coverage_audit.py`, plus the two already covered) was enumerated by grep
> and run locally, not assumed clean by proximity — real CI confirmed green (8/8) after, not
> just re-claimed from local runs alone. Recorded here plainly rather than folded quietly into
> the "three bugs, all caught by the proof" framing above: this one was caught by the *push*,
> which is a real gap in verification discipline for a fix this load-bearing, not just a gap in
> the allowlist.
>
> **Standing checklist item, binding on every future change to a shared/core module, alongside
> the `G6_RULE_IDS` one above — bank the process lesson, not just the fixture fix.** "Green
> locally" here meant "green on the files I happened to run," not "green on every file the
> change actually affects" — the exact recurring shape of "worked where I checked, failed where
> it ships." The durable fix is not any one fixture; it's a standing rule: **before pushing a
> change to a shared classifier/gate/module, `grep` for every file that imports or calls it
> (test files and production callers alike), and run every one of them locally** — do not infer
> "probably fine" from having run the file whose name matches the module being changed. Two
> real, distinct failure shapes now confirmed this session from skipping this: a finding
> silently dropped one hop downstream (`G6_RULE_IDS`) and a real fixture regression invisible
> until it ran on someone else's machine (`terraform_data`, real CI). Both were only found
> because *something* eventually exercised the untested path — the checklist item exists so
> that "something" is a deliberate local check next time, not a real CI run or a user report.
>
> **The recurring disk hazard flagged earlier this session recurred again, exactly as
> predicted, not a new surprise, and is now logged as a standing, known-recurring hazard, not a
> one-off incident.** `pytest-of-shubh` regrew to 37GB and the C: drive hit 90% used (38GB free)
> mid-way through this exact work, on top of a *separate* instance of 180 stray
> `terraform-provider*` binaries (~46GB) extracted directly into Temp root by repeated real
> `terraform init`/`apply` runs — Terraform's own per-invocation provider-binary extraction into
> the OS Temp root, which `tests/conftest.py`'s `TF_PLUGIN_CACHE_DIR` mitigation does not stop
> (it only avoids re-*downloading* the provider package; Terraform still extracts a working copy
> into Temp per run). Both are the same already-documented, already-safe-to-clear culprits.
> `pytest-of-shubh` was cleared directly by this agent (already-established precedent from
> earlier in this session); the Temp-root stray-binary wildcard delete required explicit user
> authorization first (a wildcard delete in a shared OS directory — correctly held back by the
> permission classifier until the user confirmed it), then was run and confirmed clean (0
> remaining `terraform-provider*` files, ~98GB free afterward). Restated once more, plainly,
> since two recurrences in one session confirms it's not going away on its own: **check
> `df -h` before, during, and after any terraform-plan-heavy stretch of work**, not just before.

> **2026-07-02 (later): ALL ROADMAP PHASES SHIPPED + PUSHED** (`c31fe53`…`c50d787`).
> Phase B (volume wiring, budget check, showback tags, drift alert), loopholes #1/#2
> (sandbox-account gate, audited guard refresh), Phase C (tier-aware conformance
> TIER-COMPACTION/WAREHOUSE/TABLE-FORMAT, five new tier modules all terraform-validated,
> BCM scale-curve, Databricks/Snowflake alternatives in decisions), Phase D (scenario
> shortcuts panel, decision versioning, cross-run trend table). Verified live with a
> TB-tier run: 5 TB/day auto-priced at $3,495.79/mo = $0.0228/GB (vs $0.0389/GB at
> 100 GB/day — economies of scale visible in the Readiness trend). 200 tests passing.
> Remaining (lower): conformance/DATA-* stay advisory in production; apply not
> cryptographically bound to approver; Databricks/Snowflake Terraform module packs.

**Date:** 2026-07-02 · **Branch:** `restructure/multi-cloud-foundation` · **Status:** all work **uncommitted**, `177 tests passing`, full composed lakehouse passes `terraform validate`.

---

## 1. Direction (decided this session)

MinusOps is being positioned as a **requirements-first, governed IaC tool for *data pipelines*** — not a generic IaC tool. Principle: **keep the engine generic/robust** (classification fallbacks, multi-cloud prefixes so it never breaks on non-data / Azure / GCP resources), but aim all **value-add** (blueprints, conformance, diagrams, requirements schema, optimization) at data pipelines.

Grounding reference: the AWS Serverless Data Analytics Pipeline six-layer model (ingestion → storage[raw/cleaned/curated] → cataloging → processing → consumption → cross-cutting security/governance) + the Well-Architected **Data Analytics Lens**. See `docs/architecture_svg_spec.md` and the memory notes `aws-reference-architectures-for-design`, `data-pipeline-specialization`.

---

## 2. What shipped

### Phase 1 — six-layer model (the shared brain)
- **`core/architecture/architecture_model.py`** (new): generic, cloud-agnostic `classify_role()` (ingest/stage/store_other/catalog/transform/orchestrate/consume/security/observability + `other` fallback), `layer_of()`, `module_dependencies()` (real refs from `configuration.module_calls`), and `conformance()` scoring a plan vs the reference architecture + WA Lens (each finding cites its BP). Multi-cloud keyword rules (AWS/Azure/GCP) with graceful fallback.
- Tests: `tests/test_architecture_model.py`.

### Phase 2 — conformance surfaced everywhere
- `minusctl conformance --run <id> [--json] [--strict]`.
- Folded into `minusctl._readiness` (a check + full `conformance` object), the **enterprise package** (new section), and the **dashboard** "Reference conformance" panel (`app/dashboard_app.py`).

### Phase 3 — data-aware requirements
- **`core/architecture/requirements.py`**: additive data-pipeline FR/NFR profile (`DATA_FR`/`DATA_NFR` mapped to the six layers + WA pillars), `is_data_pipeline()`, `validate_data_pipeline()`, `requirements.py data-check <file>` CLI. Generic `validate()` untouched (backward-compatible). Surfaced as a non-blocking readiness warning.
- **`core/generation/accelerators.py`**: `aws-lakehouse` now populates the data-pipeline profile (`sources` explicitly deferred).

### Phase 4 — data optimization analyzer
- **`core/reporting/optimize_analyzer.py`**: `DATA-01` (Glue job without job bookmarks / not incremental), `DATA-02` (Glue table not partitioned), `DATA-03` (Athena workgroup without scan cutoff). Advisory (non-blocking). Grounded in WA BP10.

### Phase 6 — observability generation (design-time slice)
- **`modules/compute-glue-etl/main.tf`**: per-job Glue-failure EventBridge rule → SNS (BP 6.2/6.3), wired via synthesizer to the governance alerts topic.
- **Deferred (honestly):** a *live* 5-pillar data-observability dashboard (freshness/volume/schema/distribution/lineage) — those are runtime metrics that need real data flowing, which a pre-apply governance tool doesn't have. Faking them would violate the no-fabrication principle.

### Phase 8 — multi-cloud
- `architecture_model` classifier hardened with Azure/GCP data services (Data Factory, Pub/Sub, Dataproc, Synapse, BigQuery, Cosmos/Spanner/Bigtable, Key Vault, …). Still fallback-safe.

### B′ — loop-close (make generated pipelines runnable/conformant)
- **`modules/orchestrator-stepfunctions/main.tf`**: `definition_json` optional; builds a **real** state machine from wired Glue job names (`glue:startJobRun.sync`).
- **`core/generation/synthesizer.py`** (`_module_args`): wires `glue_job_names`/`task_role_arns = module.compute_glue_etl.*` (creates the orchestration→processing edge) + a default `bronze_to_silver` job + `alarm_sns_topic_arn = module.governance_observability.alerts_topic_arn`.
- **`modules/compute-glue-etl/main.tf`**: default job + uploads bundled starter `scripts/etl.py` (`aws_s3_object`) + `glue_job_arns` output.
- **`modules/governance-observability/main.tf`**: creates an **SNS alerts topic**, wired to the alarm + budget → resolves `WA-REL-NOTIFY`.
- `core/generation/modules.py`: registry `inputs`/`provides` updated to match.
- Net: a fresh accelerator run is conformant-by-construction (only INFO "no ingestion" remains → ~100/READY), diagram shows a solid `orchestrates` edge. Verified with `terraform validate`.

### Diagram (v3, additive)
- **`core/reporter.build_dataflow_svg`**: emits **`dataflow.svg`** alongside the v2 `architecture.svg` (which remains the binding contract for the dashboard pan-zoom viewer + tests — untouched). Shares the six-layer classifier, so the picture and the conformance report agree. Honest orchestration edge (solid only when the plan wires it, else `not wired — placeholder`). Icons are **opt-in** via `MINUS_ARCH_ICONS_DIR` / `assets/architecture-icons/<slug>.svg` with generic-glyph fallback — **no vendor icons committed**. Spec: `docs/architecture_svg_spec.md` v3.

### terraform-validate self-check (non-mutating, credential-free)
- **`core/governance/tf_validate.py`** (new): `terraform init -backend=false` + `validate -json`, offline, never raises. `validate_and_record()` writes `validation.json`.
- Wired: `synthesize(..., validate=True)` (CLI default on; `--no-validate` to skip), `minusctl validate --run <id>`, and a readiness check reading the recorded result.

### Earlier this session (governance hardening — also uncommitted)
- **`core/governance/plan_gate.py`**: `--policy-mode production` now enforces an approver allowlist, two-person rule (approver ≠ planner), and rejects `MINUS_ALLOW_STATIC_CREDS` (Phase 1 warn → Phase 2 enforce). `stage_plan` records the planner. Dev mode unchanged. See memory `deploy-gate-bypass`.

---

## 3. New/changed files (quick map)

**New:** `core/architecture/architecture_model.py`, `core/governance/tf_validate.py`, `modules/compute-glue-etl/scripts/etl.py`, `tests/test_architecture_model.py`, `tests/test_tf_validate.py`, this file.
**Core changed:** `minusctl.py`, `requirements.py`, `accelerators.py`, `reporter.py`, `optimize_analyzer.py`, `synthesizer.py`, `modules.py`, `plan_gate.py`.
**Modules changed:** `orchestrator-stepfunctions`, `compute-glue-etl`, `governance-observability`.
**Other:** `app/dashboard_app.py`, `docs/architecture_svg_spec.md`, several `tests/test_*.py`.

## 4. Key commands
```
python core/reporting/minusctl.py conformance --run <id>        # six-layer + WA gap analysis
python core/reporting/minusctl.py validate    --run <id>        # offline terraform validate (no creds)
python core/architecture/requirements.py data-check <requirements.json>
python core/generation/synthesizer.py "<summary>" --run <id> --requirements-file ... --decision-file ...   # validates by default
MINUS_ARCH_ICONS_DIR=<dir> ...                        # opt-in real AWS icons for dataflow.svg
```

## 5. Live-infra status
The demo lakehouse run `20260701-040620-requirements-first` was applied to the sandbox AWS account earlier, then **fully destroyed** (`terraform destroy`, 33/33). Its state is empty. (The run workspace was later purged with all generated artifacts for a fresh end-to-end test.)

---

## 6. Known loopholes / open items (from the audit)

**High**
1. **Gate controls are opt-in.** Production controls only fire in `--policy-mode production`; default is `dev` and nothing ties policy mode to the real target account. The `MINUS_ALLOW_STATIC_CREDS + --mode auto-approve` self-apply is still open in dev. Same root cause: `authz.py`'s approver allowlist defaults to `"open"` mode (everyone authorized) unless `.minus/approvers.json`/`MINUS_APPROVERS` is explicitly configured (`authz.py:8-10`, self-reported, not a silent gap). **Externally validated (2026-07-10, competitor/standards research):** this inverts the secure default and is exactly the control SOC 2 CC8.1 (segregation of duties — author ≠ approver) tests first; a single unapproved-but-"authorized" apply is the classic finding that qualifies an audit. **Not fixed — resolution pending a positioning decision** (product/governance-compliance tool vs. single-operator reference implementation; nothing in README.md/project_plan.md settles which MinusOps is meant to be). If ever pointed at real multi-team production, this is a hole; as a solo-dev default it's a defensible, explicitly-disclosed choice. *Fix if positioning calls for it:* default-deny (explicit allowlist required in every environment), block self-approval, require production-mode policy inference tied to the real target account (see original fix note below).
2. **Source guard can be re-baselined.** An operator can hand-edit generated TF then `guard refresh` to bless it (the prior run did this). Protects drift, not tampering.
3. ~~**Icon SVG embedding is unsanitized (introduced here).**~~ **FIXED (2026-07-02).** `reporter._sanitize_svg_fragment` now strips script/foreignObject/embedding/animation elements, `on*` attributes, and non-fragment `href`s on embed, and fails closed to the generic glyph if anything active survives. Regression tests: `test_dataflow_icon_embedding_is_sanitized`, `test_dataflow_benign_icon_still_embeds`.
11. ~~**Audit chain has no concurrency lock — a race can corrupt or silently drop entries.**~~ **FIXED FOR REAL (2026-07-13), root cause confirmed, not re-guessed.** History: the original unlocked `append()` was fixed with a sidecar-file lock (`_AppendLock`, `os.O_CREAT|O_EXCL`, deleted on release); that fix was reported "FIXED" once, wrongly — an independent audit found it flaky (`PermissionError(13)` on lock-file cleanup); a 4-clean-CI-runs streak then pointed the framing toward "probably a local artifact," which the Phase 4 push's CI run falsified outright (both windows-latest jobs hit the identical failure in the same run — no local antivirus/file-watcher to blame on a GitHub-hosted runner). **Root cause, confirmed empirically, not theorized**: `os.open(path, O_CREAT|O_EXCL)` racing a concurrent `os.remove()` of the same lock filename — NTFS's delete-then-recreate semantics for one path have no POSIX-equivalent atomic-unlink-while-open guarantee, so a racing create can return `PermissionError(13)` instead of either succeeding or `FileExistsError`. Reproduced directly (852/4800 cycles in a tight repro; the real 8×15 test failed 2/8 consecutive local runs, byte-identical to the CI failure) before writing any fix. **Real fix**: removed the delete-recreate cycle entirely rather than widen what's caught — the lock sidecar is created once and never deleted; acquire/release now toggle an OS-native advisory region lock (`fcntl.flock` POSIX / `msvcrt.locking` Windows) on that persistent file, so there is no delete window left to race. A broad `except PermissionError` was explicitly rejected (would trade a fail-loud crash for a fail-open hang on a genuine, non-transient permission denial) — three outcomes stay structurally distinct: can't-open (immediate raise, outside the retry loop), region-held (the only retried case, matched by a narrow per-platform contention signal — `BlockingIOError` POSIX, `OSError`/`errno==EACCES` specifically from `msvcrt.locking`, confirmed live not assumed), anything else (re-raised immediately). A belt-and-suspenders `threading.Lock` per lock path guarantees intra-process thread-safety independent of `flock()`'s open-file-description semantics across threads, which this session's Windows-only dev environment could not itself verify on POSIX. Proof, all done before closing: the same 4800-cycle repro that found the bug now shows **zero** exceptions (not fewer); the real test run **25 consecutive times** clean plus **10 more full-file runs** clean; a new `test_append_lock_serializes_threads_not_just_avoids_exceptions` records real enter/exit timestamps across 12 threads × 40 iterations and asserts **zero interval overlap** (proof of actual serialization, not "no exception raised"); a new negative control (`test_append_lock_fails_fast_on_a_genuine_access_error_not_a_10s_hang`) confirms a genuinely un-openable path raises in <1s, not after the 10s timeout; the crashed-writer test was rewritten to hold a real, live OS-level lock (a bare stale `.lock` **file** with no live holder is confirmed to need no manual cleanup at all now — the kernel releases advisory locks automatically on process death/crash, confirmed by killing a real subprocess mid-lock and watching the next acquire succeed immediately). Full scope + empirical findings: `docs/audit_chain_lock_fix_scope.md`.

**Medium**
4. Conformance / data-profile / `tf_validate` / DATA-* findings are **advisory** (only `SEC-*` block apply). Broken/non-conformant pipelines can still be approved.
5. **"core Terraform files present"** readiness check tests presence, not content — empty stubs pass it.
6. Conformance **"wired" detection is heuristic** (module-input refs only) — literal-name wiring → false "unwired"; unrelated module ref → false "wired". *(2026-07-02: the dataflow diagram now uses the exact same test as `conformance()`, so at least the picture and the report can no longer disagree; the heuristic itself is unchanged.)*

**Lower**
7. `tf_validate` (init -backend=false + validate) ≠ full correctness (misses provider-side + unknown-value checks).
8. ~~**`terraform apply tfplan` uses ambient creds — not cryptographically bound to the approver's account.**~~ **FIXED (2026-07-07).** `authz.verified_operator()` derives the RBAC identity from AWS STS `get-caller-identity` (unspoofable by `MINUS_OPERATOR`); `plan_gate.py` records `approver_verified_identity` at approval and `_reject_if_apply_identity_mismatches_approver()` refuses apply in production if the applying session's verified identity doesn't match who approved it.
9. **Catalog gap:** no `storage → orchestrator-mwaa` wiring branch in `synthesizer.py`. When `storage-medallion-s3` and `orchestrator-mwaa` are composed together, `dag_s3_bucket_arn` still renders as `# REVIEW: set dag_s3_bucket_arn` instead of being wired to the storage module's bucket output (2026-07-08, surfaced while composing `orchestrator-mwaa` + `networking-vpc` for the Phase 1 VPC module's end-to-end proof — out of scope for that phase, tracked here so it doesn't get lost). *Fix shape:* mirror the existing `has_storage and module_id == "compute-glue-etl"` branch in `_module_args()` — wire `args["dag_s3_bucket_arn"] = f'{_STORAGE}.bucket_names["bronze"]'` (or whichever zone holds DAG artifacts) when both modules are present.
10. **Phase 2 live create+destroy test: pending**, deliberately deferred (2026-07-08) to run against the real intended-use account rather than a scratch one. `databricks-workspace` is validated via `terraform test`/`terraform validate`/composition-level proof only, not a live apply. Watch for the metastore teardown needing a manual nudge: `force_destroy = true` (already set on `databricks_metastore.this`) covers the documented "default catalog blocks deletion" failure mode ([Databricks KB](https://kb.databricks.com/unity-catalog/cannot-delete-unity-catalog-metastore-using-terraform)); the "root storage credential blocks deletion even with force_destroy" failure mode ([GitHub #3396](https://github.com/databricks/terraform-provider-databricks/issues/3396), still open) is avoided by design (the module configures no `databricks_metastore_data_access`/root credential) but that's unproven against a real account, not confirmed clean. **Confirmed by 2026-07-10 external research: this is an ecosystem problem, not a MinusOps defect** — #3396's own maintainer workaround is `force_destroy=true` specifically on `databricks_metastore_data_access` (not just the metastore), and the same "dependent object blocks destroy" pattern recurs across the provider (#4000 storage credential, #2188 external location, #2711 metastore-with-catalogs). Separately, the account-vs-workspace provider split this module's `provider_config { workspace_id }` pattern relies on (for `databricks_metastore_assignment`/`databricks_catalog`/`databricks_sql_endpoint`) is documented as genuinely unsettled by Databricks itself — its own docs state catalog/SQL/workspace-conf resources "can only be used with a workspace-level provider," while `provider_config` is a newer mechanism letting an account-level provider reach them; the provider's own changelog shows related resources still being bug-fixed to honor it as recently as PR [#5680](https://github.com/databricks/terraform-provider-databricks/pull/5680). Disclose both openly; don't treat either as something MinusOps' code can pre-emptively fix without a real account to test against.
12. **No live drift detection** (MinusOps' recorded state vs. what's actually in the AWS/Databricks account — distinct from the provider-*schema* watch in `schema_watch.py`, which tracks the Terraform provider's own resource shapes, not live infra state). Confirmed absent via code search. **Externally validated (2026-07-10):** this is a first-class, usually paid feature across comparable tools (Spacelift's scheduled drift detection, env0's `auto_drift_remediation`, HCP Terraform/Terraform Enterprise health assessments, Atlantis's opt-in drift API) — MinusOps is behind category norm for paid competitors, though normal for an early-stage/self-hosted tool. Not a SOC 2 requirement, so not a blocker — a competitive/disclosure gap, not a correctness one. *If ever prioritized:* the DIY baseline is a scheduled `terraform plan -refresh-only -detailed-exitcode` job, read-only, no auto-remediation.
13. **Two demo-blueprint `controls[]` claims are false — advertised, not built (found by Phase 4's `intent_assertions.py`, 2026-07-12).** The `aws-data-pipeline-standard` blueprint claims "CloudWatch alarms and log retention" (real Terraform generates the alarm, no `aws_cloudwatch_log_group` at all) and "Budget and anomaly detection hooks" (generates the budget, no `aws_ce_anomaly_*` resource). Confirmed against a real `terraform plan` of the actual generated output, not a synthetic fixture. Under the compliance-carrying posture this is a false claim to the user, not a cosmetic gap. **Phase 6 generation-fidelity input** — fix generation to honor each control (add the log_group + retention config; add a `aws_ce_anomaly_monitor`/`aws_ce_anomaly_subscription` pair), or remove the two claims from `controls[]` if they're not going to be built. Do not leave both standing.

---

## 7. Recommended next steps
1. ~~**Patch loophole #3** (sanitize icon SVG embed)~~ — **done 2026-07-02** (see §6 #3). Also fixed the same session: dataflow diagram no longer silently drops transforms that don't fit between stages (appended to spine) or extra consumption/catalog/orchestrator nodes (`+n more` markers); its wired/unwired verdict now uses the identical test as `conformance()`; `dataflow.svg` is now actually served + linked by the dashboard (was manifest-listed but 404 behind the route allowlist); spec doc internal contradictions corrected (group list incl. `edges`, node-card height 44).
   *Also 2026-07-02 (round 4 — FinOps-grade cost report):* per-service table now shows real **usage quantities + units and effective $/unit** (BCM cost ÷ BCM quantity — `load_bcm_estimate` was dropping the `quantity` object), **unpriced plan services are listed as "not estimated"** rows (absence of a price ≠ $0), a **What-if scenarios** section points at the existing `scenario` command (scale up/down, SP/RI commitments), **unit economics** (cost/GB processed) renders when the run states a data volume, and the overview Cost-evidence KPI shows the actual `$X/mo`. Grounded in FinOps framework guidance (unit economics, scenario planning, showback).
   *Also 2026-07-02 (round 3 — estimates are frictionless now):* **BCM estimates no longer require human approval** — an estimate is a free, deletable pricing object, so `bcm_pricing_calculator.run/scenario` default to auto-approve (still audited + RBAC-checked); human-in-the-loop stays on APPLY. New `auto_estimate()` runs during every report generation (`MINUS_BCM_AUTO=0` to disable; tests force it off in conftest): amounts derived from run inputs + recorded assumptions, catalog fields from the example profile (amounts stripped — never submitted), only complete lines submitted, skipped services recorded as `not_estimated_services`. The example profile's catalog triples were **verified against the AWS Price List API** (Glue = `USE1-ETL-DPU-Hour/Jobrun`, S3 us-east-1 = `TimedStorage-ByteHrs` with an EMPTY operation — `validate_usage` now allows empty operation). Verified live on the agy sales-pipeline run: AWS returned **$116.59/mo** (Glue $105.60 = 240 DPU-h × $0.44, Athena $10.99 = 2.1973 TB × $5), readiness went to 100/100 READY. Known gap: S3 goes not-estimated when the plan lacks a `daily_data_gb` variable — the synthesizer should map the requirements' volume answer into that variable. Report title fix: reports of run workspaces now title themselves from run.json blueprint instead of the directory basename "terraform".
   *Also 2026-07-02 (round 2):* dataflow spine now places each transform between the stages its `<from>_to_<to>` name bridges (positional interleave only as fallback), a stage boundary with **no transform in the plan renders a faint dashed gap labelled `no transform in plan`** instead of a fabricated solid arrow, and consumption anchors to the last storage stage. Overview no longer embeds the architecture (moved to the top of the **Reports** tab). Spend charts follow Cost Explorer conventions: monthly **bars** (no spline over near-zero months), emphasis coloring on spend-by-service, adaptive money ticks, micro-spend (<1¢) hides the axis and direct-labels bars, zero slices dropped from the plan donut, `.col-side` gap fixed. **Estimate path verified end-to-end** with a fixture run: BCM totals rendered verbatim ($123.45), annual ×12, variance math exact (+15.0% Glue, −58.6% S3, −10.9% total). Fixture deleted after verification.
   *Also 2026-07-02:* **dashboard overview rebuilt** around the pipeline instead of the wallet — KPIs are now Readiness / Conformance / Plan changes / Cost evidence; the dataflow diagram is embedded on the overview; the three account-level $0 charts collapsed into one compact "Account spend" evidence panel; brand renamed to "MinusOps — governed data-pipeline console". The interactive viewer gained a **Data flow ⇄ Topology toggle**. **Official AWS service icons** installed locally at `assets/architecture-icons/` (17 slugs from the aws-svg-icons npm package; the dir is gitignored — never commit vendor assets); `_df_embed_icon` now carries the source viewBox through so 80×80 icon sets aren't cropped. **All generated artifacts purged** (`runs/`, `artifacts/`, `.pytest_tmp*`) for a fresh end-to-end pipeline test; the demo run record from §5 is gone with them (its infra was already destroyed).
2. **Address #1** — refuse/loudly-audit `dev` policy when the target account isn't a known sandbox.
3. Decide whether conformance/data-profile should **block** (not just warn) in production mode.
4. Harden #5 (check core files are non-empty / contain expected resources).
5. Optionally: `dq-great-expectations` failure notification (same pattern as compute); live observability dashboard once a pipeline actually runs.
6. **Commit**: stage the whole specialization on this branch (co-author trailer, GitHub noreply email per project convention). Nothing is committed yet.

## 8. Verification
- `python -m pytest -q` → 177 passing.
- `python core/generation/synthesizer.py ... ` (or `compose`) then `terraform validate` → "Success! The configuration is valid."
- `python core/reporting/minusctl.py conformance --run <id>` → layer coverage + WA gaps.
