---
name: grill-me
description: Gather complete requirements before building ANY system (web/app backend, API/service, data pipeline, ML/inference, batch/event system, internal tool, or anything else), and stress-test uncertain plans. Grounded in standard requirements engineering — functional vs. non-functional requirements, the ISO/IEC 25010 quality model and FURPS+ as the non-functional checklist, the 5 W's, and MoSCoW for scope. Interrogate one question at a time with a recommended default, quantify vague terms, cross-question contradictions, flag the requirements people forget, then map answers to a governed blueprint. Use when the user wants to build something, when a request is vague/too-simple/too-broad, or when they say "grill me".
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

## Map to a MinusOps blueprint + inputs

As answers land, map them to a governed blueprint and its required inputs. The supported
blueprint today is **`aws-data-pipeline-standard`**, inputs: `environment`, `region`, `owner`,
`ingestion_mode` (`batch`|`streaming`), `daily_data_gb` (verify against `core/blueprints.py` —
it is the source of truth). When the requirements match, end by emitting the exact command:

```bash
minusctl create "governed AWS data pipeline" \
  --input owner=<team> --input environment=<env> --input region=<region> \
  --input ingestion_mode=<batch|streaming> --input daily_data_gb=<n> --generate
```

For a system class with no existing blueprint (e.g., a web backend), **hand off to the
[`architect`](../architect/SKILL.md) skill** with the gathered requirements: it deep-researches
current services and reference architectures, picks the best-fit for these requirements, and
synthesizes governed Terraform that flows through the same deploy gate — so a missing blueprint is
not a dead end. Pass the MoSCoW-prioritized requirements spec (Steps 0–2) as its input.

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

## Exit criteria

Stop interrogating when: the goal, scope, and system class are set; the **Must-have** functional
capabilities are listed; each relevant non-functional requirement has a **number or an explicit
deferral**; contradictions are resolved; MoSCoW prioritization is done; and the blueprint +
inputs (or a requirements spec) are confirmed. **Summarize the gathered requirements back to the
user** and show the resulting `minusctl create` command before generating anything.

## References

Grounded in standard requirements engineering, not one domain: functional vs. non-functional
requirements; the **ISO/IEC 25010** software product quality model (functional suitability,
performance efficiency, compatibility, usability, reliability, security, maintainability,
portability); **FURPS+** (Robert Grady); **MoSCoW** prioritization; the 5 W's elicitation
heuristic; and quantified NFRs + capacity estimation from system-design practice.
