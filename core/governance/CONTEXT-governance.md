# Governance Architecture Context: `core/governance/`

This document provides an exhaustive reference for all governance components in the MinusOps control plane. It details the plan-bound deploy loop, Human-In-The-Loop (HITL) gates, cryptographic audit trails, policy evaluation, and safety guards.

---

## Executive Overview & Architectural Model

The `core/governance/` engine enforces state-aware, plan-bound, tamper-evident infrastructure delivery. 

```
                                    ┌──────────────────────┐
                                    │    verify stage      │
                                    │  fmt / validate /    │
                                    │  optimize_analyzer   │
                                    └──────────┬───────────┘
                                               │
                                               ▼
┌──────────────────────┐            ┌──────────────────────┐            ┌──────────────────────┐
│  source_guard.py     ├───────────►│      plan stage      │◄───────────┤ address_churn.py     │
│ (source baseline)    │            │ tfplan -> sha256 hash│            │ (rename-shaped data) │
└──────────────────────┘            └──────────┬───────────┘            └──────────────────────┘
                                               │
                                               ▼
┌──────────────────────┐            ┌──────────────────────┐            ┌──────────────────────┐
│ destructive_change   ├───────────►│    approve stage     │◄───────────┤ authz.py             │
│ (autonomy boundary)  │            │ hash-bound signoff   │            │ (STS identity / RBAC)│
└──────────────────────┘            └──────────┬───────────┘            └──────────────────────┘
                                               │
                                               ▼
┌──────────────────────┐            ┌──────────────────────┐            ┌──────────────────────┐
│ cloud_drift.py       ├───────────►│     apply stage      │◄───────────┤ ephemeral_apply.py   │
│ (out-of-band revert) │            │ tfplan execution     │            │ (G9 LocalStack test) │
└──────────────────────┘            └──────────┬───────────┘            └──────────────────────┘
                                               │
                                               ▼
                                    ┌──────────────────────┐
                                    │    audit_chain.py    │
                                    │ append-only hash log │
                                    └──────────────────────┘
```

---

## File Details

### 1. [`plan_gate.py`](./plan_gate.py)

- **Exact Purpose**: Serves as the central state-aware, plan-bound deploy orchestrator enforcing the secure deployment loop (`verify` → `plan` → `approve` → `apply`). Guarantees that `apply` executes only the exact reviewed plan hash.
- **Key Functions / Classes**:
  - [`stage_verify(dir_, policy_mode)`](./plan_gate.py): Runs `terraform fmt -check`, `init -backend=false`, `validate`, and security scans (`optimize_analyzer.py`).
  - [`stage_plan(dir_, policy_mode, destroy)`](./plan_gate.py): Executes `terraform plan -out=tfplan`, calculates sha256 `_plan_hash`, records pending state (`pending_plan.json`), runs G6 shadow Rego evaluation, G9 ephemeral apply check, cloud drift detection, and address churn analysis.
  - [`stage_approve(dir_, mode, policy_mode)`](./plan_gate.py): Verifies approver identity via STS/RBAC, checks plan hash integrity, checks budget guardrails, prompts human (in `gatekeeper` mode), and records an immutable approval JSON (`approvals/<plan_hash>.json`).
  - [`stage_apply(dir_, mode, policy_mode)`](./plan_gate.py): Validates active cloud session, credential posture (rejecting static/root creds), two-person rule in production, out-of-band revert flags, G9 clean verdicts, and runs `terraform apply -json tfplan`.
  - [`stage_run(dir_, mode, policy_mode, destroy)`](./plan_gate.py): Executes all four stages sequentially.
  - [`_apply_with_json_capture(dir_, applied, failed, errors)`](./plan_gate.py): Streams `terraform apply -json` output, updating progress and recording per-resource applied/errored status.
  - [`_gate_state_lock(path)`](./plan_gate.py): Provides inter-process advisory locking for atomic gate state reads/writes.
  - [`_write_json_atomic(path, payload)`](./plan_gate.py): Writes temporary files (`.pid.thread.tmp`) and uses `os.replace` to prevent state corruption on crashes.
