# Generation pivot — first end-to-end proof (2026-07-18)

> **PLAN ONLY. NEVER APPLIED.** No AWS resources were created. No cloud cost, no drift, nothing
> to tear down in AWS. Only a local `terraform plan` was run. If this proof is ever cited as
> evidence that generated infrastructure has been deployed, that reading is wrong — correct it.

## What this proves

The first time generated-rather-than-copied HCL ran the full gated path, end to end, with a real
live schema fetch and a real `terraform plan` against a real AWS account — not a unit test, not a
mock, not the catalog.

Loop exercised: `synthesizer.py author-context` (real live schema + grounding) → an agent (this
session) authoring real HCL from that schema → `synthesizer.py author` (the CLI intake built this
session, commits `06fa6eb`/`1700910` on `restructure/multi-cloud-foundation`) → the existing
`_validate_novel_resources()` → `gate_content()` (G2) → `compose()` path, unmodified → real
`terraform init` + `terraform plan`.

## Facts of the run

- **Date:** 2026-07-18
- **Resource type:** `aws_s3_bucket`
- **Run id:** `20260718-124124-synthesized`
- **Run directory:** `runs/20260718-124124-synthesized/terraform/` (gitignored, left in place for
  inspection — not deleted as of this writing)
- **AWS account:** `450374452930` (real account, `TerraForm-admin` IAM user; only read/plan-time
  calls made — `sts:GetCallerIdentity`, the S3 existence check `terraform plan` itself performs)
- **Command:** `synthesizer.py author aws_s3_bucket --file <authored.tf> --allow-incomplete --justification "..."`
- **Gate result:** G2 passed, composed cleanly (`status: composed` in the CLI's own JSON output)
- **`terraform plan` result:**
  ```
  Plan: 1 to add, 0 to change, 0 to destroy.
  ```
- **`terraform apply`:** not run. Not attempted. Out of scope for this proof by deliberate,
  explicit decision — plan-only is the hard line for this class of proof, held without being
  asked twice.

## Two things worth recording as load-bearing, not just the headline

Neither was guaranteed by the design before this run; both are now confirmed:

1. **The authored file landed byte-identical in the composed root.** `compose()` does not
   transform, reformat, or template authored content the way it templates catalog modules — what
   the agent wrote is exactly what Terraform planned. Verified by direct comparison, not assumed.
2. **`tags_all` (`managed_by`, `owner`, `run_id`) came from the existing composition machinery
   wrapping the authored resource**, identically to how it wraps catalog modules. The governance
   envelope applies to generated resources the same way it applies to catalog ones — this was an
   open question the design implied but had never actually exercised until this run.

## Scope: what this does NOT prove

This is the CLI intake leg for the **existing, flat** `authored_content` seam (`dict[str, str]`,
one resource per entry) — the v1 scope explicitly disclosed when `synthesizer.py author` was
built (no `--asset`/`--module-arg`, no module-shaped unit). It does **not** satisfy
`docs/phase7_generation_engine_plan.md`'s item 5 ("the authoring mechanism itself"), which is
explicitly scoped to depend on that plan's items 1–4 (module-shaped `authored_content`, a
catalog-free `synthesize()` path, the requirements-schema symbolic-vs-real decision, a wired
live-schema query function) — none of which are built. This proof also did not involve any
internal generation/authoring logic inside MinusOps: a human/agent read the real schema and wrote
the HCL directly, exactly as the agent-neutral design intends (MinusOps gates and composes; it
never authors).

## Full detail

- The CLI itself: commits `06fa6eb` (bug fix: structured G2 refusal reporting) and `1700910`
  (feature: the `author` subcommand), `core/generation/synthesizer.py`.
- Test coverage: `tests/test_synthesizer.py`, the `author_cli`-prefixed tests plus
  `test_validate_novel_resources_g2_failure_raises_authored_content_rejected_with_findings`.
