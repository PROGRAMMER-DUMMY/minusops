---
name: grill-me
description: Interview the user relentlessly about any uncertain architecture, process, plan, product, implementation, or decision until the user and AI reach shared understanding and reliable choices. Use when the user wants to stress-test an idea, resolve ambiguity, compare options, get grilled, or mentions "grill me".
---

# Grill Me

## Interview Protocol

Interrogate the uncertain topic until the decision tree is explicit and every meaningful branch has either been chosen, rejected, deferred with rationale, or identified as unknown.

Ask exactly one question at a time. Make each question specific, decision-oriented, and grounded in the current state of the topic.

For every question, include a recommended answer and the reasoning behind that recommendation. Keep the recommendation concise enough that the user can accept, reject, or modify it directly.

When the user has not explicitly specified a branch, provide a small set of concrete options before the recommended answer. Include compatibility notes and feedback notes alongside the recommendation so the user can understand the impact quickly.

## Working Method

Start by identifying the highest-leverage unresolved decision. Prefer questions that unblock later branches over questions that merely collect preferences. Use the skill for architecture choices, process design, product scope, implementation strategy, operational policy, and any other situation where the user is unsure and needs reliable option selection.

Resolve dependencies in order:

1. Clarify goals, non-goals, users, and success criteria.
2. Establish constraints such as time, budget, technical environment, operational limits, and risk tolerance.
3. Explore architecture, data flow, interfaces, ownership, failure modes, and migration or rollout strategy.
4. Test edge cases, reversibility, observability, security, privacy, performance, maintainability, and support burden.
5. Confirm the chosen path and any explicitly deferred decisions.

After each answer, update the inferred plan silently and choose the next most important unresolved branch. Do not dump a long questionnaire.

## Codebase Rule

If a question can be answered by exploring the codebase, inspect the codebase instead of asking the user. Report the discovered fact briefly only when it matters to the next question or recommendation.

Use local evidence for implementation details, existing patterns, dependencies, APIs, tests, and constraints. Ask the user only for intent, priorities, tradeoffs, or facts that are not reasonably discoverable.

## Question Shape

Use this format:

```markdown
Question: ...

Options:
- Option A: ...
- Option B: ...

Recommended answer: ...

Compatibility: ...

Feedback note: ...
```

Use two or three options only. Mark tradeoffs plainly, and keep compatibility and feedback notes specific to the current decision. Add one short rationale when the recommendation is not obvious. Avoid multi-part questions unless the parts are inseparable.

When the user accepts or modifies a recommendation, proceed to the next question. When the user rejects it, ask the next question needed to understand the rejected branch.
