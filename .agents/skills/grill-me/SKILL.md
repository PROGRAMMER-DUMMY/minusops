---
name: grill-me
description: Gather complete requirements before building ANY system (web/app backend, API/service, data pipeline, ML/inference, batch/event system, internal tool, or anything else), and stress-test uncertain plans. Grounded in standard requirements engineering — functional vs. non-functional requirements, the ISO/IEC 25010 quality model and FURPS+ as the non-functional checklist, the 5 W's, and MoSCoW for scope. Interrogate one question at a time with a recommended default, quantify vague terms, cross-question contradictions, flag the requirements people forget, interrogate the 7 data-engineering pillars and the TerraShark failure modes, then map answers to vetted modules for composition. Use when the user wants to build something, when a request is vague/too-simple/too-broad, or when they say "grill me".
---

# Grill Me — Requirements Interrogation

Spend the first questions clarifying, not drawing architecture. A one-line ask ("build me X")
is never enough — the right design changes completely with the goal, the users, the scale, and
the quality bar. This skill is **not** tied to one domain: it follows standard requirements
engineering so it works for a web backend, an API, a data pipeline, an ML service, internal
tooling, or anything else. Ask **one question at a time**, each with a recommended default.

The method (all domain-agnostic):
- **Functional vs. non-functional** requirements — *what* it does vs. *how well* it does it.
- **ISO/IEC 25010** quality model + **FURPS+** (Functionality, Usability, Reliability,
  Performance, Supportability, + constraints) as the non-functional **checklist** — NFRs are
  the part people forget, so a checklist is the point.
