# Requirements-Driven Module Preplan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace free-text keyword matching with a deterministic module-id recommendation
derived from the *structured* requirements fields `grill-me` already gathers, fixing the
demonstrated bug where `match_modules()` on a raw sentence missed `query-athena` entirely and
pulled in an unwanted `speed-layer-kinesis`/`ingest-firehose` streaming layer.

**Architecture:** A new function, `derive_module_ids()`, added to the existing
`core/generation/modules.py` (not a new file — this is the module-selection home already).
It reads exactly the "named carve-out" fields `core/architecture/requirements.py`'s own
docstring already identifies as enumerable, gathered, and currently unused:
`non_functional.latency` and `data_pipeline.consumption`/`orchestration`/`catalog`. For each
field, it checks the field's text against the *same* `satisfies` phrase lists `match_modules()`
already uses — reusing one source of truth for what a module "means" — but with stricter,
whole-phrase-or-all-tokens matching (never single-token overlap), because a false positive here
would wrongly recommend a module for review, the exact failure this plan fixes. Output is a
list of `{module_id, reason, source_field}` recommendations for human/agent review into
`architecture_decision.json`'s `selected_modules` — never auto-applied, matching every other
selection path in this codebase.

**Tech Stack:** Python 3, pytest. No new dependencies.

## Global Constraints

