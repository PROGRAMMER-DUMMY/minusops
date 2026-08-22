# PRD v3.0 — Alignment Review Against the Codebase

| Attribute | Details |
| :--- | :--- |
| **Reviews** | [`prd_v3_enterprise_data_platform.md`](./prd_v3_enterprise_data_platform.md) (PRD-ARCH-2026-003) |
| **Date** | 2026-08-21 |
| **Method** | Every claim checked against the repo. Nothing accepted from the document's own checklist. |
| **Verdict** | Accurate about what shipped. **Three of thirteen pillars and two of twelve FRs describe work that does not exist**, and the document does not distinguish them from the parts that do. |

---

## 1. What v3 gets right

The FM taxonomy in §6 now matches `architecture_decision.FAILURE_MODES` exactly — Identity Churn,
Secret Exposure, Blast Radius, CI Drift, Compliance Gate Gaps. That was the landmine in the
previous revision and it is properly closed: an ADR authored from this document will pass
`validate()`.

These claims were checked individually and hold:

| Claim | Verified |
| :--- | :--- |
| §4 teams-agent Adaptive Cards **v1.4** | `teams_hook` sets `"version": "1.4"` |
| §4 outlook-agent **SMTP 587 STARTTLS** | `outlook_hook` defaults to 587 and calls `starttls()` |
| §4 confluence-agent **`version = current + 1`** | implemented and asserted by test |
| §4 jira-agent **ADF** | `_adf()` posts a doc node to `/rest/api/3/issue` |
| §4 dedup **bypassed for docs/tickets** | confluence/jira/outlook pass `dedup_window=0` |
| FR-08 **Glue `timeout = var.timeout_minutes`** | present, defaulted to 120, mutation-tested |
| FR-09 **feed factory + Jenkins parity** | `core/generation/cicd.py`, 16 tests |
| FR-10 **5 transports + 5-minute window** | 5 manifests, `DEDUP_WINDOW_SECONDS = 300` |
| §8 six checked test files | all exist and pass |

---

## 2. Claimed but not built

| PRD claim | Reality |
| :--- | :--- |
| **§2 / FR-06: 13-pillar grilling** | `grill-me` has **10** pillars. Pillars 11 (Data Modeling & SCD), 12 (Warehouse FinOps), 13 (Data Governance) do not exist. FR-06 is unmet as written. |
| **FR-11: worker sizing from the data model** | Not implemented. `modules.compute_tier()` sizes by **daily volume and latency SLA**; nothing maps SCD Type 2 to `G.2X`. |
| **FR-12: Lake Formation TBAC + column masking** | Not implemented. There is no `aws_lakeformation_*` resource anywhere in `modules/`. |
| **§3: partition projection in `aws_glue_catalog_table`** | Not implemented. No module sets `projection.enabled`. |
| **§12: Snowflake auto-suspend (60s)** | Not implemented, and not implementable in the named module — see §3.3. |
| **§12: Redshift max RPU cap** | Not implemented. `consumption-redshift-serverless` exposes `base_capacity_rpu` only; there is no `max_capacity`. |
| **§5: 2-tier permissions boundary** | Specification only. No policy document exists in the repo and nothing generates one. |

§8's acceptance list contains no entry for FR-11 or FR-12. That is consistent with them being
unbuilt, but it means the checklist reads as complete while two requirements have no coverage
at all.

---

## 3. Conflicts that needed a decision — RESOLVED 2026-08-21

All three were ruled on and implemented. Kept below as the record of what was decided and
why, with the resolution noted under each.


### 3.1 Two sizing authorities that will disagree

§3 sizes the Glue **worker class** from the data model. `modules.compute_tier()` selects the
**compute module** from volume, with recorded crossovers: under 1 TB/day Glue, 1–5 TB EMR
Serverless, above 5 TB EMR on EC2 with Spot task fleets.

At 10 TB/day append-only these give incompatible answers. §3 says `G.1X`; `compute_tier` says
EMR on EC2, where Glue worker classes do not exist as a concept. Whichever runs second wins,
and nothing detects the contradiction.

**RULED:** memory-intensive patterns (SCD Type 2 merge, wide joins) mandate `G.2X`
regardless of volume; `G.1X` is for append-only and simple filter/map.

**Implemented** as `modules.worker_class(access_pattern, module_id=...)`. `compute_tier()`
still selects the engine from volume, so "regardless of volume" governs the worker *within*
Glue -- asking for a worker class on an EMR module returns `None` rather than a
plausible-looking string, because instance fleets are not worker classes.

### 3.2 A third state layout has appeared

| Source | Layout |
| :--- | :--- |
| PRD v2 §2.1 | `state/<domain>/<repo>/<pipeline>/` |
| PRD v3 §6 FM-03 | `teams/<domain>/<project>/<workload>/` |
| `synthesizer._render_backend` | `<name_prefix>/<run_id>/terraform.tfstate` |
| `team_resolver.state_key` | `teams/<team_id>/<workload_id>/terraform.tfstate` |

**RULED:** `teams/<domain_id>/<project_id>/<workload_id>/terraform.tfstate`.

**Implemented** in `team_resolver.state_key()`, now three validated segments, with the
synthesizer defaulting `domain_id` to the team when unstated so single-team callers keep
working.

**Migration hazard, stated rather than assumed:** keys written before this ruling have two
segments. Terraform does not error on the change -- it finds an empty key, reports no state,
and plans to CREATE everything already deployed. Move the object first (`aws s3 mv` or
`terraform init -migrate-state`) and confirm the next plan is a no-op before approving it.

### 3.3 Snowflake auto-suspend is in the wrong module

`modules/warehouse-snowflake-aws/` creates exactly four resource types, all AWS:
`aws_iam_role`, `aws_iam_role_policy`, `aws_sqs_queue`, `aws_sqs_queue_policy`. It is the
AWS-side handshake — storage integration role, external ID, Snowpipe queue. It declares no
Snowflake provider.

**RULED:** AWS-native modules remain the core default; Snowflake is authored as an optional
registry-composed module through the `architect` skill.

**Implemented** as a regression lock rather than code: `tests/test_modules.py` pins the
catalog's third-party providers to a reviewed allowlist (`databricks/databricks`, which
predates the ruling) and fails if `snowflake/snowflake` appears. Verified by planting one and
watching the test fail. PRD v3 §12's Snowflake `auto_suspend` therefore stays unimplemented
by decision, not by omission.

---

## 4. Recommended sequencing

1. **Split the document.** Mark §2 pillars 11–13, FR-11 and FR-12 as *proposed*, not
   *approved*. The header says APPROVED ARCHITECTURE SPECIFICATION and §8's boxes are ticked,
   so a reader cannot currently tell shipped from planned. That is the same defect flagged in
   the v1 review, in a different place.
2. **Decide 3.1 and 3.2 before writing code.** Both are one-paragraph rulings that unblock
   implementation; both become expensive to change once HCL is generated against them.
3. **Then build, cheapest first:** pillars 11–13 in `grill-me` (documentation, testable the
   same way pillars 8–10 were), then Redshift `max_capacity` and partition projection (small,
   contained module changes), then FR-11 sizing once 3.1 is settled.
4. **Treat FR-12 as its own increment.** Lake Formation TBAC touches cross-account permissions
   and cannot be verified without a consumer account, which is the same reason MINUS-126 was
   declined earlier.
5. **Defer §12 Snowflake auto-suspend** pending the provider decision in 3.3.