- The **5 W's + How** to make each capability concrete.
- **MoSCoW** (Must / Should / Could / Won't) to bound scope.
- **Quantify** every non-functional target — a number, not an adjective.

## Step 0 — Frame it (goal, scope, stakeholders, constraints)

Clarify the "why" before the "what"; it's cheap and prevents building the wrong thing.

- **Goal & success criteria** — what problem, and what does "working" look like as a measurable
  outcome.
- **System class** — web/app backend · API or service · data/analytics pipeline · ML/inference ·
  batch/event/queue · internal tool · *something else*. Don't assume; this drives everything.
- **Stakeholders & decider** — who uses it, who owns/operates it, who signs off.
- **Scope boundaries** — explicitly in vs. out (the non-goals).
- **Hard constraints & assumptions** — budget, deadline, team/skills, existing stack, cloud,
  region, compliance.

## Step 1 — Functional requirements (what it does)

Capture core capabilities as **"<user/client> should be able to <do X>"** — that *is* the
system, so do it first. For each, use the **5 W's + How** to make it concrete: who triggers it,
what exactly, when/where, why, and how they interact (UI, REST/GraphQL API, direct SQL, a BI
tool, a scheduled job, an event). Cover the primary happy path **and** the important edge/failure
cases. Then **MoSCoW** each capability so scope is bounded, not infinite.

## Step 2 — Non-functional requirements (how well) — the checklist

Run the ISO 25010 / FURPS+ checklist and **quantify** each one that matters ("< 200 ms p99", not
"fast"). Only ask the categories relevant to the chosen system class:

- **Performance efficiency** — latency / throughput targets; capacity (requests/sec, data
  volume/day, concurrency).
- **Reliability / availability** — uptime SLA (e.g. 99.9%), fault tolerance, backup + disaster
  recovery (RTO / RPO).
- **Security** — authentication / authorization, encryption, data classification / PII, secrets,
  audit, threat surface.
- **Compliance & data residency** — GDPR / HIPAA / SOC 2; region or residency constraints.
- **Usability / accessibility** — who the users are and how technical; accessibility needs.
- **Compatibility / integration** — systems it must interoperate with; the interfaces / APIs /
  events it exposes or consumes.
- **Maintainability / supportability** — observability (logs, metrics, traces, alerts); deploy +
  rollback; who operates it.
- **Portability** — target environments, cloud / region, tolerance for lock-in.
- **Cost / budget** — a ceiling; drives sizing, lifecycle, and commitments.
- **Scalability / growth** — expected 6–12-month scale, so the design isn't boxed in.

## Step 3 — Capacity sanity-check (when it affects the design)

A rough back-of-envelope — requests/sec, storage/day, bandwidth, concurrency — decides
single-node vs. distributed compute, caching / CDN, sharding, and batch vs. streaming. Do it
*before* choosing an architecture, not after.

## Step 3.4 — The 7 pillars (data pipelines only)

Skip this for a web backend or an internal tool. For a **data pipeline**, Steps 0-3 stop short
of the answers the generator actually needs, and the gap is not academic: the 2026-08-17 live
run provisioned three empty buckets and a Glue job that crashed, because nobody had been asked
where the data comes from or what triggers the job.

Ask in this order. **Pillar 1 is question one** — everything downstream is shaped by it, and a
lake with no inbound path is the one failure that cannot be fixed after the fact.

| # | Pillar | Ask | Answers map to |
| :-- | :--- | :--- | :--- |
| **1** | **Ingestion source** | "Where does the data come from *today*?" Give the archetypes, don't ask open-ended: an operational **database** (CDC), a **SaaS** system, a partner dropping **files**, a system that **pushes events** to you, or data already **landing in S3**. | `ingestion-dms` · `ingestion-appflow` · `ingestion-sftp` · `ingestion-webhook` · `ingest-firehose` · none |
| **2** | **Storage & format** | Zones (bronze/silver/gold, or their names for them), file format, partitioning, retention per zone, and whether any zone holds PII. | `storage-medallion-s3` · `table-format-iceberg` |
| **3** | **Compute engine** | Volume per run and the transformation's shape. **SQL-only transformations do not need Spark** — that is `transform_engine: "dbt"`, and it deletes the whole Glue bill. | `compute-glue-etl` · `compute-emr-serverless` · dbt-on-Athena |
| **4** | **Orchestration** | What starts a run: a **schedule** (which cadence, exactly), an **event** (a file landing), or a human. "Whatever's easiest" means nothing will ever start it. | `orchestrator-stepfunctions` · `orchestrator-mwaa` |
| **5** | **Data quality** | Which assertions must hold, and **what happens to a row that fails** — is the run aborted, or is the row quarantined and the run continues? Most teams say "abort" and mean "quarantine". | `dq-great-expectations` (+ quarantine zone) |
| **6** | **Serving layer** | Who reads the output and with what — ad-hoc SQL, a BI tool, a reverse ETL, another pipeline. Concurrency matters more than volume here. | `query-athena` · `consumption-redshift-serverless` |
| **7** | **Alert routing** | **Three questions, not one.** Who is paged when the pipeline *crashes*; who is told when *data quality* fails; who is told about *spend*. One inbox for all three is why nobody reads the inbox. | `governance-observability` 3-tier routing |

Two of these are the ones people skip and then discover in production: **Pillar 4** (a pipeline
nobody scheduled never runs) and **Pillar 5**'s failure branch (one bad row kills the run).
Push on both even when the user waves them off.

## Step 3.5 — Failure-mode pre-flight (FM-01..05)

Steps 0–3 ask what the system must do. This step asks how the *Terraform for it* typically
breaks, **before** any HCL exists — the TerraShark taxonomy (`NextStackHelper.md` §2, mirrored in
code as `architecture_decision.FAILURE_MODES`). Ask only the modes the answers put in play; note
the ones you rule out and why.

| ID | Ask | Put in play by |
| :--- | :--- | :--- |
| **FM-01** Identity churn | "Is this a refactor of existing infrastructure, or greenfield? If a refactor, which addresses move?" Capture `old_address -> new_address` tuples — they drive `moved {}` generation. Prefer `for_each` over `count` on anything keyed by a mutable list. | any refactor, module upgrade, or resource keyed on a list |
| **FM-02** Secret exposure | "Which inputs are credentials, and where do they come from?" No hardcoded variable defaults; `sensitive = true` hides output, **not** state; plan JSON must not land in a CI artifact. | any credential, connection string, or API token |
| **FM-03** Blast radius | "Which environments share this state file, and what is the largest thing one apply can destroy?" One state per environment; state locking on. | more than one environment, or persistent data resources |
| **FM-04** CI drift | "Will this apply from CI? Is `.terraform.lock.hcl` committed, and are provider versions pinned?" The apply must consume the *reviewed* `tfplan`, never re-plan. | any pipeline-driven apply |
| **FM-05** Compliance gate gaps | "Which policies must be machine-enforced rather than documented?" Prefer a blocking OPA/Checkov rule over a paragraph; no blanket `ignore_changes`. | any stated compliance, audit, or regulatory requirement |

Record the modes the design actively mitigates on the decision record:
`python core/architecture/architecture_decision.py add-failure-mode <path> FM-03`.

## How to ask — cross-question, recommend, catch problems

This is the value of the skill, not just collecting answers:

- **One question at a time**, highest-leverage first (usually the goal, the system class, then
  the dominant NFR — latency or scale).
- **Recommend a default** with one line of reasoning for every question, so the user can accept,
  reject, or tweak in a word.
- **Quantify vague terms** — "fast", "scalable", "real-time", "cheap", "a lot of data" become a
  number (an SLA, req/s, a dollar ceiling, GB/day) before they drive a decision.
- **Cross-question contradictions** with a proposed resolution — e.g. "99.99% uptime" + "single
  instance, no DR"; "sub-second" + "nightly batch"; "petabytes" + "one Postgres".
- **Flag the requirements people forget** — the checklist exists precisely because NFRs get
  dropped: no auth, no backups, no observability, no rate limiting, unbounded cost, no owner,
  PII without encryption.

## Codebase rule

If a question can be answered from the repo (existing blueprints, inputs, patterns, configs),
inspect it instead of asking. Ask the user only for intent, priorities, tradeoffs, and business
facts not discoverable locally. For deciding *whether* to ask on a borderline point, the
companion `resolve-ambiguity` skill applies.

## Map to modules, not to a blueprint

There is no single production blueprint to map onto. Generation is **requirements -> research
-> compose -> govern**: the answers select vetted modules from the catalog, which the
synthesizer composes into one governed Terraform root.

As answers land, name the modules they imply (the right-hand column of the pillar table) and
say so out loud, so the user can object before anything is generated. Check the choice against
the registry rather than memory:

```bash
python core/generation/modules.py match "<the requirements so far>"
```

Then hand off to [`architect`](../architect/SKILL.md) with the gathered requirements. It
researches the current services, confirms the module set, records the decision, and calls the
synthesizer.

> **Do not** emit `minusctl create ... --generate` or map onto `aws-data-pipeline-standard`.
> That blueprint is the cached demo fixture behind `minusctl demo` and the golden tests, not
> the production generator; `AGENTS.md` calls guidance that points at it stale. Requirements
> first, always.

## Question shape

```markdown
Question: ...

Options:
- Option A: ...
- Option B: ...

Recommended answer: ...

Compatibility: ...

Feedback note: ...
```

Two or three options. Keep compatibility and feedback notes specific to the current decision.
Avoid multi-part questions unless the parts are inseparable. When the user accepts or modifies,
move to the next highest-leverage branch; when they reject, ask what's needed to understand the
rejected branch.

## Exit criteria — write the requirements record (the generator is gated on it)

Stop interrogating when: the goal, scope, and system class are set; the **Must-have** functional
capabilities are listed; each non-functional axis (latency, scale, availability, retention,
security, budget) has a **number or an explicit `deferred: <reason>`**; contradictions are
resolved; and MoSCoW prioritization is done. Then:

1. **Summarize the gathered requirements back to the user** for confirmation.
2. **Write the requirements record** the generator is gated on: start from
   `python core/architecture/requirements.py template`, fill `goal`, `system_class`, `functional` (≥1
   capability), and every `non_functional` axis (value or `deferred: …`), save it as the run's
   `requirements.json`, and verify with `python core/architecture/requirements.py check <path>`.
3. Hand off to [`architect`](../architect/SKILL.md), which calls the synthesizer with that record.

The architecture decision that follows is held to a **4-part output contract** — the record is
incomplete, and synthesis stays blocked, until all four are answered:

| Part | Field on `architecture_decision.json` |
| :--- | :--- |
| Assumptions | `assumptions` — what is taken as true and would invalidate the design if wrong |
| Tradeoffs | `alternatives` — each `name \| decision \| reason` |
| Validation | `validation` — the checks that prove this design correct (validate, SEC scan, conformance, BCM evidence) |
| Rollback | `rollback` — how the change is undone once applied |

`failure_modes` (Step 3.5) is optional, but an id outside FM-01..05 is refused rather than stored.

The synthesizer is **fail-closed**: without a complete record it refuses to generate and lists
what's unanswered. A vague request can never be silently turned into infrastructure — it's blocked
until requirements are gathered and justified. Don't bypass with `--allow-incomplete` for real
work (it's a demo/testing override and is audited).

## References

Grounded in standard requirements engineering, not one domain: functional vs. non-functional
requirements; the **ISO/IEC 25010** software product quality model (functional suitability,
performance efficiency, compatibility, usability, reliability, security, maintainability,
portability); **FURPS+** (Robert Grady); **MoSCoW** prioritization; the 5 W's elicitation
heuristic; and quantified NFRs + capacity estimation from system-design practice.
