# Architecture Layer Context (`core/architecture`)

The `core/architecture` directory contains the requirements gating, architecture decision recording, reference model conformance scoring, intent assertion checkers, and documentation discovery helpers for the MinusOps control plane.

Generation in MinusOps is **requirements-first** and bound to reviewed records (`requirements.json` and `architecture_decision.json`). This directory houses the mechanisms that prevent vague requests from being silently guessed into production infrastructure.

---

## Directory Overview & File Map

| File | Purpose | Key Responsibilities |
| :--- | :--- | :--- |
| [`__init__.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/__init__.py) | Package initialization | Module docstring defining requirements & architecture decision boundaries |
| [`requirements.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/requirements.py) | Requirements gate | Template, validation, deferral sign-off, volume/budget parsing for `requirements.json` |
| [`architecture_decision.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_decision.py) | Architecture decision gate | Record template, validation (incl. the TerraShark 4-part output contract and FM-01..05 taxonomy), module/novel resource registration for `architecture_decision.json` |
| [`architecture_model.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_model.py) | Reference model & conformance | Analytics layer classification, Well-Architected & scale-tier scoring against terraform plans |
| [`intent_assertions.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/intent_assertions.py) | Plan-to-intent assertion engine | Verifies generated plans against module selection, blueprint controls, and numeric ceilings |
| [`discovery.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/discovery.py) | Authoritative source builder | Deterministic doc/pricing URL generation and research record caching for synthesis |
| [`team_resolver.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/team_resolver.py) | Team directory resolver | Resolves team metadata, team DLs, Slack/Teams webhooks, cost centers, and sanitizes team IDs for S3 state/IAM role scoping |

---

## Exhaustive File Specifications

### 1. [`__init__.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/__init__.py)

* **Exact Purpose:** Defines the `core.architecture` Python package namespace and documents its high-level responsibility.
* **Key Functions / Classes:** None (package docstring only).
* **Inputs / Outputs:** None.
* **Failure Modes:** None.
* **Architectural Role:** Package marker identifying the architecture governance sub-system.

---

### 2. [`requirements.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/requirements.py)

