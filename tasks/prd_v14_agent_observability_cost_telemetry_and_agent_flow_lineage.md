# Product Requirements Document (PRD) — Agent Observability, AI Token Economics, Workflow Tracing & SOC2/HIPAA Compliance (v14.0)

| Attribute | Details |
| :--- | :--- |
| **Document ID** | PRD-ARCH-2026-014 (Revision 14.0 — Agent Observability, Cost Telemetry, Agent Flow Lineage & Enterprise Compliance) |
| **Document Name** | `tasks/prd_v14_agent_observability_cost_telemetry_and_agent_flow_lineage.md` |
| **Status** | APPROVED SPECIFICATION FOR IMPLEMENTATION |
| **Lead Architect** | Matt (Principal Cloud Architect & CLI Design Lead) |
| **Target Components** | `core/governance/agent_tracer.py`, `core/cost/agent_cost_calculator.py`, `core/reporting/agent_flow_graph.py`, `app/console_app.py`, `tests/test_agent_cost.py`, `tests/test_agent_flow.py` |
| **Target Audience** | Coding Agent, Platform Engineers, Enterprise Cloud Architects, SecOps & Compliance Teams |
| **Date** | August 24, 2026 |

---

## 1. Executive Summary & Problem Statement

### 1.1 The Need
As autonomous agent teams (`grill-me`, `architect`, `synthesizer`, `reflector`, `plan_gate`, `proving`, `slack-agent`) synthesize and govern production infrastructure, enterprises require complete **glass-box observability** into how AI agents reason, what tools they call, what context they consume, and what each autonomous step costs in compute tokens and latency.

Furthermore, regulated enterprises operating under **SOC 2 Type II and HIPAA** mandates require mathematical non-repudiation, tamper-evident audit trails, and strict access governance over agent execution logs and raw payloads.

### 1.2 The Solution
This specification defines the dedicated **Agent Observability, Token Economics, and Workflow Tracing Engine** integrated directly into the new web console under two authoritative sections:
1. **`COST` Section -> `AGENTS COST` Sub-Section:**
   * High-level conversation summary: Total Token Consumption (Input, Output, Cached/Context), Total Dollar Cost (USD), Total Latency, and Context Window Capacity / Pressure.
   * Expandable step-by-step breakdown per subagent and task.
   * RBAC-governed, safe Raw JSON Inspector with sensitive credential redaction.
2. **`FLOW` Section -> `AGENT FLOW` Sub-Section:**
   * End-to-end conversation workflow relay tracing (Prompt -> Requirements -> Architecture Decision -> Synthesis -> Diagramming -> Reflector Gates -> Plan Hash -> Proving -> Notifications).
   * Interactive, zoomable Agent Execution Lineage Graph (DAG) with node click-to-inspect drawers.
   * Full SOC 2 / HIPAA compliance linkage: Cryptographic SHA-256 hash chaining (`audit.jsonl`), immutable tamper-evident logs, and data minimization.

---

## 2. Telemetry Ingestion Architecture (How to Find the Data)

