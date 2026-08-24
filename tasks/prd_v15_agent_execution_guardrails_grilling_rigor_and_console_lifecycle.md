# Product Requirements Document (PRD) — Agent Execution Guardrails, Grilling Rigor, Budget Alignment & Console Lifecycle (v15.0)

> **Directive from Matt (Principal Cloud Architect & CLI Design Lead):**
> 
> *"Team: We are locking down three critical gaps discovered during live interactive agent testing:*
> 1. **Agent Sandboxing & Destructive Command Interceptor:** Autonomous agents must be structurally blocked from executing dangerous shell commands (`rm -rf`, `del /s`, `terraform destroy`, `git reset --hard`) and mutating deploy gates (`minusctl gate apply`, `minusctl prove --execute`) without explicit, cryptographically signed Human-in-the-Loop approval.
> 2. **Grilling Rigor & Dynamic Budget Alignment:** The grilling engine must thoroughly interrogate the 7 Data Engineering Pillars and TerraShark failure modes upfront. Furthermore, we must eliminate false-positive budget warnings by dynamically calculating the `aws_budgets_budget` guardrail from the synthesized architecture rather than inserting a hardcoded $500 default.
> 3. **Single-Instance Console Lifecycle:** The `minusctl console` launcher must be idempotent. If port 8050 is already listening, it must open the existing browser instance rather than crashing on port conflicts or spawning duplicate servers.
> 
> *Read this specification, author the guardrail interceptor and console lifecycle modules, and maintain 100% test coverage with zero emojis across all code, logs, and UI."*

---

| Attribute | Details |
| :--- | :--- |
| **Document ID** | PRD-ARCH-2026-015 (Revision 15.0 — Agent Execution Guardrails, Grilling Rigor, Dynamic Budget Alignment & Console Lifecycle) |
| **Document Name** | `tasks/prd_v15_agent_execution_guardrails_grilling_rigor_and_console_lifecycle.md` |
| **Status** | APPROVED SPECIFICATION FOR IMPLEMENTATION |
| **Lead Architect** | Matt (Principal Cloud Architect & CLI Design Lead) |
| **Target Components** | `core/governance/agent_guardrails.py`, `core/cli/commands/console.py`, `core/architecture/requirements.py`, `core/governance/plan_gate.py`, `tests/test_agent_guardrails.py`, `tests/test_console_lifecycle.py` |
| **Target Audience** | Coding Agent, Platform Engineers, SecOps & Reliability Teams |
| **Date** | August 24, 2026 |

---

## 1. Problem Statement & Root Cause Analysis

### 1.1 The False-Positive Budget Warning Issue
* **Observed Behavior:** During `minusctl gate approve`, the gate alerted:
  `[gate] WARNING: the AWS forecast ($1,258.29/mo) is 252% of this plan's own budget guardrail ($500.00/mo aws_budgets_budget).`
* **Root Cause:** The synthesized `governance-observability` module defaulted to a static `$500.00` limit, while the actual Glue PySpark ETL workers and S3 storage priced by the BCM Pricing Calculator totaled `$1,258.29 / mo`. The grilling agent did not dynamically align the budget guardrail with the synthesized compute profile.

### 1.2 Unchecked Agent Command Execution Risks
* **Risk:** AI agents have tool access to `run_command`. Without an explicit interceptor, a malfunctioning or misdirected agent could execute destructive commands (`rm -rf`, `del /s`, `terraform destroy`, `git push --force`) or bypass human approval by calling `minusctl gate apply`.

### 1.3 Console Server Port Collisions
* **Observed Behavior:** Invoking `minusctl console` when an instance is already running throws a socket binding error (`OSError: [Errno 98/10048] Address already in use: 8050`).

---

