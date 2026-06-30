# Architecture Synthesis — composition over monolithic blueprints

MinusOps does **not** ship one fixed recipe per workload. Every company differs on the axes that
matter — orchestrator (Airflow/MWAA vs Step Functions vs Dagster), architecture pattern
(lambda/kappa/batch), data-quality tooling, schema enforcement, storage, compute, cloud — so a
monolithic blueprint either forces clients into the wrong stack or explodes into an unmaintainable
recipe-per-permutation. Instead, the production path **gathers requirements, researches current
services, composes vetted modules, and governs the result.**

```
grill-me            architect                                   the deploy gate
(requirements)  →   research → choose → compose modules     →   verify → plan → approve → apply
                    (core/discovery, modules, synthesizer)      (validate + SEC scan + BCM cost + human)
```

## The pieces

| Concern | Where |
| :-- | :-- |
| Gather functional + non-functional requirements (ISO 25010 / FURPS+, quantified, MoSCoW) | [`grill-me` skill](../.agents/skills/grill-me/SKILL.md) |
| Research current services + reference architectures, choose best-fit | [`architect` skill](../.agents/skills/architect/SKILL.md) |
| Resolve authoritative sources for a service (Registry / CLI / pricing URLs) | `core/discovery.py` |
| Vetted, composable Terraform building blocks + requirement→module matching | `core/modules.py` + `modules/<id>/` |
| Compose selected modules into a governed Terraform workspace | `core/synthesizer.py` |
| Reuse an approved composition (cache that grows from real work) | `core/patterns.py` |
| Govern the result (unchanged) | `core/plan_gate.py` — works on any `--dir` |

## The module library (`modules/`)

Small, vetted Terraform modules selected by requirement keywords (`core/modules.py`):

- **storage-medallion-s3** — tiered S3 data lake + KMS, versioning, lifecycle
- **orchestrator-mwaa** — Managed Airflow (MWAA)
- **orchestrator-stepfunctions** — Step Functions state machine
- **compute-glue-etl** — Glue Spark batch jobs
- **speed-layer-kinesis** — Kinesis (+ optional Managed Flink) streaming/speed layer (lambda architecture)
- **dq-great-expectations** — data-quality checks on Glue
- **schema-registry-glue** — schema enforcement / data contracts
- **query-athena** — Athena workgroup for analyst/BI SQL
- **governance-observability** — Budget guardrail + CloudWatch alarm

Add a capability by dropping in `modules/<id>/main.tf` + a row in `core/modules.py` — never by
forking a giant recipe.

## CLI

```bash
python core/modules.py match "airflow, lambda architecture, data quality, schema enforcement"
python core/discovery.py "mwaa airflow" --resource aws_mwaa_environment --service-code AmazonMWAA
python core/synthesizer.py "<requirements>" --owner <team>      # compose into a run workspace
python core/plan_gate.py verify --dir runs/<run-id>/terraform   # govern it (same gate)
python core/patterns.py match "<requirements>"                  # reuse a prior approved composition
```

## Safety invariant

A synthesized composition is **not trusted because the agent proposed it.** Every resource is
grounded in its real Terraform Registry schema; `terraform validate` rejects garbage; the
`SEC-*` scanner blocks wildcard IAM / missing encryption / public exposure; a human approves the
exact plan-hash; BCM prices it. The synthesizer emits a **scaffold the architect refines and the
gate validates** (module-specific inputs are flagged `REVIEW` in the composed `main.tf` and
`COMPOSITION.md`) — never an apply-without-review shortcut.

## Where the blueprint fits

`aws-data-pipeline-standard` (`core/blueprints.py`, `core/terraform_generator.py`) is a
**demo / cached fixture** — it powers `minusctl demo` and the golden tests as a reproducible
worked example. It is not the production generator. Approved syntheses captured via
`core/patterns.py` are how the reusable-recipe set grows: from real, governed work, not
hand-authored up front.
