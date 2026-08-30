# Architecture Layer Context (`core/architecture`)

The `core/architecture` directory contains the requirements gating, architecture decision recording, reference model conformance scoring, intent assertion checkers, and documentation discovery helpers for the MinusOps control plane.

Generation in MinusOps is **requirements-first** and bound to reviewed records (`requirements.json` and `architecture_decision.json`). This directory houses the mechanisms that prevent vague requests from being silently guessed into production infrastructure.

---

## Directory Overview & File Map

| File | Purpose | Key Responsibilities |
| :--- | :--- | :--- |
| [`__init__.py`](./__init__.py) | Package initialization | Module docstring defining requirements & architecture decision boundaries |
| [`requirements.py`](./requirements.py) | Requirements gate | Template, validation, deferral sign-off, volume/budget parsing for `requirements.json` |
| [`architecture_decision.py`](./architecture_decision.py) | Architecture decision gate | Record template, validation (incl. the TerraShark 4-part output contract and FM-01..05 taxonomy), module/novel resource registration for `architecture_decision.json` |
| [`architecture_model.py`](./architecture_model.py) | Reference model & conformance | Analytics layer classification, Well-Architected & scale-tier scoring against terraform plans |
| [`intent_assertions.py`](./intent_assertions.py) | Plan-to-intent assertion engine | Verifies generated plans against module selection, blueprint controls, and numeric ceilings |
| [`discovery.py`](./discovery.py) | Authoritative source builder | Deterministic doc/pricing URL generation and research record caching for synthesis |
| [`team_resolver.py`](./team_resolver.py) | Team directory resolver | Resolves team metadata, team DLs, Slack/Teams webhooks, cost centers, and sanitizes team IDs for S3 state/IAM role scoping |

---

## Exhaustive File Specifications

### 1. [`__init__.py`](./__init__.py)

* **Exact Purpose:** Defines the `core.architecture` Python package namespace and documents its high-level responsibility.
* **Key Functions / Classes:** None (package docstring only).
* **Inputs / Outputs:** None.
* **Failure Modes:** None.
* **Architectural Role:** Package marker identifying the architecture governance sub-system.

---

### 2. [`requirements.py`](./requirements.py)

* **Exact Purpose:** Enforces the requirements gate binding generation to a recorded, justified requirements set. Prevents vague requests from bypassing human interview/definition.
* **Key Functions & Classes:**
  * [`RequirementsIncomplete(Exception)`](./requirements.py): Exception raised when required fields are missing or lazy deferrals are supplied.
  * [`template()`](./requirements.py): Generates a blank `requirements.json` structure.
  * [`is_deferred(value)`](./requirements.py): Checks if an NFR value is a valid `deferred: <real reason>` (min 10 chars, not lazy like "TBD"/"N/A").
  * [`validate(data)`](./requirements.py): Validates required functional fields (`goal`, `system_class`, `functional`) and non-functional axes (`latency`, `scale`, `availability`, `retention`, `security`, `budget`). Requires `deferral_signoff` if > 2 NFR axes are deferred.
  * [`validate_data_pipeline(data)`](./requirements.py): Validates data-pipeline specific FR/NFR fields (`sources`, `storage_zones`, `transforms`, `catalog`, `consumption`, `data_quality`, `freshness_sla`, `data_volume`, `governance`, `orchestration`).
  * [`parse_daily_gb(data)`](./requirements.py): Regex parser extracting daily volume in GB (taking conservative upper bound for ranges).
  * [`parse_budget_usd(data)`](./requirements.py): Regex parser extracting monthly budget ceiling in USD (taking smallest figure for guardrails).
  * [`require(data)`](./requirements.py): Raises `RequirementsIncomplete` if `validate()` fails.
  * [`write(directory, data, gathered_by="")`](./requirements.py) / [`load(path)`](./requirements.py): Standard I/O for `requirements.json`.
