---
name: architect
description: Research-driven architecture synthesis for ANY system class, so we don't hand-write a blueprint per scenario. Given the requirements grill-me gathered, deep-research current cloud services and reference architectures from authoritative sources, compare candidate stacks against the actual requirements and the Well-Architected pillars, choose the best-fit with explicit tradeoffs, then write governed Terraform for it. The synthesized Terraform flows through the SAME deploy gate (terraform validate + per-resource security scan + human plan-hash approval + BCM cost) before anything is applied — research proposes, the gate and a human authorize.
---

# Architect — Research-Driven Architecture Synthesis

Use after [`grill-me`](../grill-me/SKILL.md) has gathered requirements — especially when no
existing blueprint matches the system class. The point: pick the best-fit architecture for *these*
requirements from *current* service options (researched, not remembered), and produce governed
Terraform — instead of hand-authoring a blueprint for every scenario. New, approved syntheses can
be captured as blueprints afterward, so the registry grows from real work rather than up front.

**Governance invariant (read first).** A synthesized architecture is NOT trusted because the
agent proposed it. It is trusted only after it passes `plan_gate verify` (terraform validate +
`SEC-*` scan, which *blocks*), a human approves the exact **plan-hash**, and BCM prices it. The
deploy gate works on *any* `--dir`, so synthesized Terraform takes the normal path — no special,
less-governed route. **Never apply un-gated, un-approved, AI-generated infrastructure.**

## Step 1 — Research (always; do not rely on memory)

Anchor on authoritative sources before deciding. Start from the repo's curated catalogs
(`docs/information_library.md`, `docs/documentation_ledger.md`), then web search:

- Candidate services and managed options (vendor service docs).
- Reference architectures and design patterns (AWS Architecture Center; AWS / HashiCorp
  **Well-Architected**; vendor solution libraries).
- The exact **Terraform Registry resource schemas** for the services you'll use — required and
  optional arguments — *before* writing any HCL, so resources aren't guessed/hallucinated.
- The current pricing basis for the cost comparison (never invent prices — BCM prices the final).

Cite what you used; prefer docs matching the installed provider/CLI version (the Codebase rule and
version checks in the catalogs apply).

## Step 2 — Generate candidates and choose advantageously

Produce **2–3 candidate architectures**, not one. Map each to the requirements and score it
against what the user actually asked for *and* the Well-Architected pillars — operational
excellence, security, reliability, performance efficiency, cost optimization, sustainability.

Build a short **tradeoff matrix** (candidate × {fit to each key requirement, cost, ops burden,
lock-in, complexity}). Recommend the best-fit with explicit reasoning, and say why the others
lost — **tie every choice back to a stated requirement**. For example: "Aurora Serverless v2 over
a fixed RDS instance because *spiky, mostly-idle traffic* + *minimize cost* favor scale-to-low;
rejected DynamoDB because *ad-hoc SQL by analysts* needs relational queries." No requirement → no
decision driver. Treat the user's hard constraints (cloud, region, budget, compliance) as filters
that eliminate candidates, not as soft preferences.

## Step 2.5 — Use the tooling (don't do it all by hand)

Concrete tools back this skill — prefer them over free-form work:

```bash
python core/generation/patterns.py match "<requirements>"        # reuse a prior approved composition first
python core/generation/modules.py  match "<requirements>"        # vetted building blocks for the requirements
python core/architecture/discovery.py "<topic>" --resource aws_<type> --service-code <Code>   # authoritative source URLs
python core/generation/synthesizer.py "<requirements>" --requirements-file requirements.json --owner <team>
```

The synthesizer is **fail-closed on the requirements gate**: it refuses to generate without a
complete `requirements.json` (from grill-me) and lists what's unanswered. Never pass
`--allow-incomplete` for real work — that override is demo/testing only and is audited.

Start by checking `patterns.py match` (reuse an approved design); then `modules.py match` to pick
building blocks; use `discovery.py` to ground each new resource in its Registry schema; then
`synthesizer.py` to compose a run workspace. Add a missing capability by writing a new
`modules/<id>/main.tf` + a row in `core/generation/modules.py`, not by forking a monolith.

## Step 3 — Synthesize governed Terraform

`synthesizer.py` composes the selected modules and flags module-specific inputs as `REVIEW` in the
generated `main.tf` / `COMPOSITION.md`. Complete that wiring (and any net-new resources) guided by
the Registry schemas from Step 1 (not guessed). Bake in the same controls the modules enforce, so
it passes the gate:

- least-privilege, per-service IAM — **no `Resource: "*"`** (the `SEC-02` scanner blocks it),
- encryption at rest (KMS / SSE) and in transit,
- no public exposure by default; private networking where applicable,
- `owner` / `environment` tags, remote state, and budget + observability (alarms/logs) hooks.

Keep it reviewable: split by concern, no secrets in code.

## Step 4 — Govern it (the same gate, unchanged)

The synthesized dir goes through the normal control plane:

```bash
python core/governance/plan_gate.py verify --dir <run>/terraform   # fmt + terraform validate + SEC scan (blocks on findings)
python core/governance/plan_gate.py plan   --dir <run>/terraform   # plan-hash + deploy report (architecture diagram, cost, etc.)
minus-bcm prepare --report-dir <run>/reports/<hash> ... && minus-bcm run ...   # real cost from the BCM Pricing Calculator
python core/governance/plan_gate.py approve --dir <run>/terraform   # a human reviews the exact plan-hash (RBAC + MFA-backed session)
python core/governance/plan_gate.py apply   --dir <run>/terraform   # applies ONLY the approved hash; one-shot
```

If `verify` fails (a validate error or a `SEC-*` finding), fix the Terraform and re-verify — do
not proceed. The architecture diagram, cost report, and audit chain are produced automatically, so
the human reviews a **researched, validated, priced** proposal, not a black box.

## Step 5 — Capture it as a reusable blueprint (optional)

When a synthesized architecture is approved and works, persist it as a blueprint-shaped spec
(`id`, `cloud`, `services`, `required_inputs`, `controls`) that passes
`blueprints.validate_blueprint`, so the next identical request reuses the governed recipe instead
of re-researching. The registry then **grows from real, approved work** rather than being
hand-authored in advance.

## Boundaries

- Research and propose for any cloud / class; only *generate* what you can ground in real Registry
  schemas and pass `terraform validate`. If you can't ground it, hand back a requirements spec and
  say what's missing — don't emit speculative HCL.
- Never fabricate costs (BCM only); never bypass or weaken the deploy gate.
- Surface the tradeoff matrix and the rejected alternatives to the user before generating — the
  recommendation is theirs to accept, not yours to assume.

## References

AWS / HashiCorp **Well-Architected** frameworks, the **AWS Architecture Center**, **Terraform
Registry** provider schemas, and the repo's `docs/information_library.md` /
`docs/documentation_ledger.md` source catalogs.
