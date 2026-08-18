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
   *Exhaustive breakdown of all 16 files: plan gates, approval workflows, tamper-evident audit chains, RBAC authorization, cloud drift, destructive change classification, ephemeral applies, and source guards.*

2. 🏗️ **Generation & Synthesis**: [`core/generation/CONTEXT-generation.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/generation/CONTEXT-generation.md)  
   *Exhaustive breakdown of all 17 files: requirements-driven HCL synthesis, module registry, provenance pinning, pattern caching, blueprint resolvers, schema linting, and knowledge graph storage.*

3. 🏛️ **Architecture & Decisions**: [`core/architecture/CONTEXT-architecture.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/architecture/CONTEXT-architecture.md)  
   *Exhaustive breakdown of all 6 files: requirements validation contracts, architecture decision records, 6-layer reference architecture classifier, and documentation discovery tools.*

4. 💰 **Cost & FinOps Evidence**: [`core/cost/CONTEXT-cost.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/cost/CONTEXT-cost.md)  
   *Exhaustive breakdown of all files in `core/cost/`: AWS BCM Pricing Calculator integration, pricing catalog resolution, cost budget estimators, and pricing coverage audits.*

5. 📊 **Reporting & CLI**: [`core/reporting/CONTEXT-reporting.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/reporting/CONTEXT-reporting.md)  
   *Exhaustive breakdown of all 9 files in `core/reporting/`: `minusctl` CLI, report builder, plan inspector, static HCL scanner (`optimize_analyzer`), FinOps agent, health probes, and run manager.*

6. ☁️ **Provider Abstraction**: [`core/providers/CONTEXT-providers.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/providers/CONTEXT-providers.md)  
   *Exhaustive breakdown of all provider files: `CloudProvider` base interface, AWS provider implementation, and Azure/GCP scaffolds.*
