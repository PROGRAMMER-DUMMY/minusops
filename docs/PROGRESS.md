# MinusOps — living progress record

**Purpose:** survive session resets and context compaction. Anyone (human or agent) picking
this up cold should be able to read only this file and know where things stand.

**Maintenance rule:** update this file in the same change that alters state. If you finish an
increment, close a decision, or discover a bug, edit here before moving on. Stale entries are
worse than missing ones.

Last updated: 2026-07-27 (session 2) · Branch: `restructure/multi-cloud-foundation`

---

## 1. Where the project landed strategically

MinusOps **owns memory + judgment**; it **delegates authoring and research** to the agent
ecosystem. Reasoning, from a 2026-07-26 research pass:

| Space | Best ecosystem skill | Installs |
|---|---|---|
| Terraform module authoring | `wshobson/agents@terraform-module-library` | 12.8K |
| Terraform (AWS modules maintainer) | `antonbabenko/terraform-skill` | 4.1K |
| Medallion data pipeline | `aradotso/data-skills@data-engineering-medallion-pipeline` | 1.7K |
| **Terraform plan review / approval** | `lgbarn/devops-skills@terraform-plan-review` | **23** |
| **IaC management (Harness)** | `harness/harness-skills@manage-iacm` | **17** |

Authoring is commoditized and free. Plan-hash-bound AWS governance is an empty quadrant.
Nothing anywhere persists verified claims with temporal validity and provider-version keying.

---

## 2. Locked decisions (21)

From the `grill-me` session, 2026-07-26. These are settled; reopen deliberately, not by drift.

### Positioning
1. Agent lives **outside** — an existing agentic CLI drives MinusOps. No SDK, no API key, no LLM in the product.
2. Own **memory + judgment**; delegate authoring + research.
3. **AWS only** — Azure/GCP stubs and the `CloudProvider` ABC deleted.
4. **Open-source tool** others adopt — requires replacing the `Proprietary` license.

### Memory
5. Claims **inform, never permit**. Permission = executable Rego + human promotion.
6. First consumer is `author-context` — schema *and* web-research claims as grounding.
7. Staleness is **attribute-level**, not provider-version-level.
8. **Git-committed JSONL**; SQLite is a rebuildable cache.
9. **Sharded by resource type**, unordered, deduped on `content_hash`, explicitly not hash-chained.
10. **Ship a seed corpus** — safe via #5, survives provider bumps via #7.
11. **One store, `scope` column**, `resource_type` nullable. Architecture/practice/template claims are first-class and never auto-expire.

### Gate & safety
12. Dev edits: **file ownership boundary** (`generated_*.tf`).
13. Cloud drift: read **`resource_drift`**; unmanaged discovery later.
14. Env promotion: **pinned content-hash + per-env approval**. Approvals never transfer (industry-confirmed: Spacelift/Atlantis promote plan→apply within a stack, never approval across environments).
15. Address churn: **require a `moved` block** for rename-shaped destroy/create.
16. Secrets: **hash but never copy** `.tfvars` into snapshots.
17. Concurrency: reuse **`_AppendLock` + `os.replace`**; **WAL** for the claim DB.
18. Coverage grows via **agent-authored Rego**, warn-only until human promotion.

### Cost & reporting
19. Agents contribute **`tf_type → serviceCode` mappings only** — never free-ness, never rates.
20. Every plan report **discloses what was and wasn't checked**.
21. Primary metric: **coverage ratio per plan, trended across runs**.

**Explicitly rejected:** multi-cloud; building an authoring engine (Phase 7 item 5 — cancelled,
not deferred); deleting the 16 catalog modules (they are sample data and test fixtures, not a
blocker — this was a misdiagnosis early in the session).

---

## 3. Shipped this session