* **Exact Purpose:** Enforces the requirements gate binding generation to a recorded, justified requirements set. Prevents vague requests from bypassing human interview/definition.
* **Key Functions & Classes:**
  * `RequirementsIncomplete(Exception)` ([L83-L88](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/requirements.py#L83-L88)): Exception raised when required fields are missing or lazy deferrals are supplied.
  * `template()` ([L91-L103](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/requirements.py#L91-L103)): Generates a blank `requirements.json` structure.
  * `is_deferred(value)` ([L117-L129](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/requirements.py#L117-L129)): Checks if an NFR value is a valid `deferred: <real reason>` (min 10 chars, not lazy like "TBD"/"N/A").
  * `validate(data)` ([L144-L173](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/requirements.py#L144-L173)): Validates required functional fields (`goal`, `system_class`, `functional`) and non-functional axes (`latency`, `scale`, `availability`, `retention`, `security`, `budget`). Requires `deferral_signoff` if > 2 NFR axes are deferred.
  * `validate_data_pipeline(data)` ([L187-L193](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/requirements.py#L187-L193)): Validates data-pipeline specific FR/NFR fields (`sources`, `storage_zones`, `transforms`, `catalog`, `consumption`, `data_quality`, `freshness_sla`, `data_volume`, `governance`, `orchestration`).
  * `parse_daily_gb(data)` ([L199-L211](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/requirements.py#L199-L211)): Regex parser extracting daily volume in GB (taking conservative upper bound for ranges).
  * `parse_budget_usd(data)` ([L217-L233](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/requirements.py#L217-L233)): Regex parser extracting monthly budget ceiling in USD (taking smallest figure for guardrails).
  * `require(data)` ([L267-L272](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/requirements.py#L267-L272)): Raises `RequirementsIncomplete` if `validate()` fails.
  * `write(directory, data, gathered_by="")` ([L246-L255](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/requirements.py#L246-L255)) / `load(path)` ([L258-L264](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/requirements.py#L258-L264)): Standard I/O for `requirements.json`.
* **Inputs / Outputs:**
  * *Inputs:* Dictionary/JSON requirements payload, directory paths, strings.
  * *Outputs:* Validation tuple `(ok, missing)`, parsed numerical tuples `(amount, text_source)`, or saved file path.
* **Failure Modes:**
  * Invalid/unparseable JSON raises `json.JSONDecodeError` on load.
  * `validate()` fails if required fields are blank, or deferrals use lazy placeholders ("tbd", "n/a"), or if > 2 deferrals exist without `deferral_signoff`.
* **Architectural Role:** Entry gate for infrastructure creation; supplies numerical signals (`daily_data_gb`, `budget_usd`) directly to `synthesizer.py`.

---

### 3. [`architecture_decision.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_decision.py)

* **Exact Purpose:** Enforces the architecture decision gate binding synthesis to a reviewed record (`architecture_decision.json`) explaining *why* modules and novel resources were selected.
* **Key Functions & Classes:**
  * `ArchitectureDecisionIncomplete(Exception)` ([L23-L28](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_decision.py#L23-L28)): Raised when synthesis is attempted with an incomplete decision record.
  * `FAILURE_MODES` ([L26-L38](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_decision.py#L26-L38)): TerraShark's FM-01..FM-05 taxonomy (`NextStackHelper.md` §2) as the single in-code definition, shared by the `grill-me` skill's Step 3.5 interrogation and the decision editor.
  * `template(requirements_file="requirements.json")` ([L43-L65](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_decision.py#L43-L65)): Returns a blank architecture decision template, including the 4-part output contract fields.
  * `validate(data)` ([L92-L136](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_decision.py#L92-L136)): Checks that required fields (`requirements_file`, `selected_architecture`, `decision_summary`), module/novel resource choices, alternatives, and the required list fields (`assumptions`, `risks`, `validation`, `rollback`, `sources`) are populated and valid. `failure_modes` is optional, but any id outside `FAILURE_MODES` is reported as missing rather than accepted.
  * **4-part output contract (MINUS-136):** Assumptions -> `assumptions`, Tradeoffs -> `alternatives`, Validation -> `validation`, Rollback -> `rollback`. `validation` and `rollback` are required list fields; a record that cannot say how the design is proven or undone does not pass the gate, so synthesis stays blocked.
  * `add_list_item(path, field, value)` ([L235-L246](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_decision.py#L235-L246)): Appends to `assumptions`, `risks`, `validation`, `rollback`, `failure_modes`, or `sources`; refuses an unknown field name and an invented failure-mode id.
  * `require(data)` ([L165-L169](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_decision.py#L165-L169)): Raises `ArchitectureDecisionIncomplete` if validation fails.
  * `add_modules(path, module_ids)` ([L186-L198](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_decision.py#L186-L198)): Appends validated catalog module IDs to the decision record.
  * `add_novel_resource(...)` ([L231-L247](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_decision.py#L231-L247)): Registers an un-cataloged resource type with justification and alternatives.
* **Inputs / Outputs:**
  * *Inputs:* File paths, module IDs, alternative dicts, novel resource entries.
  * *Outputs:* Updated/validated JSON file, or `(ok, missing)` validation tuple.
* **Failure Modes:**
  * Raises `ValueError` when attempting to add unknown module IDs not in `modules.py`.
  * `validate()` fails if neither `selected_modules` nor `novel_resources` is present, or if alternatives/novel resource justifications are incomplete.
* **Architectural Role:** Secondary pre-synthesis gate preventing keyword matching from acting as an auto-recommendation engine without recorded human architecture decisions.

---

### 4. [`architecture_model.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_model.py)

* **Exact Purpose:** Serves as the reference model and conformance analyzer for data-pipeline workloads, scoring plan JSON against canonical analytics layers and AWS Well-Architected Data Analytics Lens.
* **Key Functions & Classes:**
  * `classify_role(rtype, instance_key="", name="")` ([L95-L106](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_model.py#L95-L106)): Maps Terraform resource types to roles (`ingest`, `stage`, `catalog`, `transform`, `orchestrate`, `consume`, `security`, `observability`, `other`).
  * `layer_of(role)` ([L109-L110](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_model.py#L109-L110)): Maps fine-grained roles to canonical layers (`ingestion`, `storage`, `catalog`, `processing`, `consumption`, `governance`, `other`).
  * `extract_resources(plan)` ([L122-L149](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_model.py#L122-L149)): Flattens plan JSON using `plan_reader.py` into classified resource dictionaries.
  * `module_dependencies(plan)` ([L169-L186](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_model.py#L169-L186)): Evaluates module input expressions to determine actual wiring dependencies between modules.
  * `volume_tier(daily_gb)` ([L198-L211](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_model.py#L198-L211)): Computes scale tier (`gb` < 1TB, `tb` 1-50TB, `pb` > 50TB).
  * `conformance(plan, daily_data_gb=None)` ([L230-L350](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/architecture_model.py#L230-L350)): Calculates architectural score (0-100) and status (`READY`, `NEEDS_WORK`, `INCOMPLETE`) based on layer presence, wiring, encryption, monitoring, and volume tier checks.
* **Inputs / Outputs:**
  * *Inputs:* `terraform show -json` plan dictionary, optional `daily_data_gb`.
  * *Outputs:* Conformance report dictionary containing score, status, layer coverage, resource counts, and weighted findings.
* **Failure Modes:**
  * Fail-soft handling: Malformed plan JSON or missing keys do not crash classification (returns empty resource list or default layer).
* **Architectural Role:** Plan quality and Well-Architected compliance evaluator used during report generation (`reporter.py`).

---

### 5. [`intent_assertions.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/intent_assertions.py)

* **Exact Purpose:** Provides advisory checks verifying that declared intent (`architecture_decision.json`, blueprint controls, `requirements.json`) is satisfied by the generated Terraform plan.
* **Key Functions & Classes:**
  * `check_module_presence(architecture_decision, plan_json)` ([L75-L104](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/intent_assertions.py#L75-L104)): Verifies that selected module IDs appear as `module.<label>.*` in plan resources.
  * `check_controls(blueprint, plan_json)` ([L255-L288](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/intent_assertions.py#L255-L288)): Evaluates blueprint control claims (`SSE-KMS`, `S3 public access blocks`, `Versioning`, `IAM scoping`, `Alarms`, `Budgets`) using static functions `_check_sse_kms`, `_check_public_access_blocks`, etc.
  * `check_numerics(requirements, plan_json)` ([L295-L310](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/intent_assertions.py#L295-L310)): Checks if budget ceilings in requirements are represented by `aws_budgets_budget` resources in the plan.
  * `evaluate(...)` ([L313-L332](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/intent_assertions.py#L313-L332)): Evaluates all assertion classes against a plan JSON.
* **Inputs / Outputs:**
  * *Inputs:* `requirements`, `architecture_decision`, `blueprint`, `plan_json`.
  * *Outputs:* Report dict `{"advisory": True, "evaluation_failed": bool, "findings": [...]}`.
* **Failure Modes:**
  * Malformed plan JSON triggers `_plan_malformed_finding()`, yielding an `INTENT-PLAN-MALFORMED` finding with `evaluation_failed=True`.
  * Returns `control_unmapped` if a blueprint control lacks a mapped verification function.
* **Architectural Role:** Post-generation audit verification layer surfacing intent drift in deploy reports.

---

### 6. [`discovery.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/discovery.py)

* **Exact Purpose:** Deterministically constructs official documentation URLs (Terraform Registry, AWS CLI reference, AWS Pricing, Well-Architected) and caches research records for synthesis.
* **Key Functions & Classes:**
  * `terraform_resource_url(resource_type)` ([L34-L36](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/discovery.py#L34-L36)) / `terraform_datasource_url(...)` ([L39-L40](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/discovery.py#L39-L40)): Generates direct Terraform Registry documentation URLs.
  * `awscli_url(service, action)` ([L43-L44](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/discovery.py#L43-L44)): Generates AWS CLI command reference URLs.
  * `pricing_index_url(service_code)` ([L47-L50](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/discovery.py#L47-L50)): Generates raw price-list index URLs.
  * `research_record(...)` ([L64-L77](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/discovery.py#L64-L77)): Builds a structured, citable research record dict.
  * `save_record(record)` ([L88-L93](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/discovery.py#L88-L93)) / `load_record(topic)` ([L96-L101](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/discovery.py#L96-L101)): Persists and loads research records in `artifacts/research/`.
* **Inputs / Outputs:**
  * *Inputs:* Resource types, data source types, service codes, topics.
  * *Outputs:* Structured source dictionaries and cached JSON files.
* **Failure Modes:**
  * File I/O errors during save/load if `artifacts/research` permissions fail.
* **Architectural Role:** Doc lookup & research recorder supporting the `architect` skill during requirement-to-HCL synthesis.
 
---

### 7. [`team_resolver.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/team_resolver.py)

* **Exact Purpose:** Resolves team directory definitions from `configs/teams.yaml` (or environment variable), sanitizes team and workload identifiers, constructs remote S3 state keys, and verifies team-scoped IAM deploy role ARNs.
* **Key Functions & Classes:**
  * `InvalidTeamId(ValueError)` ([L43-L44](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/team_resolver.py#L43-L44)): Raised when an ID contains invalid characters for S3 prefixes or IAM role ARNs.
  * `config_path()` ([L47-L49](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/team_resolver.py#L47-L49)): Resolves the active teams configuration file path from `$env:MINUS_TEAMS_CONFIG` or default `configs/teams.yaml`.
  * `validate_team_id(team_id)` ([L52-L59](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/team_resolver.py#L52-L59)): Enforces `_TEAM_ID_RE` (`^[a-z0-9][a-z0-9-]{0,62}$`), refusing path traversals (`..`), slashes (`/`), and wildcards (`*`) that could escape S3 state prefixes or expand IAM role patterns.
  * `load_directory(path=None)` ([L62-L85](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/team_resolver.py#L62-L85)): Parses the teams YAML directory. Missing file/PyYAML returns `{}` (optional directory); malformed YAML raises `yaml.YAMLError` or `ValueError` if `teams` is not a dict.
  * `resolve(team_id, path=None)` ([L88-L107](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/team_resolver.py#L88-L107)): Returns a validated team record dictionary (`team_id`, `configured`, `source`, `lead_email`, `team_dl`, `slack_channel`, `teams_webhook_secret`, `cost_center`, `deploy_role_pattern`).
  * `state_key(team_id, workload_id)` ([L110-L118](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/team_resolver.py#L110-L118)): Builds `teams/<team_id>/<workload_id>/terraform.tfstate` with validation on both segments to prevent prefix escape.
  * `role_matches(arn, pattern)` ([L121-L140](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/team_resolver.py#L121-L140)): Verifies if an active STS session or IAM role ARN satisfies a team's deploy role pattern (e.g. `arn:aws:iam::*:role/minusops-deploy-<team_id>`), handling assumed-role STS session formats.
* **Inputs / Outputs:**
  * *Inputs:* Team ID string, workload ID string, optional custom YAML path, role ARN string.
  * *Outputs:* Resolved metadata dictionary, state key path, or boolean role match result.
* **Failure Modes:**
  * Raises `InvalidTeamId` if team or workload ID contains invalid characters, slashes, or path traversal attempts.
  * Raises `yaml.YAMLError` or `ValueError` if `teams.yaml` is malformed or invalid.
* **Architectural Role:** Core organizational identity resolver powering multi-team remote S3 state isolation (`s3://.../teams/<team_id>/<workload_id>/`) and team-scoped deploy role enforcement (`arn:aws:iam::*:role/minusops-deploy-<team_id>`).
