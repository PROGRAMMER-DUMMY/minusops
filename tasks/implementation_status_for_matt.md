# Implementation Status — Enterprise Subagent Fabric & Governance PRD

| Attribute | Details |
| :--- | :--- |
| **Date** | 2026-08-21 |
| **Branch** | `feat/minusops-enterprise-nextgen-v2` |
| **Source documents** | [`new_prd_for_architect.md`](./new_prd_for_architect.md), [`implementation_plan_for_architect.md`](./implementation_plan_for_architect.md), [`minusops_friction.md`](../minusops_friction.md) |
| **Full suite** | exit 0 across 81 test files |
| **Working tree** | Clean. 10 commits pushed to `origin/feat/minusops-enterprise-nextgen-v2`. |

All three source documents have been read in full.

---

## 1. Implementation plan — phase status

| Phase | Status | Evidence |
| :--- | :--- | :--- |
| **1 — Integration tool hooks** | **Done** | [`core/integrations/`](../core/integrations/) — `base_hook`, `slack_hook`, `teams_hook`, `outlook_hook`, `confluence_hook`, `jira_hook`. [`tests/test_integrations.py`](../tests/test_integrations.py) passes. |
| **2 — Subagent manifests** | **Done, relocated** | [`.claude/agents/`](../.claude/agents/) — `slack-agent`, `teams-agent`, `outlook-agent`, `confluence-agent`. See §5 for why not `.agents/subagents/*.json`. |
| **3 — Metadata control table + dynamic DAG config** | **Done** | [`modules/metadata-control-table/`](../modules/metadata-control-table/) + `scripts/fetch_pipeline_config.py`. Registry now carries 25 modules. |
| **4 — CI/CD workflow generator** | **Done** | [`core/generation/cicd.py`](../core/generation/cicd.py) — 4-lane pre-merge, reusable feed factory, matrix discovery, Jenkins parity. [`tests/test_cicd.py`](../tests/test_cicd.py), 16 tests. |
| **5 — Regression + audit verification** | **Done** | Full suite exit 0 across 81 test files; context drift check clean. |

### Deviations from the plan text, and why

- **No `artifactory_hook.py`.** Binary promotion appears in PRD §6.9 but in no functional requirement in §14. No Artifactory instance is connected. Recorded in `core/integrations/CONTEXT-integrations.md` with the condition for adding it: a promotion requirement naming a repository.
- **No `BaseIntegrationHook` class.** One implementation, no state — module-level functions instead. A class here is a namespace with extra steps.
- **No hook accepts a `webhook_url` parameter.** The plan's interface was `send_slack_notification(webhook_url, ...)`. A Slack or Teams webhook URL is a bearer credential; anyone holding it can post as the workspace. Putting it in a call signature invites it into logs and tracebacks. Hooks resolve it from env or a Secrets Manager ARN, consistent with the existing FM-02 stance.
- **Slack and Jira were extracted, not rewritten.** `finops_agent.py` already implemented `cmd_notify_slack` and `cmd_notify_jira`. Those now call the extracted hooks so exactly one implementation exists.

---

## 2. PRD functional requirements — actual state

