"""
End-to-end pipeline proof: seed Bronze, run the job, query Gold.

A deployed stack that has never carried a byte is not a working pipeline, it is 30 resources
that plan cleanly. A stack can provision completely, report full readiness, and still have a
Glue job that crashes on its first argument, because nothing ever ran it.

**This is the one part of `minusctl` that mutates AWS.** Everything else in that CLI is
local-only by contract, so the default here is `plan`: it prints the exact commands and
changes nothing. `--execute` performs them, and every mutating step passes through
`approval.py` first (gatekeeper by default, fail-closed without a TTY) and lands in the audit
log. That keeps AGENTS.md rule 1 intact rather than carving an exception into it.

The three steps are three independent failure modes, in the order they break:

  1. upload   -- Bronze is empty, so nothing downstream can be true
  2. run job  -- the job exits on missing arguments, or 403s when it writes
  3. query    -- Athena has no catalog database, or the table has no rows

Step 3 failing after 1 and 2 pass is a real finding, not a flaky test: it means the transform
ran and produced nothing queryable.

Depends on: core/governance/approval.py, core/reporting/toolpath.py
Shells out to: `terraform output -json` (read-only), and the `aws` CLI in **mutating**
    ways under `--execute`: `s3 cp` (writes an object), `glue start-job-run` (starts a
    billable job), `athena start-query-execution` (runs a query). Polling calls
    (`glue get-job-run`, `athena get-query-execution|get-query-results`) are read-only.
    Without `--execute` nothing is sent to AWS except `terraform output`.
Used by: core/reporting/minusctl.py (`minusctl seed`), tests/test_seed_adopt.py
"""
import argparse
import json
import os
import subprocess
import sys
import time

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)

import approval  # noqa: E402
import toolpath  # noqa: E402

FIXTURE = os.path.join("tests", "fixtures", "sample.json")

# Long enough for a cold Glue start (a fresh JVM and Spark context is minutes, not seconds),
# short enough that a hung job does not hold a terminal all afternoon.
_JOB_POLL_SECONDS = 15
_JOB_TIMEOUT_SECONDS = 900
_QUERY_TIMEOUT_SECONDS = 180


class SeedError(RuntimeError):
    """A step failed in a way that means the pipeline does not work."""


def _aws(args, timeout=60):
    """Run one AWS CLI call. Returns (ok, parsed_or_text, stderr)."""
    binary = toolpath.find_tool("aws")
    if not binary:
        return False, None, "aws CLI not found on PATH"
    try:
        result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return False, None, str(exc)
    if result.returncode != 0:
        return False, None, (result.stderr or result.stdout or "").strip()
    text = (result.stdout or "").strip()
    try:
        return True, json.loads(text), ""
    except ValueError:
        return True, text, ""


def read_outputs(tf_dir):
    """Terraform outputs for the run, as a flat dict.

    Read from `terraform output` rather than reconstructed from name_prefix: the bucket names
    contain the AWS account id and the run hash, so re-deriving them by string surgery is
    exactly the kind of guess that seeds the wrong bucket and reports success.
    """
    binary = toolpath.find_tool("terraform")
    if not binary:
        raise SeedError("terraform not found on PATH; cannot read the stack outputs")
    try:
        result = subprocess.run([binary, f"-chdir={tf_dir}", "output", "-json"],
                                capture_output=True, text=True, timeout=120)
    except Exception as exc:
        raise SeedError(f"terraform output failed: {exc}")
    if result.returncode != 0:
        raise SeedError("terraform output failed -- has this stack been applied? "
                        + (result.stderr or "").strip()[:400])
    try:
        raw = json.loads(result.stdout or "{}")
    except ValueError as exc:
        raise SeedError(f"terraform output was not JSON: {exc}")
    return {key: value.get("value") for key, value in raw.items()}


