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
11. **Audit chain has no concurrency lock — a race can corrupt or silently drop entries.** **UNPROVEN/FLAKY (last checked 2026-07-10) — do not mark fixed.** `audit_chain.append()` read `last_hash()` and wrote the new entry as two separate, unlocked steps — concurrent writers could both read the same `prev_hash` and both append, forking the chain. Confirmed empirically before attempting a fix (not just theorized): 8 threads × 15 concurrent appends produced **115 lines instead of 120** (writes silently lost) plus chain-verify failures. Attempted fix: a stdlib-only, cross-platform mutual-exclusion lock (`_AppendLock`, an atomically-created sidecar `.lock` file — `os.O_CREAT | os.O_EXCL` is atomic on both POSIX and Windows, no new dependency, no fcntl/msvcrt split) wrapping the read+write in `append()`, plus a regression test (`test_append_is_safe_under_concurrent_writers`, `tests/test_audit_chain.py`) racing 120 concurrent appends. **This was first reported here as "FIXED" and that was wrong** — an independent audit re-ran the test 6 times and got 5 passes, 1 failure (`PermissionError(13)` on lock-file cleanup, `os.remove` racing another thread's `os.open(..., O_CREAT|O_EXCL)`). The test itself is intermittently green, which is a false-positive risk, not proof. **Do not spend time re-fixing this yet**: its fix-urgency is positioning-dependent (see the open positioning question in the 2026-07-10 banner entry above, and §6 item 1 which shares the same dependency) and single-machine testing can't distinguish a Windows-only quirk from a genuine cross-platform race — the real CI matrix (ubuntu/macos/windows) is the only thing that can answer that, and nothing has been pushed yet for it to run against. `test_append_lock_times_out_instead_of_hanging_forever` (the crashed-writer/stale-lock case) has not shown the same flakiness so far, but has also not been stress-tested to the same degree.

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