| ID | Requirement | Status |
| :--- | :--- | :--- |
| **FR-01** | Deterministic destroy blocking | **Already implemented** — `plan_gate._reject_if_destructive_and_auto_approve` |
| **FR-02** | Interactive TTY enforcement | **Already implemented** — `approval.request_approval` fails closed with `DENIED_NO_TTY` |
| **FR-03** | Hierarchical state isolation | **Conflict.** Implemented as `teams/<team_id>/<workload_id>/`; PRD §2.1 specifies `state/<domain>/<repo>/<pipeline>/`. Two incompatible layouts in one system. |
| **FR-04** | Dual Excel export engine | **Already implemented** — `excel_finops_generator.py`, stdlib OpenXML, no third-party dependency |
| **FR-05** | Cryptographic plan binding | **Already implemented** — `plan_gate` SHA-256 over `resource_changes` + `output_changes` |
| **FR-06** | Privilege escalation prevention | **Not done.** Requires the §13 boundary deployed in AWS. The §13 JSON is defective as written — see §6. |
| **FR-07** | Two-person rule | **Already implemented** — `plan_gate._enforce_production_approval` |
| **FR-08** | FinOps circuit breakers | **Done.** Athena `bytes_scanned_cutoff` ✅, Glacier lifecycle ✅, and `aws_glue_job` now sets `timeout = var.timeout_minutes` (default 120, validated against AWS's 2880 ceiling) ✅. Previously absent, so AWS applied its 48-hour default. Pinned by [`tests/test_finops_circuit_breakers.py`](../tests/test_finops_circuit_breakers.py) and mutation-checked. |
| **FR-09** | Runtime dependency pinning | **Not done, and contradicts §6.6.** See §6. |

**Six of nine already existed before this work began.** The PRD documents them as requirements without marking them as shipped, which makes the remaining scope look larger than it is.

---

## 3. Supporting work completed

**Context documentation drift — fixed and verified.**
`core/CONTEXT-core.md` carried three wrong file counts and still documented the Azure/GCP provider scaffolds that were deleted when multi-cloud left scope. Two reporting modules and one test module were undocumented. `app/CONTEXT-app.md` claims `MINUS_CLOUD` selects the provider — confirmed stale, that variable exists nowhere in `core/providers/`. **Not yet fixed.**

**Module docstring pass — 5 directories, ~50 files.**
Every file now carries a top-level docstring ending in three verified dependency lines: `Depends on:` / `Shells out to:` / `Used by:`. The third is the one that cannot be recovered by reading the file and is what makes blast radius legible. Dated narrative and ticket changelogs were removed; every comment that prevents a plausible wrong change was kept. All edits verified AST-identical against `HEAD` — no code changed.

`core/governance/audit_logger.py` and `core/reporting/health_checker.py` had no module docstring at all and now do.

**`context-graph` skill corrected and installed.**
Eight defects fixed, notably: it mandated line-number anchors (which a docstring pass invalidates wholesale), had no verification step despite "audit" in its description, and specified absolute `file:///C:/Users/...` links that break on every other clone. Installed at Claude user level and in the Gemini config, with a runnable drift check.

**Outstanding tail:** 2 `__init__.py` files without dependency lines, 3 residual ticket-ID comments in `core/generation/`, and 8 context files still carrying absolute links and line anchors.

---

## 4. Blocked on Matt's decision

**Resolved in PRD Revision 2.** All five decisions are signed off in §16. Decision 1 became grandfathered adoption (`--policy-mode brownfield` requires only `TeamId` + `ManagedBy`); Decision 2 became a 2-tier boundary separating the agent runner role from workload execution roles, which removes the circular dependency that blocked Iceberg compaction deletes.

Still outstanding and **not** blocked by a decision: FR-06 (the boundary must actually be deployed in AWS) and FR-09 (dynamic dependency verification).

---

## 5. Phase 2 relocation — why `.claude/agents/`

The plan specified `.agents/subagents/*.json`. That location is inert:

- Claude Code discovers subagents from `.claude/agents/*.md` (Markdown with YAML frontmatter). It never scans `.agents/`.
- Nothing in this repo loads `.agents/` programmatically. Two tests read a `SKILL.md` as text to assert content; that is all. Skills there activate because `AGENTS.md` instructs the driving agent to read them — a prompt convention, not runtime discovery.
- JSON is not the manifest format any CLI uses.

Manifests are now transport-focused and encode the constraints that matter: never echo a webhook URL, never report `ok: True, sent: False` as delivered, a denied approval is a denial and is not retried, and `outlook-agent` may not state a cost figure it did not read from the generated workbook.

**Known design debt:** the four manifests still bake routing policy into agent identity (`slack-agent` "handles P1 incidents"). Routing is a customer decision captured by `grill-me` pillar 7 — *who is paged for crashes, who for data quality, who for spend* — and `team_resolver.py` already resolves `slack_channel`, `teams_webhook_secret`, and `team_dl` per team from `configs/teams.yaml`. The three-tier taxonomy already exists in `governance-observability`'s SNS topics. The manifests should be transport-only with the tier→team→channel join in config. Not yet done.

---

## 6. Defects found in the PRD

Ranked by severity when first raised. **PRD Revision 2 resolved 6.1, 6.2, 6.3 and 6.5**, and cut the
unsourced scope in 6.7. They are kept here as the record of what changed and why, not as open items.

One defect was raised after Revision 2 and is **resolved**: §12 initially redefined FM-01..FM-05 with
data-execution failure modes, colliding with `architecture_decision.FAILURE_MODES`, which is enforced
by `validate()` and asserted by tests. An ADR authored from that draft would have been recorded under
the wrong meaning. §12 now matches the canonical in-code taxonomy, with the data-specific modes moved
to their own subsection.

**6.1 — §8.1 and §8.3 contradict each other.** Object Lock in COMPLIANCE mode means no identity, including root, can delete an object for the full retention window. §8.3 promises GDPR/CCPA right-to-be-forgotten via row-level deletes. These cannot both hold. The standard resolution is pseudonymization at ingest so the WORM copy carries no direct identifier, with the identity map in an erasable store. This also contradicts the repo's own recorded decision: `governance-observability` chose GOVERNANCE mode because "COMPLIANCE cannot be shortened or removed by anyone including root for the full window, which has stranded more teams than it has caught."

**6.2 — §13's permissions boundary is defective.**
- The `iam:PermissionsBoundary` condition forces this boundary onto every role MinusOps creates. It denies `s3:DeleteObject*` and `glue:Delete*`, which breaks Glue rewrites, Iceberg compaction, and the §11 lifecycle cleanup the same document requires.
- `kms:PutKeyPolicy` on `Resource: "*"` with Allow is a privilege-escalation path worse than any delete — it permits rewriting any key policy in the account.
- `s3:PutBucket*` includes `PutBucketPolicy`, permitting public or cross-account grants, which undermines SEC-01.
- `Allow AttachRolePolicy` with `Deny DetachRolePolicy` means a bad attach can never be remediated.
- No `aws:RequestedRegion` or resource-prefix conditions.
- A hardcoded, real AWS account ID in a template document (redacted here; see PRD Rev 1 s13).

**6.3 — §9.1's RPO is unachievable from §9.2's design.** Plain S3 CRR is asynchronous with no lag SLA. `< 15 min` requires S3 Replication Time Control, a separately-priced feature that must be explicitly enabled. `RTO < 2h` against a "Cold Standby" with no running compute requires the DR region to be pre-provisioned, which is not cold.

**6.4 — §14 FR-09 contradicts §6.6.** §6.6 states MinusOps "never hardcodes a fixed set of Python packages." FR-09 requires attaching `openpyxl==3.1.2`, `calamine==0.2.1`, `pandas==2.1.4`.

**6.5 — §5.3 puts PII in resource tags.** `Owner` as an email address surfaces in Cost Explorer exports, CloudTrail, and to any principal with `tag:GetResources`. This repo already gitignores `configs/teams.yaml` because it names people. Use a team ID or group alias. Separately, Decision 1's hard-blocking gate on all six keys will reject every brownfield adoption — `adopt.py` exists because enterprises do not start empty, and existing resources carry none of these tags.

**6.6 — §17 mixes shipped and aspirational verification.** Item 2 is past tense ("Proved..."). Items 4 (DR replay measured against the 2-hour SLA) and 5 (Glue 4.0 ABI compatibility) have not been run. Relatedly, §9.3's runbook instructs `seed.py --replay-from-bronze --start-date`; those flags do not exist.

**6.7 — Scope with no driving requirement.** §6.9 (artifact repository, binary promotion) appears in no FR. §2.2's four-account hub-and-spoke is sound architecture but the codebase is single-account throughout; that gap is unpriced.

**6.8 — Framework mismatches.** PRD claims 9 grilling pillars; `grill-me` implements 7. "File Complexity (multi-sheet Excel)" is a property of one ingestion type, not a peer of Account Topology. PRD specifies 4 environments; `_ENV_MATRIX` generates 3 (dev/staging/prod).

---

## 7. Verification

```bash
rm -rf .pytest_tmp                       # stale dirs cause a conftest fixture collision
python -m pytest tests/ -q               # exit 0
python -m pytest tests/test_integrations.py tests/test_metadata_control_table.py -q
```

`aws_dynamodb_table` was added to `destructive_change_gate.STATEFUL_RESOURCE_TYPES` — **not** to `AUTO_SHIP_ELIGIBLE_TYPES`. A plan creating one returns `autonomous_eligible = False`, so it stages for human review. The gate got stricter. A regression-lock test that used `aws_dynamodb_table` as its canonical never-declared type was re-based onto `aws_documentdb_cluster`, following the precedent set by the earlier `aws_secretsmanager_secret` swap.

**Known pre-existing flake:** `conftest._isolate_claim_corpus` errors intermittently when `.pytest_tmp` holds a locked `.terraform` directory. Reproducible on files this work never touched.

---

## 8. Recommended next actions

1. **Answer Decisions 1 and 2.** Everything in Phase 4 waits on Decision 2.
2. **Resolve §8.1 vs §8.3, and rewrite §13.** Both are correctness defects, not preferences, and both are cheaper to fix in the document than in deployed IAM.
3. **Commit the current tree in separate commits** — documentation pass, integrations, control table — before Phase 4 adds a fourth body of work to an already undifferentiated diff.
4. **Split §6.9 and §2.2 into a phase-2 document** so the reviewable scope fits one sitting.
