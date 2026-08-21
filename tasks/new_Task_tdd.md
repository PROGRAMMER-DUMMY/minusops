# TDD Specification: Enterprise Logging, Secrets Management, Subagent Fabric & Grilling Expansion

| Attribute | Details |
| :--- | :--- |
| **Document ID** | TASK-TDD-2026-002 |
| **Status** | DRAFT / TDD SPECIFICATION |
| **Target Components** | `.agents/skills/grill-me/SKILL.md`, `.agents/subagents/`, `core/integrations/`, `core/architecture/` |
| **Reviewers** | Matt (Principal Cloud Architect), Ponytail (Anti-Overengineering Reviewer) |
| **Date** | 2026-08-21 |

---

## 1. Executive Summary & Problem Statement

This TDD specification addresses the missing enterprise dimensions identified in the architecture review:
1. **Grilling Session Expansion (`grill-me`):** Deep architectural interrogation for **Logging & Observability**, **Secrets Management & Key Hierarchy**, and **Private VPC Networking / VPC Endpoints**.
2. **Missing Subagent Manifest:** Author `.agents/subagents/jira-agent.md` to expose the already implemented `core/integrations/jira_hook.py` (Atlassian Document Format).
3. **Alert Burst & Deduplication Guard:** Prevent notification storms (e.g., 50 failures in 10s) using a 5-minute sliding window memory-efficient deduplication cache.
4. **Config-Driven Routing Decoupling:** Decouple hardcoded channel assumptions, routing all notifications dynamically through `configs/teams.yaml` and `team_resolver.py`.

---

## 2. Grilling Session Expansion (`.agents/skills/grill-me/SKILL.md`)

We add **Three Dedicated Architectural Pillars** to the `grill-me` interview:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3 NEW ARCHITECTURAL GRILLING PILLARS (DATA PIPELINES & CLOUD INFRASTRUCTURE)│
├──────────────────────────┬──────────────────────────────────────────────────┤
│ Pillar                   │ Key Architectural Questions & Default Standards  │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ **Pillar 8: Logging &**  │ • **Retention Lifecycle:** Never Expire is a     │
│ **Observability**        │   FinOps leak! Default: 30d (Dev), 90d (Prod).   │
│                          │ • **PII/PHI Masking:** Automated CloudWatch data │
│                          │   protection policies for SSNs/NPIs/Cards.       │
│                          │ • **SIEM Export:** Stream to Splunk/Datadog/S3.  │
│                          │ • **Access Logs:** S3 Server Access + VPC Flow.  │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ **Pillar 9: Secrets &**  │ • **Store Choice:** AWS Secrets Manager (dynamic)│
│ **Key Hierarchy**        │   vs SSM Parameter Store (static config).        │
│                          │ • **KMS Key Scope:** Dedicated Customer Managed  │
│                          │   Keys (CMK) with automated 365-day rotation.    │
│                          │ • **Credential Posture:** STS AssumeRole only;   │
│                          │   strictly zero static AKIA/secret access keys.  │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ **Pillar 10: Network &** │ • **Connectivity:** Private Subnets + S3 Gateway │
│ **VPC Endpoints**        │   Endpoint + Interface Endpoints (Privatelink).  │
│                          │ • **NAT Egress Elimination:** Route S3/KMS traffic│
│                          │   internally to eliminate $0.045/GB NAT costs.   │
└──────────────────────────┴──────────────────────────────────────────────────┘
```

---

## 3. Test-Driven Development (TDD) Work Packages

### Work Package 1: `jira-agent.md` Manifest (`.agents/subagents/jira-agent.md`)
* **Goal:** Create the missing subagent manifest connecting to `core/integrations/jira_hook.py`.
* **TDD Invariant:**
  * Must never accept `JIRA_API_TOKEN` as a parameter.
  * Must invoke `jira_hook.create_change_ticket(...)` with Atlassian ADF format.
  * Must route through `approval.request_approval(mode="gatekeeper")`.

### Work Package 2: Alert Burst & Deduplication Shield (`core/integrations/base_hook.py`)
* **Goal:** Suppress duplicate alerts within a configurable cooldown window (default: 300 seconds).
* **Test Plan (`tests/test_alert_dedup.py`):**
  * `test_first_alert_dispatches_successfully()` -> returns `sent: True`.
  * `test_identical_alert_within_5_minutes_is_suppressed()` -> returns `sent: False, reason: "deduplicated"`.
  * `test_different_alert_payload_dispatches_immediately()` -> returns `sent: True`.
  * `test_alert_after_window_expires_dispatches_again()` -> returns `sent: True`.

### Work Package 3: Logging & KMS Encryption in Modules
* **Goal:** Assert all generated Glue, EMR, and CloudWatch log groups specify explicit retention and KMS encryption.
* **Test Plan (`tests/test_logging_governance.py`):**
  * `test_cloudwatch_log_groups_have_explicit_retention()`: Fails if `retention_in_days` is null/missing.
  * `test_s3_buckets_have_server_access_logging_configured()`: Fails if target logging bucket is missing on Gold/Audit zones.
  * `test_secrets_manager_and_logs_use_customer_managed_kms_cmk()`: Asserts CMK policy.

---

## 4. Anti-Overengineering Invariants (Ponytail Review)

1. **Stdlib Only:** All deduplication and rate-limiting must use standard library data structures (`collections.deque`, `time.time`). No Redis, Memcached, or external daemon.
2. **Fail-Soft Invariant:** A failed or suppressed notification must never abort a Terraform deploy or pipeline run.
3. **No Credential Echoing:** Bearer tokens and webhooks must never appear in logs, traces, or test outputs.
