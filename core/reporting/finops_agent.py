"""
FinOps Agent — live cost intelligence over the active cloud (no mock data).

Runs against whichever cloud MINUS_CLOUD selects (aws | azure | gcp) through the
CloudProvider abstraction in core/providers/. Cost + anomalies are cloud-generic;
activity-log correlation (CloudTrail) is currently AWS-only.

Read / analysis path (safe, read-only):
  --cost        Spend by service + month-over-month change
  --anomalies   List active cost anomalies
  --correlate   Root-cause anomalies via activity log + tag ownership (AWS only)

Action path (side effects — routed through the approval gate):
  --notify-slack / --notify-jira   with  --approval-mode {gatekeeper, auto-approve}
"""
import os
import sys
import json
import argparse
import datetime
import urllib.request

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
from approval import request_approval          # noqa: E402
from providers.base import get_provider        # noqa: E402

LOG_DIR = os.path.join(os.getcwd(), ".agents", "logs")


# ---------------------------------------------------------------------------
# READ PATH (cloud-generic)
# ---------------------------------------------------------------------------
def cmd_cost():
    provider = get_provider()
    result = provider.cost_by_service()
    if not result["ok"]:
        print(f"[COST] Could not retrieve cost data: {result['error']}", file=sys.stderr)
        return False
    print("=" * 60)
    print(f"{provider.name.upper()} SPEND BY SERVICE")
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


def cmd_anomalies():
    provider = get_provider()
    anomalies, err = provider.anomalies()
    if anomalies is None:
        print(f"[ANOMALIES] Could not retrieve anomalies: {err}", file=sys.stderr)
        return False
    print("=" * 60)
    print(f"{provider.name.upper()} COST ANOMALIES")
    print("=" * 60)
    if not anomalies:
        print("No anomalies detected in the lookback window.")
        return True
    for a in anomalies:
        print(f"  {a['id']} | {a['date']} | service={a['service']} | impact=${a['impact']:,.2f}")
    print("=" * 60)
    return True


def cmd_correlate():
    """Root-cause anomalies via the activity log + tag ownership. Currently AWS-only."""
    provider = get_provider()
    if provider.name != "aws":
        print(f"[CORRELATE] Activity-log correlation is AWS-only for now (active cloud: {provider.name}).")
        return False
    from providers.aws import run_aws  # AWS-specific CloudTrail lookup

    anomalies, err = provider.anomalies()
    if anomalies is None:
        print(f"[CORRELATE] Could not retrieve anomalies: {err}", file=sys.stderr)
        return False
    if not anomalies:
        print("[CORRELATE] No anomalies to correlate.")
        return True

    for a in anomalies:
        svc, start = a["service"], a["date"]
        try:
            end_dt = (datetime.date.fromisoformat(start) + datetime.timedelta(days=1)).isoformat()
        except ValueError:
            end_dt = start
        print("=" * 60)
        print(f"Anomaly {a['id']} | service={svc} | impact=${a['impact']:,.2f}")
        print("-" * 60)
        ok, ct, ct_err = run_aws([
            "cloudtrail", "lookup-events",
            "--start-time", f"{start}T00:00:00Z", "--end-time", f"{end_dt}T00:00:00Z",
            "--max-results", "20", "--output", "json",
        ])
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
                print("  No obvious mutating events in the anomaly window.")
        owner = provider.owner(svc)
        print(f"  Owner (from tags): {owner}" if owner
              else "  Owner: not resolvable from resource tags (check tagging compliance).")
    print("=" * 60)
    return True


# ---------------------------------------------------------------------------
# ACTION PATH (approval-gated)
# ---------------------------------------------------------------------------
def _latest_anomaly_summary():
    provider = get_provider()
    anomalies, err = provider.anomalies()
    if not anomalies:
        return None, err or "no anomalies"
    a = anomalies[0]
    return {
        "anomaly_id": a["id"], "service": a["service"], "date": a["date"],
        "impact_usd": a["impact"],
        "text": f"Cost anomaly {a['id']} in {a['service']} on {a['date']} - impact ${a['impact']:,.2f}.",
    }, ""


def cmd_notify_slack(approval_mode):
    summary, err = _latest_anomaly_summary()
    if not summary:
        print(f"[SLACK] Nothing to send: {err}")
        return True
    if not request_approval("send-slack-alert", summary["text"], approval_mode):
        print("[SLACK] Not authorised - nothing sent.")
        return False
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        print("[SLACK] Approved, but SLACK_WEBHOOK_URL is not set - payload prepared, not sent.")
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
        print("[JIRA] Not authorised - no ticket prepared.")
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
    parser = argparse.ArgumentParser(description="FinOps Agent (live, multi-cloud via MINUS_CLOUD)")
    parser.add_argument("--cost", action="store_true", help="Spend breakdown + month-over-month")
    parser.add_argument("--anomalies", action="store_true", help="List active cost anomalies")
    parser.add_argument("--correlate", action="store_true", help="Root-cause anomalies (AWS only)")
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
        ok = cmd_cost()
    sys.exit(0 if ok else 1)
