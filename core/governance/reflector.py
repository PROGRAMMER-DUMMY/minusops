"""
Stage Reflector -- an independent second look at a run, at each stage boundary (MINUS-129).

The failure this exists for: the agent that composed a stack is the worst possible auditor of
it. It already believes its own wiring is right, so it reports success against its own
intentions rather than against the files on disk. The 2026-08-17 run passed every self-check
it ran and still shipped a Glue job that could not write to Silver.

So every gate here **re-derives from artifacts**, never from a claim:

  G1 Scope      requirements.json volume vs. the compute module actually in main.tf
  G2 Wiring     module blocks in main.tf vs. the inputs each module declares
  G3 Security   optimize_analyzer's SEC-* findings against the composed HCL
  G4 Cost       BCM evidence on disk vs. the budget stated in requirements.json
  G5 Plan hash  a recorded, directory-bound plan hash bound to an approval

`blocked` is reserved for a gate that found a concrete defect. A gate that could not run --
no plan yet, no BCM evidence yet -- reports `unknown`, which is NOT a pass. Conflating "I
checked and it is fine" with "I could not check" is how a circuit breaker becomes decoration.

Read-only. Runs no cloud calls, mutates nothing, and never edits the run it inspects.
"""
import json
import os
import re
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)

import modules as module_registry  # noqa: E402
import optimize_analyzer  # noqa: E402
import requirements as reqgate  # noqa: E402

PASS, BLOCKED, UNKNOWN = "pass", "blocked", "unknown"

GATES = ("G1_scope", "G2_wiring", "G3_security", "G4_cost", "G5_plan_hash")

_MODULE_BLOCK_RE = re.compile(r'module\s+"([A-Za-z0-9_]+)"\s*\{', re.M)


def _result(gate, status, detail, evidence=None):
    return {"gate": gate, "status": status, "detail": detail, "evidence": evidence or {}}


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _main_tf(tf_dir):
    path = os.path.join(tf_dir, "main.tf")
    try:
        return open(path, encoding="utf-8").read()
    except OSError:
        return ""


def _module_blocks(hcl):
    """label -> block body, for every `module "label" { ... }` in the composed root.

    Brace-counting rather than a regex for the body: module blocks nest (dynamic blocks,
    object-typed inputs), and a non-greedy `.*?\\}` stops at the first inner close.
    """
    blocks = {}
    for match in _MODULE_BLOCK_RE.finditer(hcl):
        depth, i = 1, match.end()
        while i < len(hcl) and depth:
            if hcl[i] == "{":
                depth += 1
            elif hcl[i] == "}":
                depth -= 1
            i += 1
        blocks[match.group(1)] = hcl[match.end():i - 1]
    return blocks


# --- G1: scope --------------------------------------------------------------------------

def gate_scope(run_root, tf_dir):
    """The declared volume and the compute engine on disk must agree.

    A 10 TB/day requirement composed onto a 2-worker Glue job is the mismatch that surfaces
    as a job that never finishes, weeks after approval.
    """
    spec = _read_json(os.path.join(run_root, reqgate.FILENAME))
    if not spec:
        return _result(GATES[0], UNKNOWN, "no requirements.json: nothing to check the scope against")

    daily_gb, source = reqgate.parse_daily_gb(spec)
    if not daily_gb:
        return _result(GATES[0], UNKNOWN,
                       "data_volume is undeclared or unparseable, so no compute tier can be "
                       "checked against it", {"data_volume": source})

    labels = set(_module_blocks(_main_tf(tf_dir)))
    present = {m for m in ("compute-glue-etl", "compute-emr-serverless", "compute-emr-ec2-spot")
               if m.replace("-", "_") in labels}
    if not present:
        # dbt-only (MINUS-120) is a legitimate no-compute-module composition.
        return _result(GATES[0], UNKNOWN,
                       "no catalog compute module in main.tf -- expected for a dbt-only "
                       "composition, unexpected otherwise",
                       {"daily_gb": daily_gb, "modules": sorted(labels)})

    expected = module_registry.compute_tier(daily_gb)["module_id"]
    if expected in present:
        return _result(GATES[0], PASS,
                       f"{daily_gb:g} GB/day matches {expected}",
                       {"daily_gb": daily_gb, "expected": expected})
    return _result(GATES[0], BLOCKED,
                   f"{daily_gb:g} GB/day (from {source!r}) calls for {expected}, but main.tf "
                   f"composes {', '.join(sorted(present))}",
                   {"daily_gb": daily_gb, "expected": expected, "found": sorted(present)})


# --- G2: cross-module wiring -------------------------------------------------------------

