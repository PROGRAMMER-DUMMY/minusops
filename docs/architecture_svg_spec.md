# Architecture SVG Specification (v2)

> **v2 (current)** keeps every v1 hard requirement below and adds, when the data is
> available: real node-anchored **data-flow edges** (no decorative arrows), **for_each
> instance labels** (e.g. `bronze`/`silver`/`gold`), **encryption (lock) markers** on
> KMS-protected nodes, and a **governance overlay** — each node carries its security/cost/
> observability findings as a badge plus a machine-readable `data-findings` attribute, so the
> diagram doubles as the review surface. See §9 for the v2 additions.

**Purpose.** The deploy report embeds an auto-generated architecture diagram. It is
**LLM-generated**, so different CLI agents (agy, Claude, Codex, …) must all produce the
**same structure** for the same plan. This document is the binding contract: follow it
exactly. A diagram that does not conform is invalid and must be regenerated.

> One input → one shape. Given the same `terraform show -json`, any agent following this
> spec must emit a structurally identical SVG (same layers, tiers, node schema, palette).
> Only labels/counts/positions-within-a-tier may differ.

---

## 0. Hard requirements (a diagram is INVALID without these)

1. Root `<svg>` with `xmlns="http://www.w3.org/2000/svg"`, `viewBox="0 0 1280 760"`,
   `width="100%"`, `role="img"`.
2. A `<title>` and `<desc>` as the first two children (accessibility + embedding).
3. **Self-contained** — no external refs (no `<image href=...>`, no remote fonts/CSS).
   Inline everything. The SVG must render identically inside a PDF with no network.
4. The six **named layer groups** in this exact order and with these exact ids:
   `bg`, `titlebar`, `tier-sources`, `tier-storage`, `tier-compute`, `tier-orchestration`,
   `tier-observability`, `band-security`, `legend`. (Empty tiers still render their header.)
5. Every resource node carries `data-address` (the Terraform address) and a visible
   **type label** + **name label**.
6. The title bar shows: template name, cloud, short plan-hash, generated timestamp.
7. The legend shows the tier color key + the edge-style key.
8. Palette + typography tokens below are used verbatim. No other colors.

---

## 1. Canvas & coordinate bands (fixed)

```
viewBox: 0 0 1280 760

 y   0 ┌─────────────────────────────────────────────┐
       │ TITLEBAR  (id=titlebar)            h = 64    │
 y  64 ├─────────────────────────────────────────────┤
       │ CANVAS — five tier columns                  │
       │  x bands (each col 232 w, 16 gap, m=24):    │
       │   sources        x  24 .. 256               │
       │   storage        x 272 .. 504               │
       │   compute        x 520 .. 752               │
       │   orchestration  x 768 .. 1000              │
       │   observability  x 1016 .. 1248             │
 y 632 ├─────────────────────────────────────────────┤
       │ BAND-SECURITY (cross-cutting)     h = 56    │
 y 688 ├─────────────────────────────────────────────┤
       │ LEGEND  (id=legend)               h = 72    │
 y 760 └─────────────────────────────────────────────┘
```

- **Flow is left → right** (sources → observability). This encodes the data/control path.
- Each tier is a **column**. Its header sits at `y=76`; nodes stack downward from `y=108`.
- **Node card:** 232 × 60, corner radius 12, vertical gap 14 between cards in a column.
- Within a tier, order nodes **alphabetically by Terraform address** (deterministic).
- If a column overflows 8 nodes, shrink card height to 44 and gap to 8; never spill columns.

---

## 2. Tiers — the fixed skeleton

Every diagram has these five columns + one cross-cutting band, always in this order.
Map each resource to exactly one tier using the table in §5.