def plan_steps(outputs, fixture_path):
    """The exact commands `--execute` would run. Printed in `plan` mode so an operator can
    read, copy, or refuse them before anything touches AWS."""
    bronze = (outputs.get("bucket_names") or {}).get("bronze")
    jobs = outputs.get("glue_job_names") or {}
    job_name = next(iter(jobs.values()), None) if isinstance(jobs, dict) else None
    workgroup = outputs.get("athena_workgroup")
    database = outputs.get("glue_catalog_database")

    missing = [name for name, value in (("bucket_names.bronze", bronze),
                                        ("glue_job_names", job_name),
                                        ("athena_workgroup", workgroup),
                                        ("glue_catalog_database", database)) if not value]
    steps = [
        {"step": "upload", "target": bronze,
         "command": ["s3", "cp", fixture_path, f"s3://{bronze}/data/sample.json"]},
        {"step": "run_job", "target": job_name,
         "command": ["glue", "start-job-run", "--job-name", str(job_name)]},
        {"step": "query", "target": f"{database} via {workgroup}",
         "command": ["athena", "start-query-execution",
                     "--query-string", f"SELECT count(*) AS rows FROM \"{database}\".\"customer_gold\"",
                     "--work-group", str(workgroup)]},
    ]
    return {"steps": steps, "missing_outputs": missing,
            "bronze": bronze, "job_name": job_name,
            "workgroup": workgroup, "database": database}


def _upload(bronze, fixture_path):
    ok, _, err = _aws(["s3", "cp", fixture_path, f"s3://{bronze}/data/sample.json"])
    if not ok:
        raise SeedError(f"upload to s3://{bronze}/data/ failed: {err}")
    return {"step": "upload", "ok": True, "detail": f"s3://{bronze}/data/sample.json"}


def _run_job(job_name):
    ok, started, err = _aws(["glue", "start-job-run", "--job-name", job_name, "--output", "json"])
    if not ok:
        raise SeedError(f"could not start Glue job {job_name}: {err}")
    run_id = (started or {}).get("JobRunId")

    deadline = time.time() + _JOB_TIMEOUT_SECONDS
    state = "STARTING"
    while time.time() < deadline:
        time.sleep(_JOB_POLL_SECONDS)
        ok, doc, err = _aws(["glue", "get-job-run", "--job-name", job_name,
                             "--run-id", run_id, "--output", "json"])
        if not ok:
            raise SeedError(f"could not poll Glue job run {run_id}: {err}")
        run = (doc or {}).get("JobRun") or {}
        state = run.get("JobRunState", "UNKNOWN")
        if state in ("SUCCEEDED", "FAILED", "TIMEOUT", "STOPPED"):
            if state != "SUCCEEDED":
                # The error message IS the finding, so it is surfaced verbatim: SystemExit
                # means the job's paths were never wired; AccessDenied means its role
                # cannot write where it was told to.
                raise SeedError(f"Glue job {job_name} finished {state}: "
                                + (run.get("ErrorMessage") or "(no error message)"))
            return {"step": "run_job", "ok": True, "detail": f"{job_name} run {run_id} SUCCEEDED"}
    raise SeedError(f"Glue job {job_name} still {state} after {_JOB_TIMEOUT_SECONDS}s")


def _query(database, workgroup, table):
    sql = f'SELECT count(*) AS row_count FROM "{database}"."{table}"'
    ok, started, err = _aws(["athena", "start-query-execution", "--query-string", sql,
                             "--work-group", workgroup, "--output", "json"])
    if not ok:
        raise SeedError(f"could not start the Athena query: {err}")
    query_id = (started or {}).get("QueryExecutionId")

    deadline = time.time() + _QUERY_TIMEOUT_SECONDS
    while time.time() < deadline:
        time.sleep(3)
        ok, doc, err = _aws(["athena", "get-query-execution",
                             "--query-execution-id", query_id, "--output", "json"])
        if not ok:
            raise SeedError(f"could not poll the Athena query: {err}")
        status = ((doc or {}).get("QueryExecution") or {}).get("Status") or {}
        state = status.get("State")
        if state == "SUCCEEDED":
            ok, results, err = _aws(["athena", "get-query-results",
                                     "--query-execution-id", query_id, "--output", "json"])
            if not ok:
                raise SeedError(f"could not read the Athena results: {err}")
            rows = ((results or {}).get("ResultSet") or {}).get("Rows") or []
            # Row 0 is the header; a result set with only a header means the query ran
            # against a table that exists but holds nothing.
            value = rows[1]["Data"][0].get("VarCharValue") if len(rows) > 1 else "0"
            if str(value) == "0":
                raise SeedError(
                    f'"{database}"."{table}" is queryable but empty: the transform ran and '
                    "produced no rows. That is a real failure, not a slow write.")
            return {"step": "query", "ok": True, "detail": f"{value} row(s) in {database}.{table}"}
        if state in ("FAILED", "CANCELLED"):
            raise SeedError(f"Athena query {state}: "
                            + (status.get("StateChangeReason") or "(no reason given)"))
    raise SeedError(f"Athena query {query_id} did not finish in {_QUERY_TIMEOUT_SECONDS}s")