# Inputs whose absence is a runtime failure rather than a style problem, per module. Each was
# an actual production break: the Glue role could not write to Silver (MINUS-108), the job
# exited on missing paths (MINUS-109), Athena had no database (MINUS-110).
_REQUIRED_WIRING = {
    "compute_glue_etl": ("data_buckets", "kms_key_arn", "source_bucket", "target_bucket"),
    "query_athena": ("gold_bucket",),
    "dq_great_expectations": ("target_buckets",),
    "ingestion_dms": ("target_bucket",),
    "ingestion_sftp": ("target_bucket",),
}


def gate_wiring(run_root, tf_dir):
    """Every module that consumes another module's output must actually reference it.

    Checked by reading main.tf, not by trusting that the synthesizer wired what it meant to.
    A missing edge here is invisible in a plan -- the resources all create fine, and the
    pipeline fails on its first real run.
    """
    hcl = _main_tf(tf_dir)
    if not hcl:
        return _result(GATES[1], UNKNOWN, "no main.tf to inspect")

    blocks = _module_blocks(hcl)
    missing, unwired = [], []
    for label, required in _REQUIRED_WIRING.items():
        body = blocks.get(label)
        if body is None:
            continue
        for field in required:
            if not re.search(rf"^\s*{field}\s*=", body, re.M):
                missing.append(f"{label}.{field}")
            elif not re.search(rf"^\s*{field}\s*=.*module\.", body, re.M):
                # Set, but to a literal rather than another module's output. Legal, and
                # sometimes deliberate -- so it is reported, not blocked on.
                unwired.append(f"{label}.{field}")

    if missing:
        return _result(GATES[1], BLOCKED,
                       "module inputs required for the pipeline to run are absent: "
                       + ", ".join(sorted(missing)),
                       {"missing": sorted(missing), "literal_only": sorted(unwired)})
    return _result(GATES[1], PASS,
                   f"{len(blocks)} module blocks; every cross-module input present",
                   {"modules": sorted(blocks), "literal_only": sorted(unwired)})


# --- G3: security -------------------------------------------------------------------------

def gate_security(run_root, tf_dir):
    """Re-run the SEC scan rather than reading whatever a previous run recorded.

    The file count is reported alongside the verdict on purpose: "no findings" from a scan
    that read nothing looks identical to "no findings" from a clean stack, and only one of
    those is a pass.
    """
    if not os.path.isdir(tf_dir):
        return _result(GATES[2], UNKNOWN, "no Terraform directory to scan")

    scanned = sum(1 for root, dirs, files in os.walk(tf_dir)
                  for name in files if name.endswith(".tf")
                  and not any(part in optimize_analyzer.SKIP_DIRS for part in root.split(os.sep)))
    if not scanned:
        return _result(GATES[2], UNKNOWN, f"no .tf files under {tf_dir}: nothing was scanned")

    findings = optimize_analyzer.scan_hcl_files(tf_dir)
    sec = [f for f in findings if str(f.get("id", "")).startswith("SEC")]
    if sec:
        return _result(GATES[2], BLOCKED,
                       f"{len(sec)} SEC finding(s) across {scanned} .tf files: "
                       + ", ".join(sorted({f["id"] for f in sec})),
                       {"scanned_files": scanned,
                        "findings": [{"id": f["id"], "resource": f.get("resource")} for f in sec]})
    return _result(GATES[2], PASS, f"no SEC findings across {scanned} .tf files",
                   {"scanned_files": scanned, "other_findings": len(findings)})


# --- G4: cost -----------------------------------------------------------------------------

def _estimate_total(doc):
    """Monthly total out of a bcm-estimate.json, or None.

    The `estimate` block is the authoritative one: `create` is the workload estimate BEFORE
    the usage lines are attached and reads 0.0 for every stack. Preferring it would report a
    $0 forecast that passes any ceiling -- a silent false green in the one gate whose entire
    job is catching an unaffordable plan.
    """
    if not isinstance(doc, dict):
        return None
    for key in ("estimate", "create"):
        block = doc.get(key)
        if isinstance(block, dict) and block.get("totalCost") is not None:
            return float(block["totalCost"])
    for key in ("totalCost", "total_cost"):
        if doc.get(key) is not None:
            return float(doc[key])
    return None