- **Inputs & Outputs**:
  - *Inputs*: Target Terraform directory (`--dir`), approval mode (`--mode gatekeeper|auto-approve`), policy mode (`--policy-mode dev|production`), teardown flag (`--destroy`).
  - *Outputs*: Exit code (0 for success, 1 for failure), persistent state files under `.agents/logs/plan_gate/<dir_key>/`, and audit trail events.
- **Failure Modes**:
  - Fail-closed on missing/tampered audit chain ([`_reject_if_audit_chain_tampered`](./plan_gate.py)).
  - Fail-closed on plan hash mismatch or stale source baseline ([`_reject_if_source_stale`](./plan_gate.py)).
  - Fail-closed on long-term/root AWS credentials ([`_reject_if_weak_credentials`](./plan_gate.py)).
  - Fail-closed on self-approval in production mode ([`_enforce_production_approval`](./plan_gate.py)).
  - Fail-closed on dev apply outside designated sandbox accounts ([`_reject_if_nonsandbox_dev`](./plan_gate.py)).
  - Fail-closed on identity mismatch between approver and applying principal ([`_reject_if_apply_identity_mismatches_approver`](./plan_gate.py)).
  - Fail-closed on promoted policy rule violations ([`_reject_if_promoted_policy_violated`](./plan_gate.py)).
  - Fail-closed on non-autonomous-eligible plans during `auto-approve` mode ([`_reject_if_destructive_and_auto_approve`](./plan_gate.py)).
  - Fail-closed on out-of-band cloud reverts during `auto-approve` mode ([`_reject_if_reverts_out_of_band_and_auto_approve`](./plan_gate.py)).
  - Fail-closed on un-clean G9 ephemeral apply during `auto-approve` mode ([`_reject_if_g9_not_clean_and_auto_approve`](./plan_gate.py)).
- **Architectural Role**: The primary operational front-door and enforcement engine for infrastructure changes in MinusOps.

---

### 2. [`approval.py`](./approval.py)

- **Exact Purpose**: Generic approval gate for side-effecting / mutating actions (e.g. notifications, ticket creation, infrastructure mutations outside plan_gate).
- **Key Functions / Classes**:
  - [`request_approval(action, details, mode="gatekeeper")`](./approval.py): Checks approver authorization, handles TTY interactive prompts or auto-approval, logs outcome to audit chain.
  - [`_audit(action, details, mode, decision, **extra)`](./approval.py): Formats and appends an approval event record to `.agents/logs/audit.jsonl`.
- **Inputs & Outputs**:
  - *Inputs*: Action string, details string, approval mode (`gatekeeper` or `auto-approve`).
  - *Outputs*: Boolean (`True` if authorized and approved, `False` otherwise). CLI exit code 0 or 1.
- **Failure Modes**:
  - Fail-closed (`False`) if operator is not authorized per RBAC (`authz.authorize()`).
  - Fail-closed (`False`) in `gatekeeper` mode if `sys.stdin` is not an interactive TTY (`DENIED_NO_TTY`).
  - Defaults to `gatekeeper` mode if an unrecognized mode string is provided.
- **Architectural Role**: Provides standard side-effect gatekeeping for non-Terraform operations across the control plane.

---

### 3. [`audit_chain.py`](./audit_chain.py)

- **Exact Purpose**: Implements a cryptographically linked, append-only JSONL audit log to provide tamper-evident proof of all control plane actions.
- **Key Functions / Classes**:
  - [`append(path, record)`](./audit_chain.py): Appends a record bound to `prev_hash` and `entry_hash = sha256(prev_hash + canonical(record))`.
  - [`verify(path)`](./audit_chain.py): Re-derives and checks every hash link in the chain from genesis (`0` * 64). Returns `(ok, errors)`.
  - [`chain_status(path)`](./audit_chain.py): Checks chain integrity while permitting a legacy unchained prefix if present before chaining began.
  - [`seal(path)`](./audit_chain.py): Archives unchained legacy audit logs to a timestamped `.bak` file and anchors a fresh cryptographic chain with the backup's sha256 digest.
  - [`last_hash(path)`](./audit_chain.py): Returns the entry hash of the final record, or genesis for an empty log.
  - [`_AppendLock`](./audit_chain.py): Context manager providing OS-native advisory region locking (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX) and process-wide `threading.Lock` for lock sidecars.