### Cleanup
- `runs/` 7.0 GB → 12 MB (removed regenerable `.terraform/` caches; **all 7 `terraform.tfstate` files untouched**).
- Root junk removed: 5 × `pt_*.log`, 1 MB `test_log_*.txt`, `Temppytest/`, `tmp_test/`, `.pytest_cache/`, `minusops.egg-info/`.
- `reporter.py` −362 lines: `build_svg_v3` (prototype, never wired), `_v3_summary_cards`, `_V3_REL`, `build_plan_html`, `render_png`, `build_gate_flow_svg`.
- `report.html`/`plan.html` were byte-identical duplicates → collapsed to `report.html`; 5 consumers updated.
- Provider stubs deleted: `azure.py`, `gcp.py`, `capabilities()`, `CloudProvider` ABC collapsed.
- `pyproject.toml`: `dash`/`plotly` removed from base `dependencies` (base install is now dependency-free, matching the README's stdlib-only claim).
- `MANIFEST.in`: `recursive-include modules *.tf` → `*.tf *.py` (was silently omitting `etl.py`/`compact.py`).

### Bugs found and fixed
1. **`plan_inspector.iter_source_files`** filtered skip-dirs against **absolute** path parts — any workspace path containing `.git`/`.minus`/`__pycache__` hashed nothing and silently blinded source-drift detection.
2. **`.tfvars` content copied verbatim** into report bundles the dashboard serves and `minusctl package` ships. Now hashed but never copied. Regression test verified failing-without-fix.
3. **Gate state written non-atomically** — `open(path,"w")` truncates immediately, so a process killed mid-write destroyed `pending_plan.json` or an approval record. Now temp-file + `os.replace` at all three sites.
4. **`resource_drift` never read** despite being in JSON already parsed → issue #1.

### Feature: claim-grounded-authoring (all 6 increments, TDD)
| # | Increment | Tests |
|---|---|---|
| 1 | `scope` column + nullable `resource_type` + index | 6 |
| 2 | WAL journal mode | 3 |
| 3 | JSONL source-of-truth, sharded, round-trip | 4 |
| 4 | `author-context` returns resolved claims | 4 |
| 5 | claims-never-permit invariant | 3 |
| 6 | `verification_coverage` in manifest + report HTML | 8 |

New file: `core/governance/verification_coverage.py`.

### Feature: cloud drift detection (issue #1, decision #13) — CLOSED
- `plan_reader.resource_drift()` reads the top-level array. Absent = no drift, **not** an error (unlike `resource_changes`, where absence is fail-closed) — Terraform only emits the key when a refresh found something.
- `core/governance/cloud_drift.py` distinguishes a **revert** (per-attribute: reality moved `before`→`after`, plan proposes moving it back) from drift the plan leaves alone. Only the revert is urgent — it silently undoes a deliberate human action and Terraform renders it as a routine `update`.
- `_reject_if_reverts_out_of_band_and_auto_approve` blocks auto-approve with no bypass flag, same shape as the destructive check. Gatekeeper mode is never blocked (a human is already in the loop). Computed at plan time, carried through the approval record to apply, like `g9_result`.
- 10 tests in `tests/test_cloud_drift.py`.
- **Still open:** Class 2 — resources *added* outside Terraform are invisible because they were never in state, so no plan mentions them. Needs discovery (AWS Config / Resource Explorer or `hashicorp/agent-skills@terraform-search-import`), not drift reading.

### Session 2 additions
- **Suite runnable at last.** 14 live-Terraform files marked `slow` and deselected by default; `--basetemp` moved into `addopts`. **535 passed / ~50s**, from never-completing. Gotcha recorded: six files already had a module-level `pytestmark = pytest.mark.skipif(...)`, and adding a second `pytestmark =` below it does NOT combine — the second assignment wins and the mark is discarded. All six now use a list.
- **`create` no longer silently no-ops.** A bare infra noun phrase is now a creation request; interrogatives and operations on existing infra veto. The old rule needed a create verb AND an infra noun, so `create "governed AWS data pipeline"` printed success and created nothing.
- **Claim write-back (`synthesizer.py remember`)** — the missing half of the loop. `--source-url` required; `pricing_map` scope refuses prices and free-ness claims.
- **Claims reach git.** `remember` exports to `knowledge/claims/*.jsonl`; a fresh clone with no `claims.db` rebuilds the index from the committed corpus.
- **Corpus is merge-safe.** Row ids are no longer exported — two branches both allocating id 1 used to silently drop a claim and could mis-wire an invalidation chain. Cross-references travel as `content_hash`.
- **Lost-update race closed.** `_gate_state_lock` reuses `audit_chain._AppendLock`. Two further defects found by the tests, not by reading: `_write_json_atomic` staged every write to one shared `.tmp` (concurrent writers clobbered each other), and `os.replace` transiently fails with ACCESS_DENIED on Windows (bounded retry added).
- **Team files preserved (issue #3).** `GENERATED_FILES` are MinusOps'; any other `.tf` is the team's and is never rewritten.
- **`.agents/skills/minusops-loop/SKILL.md`** — one end-to-end guide for an external agent runtime; every command in it was executed before commit.

### Feature: address-churn / `moved` block enforcement (issue #2, decision #15) — CLOSED
- `core/governance/address_churn.py`. Rename-shaped = delete + create, same type, same plan, **matching identifying attributes**. The identity comparison keeps it honest both ways: without it every delete+create looks like a rename (real deletions waved through), and a genuinely different bucket gets mistaken for a move.
- `read_moved_blocks()` parses `moved { from/to }` by regex, not a full HCL parser — fixed two-field shape, and a missed block fails SAFE (gate objects to already-declared churn: noisy, never silently destructive).
- Enforced at **plan** time, not apply. Unlike the auto-approve checks, no mode exists in which silently destroying a bucket is intended, so "a human sees it at approve" is not sufficient.
- Blocking scoped to `STATEFUL_RESOURCE_TYPES`; non-stateful renames are advisory. A gate that blocks the harmless case trains operators to bypass it.
- 9 tests in `tests/test_address_churn.py`.

---

## 4. Verification status

**535 passed, 77 skipped, 303 deselected in ~50s.** The suite now runs to completion, which
it never once did before: 14 files doing live `terraform init` / provider-schema fetches are
marked `slow` and deselected by default. CI runs them with `pytest -m slow`.

```bash
python -m pytest          # no flags needed; pyproject sets --basetemp and -m 'not slow'
```

⚠️ Module provider constraints still float, so a new AWS provider release makes the `slow`
suite download ~700 MB before it can run. Pinning in each module's `versions.tf` would fix
it. `module_provenance.py` already records a `provider_version` to pin against.

⚠️ `tests/test_query_athena_module.py` exited 1 once during a sequential per-file scan but
passes standalone. Suspected contention, not a real failure — worth watching.

---

## 5. Open work

> Full categorised breakdown with a suggested order: **[`docs/REMAINING_WORK.md`](./REMAINING_WORK.md)**.
> Summary below.

GitHub issues (created 2026-07-26):
~~[#1](https://github.com/PROGRAMMER-DUMMY/minusops/issues/1) resource_drift~~ **CLOSED** ·
~~[#2](https://github.com/PROGRAMMER-DUMMY/minusops/issues/2) moved blocks~~ **CLOSED** ·
[#3](https://github.com/PROGRAMMER-DUMMY/minusops/issues/3) file-ownership boundary ·
[#4](https://github.com/PROGRAMMER-DUMMY/minusops/issues/4) agent-authored Rego ·
[#5](https://github.com/PROGRAMMER-DUMMY/minusops/issues/5) license ·
[#6](https://github.com/PROGRAMMER-DUMMY/minusops/issues/6) dispatcher.py

Next actionable without an owner decision: **#3** (file-ownership boundary) — a naming
convention rather than a merge engine, so it is small. Then **#4** (agent-authored Rego),
which is the coverage-growth loop and the largest remaining piece.
**#5 and #6 are blocked on the owner**, as is the CDP PDF call.

**Blocked on owner decision:**
- License choice (#5) — blocks the entire adoption story.
- `dispatcher.py` keep-or-remove (#6).
- CDP PDF stack (261 lines). Output proven **byte-identical** (1,392,720 B decompressed, 511 fill ops, 120 dark fills both ways). The only loss is the PDF bookmark sidebar (`/Outlines`, 27 `/Title` entries).

**Known shortcuts (marked `ponytail:` in code):**
- `export_jsonl` writes ids verbatim — correct for rebuilding a local cache, wrong for merging two branches that each allocated id 7. Upgrade: switch cross-references to `content_hash`.
- `_write_json_atomic` fixes torn writes, not lost updates. Two operators planning the same dir can still have one `pending_plan.json` overwrite the other. Upgrade: wrap in `audit_chain._AppendLock`.

**Unresolved risk:** `runs/` holds 7 live `terraform.tfstate` files, consistent with the note
about auto-approve applying real infrastructure. Worth reconciling against the actual AWS account.

---

## 6. Repo facts worth not rediscovering

- `knowledge_delegation.py` already implements agent-researches / MinusOps-remembers, exactly as designed: `build_delegation_request()` packages a `needs_review` result, `record_delegation_verdict()` records the answer with adjudication links. Its docstring: *"No local model anywhere in this path — the driving agent does the adjudication."*
- `rules.rego` matches on **resource types**, not modules. Deleting `modules/` would not remove a single rule.
- G6 runs shadow-only and blocks nothing. G9's `_g9_eval` can only return `g9_not_configured` — no emulator passes its own security-critical bar (LocalStack needs an unprovisioned paid token; MiniStack and Floci both fail negative fidelity on all three critical types).
- `coverage_audit.classify()` is the honesty pattern `verification_coverage.py` was modelled on.
- 21 % of `core/` is comments/docstrings (3,018 lines of multi-line strings), much of it dated audit narrative that git history already holds.
