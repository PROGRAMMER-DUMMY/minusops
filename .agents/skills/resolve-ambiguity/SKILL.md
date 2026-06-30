---
name: resolve-ambiguity
description: Resolve unclear, underspecified, too-simple, or overly broad user requests before acting. Use when Codex is unsure what outcome the user wants, when multiple implementation/product paths are plausible, when a short request hides important enterprise tradeoffs, or when a wrong assumption would waste work or create risk. Inspect the codebase first for facts that can be discovered locally, then ask one targeted question with a recommended answer.
---

# Resolve Ambiguity

## Overview

Use this skill to turn vague intent into an actionable decision without slowing down clear work. Prefer progress by making safe assumptions, but pause for one targeted question when the assumption changes architecture, product behavior, security posture, cost, or user experience.

## Decision Rule

Before asking, inspect local context that can answer the question:

- Read relevant repo docs, existing code, tests, configs, and prior patterns.
- If the repo answers it, state the discovered fact briefly and continue.
- If the decision depends on user intent, priorities, risk tolerance, or business context, ask.

Ask only when at least one condition is true:

- A wrong assumption would materially change the implementation.
- The request can mean two or three incompatible outcomes.
- The work could affect infrastructure, credentials, data, spend, security, or production users.
- The user asks for strategy, product design, enterprise workflow, or architecture.
- The request is too broad to execute safely in one pass.

Do not ask when the task is clear, low risk, and reversible. In those cases, state the assumption and proceed.

## Question Format

Ask exactly one question at a time:

```markdown
Question: ...

Options:
- Option A: ...
- Option B: ...
- Option C: ...

Recommended answer: ...

Compatibility: ...

Feedback note: ...
```

Use two options for simple ambiguity and three for meaningful strategy or architecture choices. Always include a concise recommended answer and the reasoning needed to accept, reject, or modify it.

## Workflow

1. Identify the highest-leverage unresolved decision.
2. Inspect the codebase for facts before asking.
3. Ask one decision-oriented question with a recommendation.
4. After the user answers, silently update the inferred plan.
5. Continue to the next unresolved branch only if it still matters.

## Enterprise Product Guidance

For this repo, a build/create request ("create a data pipeline", "set up a backend", etc.) is
**not** a low-risk, just-proceed task — it provisions cloud infrastructure. **Do not short-circuit
it to the hardcoded blueprint and a couple of inputs.** Instead, hand it to the
[`grill-me`](../grill-me/SKILL.md) skill to gather full functional + non-functional requirements,
which then routes to the [`architect`](../architect/SKILL.md) skill (research → choose → compose →
govern). The single `aws-data-pipeline-standard` blueprint is a demo/cached fixture, not the
production path — every real company differs on orchestrator, architecture pattern, data-quality,
and schema enforcement, so one fixed recipe can't serve them.

Use `resolve-ambiguity` for *genuinely ambiguous* points within that flow (which cloud, which
region, an incompatible-outcomes fork) — one targeted question with a recommendation — but the
requirements interrogation itself belongs to `grill-me`, and it is mandatory before any
generation.