- **Inputs & Outputs**:
  - *Inputs*: Audit log filepath, event dictionary.
  - *Outputs*: Complete audit record dictionary with `prev_hash` and `entry_hash`. Verification status boolean and error list.
- **Failure Modes**:
  - `verify()` flags any line modification, reordering, deletion, or invalid JSON as a chain failure.
  - `_AppendLock` raises `TimeoutError` after 10 seconds of unresolvable file lock contention.
- **Architectural Role**: The core security ledger establishing non-repudiation and auditability for MinusOps.

---

### 4. [`audit_logger.py`](./audit_logger.py)

- **Exact Purpose**: Lightweight wrapper around `audit_chain.append()` for logging general system events.
- **Key Functions / Classes**:
  - [`log_audit_event(action, details, log_dir)`](./audit_logger.py): Constructs an event record with UTC timestamp, OS user, action, details, and status `"RECORDED"`, then appends it to `audit.jsonl`.
- **Inputs & Outputs**:
  - *Inputs*: Action string, details string, target log directory.
  - *Outputs*: Boolean (`True` if logged, `False` if exception occurs).
- **Failure Modes**:
  - Returns `False` and prints an error message to `stderr` if audit file writing fails.
- **Architectural Role**: Standardized interface for general event logging across CLI tools and subagents.

---

### 5. [`authz.py`](./authz.py)

- **Exact Purpose**: Approver Role-Based Access Control (RBAC) provider, matching caller identities against configured allowlists.
- **Key Functions / Classes**:
  - [`operator()`](./authz.py): Returns `MINUS_OPERATOR` env var or falls back to `getpass.getuser()`.
  - [`verified_operator()`](./authz.py): Extracts the cryptographically authenticated principal from active AWS STS caller identity (`sts get-caller-identity`), preventing env var spoofing.
  - [`configured_approvers(workspace=".")`](./authz.py): Computes the union of approvers from `MINUS_APPROVERS` env var and `.minus/approvers.json`.
  - [`authorize(op=None, workspace=".")`](./authz.py): Evaluates operator against allowlist; returns `(allowed, mode, reason)`. Mode is `"enforced"` if allowlist exists, `"open"` otherwise.
  - [`_principal_from_arn(arn)`](./authz.py): Parses role session names or IAM usernames from STS ARNs.
- **Inputs & Outputs**:
  - *Inputs*: Operator string (optional), workspace root directory.
  - *Outputs*: Tuple `(allowed: bool, mode: str, reason: str)`.
- **Failure Modes**:
  - Returns `(True, "open", ...)` if no allowlist is configured (dev mode).
  - Returns `(False, "enforced", ...)` if an allowlist exists but operator is not listed.
- **Architectural Role**: Identity verification and access policy enforcement layer for deploy and action approvals.

---

### 6. [`cloud_drift.py`](./cloud_drift.py)

- **Exact Purpose**: Inspects Terraform plan JSON `resource_drift` entries to detect out-of-band cloud changes and flags plans that attempt to revert those manual changes.
- **Key Functions / Classes**:
  - [`classify(plan_json)`](./cloud_drift.py): Compares `resource_drift` before/after values with `resource_changes` proposed values to identify reverted attributes.
  - [`format_result(result)`](./cloud_drift.py): Generates human-readable summary lines highlighting out-of-band reverts.
  - [`_changed_attributes(before, after)`](./cloud_drift.py): Calculates top-level attribute key differences between two resource state dicts.
- **Inputs & Outputs**:
  - *Inputs*: Parsed `terraform show -json` plan dictionary.
  - *Outputs*: Dictionary with `drift_count`, `drifted` items, `reverted` items, `reverted_count`, `reverts_out_of_band_changes` boolean, and `malformed_count`.
- **Failure Modes**:
  - Never raises exceptions; malformed drift entries increment `malformed_count` and allow classification to finish gracefully.
- **Architectural Role**: Prevents accidental reversal of emergency manual cloud interventions during automated deploys.

---

### 7. [`destructive_change_gate.py`](./destructive_change_gate.py)

