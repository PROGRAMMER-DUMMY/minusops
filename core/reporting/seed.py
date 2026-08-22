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

The hops are independent failure modes, in the order they break:

  1. bronze_ingestion        -- Bronze is empty, so nothing downstream can be true
  2. spark_glue_etl          -- the job exits on missing arguments, or 403s when it writes
  3. great_expectations_dq   -- the suite never ran, or it ran and reported failures
  4. quarantine_verification -- malformed rows were dropped instead of quarantined
  5. athena_serving_query    -- Athena has no catalog database, or the table has no rows

A late hop failing after the earlier ones pass is a real finding, not a flaky test. Hop 5
failing means the transform ran and produced nothing queryable; hop 4 failing means Gold looks
clean because rows went missing rather than because they were valid.

TWO ENTRY POINTS, ONE SET OF HOPS. `seed()` is the original three-step proof and keeps its
contract for `minusctl seed`. `prove_pipeline()` (PRD-ARCH-2026-007, FR-01) runs all five and
writes `reports/<plan-hash>/proving_report.json`. Both call the same `_upload`/`_run_job`/
`_query` primitives, so there is one implementation of each hop, not two.

Hop 3 does NOT import Great Expectations. GE runs inside the Glue Python-shell job that
`modules/dq-great-expectations` deploys, which is where the data is; this harness starts that
job and reads the validation-result JSON it wrote. Adding GE (and pandas, and SQLAlchemy) to a
control plane whose base install has no runtime dependencies would be a heavy price for
assertions that already run server-side.

"Signed" means what it means elsewhere in this repo: a SHA-256 over the canonical payload, so
an edited hop no longer matches its own digest. It is tamper-EVIDENT, not authenticated -- it
proves the file changed, not who changed it.

Depends on: core/governance/approval.py, core/reporting/toolpath.py
Shells out to: `terraform output -json` (read-only), and the `aws` CLI in **mutating**
    ways under `--execute`: `s3 cp` (writes an object), `glue start-job-run` (starts a
    billable job), `athena start-query-execution` (runs a query). Polling calls
    (`glue get-job-run`, `athena get-query-execution|get-query-results`) are read-only.
    Without `--execute` nothing is sent to AWS except `terraform output`.
Used by: core/reporting/minusctl.py (`minusctl seed`, `minusctl prove --execute`),
    tests/test_seed_adopt.py, tests/test_proving_harness.py