def gate_cost(run_root, tf_dir):
    """A budget was stated; BCM evidence must exist and sit under it.

    No evidence is `unknown`, never `pass`: the whole point of the cost gate is that a number
    came from AWS, and "we did not ask" must not read the same as "it is affordable".
    """
    spec = _read_json(os.path.join(run_root, reqgate.FILENAME))
    budget, source = reqgate.parse_budget_usd(spec) if spec else (0, "")
    reports = os.path.join(run_root, "reports")
    estimates = []
    if os.path.isdir(reports):
        for entry in sorted(os.listdir(reports)):
            doc = _read_json(os.path.join(reports, entry, "bcm-estimate.json"))
            total = _estimate_total(doc)
            if total is not None:
                estimates.append((entry, total))

    if not budget:
        return _result(GATES[3], UNKNOWN,
                       "no budget ceiling stated in requirements, so no forecast can breach it")
    if not estimates:
        return _result(GATES[3], UNKNOWN,
                       f"budget ${budget:g}/mo stated ({source!r}) but no BCM evidence on disk "
                       "-- run bcm_pricing_calculator.py, do not assume",
                       {"budget_usd": budget})

    plan_hash, total = estimates[-1]
    if total > budget:
        return _result(GATES[3], BLOCKED,
                       f"BCM forecast ${total:g}/mo exceeds the stated ${budget:g}/mo ceiling",
                       {"budget_usd": budget, "forecast_usd": total, "plan_hash": plan_hash})
    return _result(GATES[3], PASS, f"BCM forecast ${total:g}/mo is under the ${budget:g}/mo ceiling",
                   {"budget_usd": budget, "forecast_usd": total, "plan_hash": plan_hash})


# --- G5: plan-hash binding ----------------------------------------------------------------

def gate_plan_hash(run_root, tf_dir):
    """A plan hash must exist for THIS directory, and be a real SHA-256 digest.

    This does not re-verify the hash -- plan_gate.py owns that and owns the apply. What it
    catches is the state the gate cannot see from inside itself: a run presented as ready to
    approve with no recorded plan at all.

    The record path comes from plan_gate's own `_pending_path()` rather than being rebuilt
    here. The layout is a sha256-of-canonical-dir key; re-deriving it would silently start
    reading the wrong file the day that changes.
    """
    try:
        import plan_gate
        pending_path = plan_gate._pending_path(tf_dir)
    except Exception as exc:  # plan_gate imports the whole governance stack
        return _result(GATES[4], UNKNOWN, f"could not resolve the plan-gate record path: {exc}")

    record = _read_json(pending_path)
    if not record:
        return _result(GATES[4], UNKNOWN,
                       "no plan-gate record for this Terraform directory -- plan before "
                       "approving", {"expected_at": pending_path})

    plan_hash = record.get("plan_hash") or ""
    if not re.fullmatch(r"[0-9a-f]{64}", plan_hash):
        return _result(GATES[4], BLOCKED,
                       f"recorded plan hash is not a SHA-256 digest: {plan_hash!r}",
                       {"plan_hash": plan_hash})
    recorded_dir = os.path.abspath(record.get("dir", ""))
    if recorded_dir != os.path.abspath(tf_dir):
        return _result(GATES[4], BLOCKED,
                       f"plan record is bound to {recorded_dir}, not {os.path.abspath(tf_dir)}",
                       {"recorded_dir": recorded_dir})
    return _result(GATES[4], PASS, f"plan hash {plan_hash[:12]}... bound to this directory",
                   {"plan_hash": plan_hash, "dir": record.get("dir")})


_GATE_FNS = (gate_scope, gate_wiring, gate_security, gate_cost, gate_plan_hash)


def reflect(run_root, tf_dir=None):
    """Run all five gates. Returns {"blocked", "gates", "summary"}.

    Every gate runs even after one blocks: an operator fixing three problems wants all three
    now, not one per round trip.
    """
    tf_dir = tf_dir or os.path.join(run_root, "terraform")
    gates = [fn(run_root, tf_dir) for fn in _GATE_FNS]
    counts = {status: sum(1 for g in gates if g["status"] == status)
              for status in (PASS, BLOCKED, UNKNOWN)}
    return {
        "blocked": counts[BLOCKED] > 0,
        "run_root": run_root,
        "terraform_dir": tf_dir,
        "gates": gates,
        "summary": counts,
    }


_MARK = {PASS: "[PASS] ", BLOCKED: "[BLOCK]", UNKNOWN: "[?]    "}


def format_result(result):
    lines = [f"Stage Reflector - {result['run_root']}", ""]
    for gate in result["gates"]:
        lines.append(f"{_MARK[gate['status']]} {gate['gate']}: {gate['detail']}")
    lines.append("")
    counts = result["summary"]
    lines.append(
        f"{counts[PASS]} pass / {counts[BLOCKED]} blocked / {counts[UNKNOWN]} unknown"
        + ("  -- unknown is not a pass; the gate could not run." if counts[UNKNOWN] else ""))
    return "\n".join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="Independent stage reflector: re-derive the 5 gates from artifacts on disk")
    ap.add_argument("--run-root", required=True, help="runs/<run-id>")
    ap.add_argument("--dir", default=None, help="Terraform directory (default <run-root>/terraform)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    result = reflect(args.run_root, args.dir)
    print(json.dumps(result, indent=2) if args.json else format_result(result))
    return 2 if result["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
