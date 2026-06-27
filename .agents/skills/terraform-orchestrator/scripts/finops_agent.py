"""
AWS FinOps Agent — live cost intelligence over the real account (no mock data).

Read / analysis path (safe, read-only AWS calls):
  --cost          Spend breakdown by service + month-over-month change   (aws ce get-cost-and-usage)
  --anomalies     List active cost anomalies                              (aws ce get-anomalies)
  --correlate     Root-cause anomalies via CloudTrail + tag ownership     (aws cloudtrail lookup-events,
                                                                           aws resourcegroupstaggingapi)

Action path (side effects — routed through the approval gate):
  --notify-slack  Post the latest anomaly summary to Slack
  --notify-jira   Prepare a Jira ticket payload for the latest anomaly
      paired with:  --approval-mode {gatekeeper, auto-approve}

The Cost Anomaly Detection monitor this reads from is provisioned by
aws-medallion-pipeline/cost_anomaly.tf. All AWS calls degrade gracefully if the
CLI is missing or credentials are not configured.
"""
import os
import sys
import json
import argparse
import datetime
import subprocess
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval import request_approval  # noqa: E402

LOG_DIR = os.path.join(os.getcwd(), ".agents", "logs")


def run_aws(args, timeout=20):
    """Run an AWS CLI command. Returns (ok, parsed_json_or_text, error_str)."""
    try:
        res = subprocess.run(["aws"] + args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, None, "AWS CLI not found. Install it and run `aws configure`."
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


# ---------------------------------------------------------------------------
# READ PATH
# ---------------------------------------------------------------------------
def fetch_cost_by_service(months_back=6):
    """
    Return structured spend data (no printing) so both the CLI and the dashboard
    can consume it: {"ok", "error", "months": [{"month", "total", "by_service": {}}]}.
    """
    start = (datetime.date.today().replace(day=1) - datetime.timedelta(days=31 * months_back)).replace(day=1)
    end = datetime.date.today().isoformat()
    ok, data, err = run_aws([
        "ce", "get-cost-and-usage",
        "--time-period", f"Start={start.isoformat()},End={end}",
        "--granularity", "MONTHLY",
        "--metrics", "UnblendedCost",
        "--group-by", "Type=DIMENSION,Key=SERVICE",
        "--output", "json",
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
        months.append({
            "month": period["TimePeriod"]["Start"][:7],
            "total": sum(by_service.values()),
            "by_service": by_service,
        })
    return {"ok": True, "error": "", "months": months}


def cmd_cost():
    """Print service-level spend for the trailing months, with month-over-month delta."""
    result = fetch_cost_by_service()
    if not result["ok"]:
        print(f"[COST] Could not retrieve Cost Explorer data: {result['error']}", file=sys.stderr)
        return False

    print("=" * 60)
    print("AWS SPEND BY SERVICE (Cost Explorer)")
    print("=" * 60)
    for m in result["months"]:
        print(f"\n{m['month']}  -  total ${m['total']:,.2f}")
        for service, amount in sorted(m["by_service"].items(), key=lambda r: r[1], reverse=True)[:8]:
            print(f"    {service:<32} ${amount:,.2f}")

    months = result["months"]
    if len(months) >= 2:
        prev, curr = months[-2]["total"], months[-1]["total"]
        delta = curr - prev
        pct = (delta / prev * 100) if prev else 0.0
        print("-" * 60)
        print(f"Month-over-month into {months[-1]['month']}: "
              f"{'+' if delta >= 0 else ''}${delta:,.2f} ({pct:+.1f}%)")
    print("=" * 60)
    return True


def get_anomalies(days_back=60):
    ok, data, err = run_aws([
        "ce", "get-anomalies",
        "--date-interval", f"StartDate={_days_ago(days_back)},EndDate={datetime.date.today().isoformat()}",
        "--output", "json",
    ])
    if not ok:
        return None, err
    return (data.get("Anomalies", []) if isinstance(data, dict) else []), ""


def cmd_anomalies():
    anomalies, err = get_anomalies()
    if anomalies is None:
        print(f"[ANOMALIES] Could not retrieve anomalies: {err}", file=sys.stderr)
        return False
    print("=" * 60)
    print("AWS COST ANOMALY DETECTION — ACTIVE ANOMALIES")
    print("=" * 60)
    if not anomalies:
        print("No anomalies detected in the lookback window.")
        return True
    for a in anomalies:
        impact = a.get("Impact", {})
        svc = (a.get("RootCauses") or [{}])[0].get("Service", "-")
        print(f"  {a.get('AnomalyId', '?')} | {a.get('AnomalyStartDate', '?')[:10]} | "
              f"service={svc} | impact=${impact.get('TotalImpact', 0):,.2f} "
              f"(score {a.get('AnomalyScore', {}).get('CurrentScore', 0)})")
    print("=" * 60)
    return True


def resolve_owner(service_hint):
    """Best-effort ownership via resource tags (Owner / Team)."""
    ok, data, _ = run_aws([
        "resourcegroupstaggingapi", "get-resources",
        "--tags-per-page", "100", "--output", "json",
    ])
    if not ok or not isinstance(data, dict):
        return None
    for r in data.get("ResourceTagMappingList", []):
        arn = r.get("ResourceARN", "")
        if service_hint and service_hint.lower() not in arn.lower():
            continue
        tags = {t["Key"]: t["Value"] for t in r.get("Tags", [])}
        owner = tags.get("Owner") or tags.get("Team")
        if owner:
            return {"resource": arn, "owner": owner, "tags": tags}
    return None


def cmd_correlate():
    """For each anomaly, find the CloudTrail events that likely caused it + the owner."""
    anomalies, err = get_anomalies()
    if anomalies is None:
        print(f"[CORRELATE] Could not retrieve anomalies: {err}", file=sys.stderr)
        return False
    if not anomalies:
        print("[CORRELATE] No anomalies to correlate.")
        return True

    for a in anomalies:
        svc = (a.get("RootCauses") or [{}])[0].get("Service", "")
        start = a.get("AnomalyStartDate", _days_ago(7))[:10]
        end_dt = (datetime.date.fromisoformat(start) + datetime.timedelta(days=1)).isoformat()
        print("=" * 60)
        print(f"Anomaly {a.get('AnomalyId', '?')} | service={svc or '-'} | "
              f"impact=${a.get('Impact', {}).get('TotalImpact', 0):,.2f}")
        print("-" * 60)

        ct_args = [
            "cloudtrail", "lookup-events",
            "--start-time", f"{start}T00:00:00Z",
            "--end-time", f"{end_dt}T00:00:00Z",
            "--max-results", "20", "--output", "json",
        ]
        ok, ct, ct_err = run_aws(ct_args)
        if not ok:
            print(f"  CloudTrail lookup failed: {ct_err}")
        else:
            events = ct.get("Events", []) if isinstance(ct, dict) else []
            mutating = [e for e in events if any(
                e.get("EventName", "").startswith(p) for p in ("Create", "Run", "Modify", "Start"))]
            if mutating:
                print("  Likely cause events:")
                for e in mutating[:5]:
                    print(f"    {e.get('EventTime', '')}  {e.get('EventName')}  by {e.get('Username', '?')}")
            else:
                print("  No obvious mutating CloudTrail events in the anomaly window.")

        owner = resolve_owner(svc)
        if owner:
            print(f"  Owner (from tags): {owner['owner']}  [{owner['resource']}]")
        else:
            print("  Owner: not resolvable from resource tags (check tagging compliance).")
    print("=" * 60)
    return True


# ---------------------------------------------------------------------------
# ACTION PATH (approval-gated)
# ---------------------------------------------------------------------------
def _latest_anomaly_summary():
    anomalies, err = get_anomalies()
    if not anomalies:
        return None, err or "no anomalies"
    a = anomalies[0]
    svc = (a.get("RootCauses") or [{}])[0].get("Service", "unknown service")
    impact = a.get("Impact", {}).get("TotalImpact", 0)
    return {
        "anomaly_id": a.get("AnomalyId", "?"),
        "service": svc,
        "date": a.get("AnomalyStartDate", "")[:10],
        "impact_usd": impact,
        "text": f"Cost anomaly {a.get('AnomalyId', '?')} in {svc} on "
                f"{a.get('AnomalyStartDate', '')[:10]} — impact ${impact:,.2f}.",
    }, ""


def cmd_notify_slack(approval_mode):
    summary, err = _latest_anomaly_summary()
    if not summary:
        print(f"[SLACK] Nothing to send: {err}")
        return True
    if not request_approval("send-slack-alert", summary["text"], approval_mode):
        print("[SLACK] Not authorised — nothing sent.")
        return False

    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        print("[SLACK] Approved, but SLACK_WEBHOOK_URL is not set — payload prepared, not sent.")
        return True
    try:
        req = urllib.request.Request(
            webhook, data=json.dumps({"text": summary["text"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        print("[SLACK] Alert delivered to the configured webhook.")
        return True
    except Exception as e:
        print(f"[SLACK] Send failed: {e}", file=sys.stderr)
        return False


def cmd_notify_jira(approval_mode):
    summary, err = _latest_anomaly_summary()
    if not summary:
        print(f"[JIRA] Nothing to file: {err}")
        return True
    if not request_approval("create-jira-ticket", summary["text"], approval_mode):
        print("[JIRA] Not authorised — no ticket prepared.")
        return False

    os.makedirs(LOG_DIR, exist_ok=True)
    ticket = {
        "project_key": os.environ.get("JIRA_PROJECT_KEY", "FINOPS"),
        "summary": f"[FinOps] Cost anomaly in {summary['service']} on {summary['date']}",
        "description": summary["text"],
        "priority": "High",
    }
    path = os.path.join(LOG_DIR, f"jira_ticket_{summary['anomaly_id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ticket, f, indent=2)
    print(f"[JIRA] Payload prepared at {path}. "
          "Wire JIRA_BASE_URL / JIRA_TOKEN to submit automatically.")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AWS FinOps Agent (live account)")
    parser.add_argument("--cost", action="store_true", help="Spend breakdown + month-over-month")
    parser.add_argument("--anomalies", action="store_true", help="List active cost anomalies")
    parser.add_argument("--correlate", action="store_true", help="Root-cause anomalies via CloudTrail + tags")
    parser.add_argument("--notify-slack", action="store_true", help="Send latest anomaly summary to Slack")
    parser.add_argument("--notify-jira", action="store_true", help="Prepare a Jira ticket for the latest anomaly")
    parser.add_argument("--approval-mode", default="gatekeeper",
                        choices=["gatekeeper", "auto-approve"], help="Approval mode for side effects")
    args = parser.parse_args()

    if args.notify_slack:
        ok = cmd_notify_slack(args.approval_mode)
    elif args.notify_jira:
        ok = cmd_notify_jira(args.approval_mode)
    elif args.anomalies:
        ok = cmd_anomalies()
    elif args.correlate:
        ok = cmd_correlate()
    else:
        ok = cmd_cost()  # default: show spend
    sys.exit(0 if ok else 1)