* **Inputs / Outputs:**
  * *Inputs:* Dictionary/JSON requirements payload, directory paths, strings.
  * *Outputs:* Validation tuple `(ok, missing)`, parsed numerical tuples `(amount, text_source)`, or saved file path.
* **Failure Modes:**
  * Invalid/unparseable JSON raises `json.JSONDecodeError` on load.
  * `validate()` fails if required fields are blank, or deferrals use lazy placeholders ("tbd", "n/a"), or if > 2 deferrals exist without `deferral_signoff`.
* **Architectural Role:** Entry gate for infrastructure creation; supplies numerical signals (`daily_data_gb`, `budget_usd`) directly to `synthesizer.py`.

---

### 3. [`architecture_decision.py`](./architecture_decision.py)

* **Exact Purpose:** Enforces the architecture decision gate binding synthesis to a reviewed record (`architecture_decision.json`) explaining *why* modules and novel resources were selected.
* **Key Functions & Classes:**
  * [`ArchitectureDecisionIncomplete(Exception)`](./architecture_decision.py): Raised when synthesis is attempted with an incomplete decision record.
  * [`FAILURE_MODES`](./architecture_decision.py): TerraShark's FM-01..FM-05 taxonomy (`NextStackHelper.md` §2) as the single in-code definition, shared by the `grill-me` skill's Step 3.5 interrogation and the decision editor.
  * [`template(requirements_file="requirements.json")`](./architecture_decision.py): Returns a blank architecture decision template, including the 4-part output contract fields.
  * [`validate(data)`](./architecture_decision.py): Checks that required fields (`requirements_file`, `selected_architecture`, `decision_summary`), module/novel resource choices, alternatives, and the required list fields (`assumptions`, `risks`, `validation`, `rollback`, `sources`) are populated and valid. `failure_modes` is optional, but any id outside `FAILURE_MODES` is reported as missing rather than accepted.
  * **4-part output contract (MINUS-136):** Assumptions -> `assumptions`, Tradeoffs -> `alternatives`, Validation -> `validation`, Rollback -> `rollback`. `validation` and `rollback` are required list fields; a record that cannot say how the design is proven or undone does not pass the gate, so synthesis stays blocked.
  * [`add_list_item(path, field, value)`](./architecture_decision.py): Appends to `assumptions`, `risks`, `validation`, `rollback`, `failure_modes`, or `sources`; refuses an unknown field name and an invented failure-mode id.
  * [`require(data)`](./architecture_decision.py): Raises `ArchitectureDecisionIncomplete` if validation fails.
  * [`add_modules(path, module_ids)`](./architecture_decision.py): Appends validated catalog module IDs to the decision record.
  * [`add_novel_resource(...)`](./architecture_decision.py): Registers an un-cataloged resource type with justification and alternatives.
* **Inputs / Outputs:**
  * *Inputs:* File paths, module IDs, alternative dicts, novel resource entries.
  * *Outputs:* Updated/validated JSON file, or `(ok, missing)` validation tuple.
* **Failure Modes:**
  * Raises `ValueError` when attempting to add unknown module IDs not in `modules.py`.
  * `validate()` fails if neither `selected_modules` nor `novel_resources` is present, or if alternatives/novel resource justifications are incomplete.
* **Architectural Role:** Secondary pre-synthesis gate preventing keyword matching from acting as an auto-recommendation engine without recorded human architecture decisions.

---

### 4. [`architecture_model.py`](./architecture_model.py)

