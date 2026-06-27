"""
AWS implementation of CloudProvider — uses the AWS CLI credential chain (never
handles secrets itself). Cost Explorer / Cost Anomaly Detection / STS / tagging.
"""
import json
import datetime
import subprocess

from .base import CloudProvider


def run_aws(args, timeout=20):
    """Run an AWS CLI command (list form, no shell). Returns (ok, parsed_json_or_text, error)."""
    try:
        res = subprocess.run(["aws"] + args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, None, "AWS CLI not found. Install it and run `aws configure` / `aws sso login`."
    except subprocess.TimeoutExpired:
        return False, None, f"AWS CLI timed out after {timeout}s."
    if res.returncode != 0:
        return False, None, (res.stderr or "Unknown AWS CLI error").strip()
    out = res.stdout.strip()
    if not out:
        return True, None, ""
    try:
        return True, json.loads(out), ""
    except json.JSONDecodeError:
        return True, out, ""


def _days_ago(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


class AWSProvider(CloudProvider):
    name = "aws"

    def identity(self):
        ok, data, _ = run_aws(["sts", "get-caller-identity", "--output", "json"])
        if ok and isinstance(data, dict):
            return data.get("Account"), True
        return None, False

    def cost_by_service(self, months_back=6):
        start = (datetime.date.today().replace(day=1)
                 - datetime.timedelta(days=31 * months_back)).replace(day=1)
        end = datetime.date.today().isoformat()
        ok, data, err = run_aws([
            "ce", "get-cost-and-usage",
            "--time-period", f"Start={start.isoformat()},End={end}",
            "--granularity", "MONTHLY", "--metrics", "UnblendedCost",
            "--group-by", "Type=DIMENSION,Key=SERVICE", "--output", "json",
        ])
        if not ok:
            return {"ok": False, "error": err, "months": []}
        months = []
        for period in (data.get("ResultsByTime", []) if isinstance(data, dict) else []):
            by_service = {}
            for grp in period.get("Groups", []):
                amount = float(grp["Metrics"]["UnblendedCost"]["Amount"])
                if amount > 0:
                    by_service[grp["Keys"][0]] = amount
            months.append({"month": period["TimePeriod"]["Start"][:7],
                           "total": sum(by_service.values()), "by_service": by_service})
        return {"ok": True, "error": "", "months": months}

    def anomalies(self, days_back=60):
        ok, data, err = run_aws([
            "ce", "get-anomalies",
            "--date-interval",
            f"StartDate={_days_ago(days_back)},EndDate={datetime.date.today().isoformat()}",
            "--output", "json",
        ])
        if not ok:
            return None, err
        out = []
        for a in (data.get("Anomalies", []) if isinstance(data, dict) else []):
            svc = (a.get("RootCauses") or [{}])[0].get("Service", "Unknown service")
            out.append({
                "id": a.get("AnomalyId", "-"),
                "service": svc,
                "date": (a.get("AnomalyStartDate", "") or "")[:10] or "-",
                "impact": float(a.get("Impact", {}).get("TotalImpact", 0) or 0),
                "raw": a,
            })
        return out, ""

    def owner(self, resource_hint):
        ok, data, _ = run_aws(["resourcegroupstaggingapi", "get-resources",
                               "--tags-per-page", "100", "--output", "json"])
        if not ok or not isinstance(data, dict):
            return None
        hint = (resource_hint or "").lower()
        for r in data.get("ResourceTagMappingList", []):
            arn = r.get("ResourceARN", "").lower()
            if hint and hint not in arn:
                continue
            tags = {t["Key"]: t["Value"] for t in r.get("Tags", [])}
            owner = tags.get("Owner") or tags.get("Team")
            if owner:
                return owner
        return None