## 2. Technical Architecture & Functional Requirements

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                     PRD v15.0 SYSTEM TOPOLOGY                                    │
├──────────────────────────┬──────────────────────────┬────────────────────────────────────────────┤
│ 1. Agent Sandbox Guard   │ 2. Dynamic FinOps Budget │ 3. Smart Console Lifecycle                 │
│    `agent_guardrails.py` │    Alignment             │    `minusctl console`                      │
│    • Blocks `rm -rf`     │    • Aligns BCM forecast │    • Probes port 8050 (TCP ping)           │
│    • Blocks `destroy`    │      with `aws_budgets`  │    • Reuses running server instance        │
│    • Restricts writes to │    • Sizing-driven cap   │    • Opens active browser tab              │
│      active `runs/<id>/` │    • Eliminates 252% warn│    • Zero duplicate processes              │
└──────────────────────────┴──────────────────────────┴────────────────────────────────────────────┘
```

---

### 2.1 FR-01: Autonomous Agent Execution Sandbox (`agent_guardrails.py`)

A dedicated security interceptor module [`core/governance/agent_guardrails.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/governance/agent_guardrails.py) intercepts and evaluates any command before execution:

1. **Strict Destructive Command Blacklist:**
   The guardrail strictly blocks and raises `PermissionError` on any command matching:
   * `rm -rf`, `rmdir /s`, `del /s /q`, `rm -r`
   * `terraform destroy`, `terraform state rm`, `terraform force-unlock`
   * `git reset --hard`, `git push --force`, `git clean -fdx`
   * `aws s3 rb --force`, `aws s3 rm s3://... --recursive` (unless targeting ephemeral test buckets)
   * `drop table`, `drop database`, `truncate table`

2. **HITL Mutating Gate Enforcement:**
   * Autonomous agents are strictly forbidden from executing `minusctl gate apply` or `minusctl prove --execute`.
   * These commands require a cryptographically signed human approval session token or direct interactive stdin from a human operator.

3. **Run-Scoped Workspace Isolation:**
   * When an agent is operating on `run_id`, all file creation and modification operations are restricted strictly to `runs/<run_id>/`.
   * Agents cannot overwrite root project files, other runs, or core engine modules (`core/`, `modules/`, `app/`) without an explicit maintenance flag.

4. **Direct Script Execution Interception & Canonical `minusctl` Re-Routing:**
   * Autonomous agents are strictly blocked from invoking raw internal Python script paths (e.g. `python core/governance/plan_gate.py ...`, `python core/generation/synthesizer.py ...`).
   * The interceptor halts raw script calls and forces canonical CLI execution:
     `minusctl gate verify`, `minusctl gate plan`, `minusctl diagram`, `minusctl cost estimate`.

5. **Pre-Hardened Master Module Invariant (Eliminating Self-Debugging Thrashing):**
   * Master module templates (including `modules/table-format-iceberg`) must be pre-configured with default partition keys (`service_date (date)`) and baseline security compliance.
   * Eliminates the 10-step micro-thrashing anti-pattern where an agent fails `plan_gate verify` on `DATA-02`, searches through `optimize_analyzer.py`, and edits HCL manually. Synthesis must pass 100% of native optimizer rules on the very first pass.

---

### 2.2 FR-02: Dynamic Budget Guardrail Alignment

To eliminate the 252% budget mismatch warning:

1. **Dynamic Sizing-Driven Budget Baseline:**
   * During synthesis (`synthesizer.py`), the `monthly_budget_usd` variable in `governance-observability` is dynamically set to:
     $$\text{Budget Guardrail} = \max(\text{Declared User Budget}, \text{BCM Estimated Spend} \times 1.25)$$
   * This provides a 25% operational headroom buffer above base compute/storage costs before triggering AWS Budgets CloudWatch alarms.
2. **Grilling Interview Sizing Reconciliation:**
   * If the user declares a volume (e.g. 50 GB/day) that requires `$1,200/mo` of AWS Glue DPUs, but specifies a `$500` budget cap, `grill-me` immediately alerts the user of the economic contradiction and offers to:
     - Scale down compute (e.g. Athena SQL or scheduled micro-batching), OR
     - Adjust the budget guardrail to `$1,500/mo`.

---

### 2.3 FR-03: Single-Instance Console Launcher (`minusctl console`)