| id                   | Header label    | Tier hue token | Holds |
| :---                 | :---            | :---           | :--- |
| `tier-sources`       | SOURCES         | `--sand`       | event sources, triggers, inbound (EventBridge rules, SQS in, API GW) |
| `tier-storage`       | STORAGE         | `--terracotta` | buckets, tables, catalogs, queues-at-rest |
| `tier-compute`       | COMPUTE         | `--terra-soft` | jobs, functions, clusters, crawlers (Glue/EMR/Lambda) |
| `tier-orchestration` | ORCHESTRATION   | `--sage`       | state machines, schedulers, workflows, pipelines |
| `tier-observability` | OBSERVABILITY   | `--gold`       | alarms, log groups, SNS, dashboards, budgets, anomaly |
| `band-security`      | SECURITY & IAM  | `--muted`      | roles, policies, boundaries, KMS — cross-cutting footer band |

Security is a **horizontal band** (not a column) because IAM/KMS cuts across every tier.

---

## 3. Node schema (every resource looks the same)

```xml
<g class="node" data-address="aws_glue_job.bronze_to_silver"
   transform="translate(<x>,<y>)">
  <rect class="card" width="232" height="60" rx="12"
        fill="var(--panel)" stroke="<tier-hue>" stroke-width="1.5"/>
  <circle cx="26" cy="30" r="10" fill="<tier-hue>"/>      <!-- icon dot, colored by tier -->
  <text class="n-type" x="48" y="26">AWS Glue Job</text>   <!-- friendly type -->
  <text class="n-name" x="48" y="44">bronze_to_silver</text> <!-- resource name -->
</g>
```

- **Friendly type** = the resource type humanized (`aws_glue_job` → "AWS Glue Job"), not raw.
- **Name** = the resource's local name (last segment of the address).
- The icon is a colored dot in v1 (no icon fonts — keeps it self-contained). A short 2–3
  letter glyph is allowed inside the dot if it stays inline `<text>`.

### Change status (from the plan)
Tint the card's left edge by planned action, using a 4px-wide `<rect>` at x=0:
`create → --sage`, `update → --gold`, `delete → --terracotta`, `no-op → --muted`.
Add `data-action="create|update|delete|no-op"` to the node `<g>`.

---

## 4. Modules & edges

### Modules → dashed container
Resources sharing a module path are wrapped in a labeled container BEFORE node placement:
```xml
<g class="module" data-module="module.iam_service_role">
  <rect class="mod-box" rx="10" fill="none" stroke="var(--muted)"
        stroke-dasharray="4 4"/>
  <text class="mod-label">module.iam_service_role</text>   <!-- tab, top-left -->
  <!-- member nodes here -->
</g>
```
A module box hugs its members with 12px padding; the label sits in a small tab top-left.

### Edges → flow arrows
- **Solid arrow** (`--text`, 1.5px, `marker-end` arrowhead) = data/dependency flow.
- **Dashed arrow** (`--sage`, dash `5 4`) = trigger/control (event → orchestrator, etc.).
- Derive edges from plan dependencies / known wiring (S3→EventBridge→StepFunctions→Glue).
- One `<defs>` arrowhead marker, reused. Edges live in a `<g id="edges">` drawn under nodes.

---

## 5. Resource → tier map (extend as needed; keep grouping stable)

| Resource type prefix | Tier |
| :--- | :--- |
| `aws_cloudwatch_event_rule`, `aws_cloudwatch_event_target`, `aws_s3_bucket_notification`, `*_api_gateway*` | sources |
| `aws_s3_bucket*`, `aws_glue_catalog_database`, `aws_dynamodb_table`, `aws_sqs_queue`, `aws_glue_crawler`'s target | storage |
| `aws_glue_job`, `aws_glue_crawler`, `aws_lambda_*`, `aws_emr_*`, `aws_ecs_*`, `aws_batch_*` | compute |
| `aws_sfn_state_machine`, `aws_scheduler_*`, `aws_mwaa_*`, `aws_datapipeline_*` | orchestration |
| `aws_cloudwatch_metric_alarm`, `aws_cloudwatch_log_group`, `aws_sns_*`, `aws_budgets_*`, `aws_ce_*` | observability |
| `aws_iam_*`, `aws_kms_*`, `*_policy`, `*_role`, `aws_s3_bucket_public_access_block`, `*_server_side_encryption*` | security (band) |

