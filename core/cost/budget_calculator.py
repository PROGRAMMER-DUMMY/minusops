"""
Cost guidance — reportable totals come only from the AWS BCM Pricing Calculator API.

This module deliberately does NOT compute, estimate, or hardcode cost totals, and it
does not invent SKU prices. Reportable enterprise cost evidence is produced solely by
core/cost/bcm_pricing_calculator.py against the AWS BCM Pricing Calculator API (gated,
review-required). This responder exists so the dispatcher's BUDGET intent returns honest
guidance and the required commands — never a fabricated number.
"""
import argparse
import json
import os
import sys

BCM_COMMANDS = [
    "python core/cost/bcm_pricing_calculator.py prepare --report-dir <report-dir> --account-id <account-id>",
    "# review bcm-usage.json (resolve REVIEW_REQUIRED) or pass --usage-profile examples/bcm-usage-profile.example.json",
    "python core/cost/bcm_pricing_calculator.py run --report-dir <report-dir> --mode gatekeeper",
]


def cost_guidance():
    """Honest cost record: no total, BCM API required, with the exact commands."""
    return {
        "reportable": False,
        "pricing_source": "AWS BCM Pricing Calculator API required for reportable totals",
        "bcm_pricing_calculator_required": True,
        "note": (
            "MinusOps does not compute or hardcode cost totals. Generate reportable cost "
            "evidence via the gated AWS BCM Pricing Calculator workflow below."
        ),
        "commands": BCM_COMMANDS,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Cost guidance (reportable totals require the AWS BCM Pricing Calculator API)")
    parser.add_argument("--log-dir", default=os.path.join(os.getcwd(), ".agents", "logs"))
    parser.add_argument("--json", action="store_true")
    # Tolerate legacy sizing flags (--service/--scale/...) so older callers don't crash.
    args, _unknown = parser.parse_known_args(argv)

    record = cost_guidance()
    os.makedirs(args.log_dir, exist_ok=True)
    with open(os.path.join(args.log_dir, "budget_estimation.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)

    if args.json:
        print(json.dumps(record, indent=2))
    else:
        print("=" * 60)
        print("COST GUIDANCE")
        print("=" * 60)
        print(record["note"])
        print("-" * 60)
        print("Reportable totals require AWS BCM Pricing Calculator API evidence:")
        for cmd in record["commands"]:
            print(f"  {cmd}")
        print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