* **Exact Purpose:** Serves as the reference model and conformance analyzer for data-pipeline workloads, scoring plan JSON against canonical analytics layers and AWS Well-Architected Data Analytics Lens.
* **Key Functions & Classes:**
  * [`classify_role(rtype, instance_key="", name="")`](./architecture_model.py): Maps Terraform resource types to roles (`ingest`, `stage`, `catalog`, `transform`, `orchestrate`, `consume`, `security`, `observability`, `other`).
  * [`layer_of(role)`](./architecture_model.py): Maps fine-grained roles to canonical layers (`ingestion`, `storage`, `catalog`, `processing`, `consumption`, `governance`, `other`).
  * [`extract_resources(plan)`](./architecture_model.py): Flattens plan JSON using `plan_reader.py` into classified resource dictionaries.
  * [`module_dependencies(plan)`](./architecture_model.py): Evaluates module input expressions to determine actual wiring dependencies between modules.
  * [`volume_tier(daily_gb)`](./architecture_model.py): Computes scale tier (`gb` < 1TB, `tb` 1-50TB, `pb` > 50TB).
  * [`latency_floor_violation(target_ms, cross_region=False, multi_az=False)`](./architecture_model.py): Validates whether a latency SLA violates physical networking floors (cross-region fiber RTT 30-200ms or inter-AZ sync 1-4ms).
  * [`conformance(plan, daily_data_gb=None)`](./architecture_model.py): Calculates architectural score (0-100) and status (`READY`, `NEEDS_WORK`, `INCOMPLETE`) based on layer presence, wiring, encryption, monitoring, and volume tier checks.
* **Inputs / Outputs:**
  * *Inputs:* `terraform show -json` plan dictionary, optional `daily_data_gb`.
  * *Outputs:* Conformance report dictionary containing score, status, layer coverage, resource counts, and weighted findings.
* **Failure Modes:**
  * Fail-soft handling: Malformed plan JSON or missing keys do not crash classification (returns empty resource list or default layer).
* **Architectural Role:** Plan quality and Well-Architected compliance evaluator used during report generation (`reporter.py`).

---

### 5. [`intent_assertions.py`](./intent_assertions.py)

* **Exact Purpose:** Provides advisory checks verifying that declared intent (`architecture_decision.json`, blueprint controls, `requirements.json`) is satisfied by the generated Terraform plan.
* **Key Functions & Classes:**
  * [`check_module_presence(architecture_decision, plan_json)`](./intent_assertions.py): Verifies that selected module IDs appear as `module.<label>.*` in plan resources.
  * [`check_controls(blueprint, plan_json)`](./intent_assertions.py): Evaluates blueprint control claims (`SSE-KMS`, `S3 public access blocks`, `Versioning`, `IAM scoping`, `Alarms`, `Budgets`) using static functions `_check_sse_kms`, `_check_public_access_blocks`, etc.
  * [`check_numerics(requirements, plan_json)`](./intent_assertions.py): Checks if budget ceilings in requirements are represented by `aws_budgets_budget` resources in the plan.
  * [`evaluate(...)`](./intent_assertions.py): Evaluates all assertion classes against a plan JSON.
* **Inputs / Outputs:**
  * *Inputs:* `requirements`, `architecture_decision`, `blueprint`, `plan_json`.
  * *Outputs:* Report dict `{"advisory": True, "evaluation_failed": bool, "findings": [...]}`.
* **Failure Modes:**
  * Malformed plan JSON triggers `_plan_malformed_finding()`, yielding an `INTENT-PLAN-MALFORMED` finding with `evaluation_failed=True`.
  * Returns `control_unmapped` if a blueprint control lacks a mapped verification function.
* **Architectural Role:** Post-generation audit verification layer surfacing intent drift in deploy reports.

---

### 6. [`discovery.py`](./discovery.py)

* **Exact Purpose:** Deterministically constructs official documentation URLs (Terraform Registry, AWS CLI reference, AWS Pricing, Well-Architected) and caches research records for synthesis.
* **Key Functions & Classes:**
  * [`terraform_resource_url(resource_type)`](./discovery.py) / [`terraform_datasource_url(...)`](./discovery.py): Generates direct Terraform Registry documentation URLs.
  * [`awscli_url(service, action)`](./discovery.py): Generates AWS CLI command reference URLs.
  * [`pricing_index_url(service_code)`](./discovery.py): Generates raw price-list index URLs.
  * [`research_record(...)`](./discovery.py): Builds a structured, citable research record dict.
  * [`save_record(record)`](./discovery.py) / [`load_record(topic)`](./discovery.py): Persists and loads research records in `artifacts/research/`.
