"""
verification_coverage.py -- what the gate actually checked, stated per plan.

A green report is ambiguous today: the reviewer cannot tell whether a resource type was
checked and found clean, or never had a rule at all. Both look identical. That ambiguity is
the residual risk of agent-generated infrastructure -- an unreviewed generated resource
renders exactly like a thoroughly-verified one.

This classifies every managed resource type in a plan into one of three honest states,
mirroring core/cost/coverage_audit.py's four-state pricing disclosure (same pattern, same
reason). It never blocks and never verifies anything itself -- it only reports reach.

  rule_covered    -- an executable policy rule fired against this type
  claim_informed  -- no rule, but the claim store holds knowledge about it. NOT verification:
                     claims inform, they never permit. The label says so on purpose.
  unchecked       -- neither. The honest state the report used to render as silence.

`coverage_ratio` counts ONLY rule_covered types. Claims must never inflate it, or coverage
appears to grow when only memory did -- and coverage is the metric that says whether the
agent-authored-rules loop is actually running.
"""
import plan_reader

RULE_COVERED = "rule_covered"
CLAIM_INFORMED = "claim_informed"
UNCHECKED = "unchecked"


def _type_of_resource_ref(ref):
    """Findings name a resource address ('aws_s3_bucket.a') or bare type. Both reduce to
    the type, which is the grain rules and claims are keyed on."""
    if not ref:
        return None
    return str(ref).split(".", 1)[0]


def classify(plan_json, findings=(), claims_by_type=None):
    claims_by_type = claims_by_type or {}
    resource_changes, error = plan_reader.read_resource_changes(
        plan_json, treat_absent_as_error=False)
    # managed_only returns (managed, malformed) -- malformed entries are reported, not
    # silently dropped, or a coverage report claiming honesty would be hiding exactly the
    # entries it can say least about.
    managed, malformed = plan_reader.managed_only(resource_changes or [])

    types = {}
    for change in managed:
        rtype = change.get("type")
        if rtype:
            types.setdefault(rtype, 0)
            types[rtype] += 1

    fired = {}
    for finding in findings or ():
        rtype = _type_of_resource_ref(finding.get("resource"))
        if rtype in types and finding.get("id"):
            fired.setdefault(rtype, set()).add(finding["id"])

    rows = []
    for rtype in sorted(types):
        claims = claims_by_type.get(rtype) or []
        if rtype in fired:
            state = RULE_COVERED
        elif claims:
            state = CLAIM_INFORMED
        else:
            state = UNCHECKED
        rows.append({
            "resource_type": rtype,
            "resource_count": types[rtype],
            "state": state,
            "rule_ids": sorted(fired.get(rtype, ())),
            "claim_count": len(claims),
        })

    rule_covered = sum(1 for r in rows if r["state"] == RULE_COVERED)
    return {
        "types": rows,
        "type_count": len(rows),
        "rule_covered_count": rule_covered,
        "claim_informed_count": sum(1 for r in rows if r["state"] == CLAIM_INFORMED),
        "unchecked_count": sum(1 for r in rows if r["state"] == UNCHECKED),
        # None, not 0.0 -- an empty plan has no coverage to report, and 0.0 would read as
        # "nothing is covered", which is a different and wrong statement.
        "coverage_ratio": (rule_covered / len(rows)) if rows else None,
        "malformed_change_count": len(malformed),
        "plan_unreadable": bool(error),
    }
