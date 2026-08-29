---
name: grill-me
description: Gather complete requirements before building ANY system (web/app backend, API/service, data pipeline, ML/inference, batch/event system, internal tool, or anything else), and stress-test uncertain plans. Grounded in standard requirements engineering - functional vs. non-functional requirements, the ISO/IEC 25010 quality model and FURPS+ as the non-functional checklist, the 5 W's, and MoSCoW for scope. Interrogate one question at a time with a recommended default, quantify vague terms, cross-question contradictions, flag the requirements people forget, then interrogate the 18 enterprise data-engineering pillars and the TerraShark failure modes, deriving each later question from the answers already given. Use when the user wants to build something, when a request is vague/too-simple/too-broad, or when they say "grill me".
---

# Grill Me - Requirements Interrogation

Spend the first questions clarifying, not drawing architecture. A one-line ask ("build me X")
is never enough - the right design changes completely with the goal, the users, the scale, and
the quality bar. This skill is **not** tied to one domain: it follows standard requirements
engineering so it works for a web backend, an API, a data pipeline, an ML service, internal
tooling, or anything else. Ask **one question at a time**, each with a recommended default.

The method (all domain-agnostic):

- **Functional vs. non-functional** requirements - *what* it does vs. *how well* it does it.
- **ISO/IEC 25010** quality model + **FURPS+** (Functionality, Usability, Reliability,
  Performance, Supportability, + constraints) as the non-functional **checklist** - NFRs are
  the part people forget, so a checklist is the point.
