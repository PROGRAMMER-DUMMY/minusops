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
| Resolve authoritative sources for a service (Registry / CLI / pricing URLs) | `core/architecture/discovery.py` |
| Vetted, composable Terraform building blocks + requirement→module matching | `core/generation/modules.py` + `modules/<id>/` |
| Compose selected modules into a governed Terraform workspace | `core/generation/synthesizer.py` |
| Reuse an approved composition (cache that grows from real work) | `core/generation/patterns.py` |
| Govern the result (unchanged) | `core/governance/plan_gate.py` — works on any `--dir` |

## The module library (`modules/`)

Small, vetted Terraform modules selected by requirement keywords (`core/generation/modules.py`):

- **storage-medallion-s3** — tiered S3 data lake + KMS, versioning, lifecycle
- **orchestrator-mwaa** — Managed Airflow (MWAA)
- **orchestrator-stepfunctions** — Step Functions state machine
- **compute-glue-etl** — Glue Spark batch jobs
- **speed-layer-kinesis** — Kinesis (+ optional Managed Flink) streaming/speed layer (lambda architecture)
- **dq-great-expectations** — data-quality checks on Glue
- **schema-registry-glue** — schema enforcement / data contracts
- **query-athena** — Athena workgroup for analyst/BI SQL
- **governance-observability** — Budget guardrail + CloudWatch alarm

Add a capability by dropping in `modules/<id>/main.tf` + a row in `core/generation/modules.py` — never by
forking a giant recipe.

Installed wheels and the Docker image ship these modules as runtime data files. In source
checkouts `core/generation/modules.py` reads `./modules`; in installed environments it falls back to the
packaged data location. `MINUSOPS_MODULES_DIR` can override discovery for a client-specific module
library.

## CLI

```bash
python core/generation/modules.py match "airflow, lambda architecture, data quality, schema enforcement"
python core/architecture/discovery.py "mwaa airflow" --resource aws_mwaa_environment --service-code AmazonMWAA
python core/reporting/minusctl.py accelerator aws-lakehouse --run <run-id> --owner data-platform --daily-data-gb 100
python core/generation/synthesizer.py "<requirements>" --run <run-id> --requirements-file requirements.json --decision-file architecture_decision.json --owner <team>
python core/governance/plan_gate.py verify --dir runs/<run-id>/terraform   # govern it (same gate)
python core/governance/plan_gate.py verify --dir runs/<run-id>/terraform --policy-mode production
# production requires checkov or tfsec on PATH and blocks on external findings
python core/generation/patterns.py match "<requirements>"                  # reuse a prior approved composition
```

## Requirements and decision gates

The same way the plan-hash gate binds *apply* to a reviewed plan, the **requirements gate** and
**architecture decision gate** bind *generation* to reviewed inputs. grill-me writes a
`requirements.json` — goal, system class, ≥1 functional capability, and each non-functional axis
(latency, scale, availability, retention, security, budget) with a value **or an explicit
`deferred: <reason>`**. The architect path writes `architecture_decision.json` with the selected
architecture, selected modules, alternatives, assumptions, risks, and sources. `synthesizer` is
**fail-closed**: without both complete records it refuses and lists what's unanswered.

```bash
python core/architecture/requirements.py template > requirements.json   # grill-me fills this
python core/architecture/requirements.py check requirements.json        # completeness check
python core/reporting/minusctl.py decision template --write           # create run-bound decision record
python core/architecture/architecture_decision.py template --requirements-file requirements.json > architecture_decision.json
python core/architecture/architecture_decision.py set architecture_decision.json --architecture "<selected architecture>" --summary "<why this choice>"
python core/architecture/architecture_decision.py add-module architecture_decision.json <module-id>
python core/architecture/architecture_decision.py add-source architecture_decision.json "<official doc URL>"
python core/architecture/architecture_decision.py add-alternative architecture_decision.json --name "<option>" --decision rejected --reason "<why rejected>"
python core/architecture/architecture_decision.py check architecture_decision.json
python core/generation/synthesizer.py "<summary>" --run <run-id> --requirements-file requirements.json --decision-file architecture_decision.json --owner <team>
```

So a vague request and an unreviewed module recommendation can't be silently turned into
infrastructure — generation is blocked until requirements and the architecture choice are gathered
and justified, and both records are kept beside the run as audit evidence for what was built and
why. (`--allow-incomplete` is an audited demo/testing override.)

`minusctl accelerator aws-lakehouse` is a convenience for the one production path this repo now
supports deeply enough to use as a starting point: an AWS governed lakehouse. It writes a complete,
reviewable `requirements.json` and `architecture_decision.json` with explicit modules, alternatives,
assumptions, risks, and official sources. It is not an automatic recommendation engine and it never
generates Terraform by itself; the operator still edits/reviews the records, then runs synthesis and
the deploy gate.

## Safety invariant

A synthesized composition is **not trusted because the agent proposed it.** Every resource is
grounded in its real Terraform Registry schema; `terraform validate` rejects garbage; the
`SEC-*` scanner blocks wildcard IAM / missing encryption / public exposure; production policy mode
requires checkov/tfsec evidence and blocks on those findings; a human approves the exact plan-hash;
BCM prices it. The synthesizer emits a **scaffold the architect refines and the
gate validates** (module-specific inputs are flagged `REVIEW` in the composed `main.tf` and
`COMPOSITION.md`) — never an apply-without-review shortcut.

## Where the blueprint fits

`aws-data-pipeline-standard` (`core/generation/blueprints.py`, `core/generation/terraform_generator.py`) is a
**demo / cached fixture** — it powers `minusctl demo` and the golden tests as a reproducible
worked example. It is not the production generator. Approved syntheses captured via
`core/generation/patterns.py` are how the reusable-recipe set grows: from real, governed work, not
hand-authored up front.
