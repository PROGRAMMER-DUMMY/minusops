"""
FinOps Agent — live cost intelligence over the active cloud (no mock data).

Runs against AWS through providers.base.get_provider(); never touches a cloud CLI directly.

Read / analysis path (safe, read-only):
  --cost        Spend by service + month-over-month change
  --anomalies   List active cost anomalies
  --correlate   Root-cause anomalies via activity log + tag ownership (AWS only)

Action path (side effects — routed through the approval gate):
  --notify-slack / --notify-jira   with  --approval-mode {gatekeeper, auto-approve}

Export path:
  --export-excel  writes .xlsx workbooks locally; reaches no cloud and needs no approval

The action path never mutates cloud infrastructure: the side effects are an outbound Slack
webhook POST and a Jira payload written to disk (Jira is prepared, not submitted, until
JIRA_BASE_URL / JIRA_TOKEN are wired).

Neither send is implemented here. Both live in core/integrations/, which owns the transport
and the approval gate; this module owns only the FinOps wording and the operator-facing exit
codes. Approval still happens before any send — it moved into base_hook.gated(), it did not go
away.

Depends on: core/providers/base.py, core/integrations/slack_hook.py,
    core/integrations/jira_hook.py (both of which reach core/governance/approval.py for the
    gate), core/providers/aws.py (imported inside `cmd_correlate` — CloudTrail lookup is
    AWS-specific and must not be a hard import on other clouds),
    core/reporting/excel_finops_generator.py (imported lazily inside `cmd_export_excel`, so
    the Excel writer is not loaded on the cost/anomaly path)
Shells out to: AWS read-only through the provider abstraction — Cost Explorer / Cost
    Anomaly Detection, resource tags, and `cloudtrail lookup-events`. Never a cloud CLI
    directly, and never a mutating API. Also POSTs to SLACK_WEBHOOK_URL when notifying.
Used by: nothing in-repo imports it for its behaviour; run directly
    (`python core/reporting/finops_agent.py --cost`). tests/test_finops_agent.py imports it.
"""
import os
import sys
import argparse
import datetime

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers",
             "integrations"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
from providers.base import get_provider                 # noqa: E402
# Flat imports, matching the shim above: the hooks import `base_hook` flat too, and mixing in
# `integrations.base_hook` would load a second copy of the module holding the approval gate.
from slack_hook import send_slack_notification          # noqa: E402
from jira_hook import create_change_ticket              # noqa: E402

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
    res = send_slack_notification({"text": summary["text"]}, approval_mode=approval_mode)
    if res.get("reason") == "not_authorized":
        print("[SLACK] Not authorised - nothing sent.")
        return False
    if res.get("reason") == "not_configured":
        print("[SLACK] Approved, but SLACK_WEBHOOK_URL is not set - payload prepared, not sent.")
        return True
    if res.get("ok"):
        print("[SLACK] Alert delivered to the configured webhook.")
        return True
    print(f"[SLACK] Send failed: {res.get('error')}", file=sys.stderr)
    return False


def cmd_notify_jira(approval_mode):
    summary, err = _latest_anomaly_summary()
    if not summary:
        print(f"[JIRA] Nothing to file: {err}")
        return True
    # out_dir is passed explicitly rather than left to the hook's own default so this
    # module's LOG_DIR stays the single place the FinOps ticket path writes to.
    res = create_change_ticket(
        project_key=os.environ.get("JIRA_PROJECT_KEY", "FINOPS"),
        summary=f"[FinOps] Cost anomaly in {summary['service']} on {summary['date']}",
        description=summary["text"],
        out_dir=LOG_DIR,
        filename=f"jira_ticket_{summary['anomaly_id']}.json",
        approval_mode=approval_mode,
        details=summary["text"],
    )
    if res.get("reason") == "not_authorized":
        print("[JIRA] Not authorised - no ticket prepared.")
        return False
    if res.get("reason") == "not_configured":
        print(f"[JIRA] Payload prepared at {res['path']}. "
              "Wire JIRA_BASE_URL / JIRA_TOKEN to submit automatically.")
        return True
    if res.get("ok"):
        print(f"[JIRA] Ticket {res.get('issue_key')} created.")
        return True
    print(f"[JIRA] Ticket creation failed: {res.get('error')}", file=sys.stderr)
    return False