* **Inputs / Outputs:**
  * *Inputs:* Resource types, data source types, service codes, topics.
  * *Outputs:* Structured source dictionaries and cached JSON files.
* **Failure Modes:**
  * File I/O errors during save/load if `artifacts/research` permissions fail.
* **Architectural Role:** Doc lookup & research recorder supporting the `architect` skill during requirement-to-HCL synthesis.
 
---

### 7. [`team_resolver.py`](./team_resolver.py)

* **Exact Purpose:** Resolves team directory definitions from `configs/teams.yaml` (or environment variable), sanitizes team and workload identifiers, constructs remote S3 state keys, and verifies team-scoped IAM deploy role ARNs.
* **Key Functions & Classes:**
  * [`InvalidTeamId(ValueError)`](./team_resolver.py): Raised when an ID contains invalid characters for S3 prefixes or IAM role ARNs.
  * [`config_path()`](./team_resolver.py): Resolves the active teams configuration file path from `$env:MINUS_TEAMS_CONFIG` or default `configs/teams.yaml`.
  * [`validate_team_id(team_id)`](./team_resolver.py): Enforces `_TEAM_ID_RE` (`^[a-z0-9][a-z0-9-]{0,62}$`), refusing path traversals (`..`), slashes (`/`), and wildcards (`*`) that could escape S3 state prefixes or expand IAM role patterns.
  * [`load_directory(path=None)`](./team_resolver.py): Parses the teams YAML directory. Missing file/PyYAML returns `{}` (optional directory); malformed YAML raises `yaml.YAMLError` or `ValueError` if `teams` is not a dict.
  * [`resolve(team_id, path=None)`](./team_resolver.py): Returns a validated team record dictionary (`team_id`, `configured`, `source`, `lead_email`, `team_dl`, `slack_channel`, `teams_webhook_secret`, `cost_center`, `deploy_role_pattern`).
  * [`state_key(team_id, workload_id)`](./team_resolver.py): Builds `teams/<team_id>/<workload_id>/terraform.tfstate` with validation on both segments to prevent prefix escape.
  * [`role_matches(arn, pattern)`](./team_resolver.py): Verifies if an active STS session or IAM role ARN satisfies a team's deploy role pattern (e.g. `arn:aws:iam::*:role/minusops-deploy-<team_id>`), handling assumed-role STS session formats.
* **Inputs / Outputs:**
  * *Inputs:* Team ID string, workload ID string, optional custom YAML path, role ARN string.
  * *Outputs:* Resolved metadata dictionary, state key path, or boolean role match result.
* **Failure Modes:**
  * Raises `InvalidTeamId` if team or workload ID contains invalid characters, slashes, or path traversal attempts.
  * Raises `yaml.YAMLError` or `ValueError` if `teams.yaml` is malformed or invalid.
* **Architectural Role:** Core organizational identity resolver powering multi-team remote S3 state isolation (`s3://.../teams/<team_id>/<workload_id>/`) and team-scoped deploy role enforcement (`arn:aws:iam::*:role/minusops-deploy-<team_id>`).
---

### 8a. [`blueprint_data/`](./blueprint_data/)
* **Exact Purpose:** thirty-five published data-pipeline reference architectures, recorded as
  evidence about what a data pipeline is usually made of. AWS Prescriptive Guidance, the AWS
  Analytics Reference Architecture, the Well-Architected analytics material, Databricks, dbt
  and the Iceberg spec.
* **It exists to check `architecture_model._RULES`.** Those rules were written from what we
  had built. A service a third of the corpus names and `classify_role` cannot place lands in
  the `other` layer, in the footer beside the IAM roles, and nothing says so. `aws_vpc` is
  named in 10 of the 35 and the needles guarding it were `_vpc` and `vpc_` -- both requiring
  a delimiter a bare type does not have -- with no rule mentioning a NAT gateway, an internet
  gateway, a route table or an elastic IP at all.