Enhance [`core/cli/commands/console.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/cli/commands/console.py) with smart socket discovery:

1. **Idempotent Port 8050 Discovery:**
   * Before spawning Dash/Flask, `minusctl console` probes `127.0.0.1:8050`.
   * If an existing MinusOps console process is already listening:
     - It prints: `[console] MinusOps Console is already running on http://127.0.0.1:8050 (PID <pid>)`.
     - Automatically launches the default web browser to `http://127.0.0.1:8050` using `webbrowser.open()`.
     - Returns exit code 0 without crashing or spawning a second instance.
2. **Headless Background Launch (`--daemon` / `--background`):**
   * Supports running the console in the background with PID tracking under `.minus/console.pid`.
   * `minusctl console stop` terminates the running console process cleanly.

---

### 2.4 FR-04: Upfront 7-Step Lifecycle Roadmap in `grill-me`

Whenever an agent initiates a `build` or `create` workflow, it must open with the standardized 7-step roadmap:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                            MINUSOPS INFRASTRUCTURE LIFECYCLE ROADMAP                             │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│  [1] Requirements Grilling   ──► Interrogate 7 Data Pillars, NFRs & Budget Caps (grill-me)      │
│  [2] Architecture Decision   ──► Select vetted modules & justify tradeoffs (ADR)                 │
│  [3] Modular HCL Synthesis   ──► Synthesize production Terraform in runs/<run-id>/terraform/     │
│  [4] Visual Topology & DAG   ──► Compile Draw.io architecture XML & 1-click browser URL          │
│  [5] Reflector Circuit Gate  ──► Evaluate 5 independent validation gates (Reflector 5/5)         │
│  [6] Plan Gate & BCM Pricing ──► Generate cryptographic plan_hash and live AWS pricing estimate  │
│  [7] Human-in-the-Loop Gate  ──► Review plan hash, approve, and execute 5-hop live data proving   │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Implementation Work Packages

```
core/
├── governance/
│   ├── agent_guardrails.py    <── WP-01: Command blacklist, HITL barrier & workspace isolation
│   └── plan_gate.py          <── WP-02: Dynamic budget reconciliation & 25% headroom logic
├── cli/
│   └── commands/
│       └── console.py         <── WP-03: Single-instance port discovery, browser open & stop CLI
└── architecture/
    └── requirements.py        <── WP-04: Contradiction detector (Volume vs Budget ceiling)

tests/
├── test_agent_guardrails.py   <── WP-05: Unit tests for command blocking & permissions
└── test_console_lifecycle.py  <── WP-05: Unit tests for socket probing & browser open
```

| Work Package | Deliverable | Target Files |
| :--- | :--- | :--- |
| **WP-01** | Agent Sandbox Guardrail Interceptor | `core/governance/agent_guardrails.py` |
| **WP-02** | Dynamic Budget Alignment & FinOps Headroom | `core/governance/plan_gate.py`, `synthesizer.py` |
| **WP-03** | Single-Instance Console Launcher & CLI | `core/cli/commands/console.py`, `app/console_app.py` |
| **WP-04** | Upfront 7-Step Roadmap & Contradiction Guard | `core/architecture/requirements.py`, `grill-me/SKILL.md` |
| **WP-05** | Test Suite & Zero-Emoji Verification | `tests/test_agent_guardrails.py`, `tests/test_console_lifecycle.py` |

---

## 4. Acceptance Criteria & Non-Negotiable Invariants

1. **Destructive Command Blocking:** `agent_guardrails.py` must intercept and block 100% of blacklisted destructive patterns (`rm -rf`, `terraform destroy`, `git reset --hard`) with exit code 1 / `PermissionError`.
2. **Mutating Command HITL Gate:** `minusctl gate apply` cannot be executed by autonomous subagents without verified human confirmation.
3. **No False-Positive Budget Warnings:** Dynamic budget calculation must size `aws_budgets_budget` with 25% headroom above BCM cost estimates, eliminating false-positive 252% warnings.
4. **Idempotent Console Launch:** Running `minusctl console` twice must reuse the active server without port binding errors.
5. **Standard Library Only for Core Engine:** All backend guardrail and discovery modules must use Python standard library (`socket`, `subprocess`, `re`, `os`, `webbrowser`).
6. **Zero-Emoji Doctrine:** Strictly zero emojis across all code, comments, logs, and terminal text.
7. **100% Test Pass Rate:** All new and existing pytest test suites must pass with exit code 0.
