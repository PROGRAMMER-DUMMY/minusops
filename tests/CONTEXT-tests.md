# Tests Context Index

This document provides exhaustive context for all test files, test suites, and test fixtures within the [`tests`](./) directory.

---

## 1. Test Fixtures & Configuration

- [`tests/conftest.py`](./conftest.py): Pytest configuration and shared fixtures for mocking AWS providers, temporary directories, audit logs, and environment variables.

---

## 2. Core Governance & Gate Tests

- [`tests/test_approval.py`](./test_approval.py): Tests `core/governance/approval.py` gating behavior for `gatekeeper` and `auto-approve` modes.
- [`tests/test_audit_chain.py`](./test_audit_chain.py): Tests tamper-evident audit log hashing, chain verification, and lock integrity.
- [`tests/test_authz.py`](./test_authz.py): Tests role-based and claim-based authorization rules.
- [`tests/test_claim_security.py`](./test_claim_security.py): Tests security validation and signature checks for authorization claims.
- [`tests/test_claim_writeback.py`](./test_claim_writeback.py): Tests state persistence and writeback logic for authorization claims.
- [`tests/test_claims_never_permit.py`](./test_claims_never_permit.py): Verifies fail-closed behavior ensuring unauthorized claims are rejected.
- [`tests/test_destructive_change_gate.py`](./test_destructive_change_gate.py): Tests detection and blocking of destructive plan changes (resource deletions/recreations).
- [`tests/test_destructive_governance.py`](./test_destructive_governance.py): Tests policy enforcement on destructive infrastructure operations.
- [`tests/test_ephemeral_apply.py`](./test_ephemeral_apply.py): Tests isolated ephemeral apply workflows and short-lived credentials handling.
- [`tests/test_gate_concurrency.py`](./test_gate_concurrency.py): Tests locking mechanisms and concurrent execution handling in plan gates.
- [`tests/test_gate_e2e.py`](./test_gate_e2e.py): End-to-end integration tests for the full deploy gate workflow (verify -> plan -> approve -> apply).
- [`tests/test_plan_gate.py`](./test_plan_gate.py): Exhaustive unit tests for `core/governance/plan_gate.py` plan-hash generation and MFA enforcement.
- [`tests/test_pr_reviewer.py`](./test_pr_reviewer.py): Tests the GitHub Actions PR reviewer action and sticky comment renderer (`.github/actions/pr-reviewer`), asserting plan generation, security checks, and BCM pricing comment rendering on pull requests.
- [`tests/test_rego_gate.py`](./test_rego_gate.py): Tests OPA Rego policy evaluation against Terraform plan JSON outputs and mandatory OPA presence in production mode.
- [`tests/test_rule_stages.py`](./test_rule_stages.py): Tests parsing and enforcement of static analysis rule stages defined in `policy/rule_stages.json`.
- [`tests/test_source_guard.py`](./test_source_guard.py): Tests detection of manual source file modifications against baseline hashes.
- [`tests/test_team_isolation.py`](./test_team_isolation.py): Tests multi-team state key partitioning (`teams/<team_id>/<workload_id>/`), team-scoped deploy role assertions, and path traversal rejection (`..`).

---

## 3. Architecture & Generation Engine Tests

