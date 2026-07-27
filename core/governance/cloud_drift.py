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
"""
import plan_reader


def _changed_attributes(before, after):
    """Attribute names whose value differs between two states. Shallow by design: Terraform
    reports drift at the top-level attribute grain, and a deep diff would name nested paths a
    reviewer cannot match against the plan output."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return set()
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


def classify(plan_json):
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
        row = {
            "address": entry["address"],
            "type": entry.get("type"),
            "drifted_attributes": sorted(drifted_attrs),
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

    return {
        "drift_count": len(drifted),
        "drifted": drifted,
        "reverted": reverted,
        "reverted_count": len(reverted),
        "reverts_out_of_band_changes": bool(reverted),
        "malformed_count": malformed,
    }


def format_result(result):
    """One-line-per-finding summary for the gate's stdout."""
    if not result["drift_count"] and not result["malformed_count"]:
        return "[gate] cloud drift: none detected"
    lines = [f"[gate] cloud drift: {result['drift_count']} resource(s) changed outside Terraform"]
    for row in result["reverted"]:
        lines.append(f"  - REVERTS out-of-band change: {row['address']} "
                     f"({', '.join(row['attributes'])}) -- this plan undoes a change someone "
                     f"made directly in the account")
    if result["malformed_count"]:
        lines.append(f"  - {result['malformed_count']} malformed drift entry(ies) could not be read")
    return "\n".join(lines)
