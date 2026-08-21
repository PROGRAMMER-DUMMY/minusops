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

## 3. Conflicts that need a decision, not code

### 3.1 Two sizing authorities that will disagree

§3 sizes the Glue **worker class** from the data model. `modules.compute_tier()` selects the
**compute module** from volume, with recorded crossovers: under 1 TB/day Glue, 1–5 TB EMR
Serverless, above 5 TB EMR on EC2 with Spot task fleets.

At 10 TB/day append-only these give incompatible answers. §3 says `G.1X`; `compute_tier` says
EMR on EC2, where Glue worker classes do not exist as a concept. Whichever runs second wins,
and nothing detects the contradiction.

The two are answering different questions and both are useful. What is missing is the
precedence rule: volume selects the engine, and the data model then sizes the worker *within*
Glue and is ignored for EMR. That rule should be written down before either is implemented.

### 3.2 A third state layout has appeared

| Source | Layout |
| :--- | :--- |
| PRD v2 §2.1 | `state/<domain>/<repo>/<pipeline>/` |
| PRD v3 §6 FM-03 | `teams/<domain>/<project>/<workload>/` |
| `synthesizer._render_backend` | `<name_prefix>/<run_id>/terraform.tfstate` |
| `team_resolver.state_key` | `teams/<team_id>/<workload_id>/terraform.tfstate` |

Four descriptions, three shapes, one system. This has now survived three PRD revisions and is
drifting further apart rather than converging. FM-03 is the blast-radius control; a mitigation
that names a path the generator does not emit is documentation, not a control.

### 3.3 Snowflake auto-suspend is in the wrong module

`modules/warehouse-snowflake-aws/` creates exactly four resource types, all AWS:
`aws_iam_role`, `aws_iam_role_policy`, `aws_sqs_queue`, `aws_sqs_queue_policy`. It is the
AWS-side handshake — storage integration role, external ID, Snowpipe queue. It declares no
Snowflake provider.

`auto_suspend` is a property of a Snowflake **warehouse**, which lives in the `snowflake`
Terraform provider. Implementing §12 as written means adding a third-party provider to the
catalog. That is an architectural decision with credential, versioning, and blast-radius
consequences, not a module tweak — and it sits oddly beside the FM-02 stance that no module
takes a credential as a variable, since the Snowflake provider needs one.

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