- **Exact Purpose**: Evaluates plan JSON to establish the autonomy boundary: determines if a plan can ship autonomously (`auto-approve`) or requires staged human review.
- **Key Functions / Classes**:
  - [`classify(plan_json)`](./destructive_change_gate.py): Strict allowlist evaluation (`actions == ["create"]` on `AUTO_SHIP_ELIGIBLE_TYPES`). Checks for stateful resource types ([`STATEFUL_RESOURCE_TYPES`](./destructive_change_gate.py)), IAM resource types ([`IAM_RESOURCE_TYPES`](./destructive_change_gate.py)), reviewed unsafe types ([`REVIEWED_UNSAFE_TYPES`](./destructive_change_gate.py)), non-create actions, unreviewed types ([`AUTO_SHIP_ELIGIBLE_TYPES`](./destructive_change_gate.py)), and Databricks resources.
  - [`classify_file(path)`](./destructive_change_gate.py): Loads JSON file from disk and calls `classify()`.
  - [`_fail_closed(reason, address, rtype)`](./destructive_change_gate.py): Returns non-autonomous dictionary for malformed inputs.
- **Inputs & Outputs**:
  - *Inputs*: Parsed plan JSON dict or file path.
  - *Outputs*: Dict containing `autonomous_eligible` (bool), `findings` (list), `reduced_assurance` (bool), `databricks_resources` (list), and `resource_change_count` (int).
- **Failure Modes**:
  - Fail-closed: Any unreadable plan, missing `resource_changes`, unreviewed resource type, IAM change, stateful resource touch, replace action, or Databricks resource sets `autonomous_eligible = False`.
- **Architectural Role**: Core autonomous deployment safety gate enforcing strict boundaries against unattended destructive changes.

---

### 8. [`ephemeral_apply.py`](./ephemeral_apply.py)

- **Exact Purpose**: Ephemeral apply engine (G9) that tests Terraform plans against local cloud emulators (LocalStack, MiniStack, Floci) to detect apply-time runtime and dependency failures.
- **Key Functions / Classes**:
  - [`run_ephemeral_apply(dir_, emulator, ...)`](./ephemeral_apply.py): Generates emulator provider overrides, runs `terraform init`, `plan`, `apply -auto-approve -json`, and `destroy -auto-approve`.
  - [`classify_coverage(plan_json)`](./ephemeral_apply.py): Categorizes plan AWS/Databricks footprint into `"full"`, `"partial"`, or `"none"`.
  - [`unverified_types_in_plan(plan_json, emulator)`](./ephemeral_apply.py): Finds resource types lacking positive verification for the specified emulator.
  - [`negative_fidelity_unverified_types_in_plan(plan_json, emulator)`](./ephemeral_apply.py): Identifies security-critical types (IAM, KMS, S3 policy) lacking negative fidelity verification on the emulator.
  - [`compose_with_g5(g5_classification, g9_result)`](./ephemeral_apply.py): Merges G5 destructive classification with G9 ephemeral apply verdict.
  - [`log_result(dir_, result)`](./ephemeral_apply.py): Appends G9 execution verdict to shared audit chain.
  - [`_generate_provider_override(endpoint)`](./ephemeral_apply.py): Formats a dedicated `provider "aws"` HCL block with dummy keys and custom service endpoint overrides.
- **Inputs & Outputs**:
  - *Inputs*: Target Terraform directory, emulator name (`localstack`, `ministack`, `floci`), endpoint URL, timeouts.
  - *Outputs*: Dict with `evaluation_failed` (bool), `coverage` (str), `emulator` (str), `aws_resources_applied` (list), `findings` (list).
- **Failure Modes**:
  - Fail-closed: Missing terraform binary, init failure, plan failure, unverified resource types, negative fidelity gaps, apply errors, JSON stream corruption, or teardown failures set `evaluation_failed = True`.
- **Architectural Role**: Real pre-deploy execution testing environment catching implicit dependency and provider-validation bugs before live cloud deployment.

---

### 9. [`plan_reader.py`](./plan_reader.py)