- [`tests/test_accelerators.py`](./test_accelerators.py): Tests infrastructure pattern accelerators and blueprint speedups.
- [`tests/test_architecture_decision.py`](./test_architecture_decision.py): Tests recording and validation of `architecture_decision.json` schema, including the TerraShark 4-part output contract (`validation` and `rollback` required; `failure_modes` optional but id-checked against FM-01..05).
- [`tests/test_dbt_scaffold.py`](./test_dbt_scaffold.py): Step 5 (MINUS-110/119/120) -- the `dbt_schema()` vs `aws_glue_catalog_database.gold` drift check asserted against the module's own HCL, `profiles.yml` contents, `transform_engine` defaulting to Glue rather than dbt, `_render_outputs` skipping absent modules, and dbt-only mode dropping Glue / refusing a composition with no workgroup. Fast: exercises the renderers and the selection rule, not Terraform.
- [`tests/test_promotion_matrix.py`](./test_promotion_matrix.py): Step 6 (MINUS-114/130/131/132) -- `envs/*.tfvars` generation, the invariant that `force_destroy` is **not** settable from a var-file, per-tier scaling and budget scaling, mandatory tags demanded only where enforced, the `check` block's scope, per-zone CRR destinations (a regression to a single ARN reintroduces cross-zone key collisions), and the audit bucket never being force-destroyable. Whitespace is normalized before matching because `terraform fmt` aligns `=` inside a generated workspace but the raw renderers do not.
- [`tests/test_ingestion_modules.py`](./test_ingestion_modules.py): Step 7 (MINUS-116/117/118/123-125) -- registry validity, each ingestion archetype being reachable by how someone would actually phrase it, **no module taking a credential as a Terraform variable** (FM-02), DMS not being publicly accessible, per-user SFTP roles and chroot, the webhook DLQ/throttle and verbatim body, the quarantine bucket and its separate CMK, three distinct alert topics with the budget alarm not reaching on-call, the scaffold never overwriting, and grill-me reading its pillars from the registry rather than transcribing them, with the demo blueprint surviving only as a prohibition.
- [`tests/test_reflector.py`](./test_reflector.py): Step 8 (MINUS-128/129/135) -- tier crossovers and FLEX eligibility (including intolerant-wins-a-tie), the reflector's five gates against **real files on disk** rather than mocks (missing cross-module wiring blocks; a literal where a module reference belongs is reported but not blocked; volume outgrowing the composed engine blocks; a missing BCM estimate is never a pass), `unknown` never counting as a pass, the brace-counting module parser surviving nested blocks, and `--based-on` inheriting organisational settings with attribution while never inheriting pipeline shape.
- [`tests/test_seed_adopt.py`](./test_seed_adopt.py): Step 9 (MINUS-113/106/115) -- seed's safety contract (plan mode calls neither AWS nor approval; a denied approval leaves AWS untouched; the approval names every side effect; an empty Gold table is a failure, not a pass; a failed Glue run surfaces AWS's own message), adopt writing nothing without `--anchor` and refusing to call a directory with SEC findings adopted, and the PR reviewer never containing an apply, never inventing a cost, and using `pull_request` rather than `pull_request_target`.
- [`tests/test_doctor.py`](./test_doctor.py): Tests the `minusctl doctor` ok/warn/error contract - a missing `terraform` blocks, missing `opa`/scanners only warn, long-term credentials warn rather than read as clean, and the CLI exit code follows `diagnose()["ok"]`.
- [`tests/test_architecture_model.py`](./test_architecture_model.py): Tests data structures and graph models representing cloud architecture.
- [`tests/test_authoring_context_claims.py`](./test_authoring_context_claims.py): Tests claims propagation during HCL authoring sessions.
- [`tests/test_create_intent.py`](./test_create_intent.py): Tests intent resolution and parsing of user creation prompts.
- [`tests/test_databricks_workspace_module.py`](./test_databricks_workspace_module.py): Unit tests for Databricks workspace module synthesis.
- [`tests/test_discovery.py`](./test_discovery.py): Tests service discovery and cloud resource mapping logic.
- [`tests/test_dq_great_expectations_module.py`](./test_dq_great_expectations_module.py): Unit tests for Great Expectations data quality module synthesis.
- [`tests/test_intent_assertions.py`](./test_intent_assertions.py): Tests validation of architectural intent assertions against target HCL.
- [`tests/test_intent_resolver.py`](./test_intent_resolver.py): Tests matching user natural language requests to approval blueprints.
- [`tests/test_module_provenance.py`](./test_module_provenance.py): Tests provenance tracking for synthesized Terraform modules.
- [`tests/test_metadata_control_table.py`](./test_metadata_control_table.py): Tests the `metadata-control-table` module and its runtime helper -- registry validity, keyword reachability, no credential or IAM resource in the HCL, encryption at rest, and the column-mapping indirection (two companies' differently-named columns resolving to the same normalized keys, a missing mapped column yielding `None` rather than `KeyError`).
- [`tests/test_modules.py`](./test_modules.py): Tests module registry matching (`core/generation/modules.py`).
- [`tests/test_networking_vpc_module.py`](./test_networking_vpc_module.py): Unit tests for AWS VPC networking module synthesis.
- [`tests/test_patterns.py`](./test_patterns.py): Tests pattern matching and capture algorithms (`core/generation/patterns.py`).
- [`tests/test_query_athena_module.py`](./test_query_athena_module.py): Unit tests for Athena query module synthesis.
- [`tests/test_requirements.py`](./test_requirements.py): Tests requirements parsing, schema validation, and storage.
- [`tests/test_storage_medallion_module.py`](./test_storage_medallion_module.py): Unit tests for medallion storage (Bronze/Silver/Gold S3) module synthesis.
- [`tests/test_synthesizer.py`](./test_synthesizer.py): Exhaustive tests for HCL code synthesis engine (`core/generation/synthesizer.py`).
- [`tests/test_tf_validate.py`](./test_tf_validate.py): Tests execution of native `terraform validate` commands.
- [`tests/test_warehouse_streaming_modules.py`](./test_warehouse_streaming_modules.py): Tests module synthesis, schema validation, and IAM trust policies for `warehouse-snowflake-aws`, `streaming-msk-kafka`, and `compute-databricks-delta`.
- [`tests/test_workflow.py`](./test_workflow.py): Tests execution flow of the generation workflow orchestrator.

---

## 4. Cost, FinOps & Reporting Tests

- [`tests/test_bcm_pricing_calculator.py`](./test_bcm_pricing_calculator.py): Tests integration with AWS BCM Pricing Calculator payload generation and heuristic usage auto-derivation.
- [`tests/test_budget_calculator.py`](./test_budget_calculator.py): Unit tests for cost budget estimation utilities.
- [`tests/test_finops_agent.py`](./test_finops_agent.py): Tests live cost queries, anomaly detection, and correlation routines.
- [`tests/test_finops_unit_economics.py`](./test_finops_unit_economics.py): Tests unit economics ratios ($/GB, $/run) derived exclusively from evidenced BCM cost figures without inventing totals, scale-curve calculations, and monthly error budget burn rate tracking (PRD-FINOPS-2026-005).
- [`tests/test_latency_physics_rules.py`](./test_latency_physics_rules.py): Tests static anti-egress rules (`COST-04` cross-region transfer, `COST-05` missing S3 VPC endpoint) and validates physical networking floors (cross-region fiber RTT 30-200ms prohibiting synchronous sub-100ms commitments).
- [`tests/test_integrations.py`](./test_integrations.py): Tests `core/integrations/` — the Slack, Teams, Outlook/SMTP, Confluence, and Jira hooks — against a stubbed `urllib.request.urlopen` and `smtplib.SMTP`. Covers success, HTTP error, and timeout paths, MIME attachment construction, Markdown-to-Confluence-storage conversion, and that a denied approval reaches neither the network nor the disk.
- [`tests/test_finops_doctor_policy.py`](./test_finops_doctor_policy.py): Tests cross-cutting FinOps rules, BCM quantity derivations, and doctor container recovery policies.
- [`tests/test_optimize_analyzer.py`](./test_optimize_analyzer.py): Tests HCL static scanning rules for security, cost, and observability.
- [`tests/test_plan_inspector.py`](./test_plan_inspector.py): Tests plan diffing and resource inspection capabilities.
- [`tests/test_plan_reader.py`](./test_plan_reader.py): Tests parsing Terraform execution plans into structured JSON objects.
- [`tests/test_pricing_catalog.py`](./test_pricing_catalog.py): Tests offline pricing catalog lookups and pricing API fallback rules.
- [`tests/test_reporter.py`](./test_reporter.py): Tests versioned deploy report generation (`plan_gate` reports).

---

## 5. Control Plane, Knowledge Base & CLI Tests

- [`tests/test_address_churn.py`](./test_address_churn.py) (also covers MINUS-137 `moved {}` generation: the write -> read -> classify round trip clears the gate, advisory churn gets no block, and an existing `moved.tf` is never overwritten): Tests tracking and mitigation of IP address and resource identifier churn.
- [`tests/test_cli_diagnostics.py`](./test_cli_diagnostics.py): Tests `core/reporting/cli_diagnostics.py` -- fuzzy run-id suggestion and its cutoff, the three-part error shape, run-description tips with control-character sanitization, and prerequisite interception for a stage whose prior artifact is absent.
- [`tests/test_cicd.py`](./test_cicd.py): Tests `core/generation/cicd.py` -- `pull_request` never `pull_request_target`, no static AWS keys in any generated pipeline, the merge gate re-checking each lane result (a skipped lane is not a passed lane), lane 3 reusing the existing `pr-reviewer` action rather than a second copy, feed configs carrying no role ARN or personal email, the factory planning but never applying, Jenkins driving the same `plan_gate` commands, stable sorted feed discovery, and refusing to overwrite an edited workflow.
- [`tests/test_finops_circuit_breakers.py`](./test_finops_circuit_breakers.py): Asserts the three cost limits against the modules' own HCL -- the Glue `timeout` (absent, AWS applies a 48-hour default), the Athena 10 GiB scan cutoff, and the medallion Glacier lifecycle. Each fails silently when missing; the bill is the only other signal.
- [`tests/test_alert_dedup.py`](./test_alert_dedup.py): Tests the alert cooldown in `base_hook.gated` -- first alert delivers, an identical one inside the window is suppressed with `sent=False, reason=deduplicated` and never reaches the sender or the approval prompt, a different message delivers immediately, and the same fault after the window pages again. Also asserts a denied approval opens no window.
- [`tests/test_logging_governance.py`](./test_logging_governance.py): Asserts module HCL for explicit CloudWatch `retention_in_days` and `kms_key_id`, a CMK on every Secrets Manager secret, and opt-in S3 server access logging that never targets a medallion zone (self-logging is a feedback loop).
- [`tests/test_subagent_manifests.py`](./test_subagent_manifests.py): Invariants across every `.agents/subagents/` manifest -- one per transport hook, frontmatter present, and each one telling the agent to keep credentials out of its output, to distinguish `ok` from `sent`, and not to retry a denial. Plus jira-agent specifics: names `create_change_ticket`, explains that an unwired Jira writes a file rather than creating a ticket, and names ADF.
- [`tests/test_deployment_modes.py`](./test_deployment_modes.py): Asserts the Dockerfile (pinned slim base, non-root UID 10001, Terraform >= 1.9 for the generated `use_lockfile` backend, healthcheck, dashboard extra, no baked AWS keys) and the EKS manifests (IRSA annotation, two replicas, hardened security context, writable scratch for `terraform init` under `readOnlyRootFilesystem`, requests and limits, probes, token from a Secret, ClusterIP, internal ALB). Also link-checks every repo path the operator guide references.
- [`tests/test_cloud_drift.py`](./test_cloud_drift.py): Out-of-band cloud drift detection, the revert verdict, the opt-in fail-open telemetry correlation that explains a manual change without permitting it, the declared-vs-live summary with its conditional recommendation, and the gate wiring in both directions -- telemetry on request, silent and network-free by default (PRD v6 FR-07/AC-05).
- [`tests/test_coverage_audit.py`](./test_coverage_audit.py): Tests auditing of test coverage metrics across engine modules.
- [`tests/test_credentials.py`](./test_credentials.py): Tests safe credential handling and environment isolation.
- [`tests/test_dashboard.py`](./test_dashboard.py): Tests Plotly Dash control plane web application (`app/dashboard_app.py`).
- [`tests/test_demo.py`](./test_demo.py): Tests no-cloud dry-run demo creation routines.
- [`tests/test_file_ownership.py`](./test_file_ownership.py): Tests file ownership enforcement and path validation.
- [`tests/test_knowledge_concurrency.py`](./test_knowledge_concurrency.py): Tests concurrent reads and writes on the knowledge graph store.
- [`tests/test_knowledge_core_boundary.py`](./test_knowledge_core_boundary.py): Tests boundary enforcement between core engine logic and the knowledge store.
- [`tests/test_knowledge_degradation.py`](./test_knowledge_degradation.py): Tests graceful degradation when knowledge sources are unavailable.
- [`tests/test_knowledge_delegation.py`](./test_knowledge_delegation.py): Tests delegation of query processing in knowledge sub-agents.
- [`tests/test_knowledge_diff.py`](./test_knowledge_diff.py): Tests diff calculation and version comparisons in the knowledge base.
- [`tests/test_knowledge_jsonl.py`](./test_knowledge_jsonl.py): Tests JSONL serialization and deserialization of knowledge entries.
- [`tests/test_knowledge_scope.py`](./test_knowledge_scope.py): Tests scoping and filtering rules for knowledge queries.
- [`tests/test_knowledge_store.py`](./test_knowledge_store.py): Unit tests for knowledge graph database operations.
- [`tests/test_minusctl.py`](./test_minusctl.py): End-to-end command line test suite for the `minusctl` entry point, including semantic `create --name/--domain/--orchestrator` (AC-01/AC-02) and `minusctl export` (AC-03).
- [`tests/test_pdf_outline.py`](./test_pdf_outline.py): Tests PDF outline and report formatting functions.
- [`tests/test_providers.py`](./test_providers.py): Tests multi-cloud abstraction layer (`AWSProvider`, `AzureProvider`, `GCPProvider`).
- [`tests/test_runs.py`](./test_runs.py): Workspace creation and run state management, both run-id shapes (semantic and legacy), and the atomically-swapped central registry that never invents a cost (`core/reporting/runs.py`).
- [`tests/test_proving_harness.py`](./test_proving_harness.py): The 5-hop end-to-end proving harness -- hop ordering, short-circuiting on failure, the data-conservation arithmetic that catches dropped records, the tamper-evident report, and the no-emoji checks (`core/reporting/seed.py`).
- [`tests/test_incident_diagnostics.py`](./test_incident_diagnostics.py): PRD v9 -- signature classification across the four operational domains, the refusal to invent a diagnosis for an unknown error, cost deltas as ratios rather than dollars, the four-part report, sub-50ms offline classification with no subprocess, and fail-open telemetry.
- [`tests/test_serving_topology.py`](./test_serving_topology.py): PRD v9 -- the four serving archetypes, the rule that an endpoint is emitted only for infrastructure that exists with a fully-known address, and the credential-free connection scaffold `minusctl export` writes.
- [`tests/test_v8_governance_and_semantic_modules.py`](./test_v8_governance_and_semantic_modules.py): PRD v8 -- the four new modules exist, are registered, and are packaged into the wheel (a general guard over every module, not four one-off assertions); the Lake Formation compatibility-default revocation; the external-ID trust condition; Redshift capacity ceiling and usage limit; Athena partition projection; and that grill-me names no module the catalog cannot build.
- [`tests/test_cli_v6_completion.py`](./test_cli_v6_completion.py): PRD v6 FR-01..FR-04 completion -- the ratified precedence order (explicit flag, upward discovery, stored context, refusal), `runs list` filters and columns, the four-section attribute card and its canonical sources, `gate status` reading disk without invoking Terraform, and `--role-arn` as a verified assertion (with the absence of `--mfa-arn` pinned).
- [`tests/test_cli_help.py`](./test_cli_help.py): The grouped, coloured help screen -- every command discoverable, grouped exactly once, described in a sentence, and the colour rules that keep escape codes out of pipes, CI logs and `NO_COLOR` sessions.
- [`tests/test_docs_examples.py`](./test_docs_examples.py): The documentation linter. Resolves every symbol the extensibility guide's examples name against the module that defines it, compares the module-registry example to the real `MODULES` schema key for key, requires each link to be repo-relative and to exist on disk, and enforces NFR-01 across every tracked `.py`, `.md`, `.yml` and `.yaml` file. Written after a structural audit passed a guide whose examples could not run: three of five raised `KeyError`, `TypeError` or wrote a refusal branch that could never fire. Presence was being checked; execution was not. Box drawing is deliberately permitted -- a directory tree drawn with it is a documented convention in [`CONTEXT-MAP.md`](../CONTEXT-MAP.md) and is not an emoji.
- [`tests/test_cli_package.py`](./test_cli_package.py): The unified `minusctl` package -- `.minus/context.json` and its loud failure modes, `runs list/describe`, `--dir` defaulting and refusal, gate stage pass-through, the guard that no subcommand was lost in the refactor, and the stdlib-only import check (`core/cli/`).
- [`tests/test_export.py`](./test_export.py): Multi-repo export packaging, `--dest-dir` traversal refusal, replace-not-merge copying, and the per-pipeline OIDC workflow with its `paths:` isolation filter (`core/reporting/export.py`, `core/generation/cicd.py`).
- [`tests/test_drawio_generator.py`](./test_drawio_generator.py): The Draw.io canvas -- the URL round trip against a decoder that imitates diagrams.net (`atob` rejects the URL-safe alphabet, `decodeURIComponent` throws on a bare percent sign), edges derived only from data-carrying arguments with a dependency reference asserted NOT to become an arrow, bucket configuration resources folding into the bucket they configure and becoming badges sourced from the resource that carries the fact, the medallion zones running left to right in stage order with transforms between them, and the security band spanning the diagram while carrying no edges.
- [`tests/test_schema_lint.py`](./test_schema_lint.py): Tests schema linting rules for HCL and JSON inputs.
- [`tests/test_schema_watch.py`](./test_schema_watch.py): Tests continuous schema drift watching utilities.
- [`tests/test_teardown_regression_harness.py`](./test_teardown_regression_harness.py): Regression testing harness for infrastructure teardown operations.
- [`tests/test_verification_coverage.py`](./test_verification_coverage.py): Tests verification coverage checking tools across deployment stages.
---

## 6. Agent Governance, Console & Access Tests

- [`tests/test_agent_guardrails.py`](./test_agent_guardrails.py): The autonomous-agent
  guardrail -- destructive commands refused, the ways an agent would slip past one refused
  too (chained commands, subshells, a rewritten path), ordinary commands allowed, and
  `enforce` raising rather than returning on a refusal.
- [`tests/test_guardrails_hook.py`](./test_guardrails_hook.py): The `PreToolUse` adapter --
  the hook is registered, a destructive command is blocked, it stays blocked even when a
  human authorized the session, and one hidden in a chain is still caught.
- [`tests/test_guardrail_self_block.py`](./test_guardrail_self_block.py): Three tests holding
  one property -- no MinusOps command is refused by its own guardrail. Getting this wrong
  bricks every agent session, and the two human-gated commands are refused but NAMED as such
  rather than reported as destructive.
- [`tests/test_apply_broker.py`](./test_apply_broker.py): The release check -- an approval for
  a different plan does not release this one, no approval is a refusal that names the plan, a
  planner cannot approve their own work, and self-approval is caught through the ARN as well
  as the operator string.
- [`tests/test_agent_tracer.py`](./test_agent_tracer.py): The two-state rule. Every lifecycle
  stage the PRD names is in the catalog, each declares the artifact that proves it ran, and no
  stage is ever reported as run without an audit hash.
- [`tests/test_agent_flow.py`](./test_agent_flow.py): The execution graph and the chain it is
  read from -- an untouched chain verifies, a tampered record names where it broke, a deleted
  record breaks it, and an unparseable line fails closed.
- [`tests/test_agent_cost.py`](./test_agent_cost.py): Token economics -- tier rates match the
  pricing matrix, a step that ran no model costs a REAL zero rather than an absent figure
  rendered as one, and step costs are computed from the transcript rather than estimated.
- [`tests/test_access_model.py`](./test_access_model.py): The plan-derived IAM model, and its
  refusal to guess. An unknown trust policy is not-determinable and never an empty list, an
  absent policy is distinguishable from an unknown one, and a role's module is derived from
  its address when the module address is absent.
- [`tests/test_reconciler.py`](./test_reconciler.py): The canvas-to-HCL split. `propose()`
  touches no Terraform file, no decision record and writes no audit entry; the proposal
  carries everything the modal must show; and the summary names both sides of the change in
  plain English.
- [`tests/test_vault.py`](./test_vault.py): The catalog describes what could exist and marks
  each entry present or absent, present documents carry a size and absent ones do not, and a
  missing run yields an empty catalog rather than a fabricated one.
- [`tests/test_console_app.py`](./test_console_app.py): Every navigable view has a renderer
  and renders without a run rather than raising, the run band reports absent facts as absent,
  the console never shells out, and it writes nothing except through the vault bundle.
- [`tests/test_console_lifecycle.py`](./test_console_lifecycle.py): Port detection that does
  not assume something listening is ours -- our console is identified by its own response, and
  a second launch reuses the running one rather than failing to bind.
- [`tests/test_lineage_graph.py`](./test_lineage_graph.py): The five medallion hops for a full
  stack, no quality gate or quarantine fork for a stack without the module, edges that only
  reference nodes that exist, an empty stack producing an empty graph rather than a default
  one, and plan-derived facts replacing the pattern's defaults with `facts_source` recording
  which is which.
- [`tests/test_pillars.py`](./test_pillars.py): The 18-pillar catalogue and its arithmetic.
  The tests that matter are the REFUSALS (no volume means no worker count) and the BANDS (an
  object above the target is not described as inside it), plus the wiring guards -- a depth
  branch keyed on a string no option produces is unreachable, and an option that selects no
  generator engine says so rather than defaulting to the first.
- [`tests/test_pattern_promotion.py`](./test_pattern_promotion.py): Promotion refuses every
  run that cannot show its work -- no run, no plan, no proving report without an explicit
  skip. A registry of unproven patterns propagates into every later run that reuses one.
- [`tests/test_mcp_server.py`](./test_mcp_server.py): The stdio MCP surface -- `initialize`
  answers with a protocol version, a notification gets no reply, `tools/list` returns every
  tool with a schema, and an unknown method or tool is a protocol error rather than a crash.
- [`tests/test_mcp_gateway.py`](./test_mcp_gateway.py): The gateway controls -- PII redaction
  over nested payloads, OPA allowing a read-only tool for an analyst while requiring step-up
  for a mutating one, and an untrusted SPIFFE id denied by default.
- [`tests/test_iam_policies.py`](./test_iam_policies.py): The shipped IAM examples are valid
  policy documents, every statement carries an effect and a sid, placeholders are obvious
  rather than plausible (a plausible one gets deployed), and the plan role can read state and
  hold its lock without being able to write it.