Unmapped types → place in the **closest tier by purpose**; never drop a resource silently —
if truly unclassifiable, put it in `compute` and add `data-unmapped="true"`.

For other clouds, mirror this table by purpose (Azure/GCP equivalents map to the same tiers).

---

## 6. Palette & typography (verbatim — declare once in `<defs><style>`)

```css
:root{
  --bg:#14110f; --panel:#1c1714; --line:rgba(217,93,57,.18);
  --terracotta:#d95d39; --terra-soft:#e8825f; --sand:#d4a373;
  --sage:#8da189; --gold:#cb9a3e; --text:#fbf7f4; --muted:#b09c93;
}
.title{font:600 22px 'Outfit',sans-serif;fill:var(--text)}
.sub{font:500 12px 'JetBrains Mono',monospace;fill:var(--muted)}
.tier-h{font:600 13px 'Outfit',sans-serif;letter-spacing:.12em;text-transform:uppercase}
.n-type{font:600 13px 'Inter',sans-serif;fill:var(--text)}
.n-name{font:400 11px 'JetBrains Mono',monospace;fill:var(--muted)}
.mod-label{font:600 11px 'JetBrains Mono',monospace;fill:var(--muted)}
.legend{font:500 11px 'Inter',sans-serif;fill:var(--muted)}
```
Fonts are **system-stack fallbacks** (the named families may be absent in a PDF renderer);
always end font lists with a generic family. Background `bg` fills the whole canvas.

---

## 7. Required output structure (copy this skeleton, fill the tiers)

See [`architecture_svg_skeleton.svg`](./architecture_svg_skeleton.svg) — the canonical empty
frame. Every agent starts from it and only injects nodes/edges/module-boxes into the tier
groups. Do not move the bands, rename ids, or alter the palette.

---

## 8. Consistency checklist (self-verify before emitting)

- [ ] viewBox `0 0 1280 760`, `<title>`+`<desc>` present, fully self-contained
- [ ] all nine layer groups present, correct ids, correct order
- [ ] every plan resource appears exactly once, in the right tier, with `data-address`
- [ ] change status tint + `data-action` on every node
- [ ] modules wrapped in dashed labeled boxes
- [ ] edges present with the solid/dashed convention + one shared arrowhead marker
- [ ] only the §6 palette colors used
- [ ] titlebar has template • cloud • short plan-hash • timestamp
- [ ] legend has tier key + edge key

---

## 9. v2 additions (binding when the data is available)

These extend v1; a v2 diagram still satisfies every §0 hard requirement.

1. **Data-flow edges are real and node-anchored.** Edges connect actual node positions
   (source right edge → target left edge), never a fixed decorative `y`. Solid `--text` =
   data flow; dashed `--sage` = control/orchestration. For the standard data-pipeline
   blueprint the medallion path is drawn: Bronze → Glue → Silver → Glue → Gold → Athena,
   with the Step Functions → Glue control edges dashed. Generic plans connect the first node
   of consecutive non-empty tiers. No edge may terminate in empty space.
2. **`for_each` instance labels.** A resource addressed `...zone["bronze"]` shows `bronze`
   as its name label, not the block name `zone`, so medallion zones are distinguishable.
3. **Encryption markers.** When the plan contains a customer-managed KMS key, KMS-protected
   nodes (S3, Athena, the key itself) carry a small lock glyph (top-right).
4. **Governance overlay.** Each node renders its highest-severity finding as a badge
   (`SEC-*`/`COST-*`/`OBS-*`, colored by severity, with `+n` for additional findings) and
   carries a machine-readable `data-findings="<comma-separated ids>"` attribute. Security-band
   chips tint their border by finding severity. Findings come from `optimize_analyzer`
   (per-resource), so the diagram is also the security/cost review surface, bound to the
   plan-hash. Tiering note: `aws_s3_object` and `aws_athena_*` map to **storage**.