def seed(tf_dir, fixture_path, table="customer_gold", execute=False, approval_mode="gatekeeper"):
    """Plan (default) or execute the three-step end-to-end proof.

    Returns {"executed": bool, "ok": bool, "plan": {...}, "results": [...], "error": str|None}.
    """
    outputs = read_outputs(tf_dir)
    planned = plan_steps(outputs, fixture_path)
    result = {"executed": False, "ok": True, "plan": planned, "results": [], "error": None}

    if planned["missing_outputs"]:
        result["ok"] = False
        result["error"] = ("the stack does not expose " + ", ".join(planned["missing_outputs"])
                           + " -- it is missing the modules this proof needs, or has not been "
                             "applied")
        return result
    if not execute:
        return result

    if not os.path.exists(fixture_path):
        result["ok"] = False
        result["error"] = f"fixture not found: {fixture_path}"
        return result

    # One approval for the whole sequence, naming every side effect, rather than three
    # prompts an operator learns to click through.
    authorised = approval.request_approval(
        "seed-pipeline",
        f"upload {fixture_path} to s3://{planned['bronze']}/data/, start Glue job "
        f"{planned['job_name']}, and run one Athena count against "
        f"{planned['database']}.{table}",
        mode=approval_mode)
    if not authorised:
        result["ok"] = False
        result["error"] = "approval denied; nothing was uploaded or started"
        return result

    result["executed"] = True
    try:
        for step in (lambda: _upload(planned["bronze"], fixture_path),
                     lambda: _run_job(planned["job_name"]),
                     lambda: _query(planned["database"], planned["workgroup"], table)):
            result["results"].append(step())
    except SeedError as exc:
        result["ok"] = False
        result["error"] = str(exc)
    return result


def format_result(result):
    plan = result["plan"]
    lines = []
    if not result["executed"]:
        lines.append("minusctl seed - PLAN ONLY, nothing was sent to AWS. Re-run with --execute.")
        lines.append("")
        for step in plan["steps"]:
            lines.append(f"  {step['step']:<8} aws " + " ".join(step["command"]))
        lines.append("")
    for step in result["results"]:
        lines.append(f"[OK]  {step['step']}: {step['detail']}")
    if result["error"]:
        lines.append(f"[ERR] {result['error']}")
    elif result["executed"]:
        lines.append("")
        lines.append("End-to-end proven: data landed, the job ran, and Gold returned rows.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Seed Bronze and prove the pipeline end to end.")
    ap.add_argument("--dir", required=True, help="Terraform directory of an APPLIED stack")
    ap.add_argument("--fixture", default=None,
                    help=f"sample rows to upload (default <run>/{FIXTURE})")
    ap.add_argument("--table", default="customer_gold", help="Gold table to count")
    ap.add_argument("--execute", action="store_true",
                    help="actually perform the uploads and job runs (routes through approval.py)")
    ap.add_argument("--approval-mode", default="gatekeeper", choices=["gatekeeper", "auto-approve"])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    fixture = args.fixture or os.path.join(os.path.dirname(os.path.abspath(args.dir)), FIXTURE)
    try:
        result = seed(args.dir, fixture, table=args.table, execute=args.execute,
                      approval_mode=args.approval_mode)
    except SeedError as exc:
        print(f"[seed] REFUSED - {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else format_result(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
