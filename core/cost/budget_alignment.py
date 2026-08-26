"""
Dynamic budget guardrail alignment.

Sizes `aws_budgets_budget.monthly_budget_usd` from what the architecture actually costs
instead of a static default. The static default is what produced the reported failure: a
`$500` guardrail against a `$1,258.29/mo` BCM forecast, and a gate warning that the forecast
was 252% of the plan's own budget.

    guardrail = max(declared budget, BCM estimate x 1.25)

TWO THINGS THIS DELIBERATELY DOES NOT DO.

It does not treat the 252% warning as false. It was true: the operator asked for a $500 cap
and the architecture costs more than twice that. What was wrong is the RESPONSE -- shipping a
guardrail nobody sized, so the alarm fired on the mismatch rather than on overspend. Raising
the guardrail resolves the alarm; it does not resolve the economic contradiction, which
belongs upstream in grilling, before the architecture is chosen.

It does not raise a declared budget silently. Provisioning a $1,573 alarm over an operator's
stated $500 cap turns a cost control into a rubber stamp, so an override is RECORDED --
what was declared, what it became, and why -- and the caller is expected to surface it.

With no estimate there is nothing to align to, and inventing headroom over a number nobody
computed would be fabrication. With neither input the answer is None, not a default.

Depends on: nothing
Shells out to: nothing. Standard library only.
Used by: core/generation/synthesizer.py, core/governance/plan_gate.py,
    tests/test_agent_guardrails.py
"""

# 25% operational headroom above measured spend, per FR-02. Enough that ordinary variance --
# a re-run, a backfill, a month with 31 days -- does not page anyone, and not so much that
# the alarm stops meaning anything.
HEADROOM = 1.25


def align(declared_usd=0, estimated_usd=None, headroom=HEADROOM):
    """Return the budget guardrail to provision, and how it was arrived at.

    {guardrail_usd, declared_usd, estimated_usd, aligned, overridden, reason}

    `aligned` says an estimate was available and used. `overridden` says an operator's own
    figure was raised, which is the case a caller must not swallow.
    """
    declared = float(declared_usd or 0)
    estimate = None if estimated_usd in (None, "") else float(estimated_usd)

    if estimate is None:
        return _result(declared or None, declared, estimate, aligned=False, overridden=False,
                       reason=("no BCM estimate is available, so the declared budget stands "
                               "unaligned" if declared else
                               "no budget was declared and no estimate exists"))

    sized = round(estimate * headroom, 2)
    if declared and declared >= sized:
        return _result(declared, declared, estimate, aligned=True, overridden=False,
                       reason=(f"declared ${declared:,.2f} already covers the "
                               f"${estimate:,.2f} forecast with headroom"))

    overridden = bool(declared)
    reason = (f"raised from the declared ${declared:,.2f} to ${sized:,.2f}: the forecast is "
              f"${estimate:,.2f} and a guardrail below it alarms on the mismatch rather "
              f"than on overspend"
              if overridden else
              f"sized at ${sized:,.2f} from the ${estimate:,.2f} forecast plus "
              f"{int((headroom - 1) * 100)}% headroom")
    return _result(sized, declared, estimate, aligned=True, overridden=overridden,
                   reason=reason)


def _result(guardrail, declared, estimate, aligned, overridden, reason):
    return {
        "guardrail_usd": guardrail,
        "declared_usd": declared,
        "estimated_usd": estimate,
        "aligned": aligned,
        "overridden": overridden,
        "reason": reason,
    }


def contradiction(declared_usd=0, estimated_usd=None):
    """The economic contradiction to raise DURING grilling, before an architecture is fixed.

    Returns None when there is none. Alignment silences an alarm; only this puts the choice
    back in front of the operator, which is the point at which it can still be answered by
    changing the architecture rather than by changing the number.
    """
    declared = float(declared_usd or 0)
    if not declared or estimated_usd in (None, ""):
        return None
    estimate = float(estimated_usd)
    if estimate <= declared:
        return None
    return {
        "declared_usd": declared,
        "estimated_usd": estimate,
        "over_by_pct": round(estimate / declared * 100, 1),
        "message": (f"The architecture as described costs about ${estimate:,.2f}/mo, which "
                    f"is {estimate / declared * 100:.0f}% of the ${declared:,.2f} budget "
                    f"you set."),
        "options": (
            "Scale the compute down -- Athena SQL or scheduled micro-batching instead of "
            "always-on Glue DPUs.",
            f"Raise the budget to about ${round(estimate * HEADROOM, 2):,.2f}, which is the "
            f"forecast plus 25% headroom.",
            "Reduce the declared volume or retention, which is what drives most of it.",
        ),
    }