The backend engine (`core/governance/agent_tracer.py` and `core/cost/agent_cost_calculator.py`) parses telemetry from three authoritative on-disk data sources:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   TELEMETRY INGESTION PIPELINE                                   │
├──────────────────────────┬──────────────────────────┬────────────────────────────────────────────┤
│ 1. Agent Execution Logs  │ 2. Audit Ledger          │ 3. Workspace State Records                 │
│    `transcript.jsonl`    │    `audit.jsonl`         │    `workflow.json` & `decision.json`       │
│    • Token usage metrics │    • Cryptographic hashes│    • Lifecycle stage transitions           │
│    • Latency per step    │    • Tamper-evident chain│    • Decision branches & tradeoffs         │
│    • Tool calls & inputs │    • Operator identity   │    • Rejected alternatives                 │
│    • Model reasoning     │    • Non-repudiation     │    • Plan-hash bindings                    │
└──────────────────────────┴──────────────────────────┴────────────────────────────────────────────┘
```

### 2.1 File Locations & Schemas

#### A. Agent Conversation Transcript (`transcript.jsonl`)
* **Location:** `<appDataDir>/brain/<conversation-id>/.system_generated/logs/transcript.jsonl`
* **Parsed Attributes:**
  * `step_index`: Sequential execution step counter.
  * `source`: `MODEL` | `USER_EXPLICIT` | `SYSTEM`.
  * `type`: `PLANNER_RESPONSE` | `USER_INPUT`.
  * `created_at`: ISO 8601 timestamp (used to compute step latency $\Delta t = t_{n} - t_{n-1}$).
  * `thinking`: Model internal chain-of-thought and architectural reasoning.
  * `tool_calls`: Array of `{ name, arguments, toolAction, toolSummary }`.
  * `token_usage`: `{ prompt_tokens: int, completion_tokens: int, cached_tokens: int }`.

#### B. Cryptographic Audit Ledger (`audit.jsonl`)
* **Location:** `.agents/logs/audit.jsonl`
* **Parsed Attributes:**
  * `timestamp`: Precise UTC execution time.
  * `operator`: Subagent role or human operator ID.
  * `action`: Governed action tag (e.g. `GATE_PLAN_LOCKED`, `REQUIREMENTS_CAPTURED`).
  * `details`: Execution metadata (plan hash, BCM cost, changes count).
  * `prev_hash` & `entry_hash`: SHA-256 cryptographic chaining verifying tamper-resistance.

#### C. Run Lifecycle & Decision State (`workflow.json` + `architecture_decision.json`)
* **Location:** `runs/<run-id>/workflow.json` and `runs/<run-id>/architecture_decision.json`
* **Parsed Attributes:**
  * Stage statuses and durations (`requirements`, `decision`, `synthesis`, `reflector`, `plan_gate`, `proving`).
  * `chosen_modules`, `justifications`, `rejected_alternatives`, `failure_modes_addressed`.

---

## 3. Detailed UI Specifications

### 3.1 Section: `COST` -> Sub-Section: `AGENTS COST`

The `AGENTS COST` view provides full transparency into AI inference spend, token efficiency, and context health.

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│  COST / AGENTS COST                                                                              │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│  RUN SUMMARY METRICS CARD:                                                                       │
│  • Total Agent Cost:    $0.0842 USD             • Total Latency:          42.6s                  │
│  • Total Input Tokens:  48,250 tokens           • Total Output Tokens:    3,840 tokens           │
│  • Cached Context:      182,400 tokens          • Peak Context Pressure:  14.2% (142k / 1M)      │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│  AGENT EXECUTION COST LEDGER TABLE:                                                              │
│                                                                                                  │
│  [>] Step 01 | grill-me-agent        | pro     | 8,400 in / 1,200 out | 4.2s  | $0.0210 | PASS   │
│  [v] Step 02 | architect-agent       | pro     | 14,200 in / 1,450 out| 6.8s  | $0.0380 | PASS   │
│      ├─ Sub-Task: Service Research & Module Matching                                             │
│      ├─ Input Tokens: 14,200 ($0.0177) | Output Tokens: 1,450 ($0.0145) | Cached: 84k ($0.0058)   │
│      ├─ Context Window Utilization: 98,450 / 1,000,000 tokens (9.8%)                            │
│      └─ [ View Step Raw JSON Telemetry ] ────────────────────────────────────────┐               │
│                                                                                  ▼               │
│  [>] Step 03 | synthesizer-engine    | stdlib  | 0 in / 0 out         | 0.4s  | $0.0000 | PASS   │
│  [>] Step 04 | diagrammer-agent      | stdlib  | 0 in / 0 out         | 0.2s  | $0.0000 | PASS   │
│  [>] Step 05 | reflector-agent       | stdlib  | 0 in / 0 out         | 0.5s  | $0.0000 | PASS   │
│  [>] Step 06 | orchestrator-gate     | stdlib  | 0 in / 0 out         | 4.1s  | $0.0000 | LOCKED │
│  [>] Step 07 | proving-agent         | stdlib  | 0 in / 0 out         | 18.2s | $0.0000 | VERIF  │
│  [>] Step 08 | slack-agent           | haiku   | 1,200 in / 180 out   | 0.3s  | $0.0008 | SENT   │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

#### FR-01: Summary Metrics Card
* **Total Token Economics:** Aggregates input tokens, output completion tokens, cached context tokens, and computes dollar spend using the model pricing matrix:
  * Pro Tier: `$1.25 / 1M input`, `$10.00 / 1M output`, `$0.30 / 1M cached`.
  * Flash / Haiku Tier: `$0.10 / 1M input`, `$0.40 / 1M output`, `$0.025 / 1M cached`.
  * Standard Library / Local Tools: `$0.0000` (highlighting zero AI inference cost for deterministic governance gates).
* **Context Window Capacity Gauge:** Visual progress bar tracking peak token load against model context ceiling (e.g. `142k / 1M tokens` $\rightarrow$ `14.2% capacity`).

#### FR-02: Expandable Step Cost Ledger (Accordion Drill-Down)
* Clicking any step row expands the detailed task breakdown:
  * Specific tool calls executed during that step.
  * Exact token breakdown (Input vs. Output vs. Cache Hits).
  * Latency duration for that specific hop.
  * Model tier and persona assigned.

#### FR-03: Governed Raw JSON Telemetry Inspector
* **Permission Policy:** Users are granted full permission to view the step's raw telemetry JSON for auditability and debugging.
* **Security & Credential Redaction Invariant:**
  * Before rendering in the UI modal, the backend scrubs any sensitive strings matching bearer tokens (`ghp_*`, `xoxb-*`, `AKIA...`, private keys, or passwords), replacing them with `[REDACTED_SECRET]`.
  * Renders formatted, syntax-highlighted JSON with instant `[Copy JSON]` action.

---

### 3.2 Section: `FLOW` -> Sub-Section: `AGENT FLOW`

The `AGENT FLOW` view visualizes the end-to-end multi-agent execution pipeline as an interactive Directed Acyclic Graph (DAG) and timeline.

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│  FLOW / AGENT FLOW                                                                               │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│  INTERACTIVE AGENT EXECUTION DAG (Zoom / Pan / Inspect):                                         │
│                                                                                                  │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                 │
│  │ 1. User Prompt│────►│ 2. grill-me  │────►│ 3. architect │────►│4. synthesizer│                 │
│  │ (Clickstream)│     │ (7 NFRs Q&A) │     │ (Module Match│     │(Generate HCL)│                 │
│  └──────────────┘     └──────────────┘     └──────┬───────┘     └──────┬───────┘                 │
│                                                   │                    │                         │
│                                                   ▼                    ▼                         │
│                                            ┌──────────────┐     ┌──────────────┐                 │
│                                            │ 5. diagrammer│     │ 6. reflector │                 │
│                                            │ (Draw.io XML)│     │ (5 Gates)    │                 │
│                                            └──────────────┘     └──────┬───────┘                 │
│                                                                        │                         │
│                                                                        ▼                         │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                 │
│  │ 9. slack-agent│◄────│ 8. proving   │◄────│7. plan_gate  │◄────│ (Gate Lock)  │                 │
│  │ (Block Kit)  │     │ (5-Hop Test) │     │(Plan Hash)   │     └──────────────┘                 │
│  └──────────────┘     └──────────────┘     └──────────────┘                                      │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│  STEP DETAILS DRAWER (Selected Node: 3. architect-agent):                                        │
│  • Agent Persona:        Principal Cloud Architect (AI Pro Model)                                │
│  • Execution Duration:   2.4 seconds                                                             │
│  • Decision Branch:      Selected Glue 4.0 PySpark; Rejected EMR Serverless (Cost optimization) │
│  • Inputs Received:      requirements.json (50GB/day, 15-min batch, S3 Medallion)                │
│  • Output Produced:      architecture_decision.json + Module Manifest                            │
│  • Audit Hash Seal:      sha256:4f8e... (audit.jsonl Line #42)                                   │
│  • [ View Step Raw JSON ]   [ Open Produced Artifact ]                                            │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

#### FR-04: Interactive Agent Execution Lineage Graph (DAG)
* Visual node-link diagram tracing the sequence of agent handoffs.
* Interactive zoom, pan, and minimap navigation for complex multi-agent workflows.
* Color-coded status per node:
  * Green: Completed successfully.
  * Amber: Waiting on Human-in-the-Loop review / Gate Approval.
  * Red: Gate Blocked / Circuit breaker tripped.
  * Blue: Actively running step.

#### FR-05: Step Details & Decision Branch Inspector
* Clicking any node in the graph opens a comprehensive inspection drawer:
  * **Agent Persona & Model Tier:** Details which agent executed the step.
  * **Decision Branch Justification:** Displays the plain-English reasoning and tradeoffs evaluated by the model.
  * **Input & Output Handoffs:** Shows what files and variables were passed into the agent and what artifacts it emitted.
  * **Cryptographic Audit Seal:** Direct reference to the hash-chained block in `.agents/logs/audit.jsonl`.
  * **Raw JSON Modal:** Direct inspection of the step's raw execution data.

---

## 4. Enterprise Compliance Engine (SOC 2 Type II & HIPAA)

To satisfy enterprise regulatory requirements, the telemetry engine enforces four foundational compliance controls:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                             SOC 2 & HIPAA COMPLIANCE INVARIANTS                                  │
├──────────────────────────┬──────────────────────────┬────────────────────────────────────────────┤
│ 1. Non-Repudiation       │ 2. Data Minimization     │ 3. RBAC & Access Control                   │
│    • SHA-256 hash chains │    • Zero PII in logs    │    • Read-only telemetry views             │
│    • Immutable audit.json│    • Redacted bearer auth│    • Authenticated operator binding        │
│    • Tamper verification │    • Ephemeral transcripts│   • Audit log export (.zip)                │
└──────────────────────────┴──────────────────────────┴────────────────────────────────────────────┘
```