- **Exact Purpose**: Shared, fail-closed utility module for reading and extracting structures from `terraform show -json` plan data.
- **Key Functions / Classes**:
  - [`read_resource_changes(plan_json, treat_absent_as_error)`](./plan_reader.py): Validates and extracts `resource_changes`. Returns `(list_or_None, error_str_or_None)`.
  - [`managed_only(resource_changes)`](./plan_reader.py): Filters out `mode == "data"` entries while isolating malformed entries.
  - [`resource_drift(plan_json)`](./plan_reader.py): Safely extracts `resource_drift` array.
  - [`data_sources(plan_json)`](./plan_reader.py): Extracts data source reads from `prior_state.values.root_module.resources`.
  - [`config_resources(plan_json)`](./plan_reader.py): Extracts `configuration.root_module.resources`.
  - [`module_calls(plan_json)`](./plan_reader.py): Extracts `configuration.root_module.module_calls`.
  - [`base_address(address)`](./plan_reader.py): Strips index suffixes (`aws_s3_bucket.b["key"]` → `aws_s3_bucket.b`).
- **Inputs & Outputs**:
  - *Inputs*: Plan JSON dict, boolean flags.
  - *Outputs*: Cleaned lists of resource changes, drift, data sources, or error identifiers.
- **Failure Modes**:
  - Returns explicit error strings (`"plan_json_not_a_dict"`, `"resource_changes_missing_or_null"`, `"resource_changes_not_a_list"`) when JSON structural invariants are broken.
- **Architectural Role**: Centralized parser ensuring consistent, hardened plan JSON ingestion across all governance components.

---

### 10. [`rego_gate.py`](./rego_gate.py)

- **Exact Purpose**: Evaluates Open Policy Agent (OPA) Rego rules (`policy/g6/rules.rego`) against plan JSON in shadow mode (G6) to detect security and cost violations.
- **Key Functions / Classes**:
  - [`evaluate(plan_json, opa_bin, policy_path)`](./rego_gate.py): Writes plan JSON to a temporary file, executes `opa eval`, and parses JSON findings.
  - [`evaluate_dir(dir_)`](./rego_gate.py): Convenience helper running `terraform show -json tfplan` before calling `evaluate()`.
  - [`_fail(reason, detail)`](./rego_gate.py): Helper constructing standardized evaluation failure dicts.
- **Inputs & Outputs**:
  - *Inputs*: Plan JSON dict or directory, OPA executable path (optional), policy file path (optional).
  - *Outputs*: Dict containing `evaluation_failed` (bool), `reason` (str), `detail` (str), and `findings` (list).
- **Failure Modes**:
  - Fail-closed evaluation status: Missing OPA binary, missing policy file, subprocess errors, non-zero OPA exit codes, or unparseable output return `evaluation_failed = True`.
- **Architectural Role**: Declarative policy engine interface enabling complex JSON-native static analysis over Terraform plans.

---

### 11. [`rule_stages.py`](./rule_stages.py)

- **Exact Purpose**: Manages rule promotion and demotion lifecycle (`policy/rule_stages.json`), ensuring Rego rules default to `warn` and only block deploys when explicitly promoted by an attributable human decision.
- **Key Functions / Classes**:
  - [`stage_of(rule_id, registry_path)`](./rule_stages.py): Returns `"blocking"` or `"warn"` for a rule ID.
  - [`is_blocking(rule_id, registry_path)`](./rule_stages.py): Returns boolean indicating if rule is blocking.
  - [`partition(findings, registry_path)`](./rule_stages.py): Splits a list of findings into `{"blocking": [...], "warning": [...]}`.
  - [`promote(rule_id, *, promoted_by, reason, registry_path)`](./rule_stages.py): Promotes a rule to `"blocking"` and saves attributable metadata.
  - [`demote(rule_id, *, demoted_by, reason, registry_path)`](./rule_stages.py): Demotes a rule back to `"warn"`.
  - [`list_rules(registry_path)`](./rule_stages.py): Returns dict of rules and their promotion state from the registry.
- **Inputs & Outputs**:
  - *Inputs*: Rule ID strings, operator names, reason strings, findings lists.
  - *Outputs*: Partition dicts, stage strings, or updated rule registry dicts.
- **Failure Modes**:
  - Fail-safe: Missing or corrupt `rule_stages.json` defaults all rules to `warn` stage (preventing unreviewed rules from wedging deployment pipelines).
  - `promote()` and `demote()` raise `ValueError` if `promoted_by`/`demoted_by` or `reason` are empty.
- **Architectural Role**: Governs the transition of static policy rules from advisory feedback to binding deploy blockers.

---