- Every file in `core/` must remain runnable both as a script and as an installed package module
  (see `core/MAP.md`'s sys.path bootstrap section) — but this task adds **no new
  cross-subpackage import**, so no bootstrap block is needed in `modules.py`.
- Storage/compute/data-quality module selection (`sources`, `storage_zones`, `transforms`,
  `data_quality` fields) is explicitly **out of scope** for this plan — those fields need real
  text understanding beyond a closed enumerable answer, per `requirements.py`'s own docstring
  reasoning, and stay on `match_modules()`'s free-text path.
- Never auto-apply a recommendation — output is for review only, same discipline as every
  existing selection path (`match_modules()`, `retrieve_grounding_examples()`).
- Bias toward fewer, correct recommendations over broad recall: a missed recommendation just
  means "human decides manually" (today's status quo); a wrong one is the exact bug this fixes.

---

### Task 1: `derive_module_ids()` in `core/generation/modules.py` — tests first

**Files:**
- Modify: `core/generation/modules.py` (add functions after `retrieve_grounding_examples`, before `module_dir`, around line 310)
- Test: `tests/test_modules.py` (add at end of file)

**Interfaces:**
- Consumes: `MODULES` (existing), `get_module(module_id)` (existing), `_tokens(text)` (existing, at line 243-244)
- Produces: `derive_module_ids(requirements_data: dict) -> list[dict]`, each dict shaped
  `{"module_id": str, "reason": str, "source_field": str}`. Later tasks (the CLI subcommand)
  consume this exact return shape.

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_modules.py`:

```python
def test_derive_module_ids_picks_athena_for_sql_consumption_not_redshift():
    data = {
        "data_pipeline": {
            "consumption": "ad-hoc SQL access for analysts over curated S3 data",
        },
        "non_functional": {
            "latency": "batch job, results needed within 4 hours",
        },
    }
    picks = modules.derive_module_ids(data)
    picked_ids = {p["module_id"] for p in picks}
    assert "query-athena" in picked_ids
    assert "consumption-redshift-serverless" not in picked_ids


def test_derive_module_ids_excludes_streaming_modules_for_batch_latency():
    # This is the exact live bug this plan fixes: match_modules() on a free-text sentence
    # containing "ingest" pulled in speed-layer-kinesis/ingest-firehose even for a plain
    # batch request. derive_module_ids reads the structured latency field instead and must
    # not recommend either streaming module when latency describes a batch cadence.
    data = {
        "data_pipeline": {
            "consumption": "ad-hoc SQL access for analysts over curated S3 data",
        },
        "non_functional": {
            "latency": "batch job, results needed within 4 hours",
        },
    }
    picks = modules.derive_module_ids(data)
    picked_ids = {p["module_id"] for p in picks}
    assert "speed-layer-kinesis" not in picked_ids
    assert "ingest-firehose" not in picked_ids


def test_derive_module_ids_picks_streaming_modules_for_real_time_latency():
    data = {
        "data_pipeline": {},
        "non_functional": {
            "latency": "sub-second streaming ingest, near real-time delivery required",
        },
    }
    picks = modules.derive_module_ids(data)
    picked_ids = {p["module_id"] for p in picks}
    assert "speed-layer-kinesis" in picked_ids
    assert "ingest-firehose" in picked_ids


def test_derive_module_ids_picks_redshift_for_high_concurrency_bi():
    data = {
        "data_pipeline": {
            "consumption": "BI dashboards at scale for many analysts, high concurrency",
        },
    }
    picks = modules.derive_module_ids(data)
    picked_ids = {p["module_id"] for p in picks}
    assert "consumption-redshift-serverless" in picked_ids
    assert "query-athena" not in picked_ids


def test_derive_module_ids_picks_mwaa_for_airflow_orchestration():
    data = {
        "data_pipeline": {
            "orchestration": "managed Apache Airflow for DAG scheduling",
        },
    }
    picks = modules.derive_module_ids(data)
    picked_ids = {p["module_id"] for p in picks}
    assert "orchestrator-mwaa" in picked_ids
    assert "orchestrator-stepfunctions" not in picked_ids


def test_derive_module_ids_picks_schema_registry_for_data_contracts():
    data = {
        "data_pipeline": {
            "catalog": "schema registry with enforced data contracts, Avro compatibility",
        },
    }
    picks = modules.derive_module_ids(data)
    picked_ids = {p["module_id"] for p in picks}
    assert "schema-registry-glue" in picked_ids


def test_derive_module_ids_skips_deferred_and_blank_fields():
    data = {
        "data_pipeline": {
            "consumption": "deferred: not decided yet, revisit after pilot",
            "orchestration": "",
            "catalog": "   ",
        },
        "non_functional": {
            "latency": "deferred: revisit after load testing",
        },
    }
    assert modules.derive_module_ids(data) == []


def test_derive_module_ids_reports_reason_and_source_field():
    data = {"data_pipeline": {"catalog": "schema registry with data contracts"}}
    picks = modules.derive_module_ids(data)
    assert len(picks) == 1
    assert picks[0]["module_id"] == "schema-registry-glue"
    assert picks[0]["source_field"] == "data_pipeline.catalog"
    assert "data_pipeline.catalog" in picks[0]["reason"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_modules.py -k derive_module_ids -v`
Expected: FAIL with `AttributeError: module 'modules' has no attribute 'derive_module_ids'` (8 failures)

- [ ] **Step 3: Implement `derive_module_ids()` and its helpers**

Insert into `core/generation/modules.py` immediately after the `retrieve_grounding_examples()`
function (which ends around line 308, right before `def module_dir(module_id):`):

```python
# Phase 7 Item 3 (docs/phase7_generation_engine_plan.md), scoped to requirements.py's own named
# carve-out (see that module's docstring): non_functional.latency and data_pipeline.consumption/
# orchestration/catalog are enumerable answers grill-me already gathers and nothing downstream
# reads today. Deliberately NOT storage/compute/data-quality (sources, storage_zones, transforms,
# data_quality) -- those need real text understanding beyond a closed enumerable answer and stay
# on match_modules()'s free-text path.
_ENUMERABLE_FIELD_MODULES = {
    "consumption": ["query-athena", "consumption-redshift-serverless"],
    "orchestration": ["orchestrator-mwaa", "orchestrator-stepfunctions"],
    "catalog": ["schema-registry-glue"],
}
_LATENCY_STREAMING_MODULES = ["speed-layer-kinesis", "ingest-firehose"]


def _is_deferred_or_blank(value):
    text = str(value or "").strip()
    return (not text) or text.lower().startswith("deferred")


def _matches_module(text, module_id):
    """Whole-phrase substring OR every one of a phrase's tokens present -- deliberately
    stricter than match_modules()'s any-single-token-overlap rule. A single shared common word
    (e.g. "analysts" alone matching "many analysts") is exactly the kind of false positive that
    caused match_modules() to recommend the wrong module on free text; this function only
    recommends a module into architecture_decision.json for human review, so biasing toward
    fewer, correct hits over broad recall is the right tradeoff here."""
    module = get_module(module_id)
    if module is None:
        return False
    text_tokens = _tokens(text)
    for phrase in module["satisfies"]:
        if phrase in text:
            return True
        phrase_tokens = _tokens(phrase)
        if phrase_tokens and phrase_tokens <= text_tokens:
            return True
    return False


def derive_module_ids(requirements_data):
    """Read exactly requirements.py's named-carve-out fields and recommend module ids --
    deterministic and explainable, never a keyword score against one accumulated free-text
    blob. Returns a list of {module_id, reason, source_field} dicts, for a human/agent to
    review into architecture_decision.json's `selected_modules` -- never auto-applied, same
    discipline as match_modules()'s own callers."""
    data_pipeline = (requirements_data or {}).get("data_pipeline") or {}
    non_functional = (requirements_data or {}).get("non_functional") or {}
    picks = []

    for field, candidate_ids in _ENUMERABLE_FIELD_MODULES.items():
        value = str(data_pipeline.get(field, ""))
        if _is_deferred_or_blank(value):
            continue
        text = value.lower()
        for module_id in candidate_ids:
            if _matches_module(text, module_id):
                picks.append({
                    "module_id": module_id,
                    "reason": f"data_pipeline.{field} = {value!r}",
                    "source_field": f"data_pipeline.{field}",
                })

    latency = str(non_functional.get("latency", ""))
    if not _is_deferred_or_blank(latency):
        text = latency.lower()
        for module_id in _LATENCY_STREAMING_MODULES:
            if _matches_module(text, module_id):
                picks.append({
                    "module_id": module_id,
                    "reason": f"non_functional.latency = {latency!r}",
                    "source_field": "non_functional.latency",
                })

    return picks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_modules.py -k derive_module_ids -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Run the full existing test_modules.py suite to confirm no regressions**

Run: `python -m pytest tests/test_modules.py -v`
Expected: all tests PASS (existing tests + the 8 new ones)

- [ ] **Step 6: Commit**

```bash
git add core/generation/modules.py tests/test_modules.py
git commit -m "$(cat <<'EOF'
feat: requirements-driven module preplan -- derive_module_ids()

Reads requirements.py's own named carve-out fields (non_functional.latency,
data_pipeline.consumption/orchestration/catalog -- enumerable, gathered, never read
downstream today) to recommend module ids deterministically, instead of keyword-scoring
one free-text blob. Fixes the live-demonstrated bug where match_modules() on "ingest data
from s3 and give me analytics" missed query-athena entirely and recommended
speed-layer-kinesis/ingest-firehose instead.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `preplan` CLI subcommand in `core/generation/modules.py`

**Files:**
- Modify: `core/generation/modules.py`'s `main()` function (currently at the end of the file,
  subparsers `list`/`validate`/`match`)
- Test: `tests/test_modules.py` (add at end of file)

**Interfaces:**
- Consumes: `derive_module_ids()` (Task 1)
- Produces: `modules.py preplan <requirements.json path>` CLI command, exit code 0 always
  (recommendations found or not — this is advisory output, never a failure state)

- [ ] **Step 1: Write the failing test**

First, add `import json` to the top of `tests/test_modules.py`, alongside the existing `import os`:

```python
import json
import os

import modules
```

Then add to the end of `tests/test_modules.py`:

```python
def test_main_preplan_prints_recommendations(tmp_path, capsys):
    req_path = tmp_path / "requirements.json"
    req_path.write_text(json.dumps({
        "data_pipeline": {"catalog": "schema registry with data contracts"},
    }), encoding="utf-8")

    exit_code = modules.main(["preplan", str(req_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "schema-registry-glue" in out
    assert "data_pipeline.catalog" in out


def test_main_preplan_reports_no_recommendations(tmp_path, capsys):
    req_path = tmp_path / "requirements.json"
    req_path.write_text(json.dumps({"data_pipeline": {}, "non_functional": {}}), encoding="utf-8")

    exit_code = modules.main(["preplan", str(req_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "no enumerable-field recommendations" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_modules.py -k main_preplan -v`
Expected: FAIL (argparse rejects the unknown `preplan` subcommand)

- [ ] **Step 3: Add the `preplan` subcommand to `main()`**

In `core/generation/modules.py`, find the `main()` function (search for `sub.add_parser("match")`)
and modify it:

```python
def main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Composable Terraform module registry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("validate")
    mp = sub.add_parser("match")
    mp.add_argument("requirements")
    pp = sub.add_parser(
        "preplan", help="recommend module ids from a requirements.json's enumerable fields")
    pp.add_argument("requirements_file")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for m in MODULES:
            print(f"{m['id']:<28} {m['category']:<14} {m['title']}")
        return 0
    if args.cmd == "validate":
        errs = validate_modules()
        if errs:
            print("module registry INVALID:")
            for e in errs:
                print(f"  - {e}")
            return 1
        print(f"module registry OK: {len(MODULES)} modules")
        return 0
    if args.cmd == "match":
        for m in match_modules(args.requirements):
            print(f"[{m['score']:>2}] {m['id']:<28} matched: {', '.join(m['matched'])}")
        return 0
    if args.cmd == "preplan":
        with open(args.requirements_file, encoding="utf-8") as f:
            data = json.load(f)
        picks = derive_module_ids(data)
        if not picks:
            print("[preplan] no enumerable-field recommendations "
                  "(fields blank/deferred, or no keyword hit)")
            return 0
        for p in picks:
            print(f"{p['module_id']:<28} <- {p['source_field']:<24} {p['reason']}")
        return 0
    return 1
```

This replaces the existing `main()` function body entirely (same function, same location at the
end of the file — only the subparser list and the dispatch `if` chain gain the `preplan` branch).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_modules.py -k main_preplan -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full test_modules.py suite**

Run: `python -m pytest tests/test_modules.py -v`
Expected: all tests PASS

- [ ] **Step 6: Manually verify against the real live bug case**

Run (writes and cleans up a scratch file in the repo root — do not commit it):
```bash
python -c "
import json
json.dump({
    'data_pipeline': {'consumption': 'ad-hoc SQL access for analysts over curated S3 data'},
    'non_functional': {'latency': 'batch job, results needed within 4 hours'},
}, open('_demo_requirements.json', 'w'))
"
python core/generation/modules.py preplan _demo_requirements.json
rm _demo_requirements.json
```
Expected output: a `query-athena` line, and no `speed-layer-kinesis`/`ingest-firehose` line —
the exact fix for the bug demonstrated live with `match_modules()` earlier.

- [ ] **Step 7: Commit**

```bash
git add core/generation/modules.py tests/test_modules.py
git commit -m "$(cat <<'EOF'
feat: add `preplan` CLI subcommand for requirements-driven module recommendations

python core/generation/modules.py preplan <requirements.json> prints derive_module_ids()'s
recommendations for human/agent review into architecture_decision.json -- never auto-applied.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## What this plan deliberately does not do

- Does not wire `derive_module_ids()` into `synthesizer.select_modules()` or
  `architecture_decision.py` automatically — recommendations are printed for review, same as
  `match_modules()`'s own CLI (`modules.py match`) has always been advisory, not load-bearing,
  until a human copies a pick into `architecture_decision.json`.
- Does not touch `sources`/`storage_zones`/`transforms`/`data_quality` — those remain on
  `match_modules()`'s free-text path, deliberately, per this plan's Global Constraints.
- Does not add architecture-topology research (AWS reference architectures, MCP grounding) —
  that remains a separate, later plan, blocked on independently verifying the MCP server names
  discussed earlier before they go into any committed file.
