"""
cloud_drift.py -- did someone change this infrastructure outside Terraform, and is this plan
about to undo it?

`source_guard` answers "did the .tf files change". This answers the other direction: did the
CLOUD change. Terraform records it in the plan's top-level `resource_drift` array, which
nothing in this repo read until now.

The distinction that matters is REVERT vs. drift-in-general:

  * Someone widened a security group in the console at 3am to stop an outage. The next plan
    proposes setting it back. Terraform calls that a routine `update`; a reviewer clicking
    approve has no idea they are undoing a deliberate human action. THAT is what this flags.
  * Drift the plan does not touch is worth showing, but it is not urgent -- nothing is being
    undone.

A revert is detected per attribute: reality moved from `before` to `after` (the drift), and
the plan proposes moving it back to `before`. Comparing whole objects instead would miss
partial reverts and fire on unrelated edits in the same resource.

Never raises. A malformed drift entry is counted and reported, never allowed to take down a
plan -- the gate failing closed on its own advisory reader would be worse than the blind spot
it replaces.

TELEMETRY CORRELATION (PRD-ARCH-2026-005, FR-06) answers the question drift alone cannot:
WHY did someone resize this Glue job by hand? CloudTrail names the principal, the job-run
history carries the OutOfMemoryError that preceded it, and a reviewer who can see both is
choosing between two known options instead of guessing. It is opt-in and fail-open: the
lookup is injected by the caller, `classify(plan_json)` alone stays offline and free, and a
lookup that raises produces no evidence rather than an exception. It never changes the
revert verdict -- an explained change is still a change the plan is about to undo.

Depends on: plan_reader; core/providers/aws.py, imported lazily inside `aws_telemetry` only
    (so the default offline path costs no import and makes no AWS call)
Shells out to: nothing directly -- `aws_telemetry` reaches the `aws` CLI through
    aws.run_aws (`cloudtrail lookup-events`, `glue get-job-runs`), both read-only
Used by: core/governance/plan_gate.py
"""
import datetime

import plan_reader

# Only these expose a run history worth correlating. Asking CloudTrail about every
# drifted S3 bucket would add a round trip per resource for nothing.
TELEMETRY_TYPES = ("aws_glue_job", "aws_emr_cluster")

_EVENT_SOURCE = {
    "aws_glue_job": "glue.amazonaws.com",
    "aws_emr_cluster": "elasticmapreduce.amazonaws.com",
}


