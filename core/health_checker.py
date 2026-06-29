import os
import sys
import json
import datetime
import argparse
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import toolpath  # noqa: E402


def _aws():
    """Resolve the aws CLI via shared discovery (no hardcoded paths)."""
    return toolpath.find_tool("aws")


def check_aws_cli():
    aws = _aws()
    if not aws:
        return False, "AWS CLI not found on PATH."
    try:
        res = subprocess.run([aws, "--version"], capture_output=True, text=True, check=True)
        return True, res.stdout.strip()
    except Exception:
        return False, "AWS CLI not found or failed to execute."

def check_s3_bucket(bucket_name):
    aws = _aws()
    if not aws:
        return False, "AWS CLI not found on PATH."
    try:
        res = subprocess.run(
            [aws, "s3api", "head-bucket", "--bucket", bucket_name],
            capture_output=True, text=True, timeout=5
        )
        return res.returncode == 0, res.stderr.strip() if res.returncode != 0 else "Accessible"
    except Exception as e:
        return False, str(e)

def check_glue_job_status(job_name):
    aws = _aws()
    if not aws:
        return False, "AWS CLI not found on PATH."
    try:
        res = subprocess.run(
            [aws, "glue", "get-job-runs", "--job-name", job_name, "--max-items", "1", "--query", "JobRuns[0].[JobRunState,ErrorMessage]", "--output", "json"],
            capture_output=True, text=True, timeout=5
        )
        if res.returncode == 0 and res.stdout.strip():
            details = json.loads(res.stdout.strip())
            if details and len(details) > 0:
                state, err_msg = details[0][0], details[0][1]
                return state in ["SUCCEEDED", "RUNNING", "STARTING"], f"Last Run: {state} | Error: {err_msg}"
            return True, "No runs recorded yet"
        return False, res.stderr.strip() or "Unknown Error"
    except Exception as e:
        return False, str(e)

def run_health_checks(log_dir, bronze_bucket=None, silver_bucket=None, gold_bucket=None, job_1=None, job_2=None):
    os.makedirs(log_dir, exist_ok=True)
    report_file = os.path.join(log_dir, "health_report.json")
    
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report = {
        "timestamp": timestamp,
        "checks": {},
        "status": "HEALTHY"
    }

    # 1. AWS CLI Availability Check
    aws_ok, aws_detail = check_aws_cli()
    report["checks"]["aws_cli"] = {
        "status": "OK" if aws_ok else "FAILED",
        "detail": aws_detail
    }
    if not aws_ok:
        report["status"] = "DEGRADED"

    # 2. AWS Connection Check (sts get-caller-identity)
    if aws_ok:
        try:
            res = subprocess.run(
                [_aws(), "sts", "get-caller-identity", "--query", "Arn", "--output", "text"],
                capture_output=True, text=True, timeout=5
            )
            if res.returncode == 0:
                report["checks"]["aws_credentials"] = {
                    "status": "OK",
                    "detail": f"Authenticated as: {res.stdout.strip()}"
                }
            else:
                report["checks"]["aws_credentials"] = {
                    "status": "FAILED",
                    "detail": res.stderr.strip()
                }
                report["status"] = "UNHEALTHY"
        except Exception as e:
            report["checks"]["aws_credentials"] = {
                "status": "ERROR",
                "detail": str(e)
            }
            report["status"] = "UNHEALTHY"
            
        # 3. Live Storage Checks (S3 Buckets) — pass any subset of buckets to probe.
        for label, bucket in (("s3_bucket_1", bronze_bucket),
                              ("s3_bucket_2", silver_bucket),
                              ("s3_bucket_3", gold_bucket)):
            if bucket:
                s3_ok, s3_detail = check_s3_bucket(bucket)
                report["checks"][label] = {
                    "status": "OK" if s3_ok else "FAILED",
                    "detail": s3_detail,
                    "target": bucket,
                }
                if not s3_ok:
                    report["status"] = "DEGRADED"

        # 4. Glue Processing Checks — pass any subset of Glue jobs to probe.
        for label, job in (("glue_job_1", job_1), ("glue_job_2", job_2)):
            if job:
                job_ok, job_detail = check_glue_job_status(job)
                report["checks"][label] = {
                    "status": "OK" if job_ok else "DEGRADED",
                    "detail": job_detail,
                    "target": job,
                }
                if not job_ok:
                    report["status"] = "DEGRADED"
    else:
        report["checks"]["aws_credentials"] = {
            "status": "SKIPPED",
            "detail": "Skipped due to AWS CLI failure."
        }
        report["status"] = "UNHEALTHY"

    # Save report
    try:
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"[HEALTH] Diagnostic status: {report['status']}")
        return report["status"] == "HEALTHY"
    except Exception as e:
        print(f"[ERR] Failed to save health report: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live health diagnostics (AWS CLI + credentials + optional S3/Glue probes)")
    parser.add_argument("--log-dir", default=os.path.join(os.getcwd(), ".agents", "logs"), help="Path to logs directory")
    parser.add_argument("--bronze-bucket", help="An S3 bucket to probe (head-bucket)")
    parser.add_argument("--silver-bucket", help="A second S3 bucket to probe")
    parser.add_argument("--gold-bucket", help="A third S3 bucket to probe")
    parser.add_argument("--job-1", help="A Glue job to probe (latest run state)")
    parser.add_argument("--job-2", help="A second Glue job to probe")

    args = parser.parse_args()
    healthy = run_health_checks(
        args.log_dir,
        bronze_bucket=args.bronze_bucket, silver_bucket=args.silver_bucket,
        gold_bucket=args.gold_bucket, job_1=args.job_1, job_2=args.job_2,
    )
    sys.exit(0 if healthy else 1)
