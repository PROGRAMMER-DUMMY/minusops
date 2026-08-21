# core Context Index

This document provides context for the top-level files in [`core`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core) and serves as an index linking to the detailed, file-by-file context indexes for every subpackage within `core/`.

---

## 1. Top-Level Core Files

- [`core/MAP.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/MAP.md): High-level architectural package map documenting the 6 subpackages of `core/`, cross-package dependencies, and stability expectations.
- [`core/__init__.py`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/__init__.py): Package initializer for the core governance and IaC synthesis engine.

---

## 2. Granular Subpackage Context Indexes

Each subpackage in `core/` maintains its own dedicated, exhaustive context documentation file detailing every python file, class, function, and edge case:

1. 🛡️ **Governance**: [`core/governance/CONTEXT-governance.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/governance/CONTEXT-governance.md)  
   *Exhaustive breakdown of: plan gates, approval workflows, tamper-evident audit chains, RBAC authorization, cloud drift, destructive change classification, ephemeral applies, and source guards.*

2. 🏗️ **Generation & Synthesis**: [`core/generation/CONTEXT-generation.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/generation/CONTEXT-generation.md)  
   *Exhaustive breakdown of: requirements-driven HCL synthesis, module registry, provenance pinning, pattern caching, blueprint resolvers, schema linting, and knowledge graph storage.*

3. 🏛️ **Architecture & Decisions**: [`core/architecture/CONTEXT-architecture.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/CONTEXT-architecture.md)  
   *Exhaustive breakdown of: requirements validation contracts, team/state-key resolution, architecture decision records, 6-layer reference architecture classifier, and documentation discovery tools.*

4. 💰 **Cost & FinOps Evidence**: [`core/cost/CONTEXT-cost.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/cost/CONTEXT-cost.md)  
   *Exhaustive breakdown of: AWS BCM Pricing Calculator integration, pricing catalog resolution, cost budget estimators, and pricing coverage audits.*

5. 📊 **Reporting & CLI**: [`core/reporting/CONTEXT-reporting.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/reporting/CONTEXT-reporting.md)  
   *Exhaustive breakdown of: `minusctl` CLI, report builder, plan inspector, static HCL scanner (`optimize_analyzer`), FinOps agent, health probes, run manager, environment doctor, brownfield adopt, pipeline seed, agent-facing CLI diagnostics, and the FinOps Excel generator.*

6. 🔌 **Outbound Integrations**: [`core/integrations/CONTEXT-integrations.md`](./integrations/CONTEXT-integrations.md)  
   *Exhaustive breakdown of: the stdlib-only, approval-gated hooks for Slack, Microsoft Teams, executive email over SMTP, Confluence page publishing, and Jira change tickets, plus the shared `base_hook` transport, secret resolution, and result-dict contract.*

7. ☁️ **Provider Abstraction**: [`core/providers/CONTEXT-providers.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/CONTEXT-providers.md)  
   *Exhaustive breakdown of: `get_provider()` factory and the `AWSProvider` implementation (STS identity, credential posture, Cost Explorer, pricing catalog). **AWS-only** -- the `azure.py`/`gcp.py` scaffolds and the one-implementation `CloudProvider` ABC were deleted when multi-cloud left scope; `get_provider("azure")` raises `ValueError`.*
