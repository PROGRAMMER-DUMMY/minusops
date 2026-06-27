import os
import sys
import json
import datetime
import argparse
import subprocess

def check_aws_cli():
    try:
        res = subprocess.run(["aws", "--version"], capture_output=True, text=True, check=True)
        return True, res.stdout.strip()
    except Exception:
        return False, "AWS CLI not found or failed to execute."

def check_s3_bucket(bucket_name):
    try:
        res = subprocess.run(
            ["aws", "s3api", "head-bucket", "--bucket", bucket_name],
            capture_output=True, text=True, timeout=5
        )
        return res.returncode == 0, res.stderr.strip() if res.returncode != 0 else "Accessible"
    except Exception as e:
        return False, str(e)

def check_glue_job_status(job_name):
    try:
        res = subprocess.run(
            ["aws", "glue", "get-job-runs", "--job-name", job_name, "--max-items", "1", "--query", "JobRuns[0].[JobRunState,ErrorMessage]", "--output", "json"],
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
    
    timestamp = datetime.datetime.utcnow().isoformat() + "Z"
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
                ["aws", "sts", "get-caller-identity", "--query", "Arn", "--output", "text"],
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
            
        # 3. Live Storage Checks (S3 Buckets)
        if bronze_bucket:
            s3_ok, s3_detail = check_s3_bucket(bronze_bucket)
            report["checks"][f"s3_bronze_bucket"] = {
                "status": "OK" if s3_ok else "FAILED",
                "detail": s3_detail
            }
            if not s3_ok:
                report["status"] = "DEGRADED"
                
        # 4. Glue Processing Checks
        if job_1:
            job_ok, job_detail = check_glue_job_status(job_1)
            report["checks"][f"glue_job_bronze_to_silver"] = {
                "status": "OK" if job_ok else "DEGRADED",
                "detail": job_detail
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
    parser = argparse.ArgumentParser(description="Health Diagnostics for Medallion Data Pipeline")
    parser.add_argument("--log-dir", default=os.path.join(os.getcwd(), ".agents", "logs"), help="Path to logs directory")
    parser.add_argument("--bronze-bucket", help="Name of S3 Bronze Bucket to test")
    parser.add_argument("--job-1", help="Name of Bronze-to-Silver Glue Job to test")
    
    args = parser.parse_args()
    healthy = run_health_checks(args.log_dir, bronze_bucket=args.bronze_bucket, job_1=args.job_1)
    sys.exit(0 if healthy else 1)
