"""
rego_gate.py -- G6 (docs/g6_scope.md), SEC-*/COST-* rules over real Terraform plan JSON via
OPA/Rego (policy/g6/rules.rego), evaluated in SHADOW MODE alongside the existing regex-over-
HCL rules in core/reporting/optimize_analyzer.py.

Shadow only: `evaluate()` never blocks anything on its own. It returns a verdict describing
whether the evaluation itself succeeded (fail-closed on every degradation case in the scope
doc's table) and, if so, the findings Rego produced. plan_gate.py's stage_plan() logs this
alongside the existing regex-based scan and computes symmetric divergence -- it does NOT
retire the regex path or let Rego's findings block anything. That only happens after the
16-module parity proof is reviewed and Phase 3 is explicitly closed, same as G2/G5.

Pure function, mirroring destructive_change_gate.classify()'s exact shape: takes an already-
parsed plan JSON dict (the same one plan_gate.py's _plan_json() already fetches for the G5
classifier -- no second `terraform show -json` subprocess call), returns a verdict dict. This
module does the actual `opa eval` invocation itself; it never talks to Terraform.
"""
import json
import os
import subprocess
import sys
import tempfile

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
import toolpath  # noqa: E402

POLICY_PATH = os.path.join(os.path.dirname(_CORE_DIR), "policy", "g6", "rules.rego")
QUERY = "data.minusops.g6.findings"


def _fail(reason, detail=""):
    """Every degradation case in docs/g6_scope.md's fail-closed table returns through here:
    evaluation itself could not produce a trustworthy verdict. Never silently treated as
    "zero findings" -- a caller (plan_gate.py) must log this distinctly from a real clean
    result, the same "genuinely nothing to check" vs. "something we can't verify" line G2
    and G5 both already draw."""
    return {"evaluation_failed": True, "reason": reason, "detail": detail, "findings": []}


def evaluate(plan_json, opa_bin=None, policy_path=None):
    """Evaluate G6's Rego rules against an already-parsed real plan JSON dict.

    Returns {"evaluation_failed": False, "findings": [...]} on success, or
    {"evaluation_failed": True, "reason": "...", "detail": "...", "findings": []} for any
    degradation case -- opa missing, malformed input, or an opa eval failure. `findings` is
    always a list (possibly containing "field_unresolved"-kind entries for unknown-until-
    apply values), never used to distinguish success from failure -- `evaluation_failed` is.

    `resource_changes` being entirely ABSENT from plan_json is not malformed -- confirmed live
    against real `terraform show -json` output: the key is omitted whenever there are zero
    managed-resource changes (a data-source-only plan, or a genuine no-op plan), never emitted
    as an empty list. Treating that as BLOCK would over-block the common data-source-only case
    (SEC-05/SEC-02 rules over aws_iam_policy_document, which live in `prior_state`, not
    `resource_changes`). Only a `resource_changes` key that's PRESENT but the wrong type is a
    genuine malformed-shape signal.
    """
    if not isinstance(plan_json, dict):
        return _fail("plan_malformed", "plan_json is not a dict")
    if "resource_changes" in plan_json and not isinstance(plan_json["resource_changes"], list):
        return _fail("plan_malformed", "resource_changes is present but not a list")

    opa_bin = opa_bin or toolpath.find_tool("opa")
    if not opa_bin:
        return _fail("opa_not_found", "opa binary not found on PATH")

    policy_path = policy_path or POLICY_PATH
    if not os.path.isfile(policy_path):
        return _fail("policy_not_found", f"no Rego policy at {policy_path}")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            json.dump(plan_json, tmp)
            tmp_path = tmp.name

        result = subprocess.run(
            [opa_bin, "eval", "--strict-builtin-errors", "-f", "json",
             "-i", tmp_path, "-d", policy_path, QUERY],
            capture_output=True, text=True,
        )
    except Exception as exc:
        return _fail("opa_invocation_failed", str(exc))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    if result.returncode != 0:
        return _fail("opa_eval_failed", (result.stderr or result.stdout or "").strip()[:2000])

    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return _fail("opa_output_malformed", str(exc))

    try:
        findings = parsed["result"][0]["expressions"][0]["value"]
    except (KeyError, IndexError, TypeError) as exc:
        # An empty result set (no findings at all) is a real, valid, non-degraded outcome --
        # OPA's own -f json shape for "the query evaluated but the set is empty" differs from
        # a genuine structural surprise, so this is deliberately conservative: anything that
        # doesn't match the expected shape is treated as a failure to parse, not silently
        # read as "clean." A real empty-findings run is exercised and asserted explicitly in
        # tests/test_rego_gate.py, not assumed to fall through here safely.
        return _fail("opa_result_shape_unexpected", str(exc))

    if not isinstance(findings, list):
        return _fail("opa_result_shape_unexpected", f"findings value is not a list: {type(findings)!r}")

    return {"evaluation_failed": False, "findings": findings}


def evaluate_dir(dir_):
    """Convenience wrapper for standalone/CLI use: fetches the plan JSON itself via
    `terraform show -json` (does not reuse plan_gate.py's own fetch -- that's the caller's
    job when already holding a parsed plan). Not used by plan_gate.py's real wiring."""
    tf = toolpath.find_tool("terraform")
    if not tf:
        return _fail("terraform_not_found", "terraform binary not found on PATH")
    result = subprocess.run([tf, f"-chdir={dir_}", "show", "-json", "tfplan"],
                             capture_output=True, text=True)
    if result.returncode != 0:
        return _fail("terraform_show_failed", (result.stderr or "").strip()[:2000])
    try:
        plan_json = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return _fail("plan_malformed", str(exc))
    return evaluate(plan_json)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="G6 Rego evaluation over a real Terraform plan (shadow mode only)")
    ap.add_argument("--dir", required=True, help="directory with a saved tfplan")
    args = ap.parse_args(argv)

    result = evaluate_dir(args.dir)
    print(json.dumps(result, indent=2))
    return 1 if result["evaluation_failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