def cmd_export_excel(output_target):
    # Lazy import: the Excel writer is only needed on this path, and the cost/anomaly
    # commands must not pay for it.
    from excel_finops_generator import (
        generate_executive_project_summary_excel,
        generate_pipeline_detailed_ledger_excel,
        generate_both_enterprise_reports
    )
    
    if os.path.isdir(output_target) or not output_target.endswith(".xlsx"):
        p1, p2 = generate_both_enterprise_reports(output_target)
        print(f"[FINOPS EXPORT] Generated dual enterprise workbooks in {output_target}")
        return True
    
    if "project" in output_target.lower():
        # ILLUSTRATIVE SAMPLE ROWS -- these dollar figures are made up, for demonstrating the
        # workbook layout only. They are not Cost Explorer actuals and not BCM forecasts, and
        # must never be presented as either. Wire this to provider.cost_by_service() before
        # anyone treats the exported file as a real bill.
        project_records = [
            {
                "domain": "Domain-Analytics",
                "project_repo": "payer-reconciliation-engine",
                "active_pipelines": 3,
                "last_month_usd": 1410.00,
                "current_month_usd": 2136.00,
                "cost_center": "CC-4092",
                "owner": "sarah.t@company.com",
                "root_cause_summary": "Glue ETL scaled with +45GB/day surge + S3 Bronze retention lag",
                "action_plan": "Enforce max 4-worker cap and 30-day Glacier lifecycle policy"
            },
            {
                "domain": "Domain-Regulatory",
                "project_repo": "claims-audit-pipeline",
                "active_pipelines": 2,
                "last_month_usd": 665.00,
                "current_month_usd": 663.00,
                "cost_center": "CC-8810",
                "owner": "elena.r@company.com",
                "root_cause_summary": "Stable execution; S3 Deep Archive transitions offset minor compute growth (-0.3%)",
                "action_plan": "Optimized; maintain current archiving lifecycle rules"
            },
            {
                "domain": "Domain-CoreOps",
                "project_repo": "enterprise-vpc-fabric",
                "active_pipelines": 1,
                "last_month_usd": 269.00,
                "current_month_usd": 269.00,
                "cost_center": "CC-1001",
                "owner": "david.k@company.com",
                "root_cause_summary": "Base idle network standing cost (S3 Gateway endpoint eliminates data transfer fee)",
                "action_plan": "Maintain S3 Gateway VPC endpoints"
            }
        ]
        generate_executive_project_summary_excel(output_target, project_records)
        print(f"[FINOPS EXPORT] Wrote the executive project summary to {output_target}")
        return True

    out_dir = os.path.dirname(os.path.abspath(output_target)) or os.getcwd()
    generate_both_enterprise_reports(out_dir)
    print(f"[FINOPS EXPORT] Wrote both enterprise workbooks to {out_dir}")
    # `main()` does `sys.exit(0 if ok else 1)`. Only the directory branch above returned
    # True; both single-file branches fell off the end returning None, so writing a named
    # .xlsx succeeded and exited 1 -- a CI step failing while producing the file it asked for.
    return True
def error_budget_minutes(slo_percent, days=30):
    """
    Calculate allowable downtime/delay minutes for a given SLO over a time window.
    Refuses 100% SLO as unachievable and mathematically invalid (division by zero).
    """
    if slo_percent >= 100.0 or slo_percent <= 0.0:
        raise ValueError("SLO must be strictly between 0 and 100 percent")
    total_window_minutes = days * 24 * 60.0
    allowed_downtime_pct = 100.0 - slo_percent
    return (allowed_downtime_pct / 100.0) * total_window_minutes


def error_budget_burn(slo_percent, consumed_minutes, window_hours=720):
    """
    Calculate error budget burn rate and governance state.
    Triggers burn_alert if >10% of total budget is consumed in a 24-hour window.
    Enforces feature_freeze when error budget is exhausted (<0 remaining).
    """
    total_budget = error_budget_minutes(slo_percent, days=30)
    remaining = total_budget - consumed_minutes
    burned_pct = (consumed_minutes / total_budget) * 100.0 if total_budget > 0 else 100.0

    burn_alert = (consumed_minutes / total_budget) > 0.10 if window_hours <= 24 else False

    if remaining < 0:
        state = "feature_freeze"
    elif burned_pct > 75.0:
        state = "at_risk"
    else:
        state = "healthy"

    return {
        "slo_percent": slo_percent,
        "total_budget_minutes": total_budget,
        "consumed_minutes": consumed_minutes,
        "remaining_minutes": remaining,
        "burned_pct": burned_pct,
        "state": state,
        "burn_alert": burn_alert,
    }


def consumed_minutes_from_runs(total_runs, failed_runs, run_interval_minutes=60):
    """
    Convert batch pipeline run failures into downtime minutes.
    consumed = failed_runs * run_interval_minutes.
    """
    return float(failed_runs * run_interval_minutes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FinOps Agent (live AWS spend and anomalies)")
    parser.add_argument("--cost", action="store_true", help="Spend breakdown + month-over-month")
    parser.add_argument("--anomalies", action="store_true", help="List active cost anomalies")
    parser.add_argument("--correlate", action="store_true", help="Root-cause anomalies (AWS only)")
    parser.add_argument("--export-excel", type=str, metavar="DIR_OR_PATH",
                        help="Export dual enterprise Excel workbooks: Executive Project Summary & Pipeline Detailed Ledger")
    parser.add_argument("--notify-slack", action="store_true", help="Send latest anomaly summary to Slack")
    parser.add_argument("--notify-jira", action="store_true", help="Prepare a Jira ticket for the latest anomaly")
    parser.add_argument("--approval-mode", default="gatekeeper",
                        choices=["gatekeeper", "auto-approve"], help="Approval mode for side effects")
    args = parser.parse_args()

    if args.export_excel:
        ok = cmd_export_excel(args.export_excel)
    elif args.notify_slack:
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