def _changed_attributes(before, after):
    """Attribute names whose value differs between two states. Shallow by design: Terraform
    reports drift at the top-level attribute grain, and a deep diff would name nested paths a
    reviewer cannot match against the plan output."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return set()
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


def classify(plan_json, telemetry=None):
    drift_entries = plan_reader.resource_drift(plan_json)
    resource_changes, _ = plan_reader.read_resource_changes(
        plan_json, treat_absent_as_error=False)

    planned_by_address = {}
    for rc in resource_changes or []:
        if isinstance(rc, dict) and rc.get("address"):
            planned_by_address[rc["address"]] = (rc.get("change") or {})

    drifted, reverted, malformed = [], [], 0
    for entry in drift_entries:
        if not isinstance(entry, dict) or not entry.get("address"):
            malformed += 1
            continue
        change = entry.get("change") or {}
        drift_before, drift_after = change.get("before"), change.get("after")
        drifted_attrs = _changed_attributes(drift_before, drift_after)
        # `before` is what Terraform recorded (what Git declares); `after` is what the API
        # returned (what is live). Carried as values, not just names, so the summary can show
        # a reviewer G.1X vs G.2X rather than the word "worker_type".
        row = {
            "address": entry["address"],
            "type": entry.get("type"),
            "drifted_attributes": sorted(drifted_attrs),
            "declared": {attr: (drift_before or {}).get(attr) for attr in sorted(drifted_attrs)},
            "live": {attr: (drift_after or {}).get(attr) for attr in sorted(drifted_attrs)},
        }
        drifted.append(row)

        planned = planned_by_address.get(entry["address"])
        if not planned or not drifted_attrs:
            continue
        planned_after = planned.get("after")
        if not isinstance(planned_after, dict):
            continue
        # The plan puts the attribute back to its pre-drift value -> it undoes a human change.
        undone = sorted(
            attr for attr in drifted_attrs
            if attr in planned_after
            and planned_after[attr] == (drift_before or {}).get(attr)
            and planned_after[attr] != (drift_after or {}).get(attr)
        )
        if undone:
            reverted.append({"address": entry["address"], "type": entry.get("type"),
                             "attributes": undone})

    evidence = _correlate(drifted, telemetry)
    return {
        "drift_count": len(drifted),
        "drifted": drifted,
        "reverted": reverted,
        "reverted_count": len(reverted),
        "reverts_out_of_band_changes": bool(reverted),
        "malformed_count": malformed,
        "telemetry_available": bool(evidence),
        "telemetry_evidence": evidence,
    }


def _correlate(drifted, telemetry):
    """Ask the injected lookup about each drifted resource. Advisory in every direction:
    no lookup, a lookup that raises, and a lookup that finds nothing all produce the same
    empty result, because "we could not tell" must never read as "nobody did this"."""
    if not telemetry:
        return []
    evidence = []
    for row in drifted:
        try:
            found = telemetry(row["address"], row.get("type"))
        except Exception:  # noqa: BLE001 -- a network failure must not fail a plan
            continue
        if not found:
            continue
        evidence.append({
            "address": row["address"],
            "type": row.get("type"),
            "identity": found.get("identity"),
            "errors": list(found.get("errors") or []),
        })
    return evidence


def aws_telemetry(address, resource_type, lookback_hours=24):
    """CloudTrail principal + recent failure signatures for one drifted resource, or None.

    Returns None for every condition that is not a positive finding -- unsupported type,
    missing CLI, no credentials, no matching events -- so the caller cannot mistake an
    unanswerable question for an answered one."""
    if resource_type not in TELEMETRY_TYPES:
        return None
    try:
        import aws
    except ImportError:
        return None

    identity = _cloudtrail_identity(aws, resource_type, lookback_hours)
    errors = _job_run_errors(aws, address) if resource_type == "aws_glue_job" else []
    if not identity and not errors:
        return None
    return {"identity": identity, "errors": errors}


def _cloudtrail_identity(aws, resource_type, lookback_hours):
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(hours=lookback_hours)).replace(microsecond=0).isoformat()
    ok, data, _ = aws.run_aws([
        "cloudtrail", "lookup-events",
        "--lookup-attributes",
        f"AttributeKey=EventSource,AttributeValue={_EVENT_SOURCE[resource_type]}",
        "--start-time", since, "--max-results", "10", "--output", "json"])
    if not ok or not isinstance(data, dict):
        return None
    for event in data.get("Events") or []:
        if isinstance(event, dict) and event.get("Username"):
            return event["Username"]
    return None


def _job_run_errors(aws, address):
    # ponytail: the Glue job NAME is derived from the Terraform resource label, which holds
    # for MinusOps-generated stacks but not for every adopted one -- the physical id lives in
    # state, which a plan classifier does not read. If a mismatch ever matters, thread the
    # drift entry's `before` attributes through instead of the address.
    job_name = address.split(".")[-1]
    ok, data, _ = aws.run_aws([
        "glue", "get-job-runs", "--job-name", job_name,
        "--max-results", "5", "--output", "json"])
    if not ok or not isinstance(data, dict):
        return []
    return [run["ErrorMessage"] for run in data.get("JobRuns") or []
            if isinstance(run, dict) and run.get("JobRunState") == "FAILED"
            and run.get("ErrorMessage")]


RECOMMENDATION = ("Recommendation: Do not revert. Update main.tf to match live attributes "
                  "and re-anchor baseline with 'minusctl source anchor'.")


def _pairs(values):
    """`attr = value, attr = value` in a stable order."""
    return ", ".join(f"{attr} = {values[attr]}" for attr in sorted(values))


def format_result(result):
    """Per-resource summary for the gate's stdout.

    Grouped by resource rather than by finding type, because the declared value, the live
    value, who changed it and what failed beforehand are one story about one resource; split
    across three lists they are four facts a reviewer has to reassemble.

    The clean case is deliberately still one line. It is what every passing plan prints.
    """
    if not result["drift_count"] and not result["malformed_count"]:
        return "[gate] cloud drift: none detected"

    reverted = {row["address"]: row for row in result["reverted"]}
    evidence = {row["address"]: row for row in result.get("telemetry_evidence") or []}

    lines = [f"[gate] cloud drift: {result['drift_count']} resource(s) changed outside Terraform"]
    for row in result["drifted"]:
        address = row["address"]
        lines.append(f"  - {address} changed outside Terraform")
        if row.get("declared"):
            lines.append(f"      Declared in Git:    {_pairs(row['declared'])}")
        if row.get("live"):
            lines.append(f"      Live in AWS Cloud:  {_pairs(row['live'])}")

        undone = reverted.get(address)
        if undone:
            lines.append(f"      REVERTS out-of-band change: {', '.join(undone['attributes'])}"
                         " -- this plan undoes a change someone made directly in the account")

        found = evidence.get(address)
        if found and found.get("identity"):
            lines.append(f"      Action: changed by {found['identity']}")
        for message in (found or {}).get("errors") or []:
            lines.append(f"      Telemetry Evidence: {message}")

        # Only with BOTH: evidence of why, and a plan that would undo it. Without evidence
        # "do not revert" is advice we cannot support; without a revert there is nothing to
        # not-revert.
        if undone and found:
            lines.append(f"      {RECOMMENDATION}")

    if result["malformed_count"]:
        lines.append(f"  - {result['malformed_count']} malformed drift entry(ies) could not be read")
    return "\n".join(lines)