- The **5 W's + How** to make each capability concrete.
- **MoSCoW** (Must / Should / Could / Won't) to bound scope.
- **Quantify** every non-functional target - a number, not an adjective.

## Step 0 - Frame it (goal, scope, stakeholders, constraints)

Clarify the "why" before the "what"; it is cheap and prevents building the wrong thing.

- **Goal & success criteria** - what problem, and what "working" looks like as a measurable
  outcome.
- **System class** - web/app backend, API or service, data/analytics pipeline, ML/inference,
  batch/event/queue, internal tool, or *something else*. Do not assume; this drives everything.
- **Stakeholders & decider** - who uses it, who owns and operates it, who signs off.
- **Scope boundaries** - explicitly in vs. out (the non-goals).
- **Hard constraints & assumptions** - budget, deadline, team and skills, existing stack,
  cloud, region, compliance.

## Step 1 - Functional requirements (what it does)

Capture core capabilities as **"<user/client> should be able to <do X>"** - that *is* the
system, so do it first. For each, use the **5 W's + How** to make it concrete: who triggers it,
what exactly, when and where, why, and how they interact (UI, REST/GraphQL API, direct SQL, a
BI tool, a scheduled job, an event). Cover the primary happy path **and** the important edge
and failure cases. Then **MoSCoW** each capability so scope is bounded, not infinite.

## Step 2 - Non-functional requirements (how well) - the checklist

Run the ISO 25010 / FURPS+ checklist and **quantify** each one that matters ("< 200 ms p99",
not "fast"). Only ask the categories relevant to the chosen system class:

- **Performance efficiency** - latency and throughput targets; capacity (requests/sec, data
  volume per day, concurrency).
- **Reliability / availability** - uptime target, fault tolerance, backup and disaster
  recovery (RTO / RPO). State a target only if the design can deliver and measure it; an
  availability figure nothing verifies is a claim, not a requirement.
- **Security** - authentication and authorization, encryption, data classification and PII,
  secrets, audit, threat surface.
- **Compatibility** - the systems it must interoperate with, the interfaces and events it
  exposes or consumes.
- **Maintainability & supportability** - who operates it, how it is deployed, how it rolls
  back, what observability it needs.

## Step 3 - Capacity sanity-check (when it affects the design)

A rough back-of-envelope - requests/sec, storage per day, bandwidth, concurrency - decides
single-node vs. distributed compute, caching and CDN, sharding, and batch vs. streaming. Do it
*before* choosing an architecture, not after.

## Step 3.4 - The 19 enterprise pillars (data pipelines only)

Skip this for a web backend or an internal tool. For a **data pipeline**, Steps 0-3 stop short
of the answers the generator actually needs, and the gap is not academic: the 2026-08-17 live
run provisioned three empty buckets and a Glue job that crashed, because nobody had been asked
where the data comes from or what triggers the job.

**Do not transcribe the pillars into your reply from memory.** They live in
`core/architecture/pillars.py`, which is the single source of truth for the question, its
options, the modules each option maps to, and the follow-ups. Read them from there:

```bash
python core/architecture/pillars.py list                  # all 19, by phase
python core/architecture/pillars.py next --answered ingestion_source,storage_format
python core/architecture/pillars.py show partitioning daily_gb=50 partitions_per_day=24
```

### Ask them in order, and derive as you go

**Pillar 0 is question one, and it is not a choice.** Residency, key management, retention
floors and spend ceilings are set above the team -- AWS's own guidance puts RTO, RPO and
residency with the business, and the architect translating them. Asking an architect to pick
a region a policy already fixed does not produce a decision, it produces a contradiction that
surfaces at audit. Establish what is already fixed, then treat it as the boundary every later
answer must fall inside.

**Pillar 1 is the first real choice.** Everything downstream is shaped by it, and a lake with
no inbound path is the one failure that cannot be fixed after the fact.

The pillars are grouped into five phases:

| Phase | Pillars | Covers |
| :--- | :--- | :--- |
| 0 | 0 | Fixed policy: residency, mandated key management, retention floors, spend ceilings |
| 1 | 1-4 | Ingestion source, medallion storage and format, partitioning and retention, data quality and quarantine |
| 2 | 5-8 | Compute engine, worker sizing, runtime packages, orchestration |
| 3 | 9-12 | Multi-AZ networking, account boundaries, multi-region DR, fine-grained access control |
| 4 | 13-18 | Serving, CI/CD and secrets, artifacts, criticality, alert routing and log retention, proving |

**The early answers are the context for the later questions.** This is the part a flat
questionnaire cannot do. Once volume and partitioning are known, the tool computes what those
answers imply and the next question carries the number rather than a generic default:

```bash
python core/architecture/pillars.py derive daily_gb=2 partitions_per_day=24 transform_shape=spark
```

> `partitioning`: 2 GB/day across 24 partitions is 85 MB each, under the 128 MB floor for
> mixed reads and under the 128 MB Parquet block size. Every query pays per-object request
> and catalog overhead for data that would fit in one object.

That is a small-file problem being designed in at the partition key, caught during the
interview rather than at the first slow query. Feed each answer back in and ask the next
question with what it already decided.

**A derivation that says `determinable: false` is naming your next question.** It tells you
which fact is missing and why the recommendation cannot be made without it. Do not fill the
gap with a plausible default - a worker count derived from a volume nobody stated looks
exactly like one derived from a real volume, and the operator cannot tell them apart.

### Depth, not just breadth

Each pillar carries **follow-ups conditioned on the answer given** and the one thing people
usually leave out. `show` prints both. Ask the follow-ups - they are where the pillar earns
its place, and several of them only make sense after the main choice:

- **Pillar 4** (data quality): everyone specifies the assertion; few specify what happens to
  the failing row, or at what failure rate a quarantined run becomes a failed run.
- **Pillar 8** (orchestration): a missing-input alarm is not the same alarm as a job-failure
  alarm, and only one of them fires when the file never lands.
- **Pillar 9** (networking): without an S3 gateway endpoint every lake read is billed as NAT
  traffic. Nothing fails; it only shows up on the invoice.
- **Pillar 12** (access control): Lake Formation **restricts** a column, it does not mask it.
  A requirement for a masked value is a requirement on the transform, before Gold.
- **Pillar 14** (CI/CD, control plane hosting and secrets): three questions that decide each
  other. **Control plane hosting** comes first - an operator laptop, a CI runner, or
  in-cluster on EKS - because it decides what CI/CD authenticates as: the ambient CLI chain,
  OIDC, or **IRSA** binding a service account to a role. Then the credential: OIDC with
  AssumeRole issues a short-lived one per run, while a long-term **AKIA** key in CI is the
  credential most often found in a breach post-mortem. Then the key hierarchy: rotating
  credentials belong in Secrets Manager, configuration that does not rotate in Parameter
  Store, and `kms:Decrypt` on the wrong principal undoes the bucket policy above it.
- **Pillar 17** (alert routing and logs): CloudWatch log retention defaults to
  **Never expire**, so the cost is silent, permanent, and invisible until someone reads the
  bill. Set it per log group, in days.
- **Pillar 18** (proving): the rollback is written down far more often than it is executed.
  Prove the pipeline with `minusctl prove --execute`, the live five-hop harness that seeds
  Bronze and checks quarantine routing. `minusctl seed --execute` is the older three-hop form
  and does neither.

Record the numeric answers in the record's `pillar_facts` block so the synthesizer can consume
them - the fields are listed by `python core/architecture/pillars.py derive --json`.

## Step 3.5 - Failure-mode pre-flight (FM-01..05)

Steps 0-3 ask what the system must do. This step asks how the *Terraform for it* typically
breaks, **before** any HCL exists - the TerraShark taxonomy (`NextStackHelper.md` section 2,
mirrored in code as `architecture_decision.FAILURE_MODES`). Ask only the modes the answers put
in play; note the ones you rule out and why.

| ID | Ask | Protects against |
| :--- | :--- | :--- |
| **FM-01** Identity churn | "Is this a refactor of existing infrastructure, or greenfield? If a refactor, which addresses move?" | A destroy-and-recreate of a stateful resource, via `moved {}` blocks |
| **FM-02** Secret exposure | "Which inputs are credentials, and where do they come from?" | Hardcoded variable defaults and non-sensitive outputs |
| **FM-03** Blast radius | "Which environments share this state file, and what is the largest thing one apply can destroy?" | A shared state file, via per-environment state and locking |
| **FM-04** CI drift | "Will this apply from CI? Is `.terraform.lock.hcl` committed, and are provider versions pinned?" | CI applying a plan the reviewer never saw |
| **FM-05** Compliance gate gaps | "Which policies must be machine-enforced rather than documented?" | A written policy nothing evaluates, via a blocking OPA rule |

Record the modes the design actively mitigates on the decision record:
`python core/architecture/architecture_decision.py add-failure-mode <path> FM-03`.

## How to ask - cross-question, recommend, catch problems

This is the value of the skill, not just collecting answers:

- **One question at a time**, highest leverage first (the system class, then the dominant NFR
  - latency or scale).
- **Always carry a recommended default**, so the user can accept, reject, or tweak in a word.
- **Quantify anything vague.** "Fast", "big", "highly available", "cheap" each become a number
  (a target, requests/sec, a dollar ceiling, GB per day) before they drive a decision.
- **Cross-question contradictions out loud.** "99.99% uptime" plus "one instance, no DR";
  "sub-second" plus "nightly batch"; "petabytes" plus "one Postgres"; a stated budget below
  the architecture's own forecast. Raise it while the architecture can still change, which is
  now - raising it afterwards only changes the number.
- **Flag the requirements people forget** - the ones that are silently dropped: no auth, no
  backups, no observability, no rate limiting, unbounded cost, no owner, PII without
  encryption. Each pillar names its own; `show` prints them.
- **Do not state a figure the design cannot deliver.** An availability target, a discount
  rate, a cost - if nothing in the generated stack produces or verifies it, ask for it rather
  than offering it as a default.

## Codebase rule

If a question can be answered from the repo (existing blueprints, inputs, patterns, configs),
inspect it instead of asking. Ask the user only for intent, priorities, tradeoffs, and business
facts not discoverable locally. For deciding *whether* to ask on a borderline point, the
companion `resolve-ambiguity` skill applies.

## Map to modules, not to a blueprint

There is no single production blueprint to map onto. Generation is **requirements -> research
-> composition**: the architect skill researches current services, and the synthesizer composes
vetted modules into one governed Terraform root.

As answers land, name the modules they imply (`maps_to` on each pillar) and say so out loud, so
the user can object before anything is generated. Check the choice against the registry rather
than memory:

```bash
python core/generation/modules.py match "<the requirements so far>"
```

> **Do not** emit `minusctl create ... --generate` or map onto `aws-data-pipeline-standard`.
> That blueprint is the cached demo fixture behind `minusctl demo` and the golden tests, not
> the production generator; `AGENTS.md` calls guidance that points at it stale. Requirements
> first, always.

Then hand off to [`architect`](../architect/SKILL.md) with the gathered requirements. It
researches the current services, confirms the module set, records the decision, and calls the
synthesizer.

## Question shape

```markdown
Question: ...

Options:
- Option A: ...
- Option B: ...
- Option C: ...

Recommended answer: ...

Compatibility: ...

Feedback note: ...
```

Two or three options. Keep compatibility and feedback notes specific to the current decision.
Avoid multi-part questions unless the parts are inseparable. When the user accepts or modifies,
move to the next highest-leverage branch; when they reject, ask what is needed to understand
the rejected branch.

## Exit criteria - write the requirements record (the generator is gated on it)

Stop interrogating when: the goal, scope, and system class are set; the **Must-have** functional
capabilities are listed; each non-functional axis (latency, scale, availability, retention,
security, budget) has a **number or an explicit `deferred: <reason>`**; every applicable pillar
is answered or explicitly deferred; contradictions are resolved; and MoSCoW prioritization is
done. Then:

1. **Summarize the gathered requirements back to the user** for confirmation.
2. **Write the requirements record** the generator is gated on: start from
   `minusctl decision template` or `python core/architecture/requirements.py template`, fill
   `goal`, `system_class`, `functional` (at least one capability), every `non_functional` axis
   (a value or `deferred: ...`), the `pillars` block, and the numeric `pillar_facts`. Save it
   as the run's `requirements.json` and verify with
   `python core/architecture/requirements.py check <path>`.
3. Hand off to [`architect`](../architect/SKILL.md), which calls the synthesizer with that record.

The architecture decision that follows is held to a **4-part output contract** - the record is
incomplete, and synthesis stays blocked, until all four are answered:

| Part | Field on `architecture_decision.json` |
| :--- | :--- |
| Assumptions | `assumptions` - what is taken as true and would invalidate the design if wrong |
| Tradeoffs | `alternatives` - each `name \| decision \| reason` |
| Validation | `validation` - the checks that prove this design correct (validate, SEC scan, conformance, BCM evidence) |
| Rollback | `rollback` - how the change is undone once applied |

`failure_modes` (Step 3.5) is optional, but an id outside FM-01..05 is refused rather than stored.

The synthesizer is **fail-closed**: without a complete record it refuses to generate and lists
what is unanswered. A vague request can never be silently turned into infrastructure - it is
blocked until requirements are gathered and justified. Do not bypass with `--allow-incomplete`
for real work (it is a demo/testing override and is audited).

## References

Grounded in standard requirements engineering, not one domain: functional vs. non-functional
requirements; the **ISO/IEC 25010** software product quality model (functional suitability,
performance efficiency, compatibility, usability, reliability, security, maintainability,
portability); **FURPS+** (Robert Grady); **MoSCoW** prioritization; the 5 W's elicitation
heuristic; and quantified NFRs plus capacity estimation from system-design practice.

Follow-up question generation - asking depth per pillar, conditioned on the answer, rather than
a flat list - follows the framework in *Requirements Elicitation Follow-Up Question Generation*
(RE'25, arXiv:2507.02858), which found questions guided by a catalogue of common interviewer
mistakes outperform unguided ones.

The published service capacities behind the derivations (Glue worker sizes, the Parquet object
targets, the Kinesis shard limits) are cited in `core/architecture/pillars.py`, next to the
arithmetic that uses them.
