---
name: architecture-diagrammer
description: Generates dynamic Draw.io architecture diagrams from Terraform plans.
---

# Architecture Diagrammer Skill

## Overview

This skill allows the agent to generate and manage dynamic architecture diagrams in Draw.io format, without relying on any external binaries or third-party PyPI dependencies. It exclusively uses Python's standard library to construct `.drawio` XML.

## Triggers

Activate this skill when:
- The user requests an architecture diagram for a Terraform plan.
- The user wants a 1-click URL to open an architecture in Draw.io.
- A deployment report needs to be supplemented with visual architecture representations.

## Capabilities

1. **Universal Provider Stencil Mapping:** Maps Terraform resource types (AWS, Azure, GCP, Databricks, Snowflake) to Draw.io stencils.
2. **Dynamic Zone & Container Clustering:** Groups resources by VPCs, Subnets, Storage Tiers, etc.
3. **Node Sizing & Metadata Extraction:** Pulls worker types, encryption, and other metadata from the plan.
4. **Topological Flow Discovery:** Connects nodes with numbered sequence hops.
5. **1-Click Browser URL:** Encodes the diagram into a self-contained `https://app.diagrams.net/#R...` link.
6. **Execution Ledger:** Embeds an execution ledger table in the diagram and markdown.

## Usage

Use the `minusctl diagram` command:

```bash
# Generate diagram from a Terraform plan directory
minusctl diagram --dir path/to/terraform

# Generate diagram from an active run context
minusctl diagram --run <run-id>

# Generate only specific formats (e.g., URL or XML)
minusctl diagram --run <run-id> --format url
```

## Constraints

- Strictly follow the **Zero-Emoji Doctrine**.
- Always ensure all node representations are grounded in the actual `plan.json`. No hallucinated resources.