### FR-06: Non-Repudiation & Cryptographic Hash Chain Verification
* Every agent handoff, plan generation, and gate decision writes an entry to `.agents/logs/audit.jsonl`.
* Each entry contains `entry_hash = SHA256(timestamp + operator + action + details + prev_hash)`.
* The UI exposes a **"Verify Audit Trail Integrity"** indicator that confirms the hash chain is unbroken and free of tampering.

### FR-07: Data Minimization & Secret Redaction
* Transcripts and audit records never persist raw secret tokens, passwords, AWS access keys (`AKIA...`), or customer PII.
* Automated sanitizers scrub all tool call arguments before writing to disk or rendering in UI modals.

---

## 5. Technical Implementation Modules

```
core/
├── governance/
│   └── agent_tracer.py           <── Reads transcript.jsonl, audit.jsonl, workflow.json; builds DAG & relay
├── cost/
│   └── agent_cost_calculator.py  <── Parses token usage, applies pricing matrix, tracks context pressure
└── reporting/
    └── agent_flow_graph.py       <── Compiles interactive Cytoscape / VisJS / SVG graph structure for UI

app/
└── console_app.py                <── Renders COST -> AGENTS COST and FLOW -> AGENT FLOW sections
```

---

## 6. Work Packages & Delivery Plan

