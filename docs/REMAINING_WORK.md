# MinusOps — remaining work

Companion to `docs/PROGRESS.md` (what's done). This is what isn't.
Written 2026-07-27. Ordered by what blocks what, not by size.

---

## P0 — do before anything else

### 0.1 Commit the working tree
33 files staged, +1,684/−696, **zero commits**. Three features, four bug fixes, and both
new gates exist only in the working tree. A crash or a stray `git checkout` loses all of it.

Suggested split (5 commits, each independently revertible):
1. `chore: repo cleanup` — runs/ purge, dead reporter renderers, provider stubs, deps, gitignore
2. `fix: three latent bugs` — plan_inspector abs-path filter, .tfvars snapshot leak, non-atomic gate writes
3. `feat: claim-grounded authoring` — scope column, WAL, JSONL, author-context claims, never-permit guard
4. `feat: cloud drift detection` — issue #1
5. `feat: address-churn / moved blocks` — issue #2

### 0.2 Pick a license (issue #5)
`pyproject.toml` says `license = { text = "Proprietary" }` on a **public repo** you intend
others to adopt. Everything in the adoption story is downstream of this. Nobody can legally
use it as-is. Needs a decision only you can make (MIT / Apache-2.0 / BSL / something else),
then `pyproject.toml` + a `LICENSE` file.

---

## P1 — correctness and safety gaps

### 1.1 Two `ponytail:`-marked shortcuts with known ceilings
- `knowledge_store.py:305` — `export_jsonl` writes ids verbatim. Correct for rebuilding a
  local cache; **breaks when two branches each allocate id 7**. Fix: make cross-references
  (`invalidated_by`, `claim_adjudications`) use `content_hash` instead of `id`.
- `plan_gate.py:107` — `_write_json_atomic` fixes torn writes, **not lost updates**. Two
  operators planning the same dir can still have one `pending_plan.json` overwrite the
  other, so operator A can approve operator B's plan hash believing it's their own. Fix:
  wrap in `audit_chain._AppendLock` (already built and Windows-tested).

### 1.2 The suite cannot be run
`tests/test_destructive_change_gate.py`'s 16-module baseline does 16 sequential
`terraform init` + `terraform test` runs; exceeds 9 minutes, killed 4× this session.
Consequence: **nobody has ever seen this suite green end-to-end.** CI is the only signal.
Fix options: mark it `@pytest.mark.slow` and default-deselect; or share one `terraform init`
across modules; or pin provider versions (see 1.3) so it stops re-downloading.

### 1.3 Floating provider constraints
Module `versions.tf` files don't pin exact provider versions, so a new AWS provider release
makes the suite download ~700 MB before it can run. Observed live: `aws 6.56.0` pulled
mid-session. `module_provenance.py` already records a `provider_version` to pin against.

### 1.4 `%TEMP%\pytest-of-shubh` is ACL-broken
`takeown` cannot repair it. Every pytest run needs `--basetemp=.pytest_tmp`. Should be
`addopts` in `pyproject.toml` so it's not tribal knowledge.

### 1.5 Live `terraform.tfstate` in `runs/`
7 state files, consistent with the note about auto-approve applying real infrastructure.
Nobody has reconciled these against the actual AWS account. Could be live billable
resources. **Unaudited.**

---

## P2 — the memory half is wired but unfed

The claim store works and is empty. Until these land, decision #2 (own memory) is aspiration.

### 2.1 Nothing writes claims back
`author-context` reads claims; no path writes them after an agent researches.
`knowledge_delegation.record_delegation_verdict()` exists and is correct — it needs a CLI
front-end so a driving agent can call it. **This is the single highest-value remaining item**:
without it memory never accumulates and the whole strategy is inert.

### 2.2 No seed corpus (decision #10)
Every adopter starts empty and re-researches AWS from zero. Mechanism is safe to build
(claims can't permit); the content is a curation effort.

### 2.3 Schema-linked staleness for semantic claims (decision #7)
`knowledge_degradation` invalidates schema claims. Nothing yet flags a *research* claim as
`needs_review` when its attribute's schema moves.

### 2.4 JSONL not wired into a workflow
`export_jsonl`/`import_jsonl` are tested but nothing calls them. Needs: export after write,
import on cache miss, and `knowledge/claims/` actually committed.

---

## P3 — gate coverage

### 3.1 Agent-authored Rego (issue #4) — largest remaining piece
`rules.rego` covers 13 rule IDs; AWS has 1000+ resource types. Almost everything an agent
generates lands in "no rule fired," which `verification_coverage` now honestly reports but
does not fix. Needs: an intake for agent-proposed rules, landing warn-only until a human
promotes them to blocking.

### 3.2 G6 shadow mode still duplicating (decision #18)
Rego runs alongside the regex scanner, blocks nothing, and the divergence is printed and
ignored. Decided outcome: promote Rego, then delete the regex rules it supersedes. ~2,000
lines of duplication until then.

### 3.3 G9 is inert
`_g9_eval` can only return `g9_not_configured` — no emulator passes its own security bar.
Decided outcome: collapse `RESOURCE_TYPE_ALLOWLIST` into claims + a separately-promoted
flag, delete the 625-line emulator machinery and the 564-line CI workflow.

### 3.4 Cloud drift Class 2
Resources *added* outside Terraform are invisible — never in state, so no plan mentions
them. Needs discovery (AWS Config / Resource Explorer, or
`hashicorp/agent-skills@terraform-search-import`), not drift reading.

### 3.5 File-ownership boundary (issue #3)
`_ensure_empty_or_overwrite` is still refuse-or-clobber. A team that added one alarm either
blocks regeneration forever or loses the alarm. Fix is a naming convention
(`generated_*.tf` owned by MinusOps), not a merge engine.

### 3.6 Env promotion content-hash linkage (decision #14)
Nothing records that a prod plan derives from an approved staging one, or diffs them.

---

## P4 — driving MinusOps from Antigravity (your next test)

Decision #1 says any agentic CLI drives MinusOps. That is **untested outside Claude Code.**

### 4.1 What exists
`.agents/skills/` ships 5 SKILL.md files (`architect`, `grill-me`, `pipeline-optimizer`,
`resolve-ambiguity`, `terraform-orchestrator`). `terraform-orchestrator` documents the
verify→plan→approve→apply loop. Three files mention `agy`/other runtimes.

### 4.2 What will likely bite during your Antigravity test
- **The `create` phrasing trap.** `is_creation_request()` needs a create verb AND an infra
  noun. `"governed AWS data pipeline"` silently classifies as `OPERATION` and creates
  nothing. Any agent phrasing it naturally will hit this.
- **10 console entry points** vs `minusctl` subcommands — two overlapping surfaces, so an
  agent has to guess which to use. Docs reference both.
- **SKILL.md files predate this session** — they don't mention `author-context`,
  `cloud_drift`, `address_churn`, or `verification_coverage`.
- **`--basetemp` requirement** isn't documented anywhere an agent would read.
- **Nothing tells an agent the claims loop exists**, because the write half doesn't.

### 4.3 Suggested prep before testing
1. Fix the `create` phrasing trap (accept a plain noun phrase, or fall through to the
   requirements path rather than silently doing nothing).
2. Refresh `terraform-orchestrator/SKILL.md` with the new gate outputs so the agent knows
   what a drift/churn block means and how to resolve it.
3. Add `addopts = "-q --basetemp=.pytest_tmp"` to `pyproject.toml`.
4. Write one `SKILL.md` describing the whole loop end to end
   (`create → requirements → decision → author-context → author → gate`).

---

## P5 — cleanup still on the table

| Item | Size | Status |
|---|---|---|
| CDP PDF stack | 261 lines | **Blocked on you.** Output proven byte-identical; only loses the PDF bookmark sidebar |
| `dispatcher.py` (issue #6) | 133 lines | **Blocked on you.** Zero prod importers, but documented in README/AGENTS |
| Docstring-as-changelog | ~1,500 lines | 3,018 lines of multi-line strings in `core/`, much of it dated audit narrative git already holds |
| `docs/` phase scope docs | ~3,300 lines | Completed-phase process artifacts; each has 3–15 inbound source references, so they must be trimmed together |
| `HANDOFF.md` | 1,803 lines | Superseded by `docs/PROGRESS.md` |
| Overlapping "where is what" docs | — | README + AGENTS.md + REPO_MAP.md + documentation_ledger.md + information_library.md |

---

## Suggested order

```
P0.1 commit  →  P0.2 license
     ↓
P4.3 Antigravity prep (create trap, SKILL.md, addopts)   ← unblocks your test
     ↓
P2.1 claim write-back CLI      ← highest value; makes memory real
     ↓
P1.1 two ponytail shortcuts    ← lost-update race is a real approval-integrity bug
     ↓
P1.2/1.3 make the suite runnable
     ↓
P3.1 agent-authored Rego       ← largest; everything else in P3 follows
```

**Honest framing:** the gate half is real and got meaningfully stronger this session. The
memory half is architecturally complete and functionally inert — one CLI (P2.1) is what
separates those two states.