### 12. [`source_guard.py`](./source_guard.py)

- **Exact Purpose**: Generates and checks source code baselines and snapshots in `.minus/` to detect manual edits in generated Terraform directories.
- **Key Functions / Classes**:
  - [`write_baseline(source_dir, label, extra)`](./source_guard.py): Hashes all source files (`.tf`, `.json`, etc.), copies them to `.minus/source_snapshot/`, and saves `.minus/baseline.json`.
  - [`status(source_dir)`](./source_guard.py): Compares current directory hashes against `.minus/baseline.json` to compute `CURRENT`, `STALE`, or `UNKNOWN`.
  - [`diff(source_dir)`](./source_guard.py): Generates unified diff strings between snapshot files and modified workspace files.
  - [`iter_source_files(source_dir)`](./source_guard.py): Yields source files while skipping hidden/meta directories (`.terraform`, `.git`, `.minus`).
  - [`source_hashes(source_dir)`](./source_guard.py): Returns dictionary of relative file paths to SHA-256 digests.
  - [`load_baseline(source_dir)`](./source_guard.py): Reads and parses `.minus/baseline.json`.
- **Inputs & Outputs**:
  - *Inputs*: Target Terraform directory, label string.
  - *Outputs*: Baseline records, status dicts (`changed`, `missing`, `added`), unified diff lines.
- **Failure Modes**:
  - `status()` returns `"UNKNOWN"` with `stale = True` if `baseline.json` is missing.
- **Architectural Role**: Baseline tracking tool ensuring generated code provenance and flagging unauthorized manual HCL modifications before approval.

---

### 13. [`tf_validate.py`](./tf_validate.py)

- **Exact Purpose**: Offline, credential-free validation wrapper around `terraform init -backend=false` and `terraform validate -json`.
- **Key Functions / Classes**:
  - [`validate(dir_, timeout)`](./tf_validate.py): Runs offline init and validate, returning structured diagnostic dictionaries.
  - [`validate_and_record(dir_, timeout)`](./tf_validate.py): Runs `validate()` and persists results to `<dir>/validation.json`.
  - [`load(dir_)`](./tf_validate.py): Reads existing `<dir>/validation.json` if available.
- **Inputs & Outputs**:
  - *Inputs*: Target Terraform directory path, timeout integer.
  - *Outputs*: Dict with `available` (bool), `ok` (bool/None), `error_count` (int), `warning_count` (int), `diagnostics` (list).
- **Failure Modes**:
  - Returns `{"available": False, "ok": None}` if `terraform` executable is missing on PATH.
  - Returns `{"available": True, "ok": False}` if init fails, validate fails, or timeout occurs.
- **Architectural Role**: Fast, lightweight pre-flight syntax and schema checker executed immediately post-synthesis.

---

### 14. [`verification_coverage.py`](./verification_coverage.py)

- **Exact Purpose**: Measures and classifies plan resource verification coverage into three honest states: `rule_covered`, `claim_informed`, and `unchecked`.
- **Key Functions / Classes**:
  - [`classify(plan_json, findings, claims_by_type)`](./verification_coverage.py): Maps managed plan resource types against fired rule findings and claim store entries. Calculates `coverage_ratio = rule_covered_count / type_count`.
  - [`_type_of_resource_ref(ref)`](./verification_coverage.py): Extracts resource type from address or type string.
- **Inputs & Outputs**:
  - *Inputs*: Plan JSON dict, findings tuple, claims dictionary.
  - *Outputs*: Dict containing `types` (list of per-type state dicts), `rule_covered_count`, `claim_informed_count`, `unchecked_count`, `coverage_ratio` (float or None).
- **Failure Modes**:
  - Handles missing plan data gracefully; sets `plan_unreadable = True` if plan reading fails. Sets `coverage_ratio = None` for empty plans.
- **Architectural Role**: Transparency reporting component exposing true policy test coverage versus unexamined infrastructure.

---