* **Counts, not text.** The pages belong to their publishers; what is stored is which service
  names appear where. A test asserts the stored shape stays that narrow.
* **Refreshed by a person.** A test that fetched its own reference would fail offline, and a
  corpus that updated itself would stop being fixed enough to measure against.
* **Not an authority.** Extraction is keyword counting over prose, so a mention is evidence a
  page discusses a service, never evidence of a hop between two of them. Declared hops still
  come only from a plan's own data-carrying arguments.
* **Tests:** [`tests/test_blueprint_corpus.py`](../../tests/test_blueprint_corpus.py).

---

### 8. [`pillars.py`](./pillars.py)

* **Exact Purpose:** the 19-pillar interview catalogue, and the arithmetic that makes each
  later question specific to the answers already given.
* **Two things, and the split matters:** `PILLARS` is data -- question, options, what each
  option maps to in the module registry, and the `depth` follow-ups that only become
  answerable once the pillar is answered. `derive()` is the arithmetic, computed over
  published service capacities that are cited next to the sum.
* **It never invents an input.** With no stated volume there is no recommended worker count;
  `derive()` returns `determinable: False` naming the missing fact. A number nobody gave,
  presented as a recommendation, is the same fabrication as a made-up price.
* **`depth` is keyed by the option chosen.** A follow-up that fits every answer belongs at
  the top level, and `depth_for` therefore has an option branch REPLACE the shared one --
  which is why the quarantine follow-ups are not asked of someone who chose fail-fast.
* **Wired pillars carry `derives` and `informs`:** an answer that reaches no derivation and
  informs no later pillar is recorded and then read by nothing.
* **Pillar 0 is a report, not a choice.** Residency, mandated key management, retention floors
  and spend ceilings are set above the team; AWS's own guidance places RTO, RPO and residency
  with the business and the architect downstream of them. It is asked first because every
  later option list is bounded by the answer, and an architect who picks a region a policy
  already fixed has produced a contradiction rather than a decision. It maps to no module --
  a constraint is not a thing to provision.
* **Tests:** [`tests/test_pillars.py`](../../tests/test_pillars.py).

---

### 9. [`access_model.py`](./access_model.py)

* **Exact Purpose:** the plan-derived IAM model behind the console's Access view -- who can
  reach what, and where the plan stops knowing.
* **Its one non-negotiable property:** it never infers an access relationship the plan does
  not state. A trust policy unknown until apply, or a policy document that will not parse, is
  reported as an explicit not-determinable marker (`resolved=False` plus a reason,
  `statements=None`) and never as an empty list. An access screen that quietly under-reports
  permissions is worse than no screen, and `[]` versus unreadable is exactly the distinction
  a parser that fails soft into a default destroys.
* **Same-account versus cross-account is not resolved by calling STS.** It is decided by the
  account id written in the principal, or left undecided.
* **Failure Modes:** fail-soft but counted -- a malformed entry lands in `malformed` and
  never crashes the read.
* **Tests:** [`tests/test_access_model.py`](../../tests/test_access_model.py).

---

### 10. [`reconciler.py`](./reconciler.py)

* **Exact Purpose:** governed visual-to-code reconciliation. Everywhere else the console
  reads; here a drag on a canvas rewrites `terraform/main.tf`, which makes this the one file
  in the console that could undo the deploy gate.
* **Two calls, and the split IS the safety property.** `propose()` is inert: it writes no
  file, touches no decision record, and emits no audit entry -- it returns a diff for a modal
  to show. `confirm()` writes, and only when `confirmed is True` by identity check, because
  `confirmed="no"` is truthy and would turn a dismissed modal into an infrastructure edit.
* **Confirming deletes the standing approval records**, so `plan_gate.gate_status()` reports
  `approved: False`. That reuses the gate's own answer rather than adding a second staleness
  flag that could disagree with it.
* **Tests:** [`tests/test_reconciler.py`](../../tests/test_reconciler.py).
