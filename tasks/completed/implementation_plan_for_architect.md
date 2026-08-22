# Technical Implementation Plan: Enterprise Subagent Fabric & Integration Hooks

| Attribute | Details |
| :--- | :--- |
| **Document ID** | IMPL-ARCH-2026-001 (Revision 2 - Architect-Harmonized) |
| **Companion PRD** | [`tasks/new_prd_for_architect.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/tasks/new_prd_for_architect.md) |
| **Lead Architect** | Matt (Architecture & Platform Engineering Lead) |
| **Review Framework** | Ponytail Anti-Overengineering (YAGNI / Zero-Bloat Standard) |
| **Handshake Target** | `coding_agent` (for execution on dedicated feature branch) |
| **Status** | READY FOR ARCHITECT & CODING AGENT HANDSHAKE |
| **Target Branch** | `feature/enterprise-subagent-fabric` (MUST be executed on this new branch) |
| **Date** | 2026-08-21 |

---

## 1. Architectural Philosophy & Ponytail Alignment

### 1.1 The Context Bloat Problem
Packing heavy REST API clients, markdown-to-storage converters, and authentication SDKs for **Slack, MS Teams, Outlook, Confluence, and Jira** directly into the core governance engine bloats the main agent's LLM context window. This degrades reasoning velocity, increases token costs, and creates cognitive clutter.

### 1.2 The Modular Subagent & Tool Hook Solution
MinusOps establishes a **Decoupled Specialized Subagent Architecture**:
* The **Main Governance Loop** remains ultra-lean, focusing strictly on Requirements, Terraform Synthesis, Plan-Gate Invariants, and FinOps Variance calculation.
* Communication, documentation, and external tooling integrations are delegated on-demand to **Isolated, Single-Purpose Subagents / Tool Hooks**.
* **Ponytail Anti-Overengineering Rule (YAGNI):** Every integration hook uses standard library `urllib.request` / pure Python with zero heavy third-party SDK bloat.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ MINUSOPS MAIN GOVERNANCE CONTROL PLANE (Lean Core Context)                  │
│ • Requirements Grilling (7 Pillars) · HCL Synthesizer · Plan-Gate Deploy   │
└──────────────┬──────────────────┬──────────────────┬─────────────────┬──────┘
               │                  │                  │                 │
       (invoke on-demand) (invoke on-demand) (invoke on-demand) (invoke on-demand)
               ▼                  ▼                  ▼                 ▼
      ┌─────────────────┐┌─────────────────┐┌─────────────────┐┌────────────────┐
      │  slack-agent    ││  teams-agent    ││  outlook-agent  ││confluence-agent│
      │  (Webhook Hook) ││ (Workflows Hook)││ (SES/SMTP Hook) ││  (REST Hook)   │
      └─────────────────┘└─────────────────┘└─────────────────┘└────────────────┘
```

---

## 2. Subagent & Hook Specifications

### 2.1 `slack-agent` & Tool Hook (`core/integrations/slack_hook.py`)
* **Role:** Formats interactive plan approval cards and posts P1 pipeline incident alerts.
* **Interface:** `send_slack_notification(webhook_url, payload_dict, interactive=False)`
* **Context Payload:** Markdown alert block + Plan Hash + `[Approve]` / `[Reject]` action elements.
* **Implementation:** Pure Python `urllib.request.urlopen` with standard JSON payload.

### 2.2 `teams-agent` & Tool Hook (`core/integrations/teams_hook.py`)
* **Role:** Formats Adaptive Cards for Data Quality failures and quarantine alerts for Domain Analytics channels.
* **Interface:** `send_teams_card(webhook_url, title, facts_list, action_url=None)`
* **Context Payload:** Microsoft Teams Adaptive Card JSON format.

### 2.3 `outlook-agent` & Tool Hook (`core/integrations/outlook_hook.py`)
* **Role:** Sends monthly executive leadership emails with attached `executive_project_summary.xlsx` and `pipeline_detailed_ledger.xlsx`.
* **Interface:** `send_executive_email(to_addresses, subject, body_html, attachments=[])`
* **Implementation:** AWS SES `SendRawEmail` API or standard library `smtplib` + `email.mime`.

### 2.4 `confluence-agent` & Tool Hook (`core/integrations/confluence_hook.py`)
* **Role:** Renders living architecture documentation and publishes/updates Atlassian Confluence Space pages.
* **Interface:** `publish_confluence_page(space_key, page_title, markdown_content, parent_page_id=None)`
* **Implementation:** Converts Markdown to Atlassian Storage XHTML format and calls Confluence Cloud REST API `PUT /wiki/rest/api/content/{id}`.

### 2.5 `jira-agent` & Tool Hook (`core/integrations/jira_hook.py`)
* **Role:** Automatically opens and updates change management tickets during Staging and Production Plan-Gate apply stages.
* **Interface:** `create_change_ticket(project_key, summary, description, plan_hash)`

---

## 3. Phased Implementation Roadmap for `coding_agent`

The `coding_agent` must execute these phases sequentially on the new feature branch:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ PHASE 1: INTEGRATION TOOL HOOKS (core/integrations/)                        │
│ • Implement zero-dependency HTTP/REST dispatchers for Slack, Teams,         │
│   Outlook, Confluence, and Jira.                                            │
├─────────────────────────────────────────────────────────────────────────────┤
│ PHASE 2: SUBAGENT MANIFEST DEFINITIONS (.agents/subagents/)                 │
│ • Create lightweight system prompts and tool bindings for each subagent.   │
├─────────────────────────────────────────────────────────────────────────────┤
│ PHASE 3: 2-TIER IAM PERMISSIONS BOUNDARY & ADOPTION GUARD                   │
│ • Agent Runner Boundary (DenyDelete) vs Workload Execution Boundary.       │
│ • Implement --policy-mode brownfield in adopt.py for legacy workloads.      │
├─────────────────────────────────────────────────────────────────────────────┤
│ PHASE 4: CI/CD WORKFLOW GENERATOR (GitHub Actions & Jenkinsfile)            │
│ • Synthesize the 4-lane parallel PR check workflows and OIDC role policies. │
├─────────────────────────────────────────────────────────────────────────────┤
│ PHASE 5: REGRESSION TESTING & AUDIT LOG VERIFICATION                        │
│ • Unit tests for all subagent hooks and end-to-end plan-gate integration.   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Standard Library Base Hook Pattern (`core/integrations/base_hook.py`)

```python
import json
import urllib.request
import urllib.error

class BaseIntegrationHook:
    """Zero-dependency HTTP dispatcher utilizing Python standard library."""
    
    @staticmethod
    def post_json(url: str, payload: dict, headers: dict = None, timeout: int = 10) -> dict:
        headers = headers or {"Content-Type": "application/json"}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return {"ok": True, "status": resp.status, "body": resp.read().decode("utf-8")}
        except urllib.error.HTTPError as e:
            return {"ok": False, "status": e.code, "error": e.read().decode("utf-8")}
        except Exception as e:
            return {"ok": False, "status": 500, "error": str(e)}
```

---

## 5. Handshake Contract with `coding_agent`

When the `coding_agent` is initialized to start implementation:

1. **Branch Prerequisite:** Must create and checkout a new branch:
   ```bash
   git checkout -b feature/enterprise-subagent-fabric
   ```
2. **Required Reading:** The coding agent must read both:
   * [`tasks/new_prd_for_architect.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/tasks/new_prd_for_architect.md) (The Harmonized Architectural Specification)
   * [`tasks/implementation_plan_for_architect.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/tasks/implementation_plan_for_architect.md) (This Technical Implementation Plan)
3. **Invariants to Preserve:**
   * Do not introduce heavy third-party dependencies (`requests`, `pandas`, `openpyxl`). Use standard library.
   * Preserve all 44 passing unit and governance regression tests.
   * All mutations must route through `plan_gate.py` and log to `.agents/logs/audit.jsonl`.
   * Enforce Brownfield grandfathering (`--policy-mode brownfield`) during adoption.

---

## 6. Verification & Test Strategy for the Feature Branch

1. **Unit Tests:** `tests/test_integrations.py` mocking webhook responses and testing MIME attachment creation.
2. **Confluence Markdown Converter Tests:** Verify Markdown tables and Mermaid code blocks convert to valid XHTML without syntax errors.
3. **Full Governance Run:** Execute `pytest` ensuring 0 regressions across existing gates.
