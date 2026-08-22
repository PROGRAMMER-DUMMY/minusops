---
name: context-graph
description: Maintain, audit, and synchronize file-by-file context documentation (CONTEXT-MAP.md and CONTEXT-[folder].md) across the entire MinusOps repository. Use whenever code is added, refactored, or moved, or when checking for documentation drift.
---

# Context Graph Maintenance Skill

This skill equips agents and engineers to maintain the exhaustive, file-by-file context documentation tree across **MinusOps**.

---

## 1. The Context Architecture

The context graph consists of two layers:
1. **[`CONTEXT-MAP.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/CONTEXT-MAP.md):** The master navigation tree mapping every directory in the repository to its local context file.
2. **`CONTEXT-[folder].md`:** Dedicated file-by-file indexes living inside each directory (e.g. `core/cli/CONTEXT-cli.md`, `modules/CONTEXT-modules.md`, `core/reporting/CONTEXT-reporting.md`).

---

## 2. When to Activate This Skill (Triggers)

Activate this skill whenever:
* A new Python file, Terraform file, or script is created.
* An existing function signature, class, parameter, or failure mode is modified.
* A file is renamed, moved, or deleted.
* A new directory is introduced.
* Performing a routine context drift audit before merging a pull request.

---

## 3. Operational Procedures (Step-by-Step)

### Step 1: Identify Modified Files
Run `git status` or inspect recent commits to identify all changed, added, or deleted files.

### Step 2: Open Directory Context Document
Open the `CONTEXT-[folder].md` corresponding to the modified directory (e.g. if editing `core/cli/commands/gate.py`, open [`core/cli/CONTEXT-cli.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/core/cli/CONTEXT-cli.md)).

### Step 3: Update File Specifications
For each modified file, verify and update:
1. **File Link:** GitHub-style markdown link using the `file://` scheme.
2. **Exact Purpose:** One or two sentences describing what the file accomplishes.
3. **Key Functions & Classes:** Exact function names, arguments, and return types.
4. **Inputs & Outputs:** CLI flags, environment variables, files read/written.
5. **Failure Modes:** How errors, exceptions, and non-zero exit codes are handled.
6. **Architectural Role & Dependencies:** Upstream callers and downstream consumers.

### Step 4: Validate Links & Format Invariants
* Ensure all links resolve to valid local paths on disk.
* Confirm strictly **zero emojis** are present.
* Use clean ASCII tables and GitHub-style code fences.

### Step 5: Update Master `CONTEXT-MAP.md` (If New Directory)
If a new folder was introduced, register it in [`CONTEXT-MAP.md`](file:///C:/Users/shubh/PycharmProjects/MinusTeraformCli/CONTEXT-MAP.md) under the Master Context Tree.