"""
import argparse
import datetime
import hashlib
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


# --- The 5-hop proving harness (PRD-ARCH-2026-007, FR-01) -----------------------------

HOP_NAMES = ("bronze_ingestion", "spark_glue_etl", "great_expectations_dq",
             "quarantine_verification", "athena_serving_query")

# Outputs the five-hop proof needs that the three-hop proof did not. A stack without the
# dq-great-expectations module cannot prove hops 3 and 4, and running the other three and
# calling the result PASS is exactly the false green this harness exists to prevent.
FIVE_HOP_OUTPUTS = ("dq_job_name", "dq_results_bucket", "quarantine_bucket")

REPORT_FILENAME = "proving_report.json"
_DIGEST_FIELD = "payload_sha256"


class _Hop:
    """One hop's outcome. A FAIL carries the reason verbatim -- the AWS error message IS the
    finding, and paraphrasing it loses the diagnosis."""

    def __init__(self, number, name):
        self.number, self.name = number, name
        self.started = time.monotonic()
        self.fields = {}

    def done(self, status, detail, **fields):
        return dict(hop=self.number, name=self.name, status=status, detail=detail,
                    latency_seconds=round(time.monotonic() - self.started, 3), **fields)


def _hop_ingest(planned, fixture_path, records, malformed):
    hop = _Hop(1, "bronze_ingestion")
    try:
        _upload(planned["bronze"], fixture_path)
    except SeedError as exc:
        return hop.done("FAIL", str(exc), records_injected=0)
    return hop.done("PASS", f"s3://{planned['bronze']}/data/sample.json",
                    target=f"s3://{planned['bronze']}/events/",
                    records_injected=records, malformed_injected=malformed)


def _hop_transform(planned):
    hop = _Hop(2, "spark_glue_etl")
    try:
        outcome = _run_job(planned["job_name"])
    except SeedError as exc:
        return hop.done("FAIL", str(exc), job_name=planned["job_name"])
    return hop.done("PASS", outcome["detail"], job_name=planned["job_name"])


def _hop_data_quality(planned):
    """Start the DQ job, then read what its Great Expectations run actually asserted.

    The job's exit status alone is not the answer: a suite that runs to completion and
    reports three failed expectations is a working suite describing a broken pipeline."""
    hop = _Hop(3, "great_expectations_dq")
    try:
        _run_job(planned["dq_job_name"])
    except SeedError as exc:
        return hop.done("FAIL", str(exc), assertions_passed=0, assertions_failed=0)

    stats, err = _read_validation_result(planned["dq_results_bucket"])
    if stats is None:
        # No result document means the suite never wrote one. Unknown is not success.
        return hop.done("FAIL", f"no validation result in s3://{planned['dq_results_bucket']}/: {err}",
                        assertions_passed=0, assertions_failed=0)
    passed = int(stats.get("successful_expectations") or 0)
    failed = int(stats.get("unsuccessful_expectations") or 0)
    if failed:
        return hop.done("FAIL", f"{failed} expectation(s) failed against Silver/Gold",
                        assertions_passed=passed, assertions_failed=failed)
    return hop.done("PASS", f"{passed} expectation(s) passed",
                    assertions_passed=passed, assertions_failed=failed)


def _read_validation_result(bucket):
    """Latest GE validation-result JSON from the results bucket, as its `statistics` dict."""
    ok, listing, err = _aws(["s3api", "list-objects-v2", "--bucket", bucket,
                             "--prefix", "validations/", "--output", "json"])
    if not ok:
        return None, err
    keys = [item.get("Key") for item in (listing or {}).get("Contents") or []
            if str(item.get("Key", "")).endswith(".json")]
    if not keys:
        return None, "no validations/*.json objects"
    ok, doc, err = _aws(["s3", "cp-to-stdout", f"s3://{bucket}/{sorted(keys)[-1]}", "-"])
    if not ok or not isinstance(doc, dict):
        return None, err or "validation result was not JSON"
    return doc.get("statistics") or {}, ""


def _hop_quarantine(planned, records, malformed, gold_rows):
    """Every injected record must be accounted for: valid rows in Gold, malformed rows in
    quarantine. A transform that DROPS malformed rows leaves Gold looking clean and the
    count looking plausible, which is the failure this hop exists to catch."""
    hop = _Hop(4, "quarantine_verification")
    quarantined, err = _count_quarantined(planned["quarantine_bucket"])
    if quarantined is None:
        return hop.done("FAIL", f"could not read s3://{planned['quarantine_bucket']}/: {err}",
                        clean_records_routed_gold=gold_rows, malformed_records_quarantined=0)

    fields = dict(clean_records_routed_gold=gold_rows,
                  malformed_records_quarantined=quarantined)
    if malformed and not quarantined:
        return hop.done("FAIL",
                        f"{malformed} malformed record(s) were injected and none were "
                        "quarantined: validation either never ran or passed rows it should "
                        "have caught", **fields)
    lost = records - gold_rows - quarantined
    if lost:
        return hop.done("FAIL",
                        f"{records} record(s) injected but {gold_rows} reached Gold and "
                        f"{quarantined} were quarantined -- {lost} unaccounted for", **fields)
    return hop.done("PASS", f"{gold_rows} to Gold, {quarantined} quarantined, none lost",
                    **fields)


def _count_quarantined(bucket):
    ok, listing, err = _aws(["s3", "ls", f"s3://{bucket}/", "--recursive"])
    if not ok:
        return None, err
    lines = [line for line in str(listing or "").splitlines() if line.strip()]
    return len(lines), ""


def _hop_serving(planned, table):
    hop = _Hop(5, "athena_serving_query")
    query = f'SELECT count(*) AS row_count FROM "{planned["database"]}"."{table}"'
    try:
        outcome = _query(planned["database"], planned["workgroup"], table)
    except SeedError as exc:
        return hop.done("FAIL", str(exc), query=query, rows_returned=0)
    return hop.done("PASS", outcome["detail"], query=query,
                    rows_returned=_leading_int(outcome["detail"]))


def _leading_int(text):
    head = str(text).split(" ", 1)[0]
    return int(head) if head.isdigit() else 0


def _sign(report):
    """SHA-256 over the canonical payload, excluding the digest field itself."""
    payload = {k: v for k, v in report.items() if k != _DIGEST_FIELD}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_report(report):
    """True when the report still matches its own digest. Tamper-evident, not authenticated."""
    if not isinstance(report, dict) or not report.get(_DIGEST_FIELD):
        return False
    return _sign(report) == report[_DIGEST_FIELD]


def _write_report(report, reports_dir, plan_hash):
    directory = os.path.join(reports_dir, plan_hash or "unbound")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, REPORT_FILENAME)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    return path


def prove_pipeline(tf_dir, fixture_path, table="customer_gold", execute=False,
                   approval_mode="gatekeeper", records=1000, malformed=0,
                   run_name=None, plan_hash=None, reports_dir=None):
    """
    Plan (default) or execute the five-hop end-to-end proof.

    `records` and `malformed` describe what the fixture injects; hop 4 uses them to prove
    nothing was lost between Bronze and Gold. They are declared rather than counted from the
    fixture because the fixture is whatever the operator points at.

    Returns {"executed", "ok", "status", "plan", "hops", "total_latency_seconds",
    "report_path", "error"}.
    """
    outputs = read_outputs(tf_dir)
    planned = plan_steps(outputs, fixture_path)
    planned["dq_job_name"] = outputs.get("dq_job_name")
    planned["dq_results_bucket"] = outputs.get("dq_results_bucket")
    planned["quarantine_bucket"] = outputs.get("quarantine_bucket")
    planned["hops"] = [{"hop": i + 1, "name": name} for i, name in enumerate(HOP_NAMES)]

    result = {"executed": False, "ok": True, "status": "PLANNED", "plan": planned,
              "hops": [], "total_latency_seconds": 0.0, "report_path": None, "error": None}

    missing = list(planned["missing_outputs"]) + [
        name for name in FIVE_HOP_OUTPUTS if not planned.get(name)]
    if missing:
        result["ok"] = False
        result["status"] = "REFUSED"
        result["error"] = (
            "the stack does not expose " + ", ".join(missing)
            + " -- a five-hop proof needs the data-quality module, so this stack can only be "
              "proven three hops deep via `minusctl seed`")
        return result
    if not execute:
        return result

    if not os.path.exists(fixture_path):
        result["ok"] = False
        result["status"] = "REFUSED"
        result["error"] = f"fixture not found: {fixture_path}"
        return result

    # One approval for the whole sequence, naming every side effect, rather than five
    # prompts an operator learns to click through.
    authorised = approval.request_approval(
        "prove-pipeline",
        f"upload {fixture_path} to s3://{planned['bronze']}/data/, start Glue job "
        f"{planned['job_name']}, start data-quality job {planned['dq_job_name']}, list "
        f"s3://{planned['quarantine_bucket']}/, and run one Athena count against "
        f"{planned['database']}.{table}",
        mode=approval_mode)
    if not authorised:
        result["ok"] = False
        result["status"] = "REFUSED"
        result["error"] = "approval denied; nothing was uploaded or started"
        return result

    result["executed"] = True
    hops = result["hops"]
    # Sequential and short-circuiting: querying Gold after the transform failed returns a
    # stale-data answer that reads as success.
    for run_hop in (
        lambda: _hop_ingest(planned, fixture_path, records, malformed),
        lambda: _hop_transform(planned),
        lambda: _hop_data_quality(planned),
        lambda: _hop_serving(planned, table),
    ):
        hop = run_hop()
        hops.append(hop)
        if hop["status"] != "PASS":
            break
    else:
        # Hop 4 is evaluated last because it needs hop 5's Gold count, then re-inserted in
        # its declared position so the report reads in pipeline order.
        gold_rows = hops[-1].get("rows_returned", 0)
        hops.insert(3, _hop_quarantine(planned, records, malformed, gold_rows))

    result["ok"] = all(hop["status"] == "PASS" for hop in hops)
    result["status"] = "PASS" if result["ok"] else "FAIL"
    result["total_latency_seconds"] = round(
        sum(hop["latency_seconds"] for hop in hops), 3)
    if not result["ok"]:
        result["error"] = next(h["detail"] for h in hops if h["status"] != "PASS")

    if reports_dir:
        report = {
            "run_name": run_name,
            "proven_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "plan_hash": plan_hash,
            "status": result["status"],
            "total_latency_seconds": result["total_latency_seconds"],
            "hops": hops,
        }
        report[_DIGEST_FIELD] = _sign(report)
        result["report_path"] = _write_report(report, reports_dir, plan_hash)
    return result


def format_proof(result):
    """ASCII only (NFR-01): these land in tickets and in terminals that render an emoji as a
    replacement box."""
    lines = []
    if not result["executed"]:
        if result["error"]:
            lines.append(f"[REFUSED] {result['error']}")
            return "\n".join(lines)
        lines.append("minusctl prove - PLAN ONLY, nothing was sent to AWS. Re-run with --execute.")
        lines.append("")
        for hop in result["plan"]["hops"]:
            lines.append(f"  hop {hop['hop']}  {hop['name']}")
        return "\n".join(lines)

    for hop in result["hops"]:
        marker = "OK " if hop["status"] == "PASS" else "ERR"
        lines.append(f"[{marker}] hop {hop['hop']} {hop['name']:<24} "
                     f"{hop['latency_seconds']:>8.3f}s  {hop['detail']}")
    lines.append("")
    lines.append(f"{result['status']} in {result['total_latency_seconds']}s "
                 f"across {len(result['hops'])} hop(s)")
    if result["report_path"]:
        lines.append(f"evidence: {result['report_path']}")
    return "\n".join(lines)


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