| Work Package | Target Files | Delivered Scope |
| :--- | :--- | :--- |
| **WP-01** | `core/cost/agent_cost_calculator.py` | Complete token extraction engine, pricing matrix calculation, context window utilization metrics, and summary aggregator. |
| **WP-02** | `core/governance/agent_tracer.py` | Multi-agent execution telemetry parser, step latency calculator, decision branch extractor, and audit.jsonl hash verifier. |
| **WP-03** | `core/reporting/agent_flow_graph.py` | Interactive DAG compiler generating nodes, edges, styling, and step metadata payloads. |
| **WP-04** | `app/console_app.py` | Full implementation of `COST / AGENTS COST` (metrics card, accordion table, raw JSON modal) and `FLOW / AGENT FLOW` (interactive DAG, node drawer, raw JSON modal). |
| **WP-05** | `tests/test_agent_cost.py`, `tests/test_agent_flow.py` | Unit and integration tests verifying token math, pricing accuracy, DAG integrity, secret redaction, and zero-emoji compliance. |

---

## 7. Acceptance Criteria (Sign-Off Invariants)

1. **Exact Token & Cost Calculation:** Step-by-step token totals must accurately match `transcript.jsonl` data and compute dollar amounts accurately using the pricing matrix.
2. **Context Window Monitoring:** Accurately displays peak context capacity and alerts if context exceeds 80% of model limits.
3. **Safe Raw JSON Inspection:** Clicking "View Raw JSON" renders formatted step data with 100% of sensitive bearer credentials redacted.
4. **Interactive Lineage DAG:** Agent flow graph must support pan, zoom, and node click to inspect decision branches and input/output artifacts.
5. **Zero External Binary Dependencies:** Backend modules must use only Python standard library (`json`, `re`, `os`, `hashlib`, `datetime`, `urllib`).
6. **Zero-Emoji Compliance:** All terminal outputs, code comments, markdown files, and UI text must strictly contain zero emojis.
7. **100% Test Pass Rate:** `pytest tests/test_agent_cost.py tests/test_agent_flow.py` and the full repo test suite must pass with exit code 0.
