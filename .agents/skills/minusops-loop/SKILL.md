---
name: minusops-loop
description: Drive MinusOps end to end - gather requirements, record an architecture decision, author HCL against live schema, and ship it through the plan-bound deploy gate. Use whenever the user asks to create, change, or deploy AWS infrastructure in this repo.
---

# The MinusOps loop

**You author. MinusOps gates and remembers.** It runs no model and makes no external calls
of its own. Your job is to research and write HCL; its job is to refuse anything unproven and
to remember what you established so the next session does not repeat your work.

AWS only. Every command below is read-only or writes local files, except `apply`.

---

## 0. Before anything

```bash
python -m pytest            # 1617 passed, 90 skipped / ~90s. If this is red, stop and report.
```

Do **not** pass `--basetemp` or `-m`; `pyproject.toml` already sets both. Live-Terraform
tests are deselected by default and run in CI with `pytest -m slow`.

---

## 1. Gather requirements — do not skip

MinusOps refuses to generate from a vague request. That refusal is the product, not an
obstacle: it forces the interview to actually happen.

```bash
minusctl create "<what the user asked for>"
```

A bare noun phrase works (`"governed AWS data pipeline for clickstream analytics"`). Asking
*about* infrastructure (`"what does my pipeline cost"`) or operating on existing
infrastructure (`"deploy this"`) correctly does **not** create a run.

It prints a run id and every missing requirement. Interview the user for the gaps — use the
`grill-me` skill — and write `runs/<id>/requirements.json`. Required non-functional axes each
need a value **or an explicit `deferred: <reason>`**; a deferral is a recorded decision, not
an omission.

```bash
minusctl readiness --run <id>
```

Keep going until requirements stop being listed as blockers.

## 2. Record the architecture decision

```bash
minusctl decision template --write --run <id>
```

Fill `architecture_decision.json`: selected architecture, `selected_modules` (from
`core/generation/modules.py`) **or** `novel_resources`, plus alternatives considered,
assumptions, risks, and sources. This is audit evidence for why the infrastructure exists.

## 3. Check what is already known — before researching

```bash
python core/generation/synthesizer.py author-context <resource_type> "<requirements summary>"
```

Returns live provider schema, grounding examples from the module catalog, and **`claims`** —
what MinusOps already verified about this type, with sources and dates. **Read the claims
first.** If one already answers your question, do not re-research it.

## 4. Research, then write it down

For anything the claims do not cover, research it (provider docs, registry, AWS docs). Then
record what you found so the next session starts from it:

```bash
python core/generation/synthesizer.py remember \
  --resource-type aws_dynamodb_table --attribute billing_mode \
  --claim "PAY_PER_REQUEST removes capacity planning for spiky writes" \
  --source-url "https://docs.aws.amazon.com/..." \
  --valid-from "2026-07-27T00:00:00Z"
```

- `--source-url` is **required**. An unsourced claim is a rumour with a timestamp.
- Use `--scope architecture` (and omit `--resource-type`) for cross-cutting knowledge:
  design choices, developer practices, templates.
- `--scope pricing_map` accepts `tf_type -> serviceCode` mappings only. It **refuses** rates
  and free-ness assertions. Real numbers come from BCM and Cost Explorer, never from you.

**Claims inform, they never permit.** Nothing you record here can make infrastructure
shippable — that requires an executable policy rule plus a human.

## 5. Author the HCL

Write real HCL against the schema from step 3, then submit it:

```bash
python core/generation/synthesizer.py author <resource_type> --file <path> --run <id>
```

G2 schema-lint rejects attributes that do not exist. A rejection means the schema disagrees
with you — re-read it rather than working around the gate.

## 6. Ship it through the gate

```bash
minusctl gate verify  --dir runs/<id>/terraform
minusctl gate plan    --dir runs/<id>/terraform
minusctl gate approve --dir runs/<id>/terraform
minusctl gate apply   --dir runs/<id>/terraform
```

`plan` records a SHA-256 plan hash. `apply` runs **only** that hash; any `.tf` edit voids the
approval. Never `terraform apply` directly.

### Three refusals you will meet, and what they mean

**`REVERTS out-of-band change`** — someone changed this resource directly in the AWS console
and your plan undoes it. Terraform shows it as a routine `update`. Ask the user whether the
console change was deliberate before proceeding. Do not assume the plan is right.

**`BLOCKING: ... is a rename of the same <id>`** — the same real resource moved to a new
Terraform address, so Terraform would **destroy and recreate** it. On S3 or RDS that is data
loss. Add the `moved` block it prints. Never work around this.

**`not autonomous-eligible`** — the plan touches stateful or IAM resources, or a type nobody
has reviewed. Re-run `apply` with `--mode gatekeeper` so a human confirms. There is no bypass
flag; do not look for one.

## 6b. If a resource type had no rule, propose one

The report will tell you which types were *unchecked*. That is the coverage gap, and closing
it is how MinusOps gets more useful over time.

Add a rule to `policy/g6/rules.rego` following the existing `finding(...)` shape, and record
a claim citing the source you based it on. **A proposed rule lands warn-only automatically** —
it appears in reports and coverage but cannot block anything until a human promotes it:

```bash
minusctl policy list
```

Do **not** promote it yourself. Promotion requires a named person and a statement of what
they actually reviewed:

```bash
minusctl policy promote SEC-42 --by alice@corp --reason "verified against AWS docs, fires on the 3 known cases"
```

Write the rule so it fires on a case you can demonstrate. A rule that never fires is worse
than no rule: it makes a type look reviewed when nothing checks it.

## 7. Hand over

```bash
minusctl readiness --run <id>
minusctl package   --run <id>
minusctl audit verify
```

The report carries a **Verification coverage** section stating which resource types had a
policy rule fire and which had none. A type marked *no rule* was **not checked** — a clean
result there means nothing was evaluated, not that nothing is wrong. Say so when you report
to the user; do not present a green report as a verified one.

---

## Rules

- Never run `terraform apply` outside the gate.
- Never bypass a G2 rejection by editing the gate.
- Never record a price or a free-ness claim.
- Never describe generated infrastructure as verified without checking coverage.
- If credentials are missing, say so plainly — everything except `apply` works offline.

## Try it with no cloud

```bash
minusctl demo governed-data-pipeline --owner data-platform --daily-data-gb 50
minusctl prove
```
