---
name: grill-me
description: Gather complete requirements before generating ANY infrastructure (data pipeline, website/app backend, event system, or anything else), and stress-test uncertain plans. Interrogate one question at a time with a recommended answer, cross-question contradictions, pin down vague terms, flag missing pieces (bugs/gaps), then map the answers to a governed MinusOps blueprint and inputs. Use when the user wants to build something, when a request is vague/too-simple/too-broad, or when they say "grill me".
---

# Grill Me — Requirements Interrogation

Spend the first questions clarifying, not drawing architecture. A one-line ask ("build me a
data pipeline") is never enough: the right design changes completely with who the users are,
how fresh the data must be, and how much of it there is. Turn the request into a complete,
contradiction-free requirements set, then map it to a governed blueprint and the exact
`minusctl` command. Ask **one question at a time**, each with a recommended default.

## Step 0 — What are we building?

Do not assume "pipeline." Establish the system class first, because the whole architecture
hinges on it. Offer concrete options with a recommendation:

- **Batch / analytics data pipeline** — S3/Glue/Athena medallion *(the supported blueprint today)*.
- **Streaming / real-time pipeline** — sub-minute freshness.
- **Web / app backend infra** — API + datastore + cache + CDN.
- **Event-driven / queue system** — async fan-out, workers.
- **Something else** — have the user describe it.

If the chosen class has no blueprint yet, say so plainly and gather a requirements spec instead
of pretending to generate it.

## The requirements frame (ask in roughly this order)

Two buckets. Lead with the highest-leverage unknowns — usually **WHO** and the **latency SLA**,
because they unblock every later decision.

### Functional — what the system must do
- **Who** — the end users and how technical they are (a marketing analyst running SQL, an ML
  team feeding a feature store, a product app calling an API, execs viewing dashboards). The
  user determines the architecture more than anything else.
- **What** — the specific data or capability (raw events, aggregated metrics, historical
  snapshots; for a backend: which entities and operations).
- **How** — how they access it (direct SQL, a BI tool like Tableau/PowerBI, a REST/GraphQL
  API, a scheduled export, a live dashboard).

### Non-functional — how the system must behave
- **Latency / freshness SLA** — seconds (streaming), minutes (micro-batch), or hours/days
  (batch). This drives the entire compute choice.
- **Volume / scale** — GB vs TB vs PB per day (or requests/sec for a backend). Decides
  single-node tools (Pandas/Polars) vs distributed compute (Spark) and the storage layout.
- **Availability** — tolerance for downtime; single-AZ, multi-AZ, or multi-region.
- **Data retention** — keep forever vs archive/expire (e.g., after 90 days). Cost and compliance.
- **Security / compliance / residency** — PII, encryption, audit, and any region constraints.
- **Budget** — a cost ceiling; drives sizing, lifecycle, and commitment modeling.
- **Growth** — expected 6–12 month scale, so the design isn't boxed in.

## Cross-question, recommend, and catch problems

This is the value of the skill — not just collecting answers:

- **Recommend a default for every question** with one line of reasoning, so the user can
  accept, reject, or tweak in a word.
- **Catch contradictions** and surface them with a proposed resolution — e.g. "sub-second
  latency" + "nightly batch"; "petabytes/day" + "a single Postgres"; "keep data forever" +
  "minimize cost, no archive tier".
- **Pin down vague terms** — "fast", "real-time", "a lot of data", "cheap" become numbers
  (an SLA, GB/day, a dollar ceiling) before they drive a decision.
- **Flag missing pieces (the bugs/gaps)** the user didn't mention: no auth, no retention
  policy, no DR/backup, unbounded cost, PII without encryption, no owner for FinOps, no
  monitoring/alerting, no idempotency/replay for a pipeline.

## Codebase rule

If a question can be answered from the repo (existing blueprints, inputs, patterns, configs),
inspect it instead of asking. Ask the user only for intent, priorities, tradeoffs, and business
facts that are not discoverable locally. For deciding *whether* to ask at all on a borderline
point, the companion `resolve-ambiguity` skill applies.

## Map to a MinusOps blueprint + inputs

As answers land, map them to a governed blueprint and its required inputs. The supported
blueprint today is **`aws-data-pipeline-standard`**, inputs:
`environment`, `region`, `owner`, `ingestion_mode` (`batch`|`streaming`), `daily_data_gb`
(verify against `core/blueprints.py` — it is the source of truth). When the requirements match,
end by emitting the exact command:

```bash
minusctl create "governed AWS data pipeline" \
  --input owner=<team> --input environment=<env> --input region=<region> \
  --input ingestion_mode=<batch|streaming> --input daily_data_gb=<n> --generate
```

For a system class with no blueprint yet (e.g., web backend), output a structured requirements
spec and label it a roadmap item — do not fabricate a generate command.

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
move to the next highest-leverage branch; when they reject, ask what's needed to understand
the rejected branch.

## Exit criteria

Stop interrogating when: the system class is chosen; functional **Who / What / How** are
answered; each non-functional SLA (latency, volume, availability, retention, security, budget)
has a number or an explicit, recorded deferral; every contradiction is resolved; and the
blueprint + inputs (or a requirements spec) are confirmed. **Summarize the gathered
requirements back to the user** and show the resulting `minusctl create` command before
generating anything.
