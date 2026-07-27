"""
rule_stages.py -- which policy rules have teeth, and who gave them teeth.

`policy/g6/rules.rego` covers 13 rule IDs. AWS exposes over a thousand resource types, so
hand-writing rules does not scale to what an agent can generate, and `verification_coverage`
now reports that gap honestly rather than hiding it. Agents proposing rules is how coverage
grows.

The failure mode to design against is NOT a generated rule that is too strict -- that is
noisy and gets noticed immediately. It is a generated rule that SILENTLY PERMITS: one that
looks like coverage, never fires, and makes a resource type appear reviewed when nothing
actually checks it. A false sense of coverage is worse than none, because the report says
"rule fired" and a reviewer stops looking.

So:

  * Every rule is WARN-ONLY until a human explicitly promotes it. The default for an
    unknown rule id is `warn`, never `blocking` -- an agent can add a rule to the .rego and
    it changes nothing about what ships until a person signs off.
  * Promotion is an attributable, recorded act (who, when, why), stored in a committed JSON
    file so it is reviewed in a PR like any other change.
  * Demotion exists so a promoted rule that turns out wrong is reversible without editing
    Rego under pressure.

Fail-SAFE, deliberately not fail-closed: a missing or corrupt registry means everything
warns. Fail-closed here would read as "every rule is blocking", which wedges every plan on
a file-read error -- an availability failure dressed up as safety. The findings are still
reported either way; only their teeth are withheld.

This never weakens the gate: warn-only rules still appear in reports and in
verification_coverage. Promotion only ever ADDS blocking power.
"""
import datetime
import json
import os

WARN = "warn"
BLOCKING = "blocking"
_VALID_STAGES = (WARN, BLOCKING)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REGISTRY = os.path.join(os.path.dirname(_REPO_ROOT), "policy", "rule_stages.json")


def _load(registry_path):
    path = registry_path or DEFAULT_REGISTRY
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        # Missing or corrupt -> no rule is promoted. See the module docstring on why this is
        # fail-safe rather than fail-closed.
        return {"rules": {}}
    if not isinstance(data, dict) or not isinstance(data.get("rules"), dict):
        return {"rules": {}}
    return data


def _save(data, registry_path):
    path = registry_path or DEFAULT_REGISTRY
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def stage_of(rule_id, registry_path=None):
    """`warn` unless a human promoted this rule. Unknown ids are warn, never blocking."""
    entry = _load(registry_path)["rules"].get(rule_id) or {}
    stage = entry.get("stage")
    return stage if stage in _VALID_STAGES else WARN


def is_blocking(rule_id, registry_path=None):
    return stage_of(rule_id, registry_path=registry_path) == BLOCKING


def partition(findings, registry_path=None):
    """Split findings into the ones that may block and the ones that only warn.

    Nothing is dropped: a warn-only finding is still a finding, still reported, and still
    counts toward verification coverage. Only its ability to stop an apply is withheld.
    """
    blocking, warning = [], []
    for finding in findings or ():
        target = blocking if is_blocking(finding.get("id"), registry_path) else warning
        target.append(finding)
    return {"blocking": blocking, "warning": warning,
            "blocking_count": len(blocking), "warning_count": len(warning)}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def promote(rule_id, *, promoted_by, reason, registry_path=None):
    """Give a rule teeth. Attributable by construction -- an anonymous promotion is exactly
    how a generated rule silently starts blocking deploys."""
    if not (rule_id or "").strip():
        raise ValueError("promote: rule_id is required")
    if not (promoted_by or "").strip():
        raise ValueError(
            "promote: promoted_by is required -- promotion is the moment a proposed rule "
            "starts blocking real applies, and it must be attributable to a person")
    if not (reason or "").strip():
        raise ValueError(
            "promote: reason is required -- a future reader needs to know what was actually "
            "reviewed, not just that someone clicked yes")
    data = _load(registry_path)
    entry = data["rules"].get(rule_id, {})
    entry.update({"stage": BLOCKING, "promoted_by": promoted_by, "reason": reason,
                  "promoted_at": _now()})
    entry.pop("demoted_by", None)
    entry.pop("demoted_at", None)
    data["rules"][rule_id] = entry
    _save(data, registry_path)
    return entry


def demote(rule_id, *, demoted_by, reason, registry_path=None):
    """Return a rule to warn-only. Exists so a bad promotion is reversible without editing
    Rego under pressure -- the fix for a false positive should never be deleting the rule."""
    if not (demoted_by or "").strip():
        raise ValueError("demote: demoted_by is required")
    if not (reason or "").strip():
        raise ValueError("demote: reason is required")
    data = _load(registry_path)
    entry = data["rules"].get(rule_id, {})
    entry.update({"stage": WARN, "demoted_by": demoted_by, "reason": reason,
                  "demoted_at": _now()})
    data["rules"][rule_id] = entry
    _save(data, registry_path)
    return entry


def list_rules(registry_path=None):
    data = _load(registry_path)["rules"]
    return {rid: dict(entry) for rid, entry in sorted(data.items())}