### 14a. [`reflector.py`](./reflector.py)
* **Exact Purpose:** the independent Stage Reflector (MINUS-129). The agent that composed a stack is the worst possible auditor of it -- it reports success against its own intentions rather than against the files on disk, which is how the 2026-08-17 run passed every self-check it ran and still shipped a Glue job that could not write to Silver. Every gate here **re-derives from artifacts**, never from a recorded claim.
* **Gates:** `gate_scope` (requirements volume vs. the compute module actually in `main.tf`, via `modules.compute_tier`), `gate_wiring` (module blocks vs. `_REQUIRED_WIRING` -- the inputs whose absence is a runtime failure rather than a style problem), `gate_security` (re-runs `optimize_analyzer.scan_hcl_files`), `gate_cost` (BCM evidence on disk vs. the stated budget), `gate_plan_hash` (a directory-bound SHA-256 record, read through `plan_gate._pending_path()` rather than a rebuilt path so the storage layout cannot silently drift).
* **Three statuses, and the third is the point:** `pass` / `blocked` / **`unknown`**. A gate that could not run -- no plan yet, no BCM evidence yet -- reports `unknown`, which is **not** a pass. Conflating "I checked and it is fine" with "I could not check" is how a circuit breaker becomes decoration. `gate_security` reports how many `.tf` files it read for the same reason: "no findings" from a scan that read nothing looks identical to "no findings" from a clean stack.
* **`reflect()` runs every gate even after one blocks** -- an operator fixing three problems wants all three now, not one per round trip.
* **Read-only.** No cloud calls, no mutations, never edits the run it inspects. CLI: `reflector.py --run-root runs/<id> [--dir <tf>] [--json]`, exit 2 when blocked.
* **Tests:** [`tests/test_reflector.py`](../../tests/test_reflector.py).

---

### 15. [`address_churn.py`](./address_churn.py)

- **Exact Purpose**: Detects rename-shaped address churn (matching deletes and creates of identical physical resource identities) and enforces `moved {}` blocks to prevent unintended data loss.
- **Key Functions / Classes**:
  - [`classify(plan_json, moved_blocks)`](./address_churn.py): Correlates deleted and created resources by matching identifying attributes (`bucket`, `name`, `identifier`, etc.). Identifies unhandled churn on stateful resources.
  - [`read_moved_blocks(tf_dir)`](./address_churn.py): Parses `moved { from = ... to = ... }` blocks from `.tf` workspace files using regex.
  - [`format_result(result)`](./address_churn.py): Generates actionable blocking error messages showing exact required `moved {}` HCL syntax.
  - [`render_moved(result)`](./address_churn.py): Renders the `moved { from/to }` blocks that would clear the detected churn (MINUS-137 / TerraShark FM-01). **Only rename-shaped (stateful, blocking) pairs get a block** -- advisory pairs such as an IAM role rename recreate harmlessly, and emitting state surgery for them turns a no-op into an unreviewed change.
  - [`write_moved(tf_dir, result, filename="moved.tf")`](./address_churn.py): Writes those blocks into the workspace and returns the path, or `None` when there is nothing to write. **Raises `FileExistsError` rather than overwriting an existing `moved.tf`** -- a hand-edited one is state surgery somebody already reviewed.
  - `main(argv)`: CLI -- `address_churn.py {check|write-moved} --dir <tf-dir> --plan-json <plan.json>`. `check` exits 2 when blocked; `write-moved` exits 2 when it refuses to overwrite.
  - **Round-trip invariant (tested):** what `write_moved()` emits must be what `read_moved_blocks()` + `classify()` then accept as declared, so generating the file actually clears the gate it was generated from.
- **Inputs & Outputs**:
  - *Inputs*: Plan JSON dict, list of parsed `moved` block dicts.
  - *Outputs*: Dict containing `rename_shaped` (blocking list), `advisory` (non-stateful list), `covered_by_moved` (int count), `blocked` (bool).
- **Failure Modes**:
  - Sets `blocked = True` if any stateful resource (e.g. S3 bucket, Redshift namespace, KMS key) is deleted and created under a new address without an explicit `moved {}` block.
- **Architectural Role**: Data loss prevention guard preventing silent resource destruction caused by refactoring or address changes.

---

### 16. [`__init__.py`](./__init__.py)

- **Exact Purpose**: Package initialization file establishing `core.governance` as a Python package.
- **Key Functions / Classes**: None (package marker file).
- **Inputs & Outputs**: N/A.
- **Failure Modes**: N/A.
- **Architectural Role**: Standard Python package declaration.
