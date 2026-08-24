"""
Architecture decision gate.

Requirements say what must be built. This record says why a particular architecture and module
set was selected after research. Production synthesis is bound to this file so keyword matching
cannot silently become a recommendation engine.

validate() is fail-closed and every required list must be non-empty, `validation` and
`rollback` included: a design that cannot state how it will be checked, or how it is undone
when it fails, is a hope rather than a decision, and both are the fields under time pressure
that someone will want to make optional.

Depends on: core/generation/modules.py (module_registry.list_modules, to reject unknown module
    ids in add_modules). Reads and writes architecture_decision.json.
Shells out to: nothing. Local JSON record-keeping and validation only.
Used by: core/generation/synthesizer.py, core/generation/accelerators.py,
    core/governance/plan_gate.py, core/reporting/minusctl.py, app/console_app.py,
    tests/test_architecture_decision.py, tests/test_synthesizer.py,
    tests/test_teardown_regression_harness.py
"""
import datetime
import json
import os
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)

import modules as module_registry

FILENAME = "architecture_decision.json"

# TerraShark's failure-mode taxonomy (NextStackHelper.md section 2). Recorded on the decision
# so a design states which of these it actively mitigates, and so `grill-me` and the analyzer
# name the same five things. Optional -- but an id that is not one of these is a typo, not a
# sixth failure mode, so it is rejected rather than stored.
FAILURE_MODES = {
    "FM-01": "Identity churn (count indexing, missing moved {} blocks, plan-unknown keys)",
    "FM-02": "Secret exposure (hardcoded defaults, state/log leakage, raw plan JSON in CI)",
    "FM-03": "Blast radius (monolithic root modules, shared state across envs, missing locks)",
    "FM-04": "CI drift (floating versions, uncommitted lock file, re-planning at apply time)",
    "FM-05": "Compliance gate gaps (static docs instead of CI policy gates, blanket ignore_changes)",
}


class ArchitectureDecisionIncomplete(Exception):
    """Raised when synthesis is attempted without a complete decision record."""

    def __init__(self, missing):
        self.missing = missing
        super().__init__("architecture decision incomplete: " + ", ".join(missing))


def template(requirements_file="requirements.json"):
    return {
        "requirements_file": requirements_file,
        "selected_architecture": "",
        "decision_summary": "",
        "selected_modules": [],
        "novel_resources": [],
        "alternatives": [
            {"name": "", "decision": "rejected", "reason": ""}
        ],
        "assumptions": [],
        "risks": [],
        # Validation + rollback complete TerraShark's 4-part output contract; `assumptions`
        # and `alternatives` above already carry the other two parts (assumptions, tradeoffs).
        # A design that cannot say how it will be checked, or how it is undone, is not a
        # decision -- it is a hope.
        "validation": [],
        "rollback": [],
        "failure_modes": [],
        "sources": [],
        "decided_by": "",
        "decided_at": "",
    }


def _answered(value):
    return bool(str(value).strip())


def _nonempty_list(value):
    return isinstance(value, list) and any(_answered(item) for item in value)


def _valid_alternative(item):
    if not isinstance(item, dict):
        return False
    return _answered(item.get("name")) and _answered(item.get("decision")) and _answered(item.get("reason"))


def _valid_novel_resource(item):
    if not isinstance(item, dict):
        return False
    return (
        _answered(item.get("resource_type"))
        and _answered(item.get("justification"))
        and _nonempty_list(item.get("alternatives_considered"))
    )


def validate(data):
    missing = []
    if not isinstance(data, dict):
        return False, ["(not an architecture decision object)"]
    for field in ("requirements_file", "selected_architecture", "decision_summary"):
        if not _answered(data.get(field, "")):
            missing.append(field)
    # A decision must select SOMETHING -- catalog modules, novel (authored) resources, or both.
    # Do NOT tighten this back to requiring selected_modules unconditionally: "zero catalog
    # modules, entirely covered by authored content" is a legitimate decision that synthesize()
    # supports, and requiring a module id here refuses the record before synthesize() ever runs.
    # novel_resources' per-entry completeness is checked separately below; this only asks
    # whether there is at least one entry to check.
    has_selected_modules = _nonempty_list(data.get("selected_modules"))
    novel_resources_present = isinstance(data.get("novel_resources"), list) and len(data.get("novel_resources")) > 0
    if not has_selected_modules and not novel_resources_present:
        missing.append(
            "selected_modules (at least one module id) OR novel_resources (at least one entry) "
            "-- a decision must select something, from the catalog or authored"
        )
    alternatives = data.get("alternatives") or []
    if not (isinstance(alternatives, list) and any(_valid_alternative(item) for item in alternatives)):
        missing.append("alternatives (at least one named choice with decision and reason)")
    for field in ("assumptions", "risks", "validation", "rollback", "sources"):
        if not _nonempty_list(data.get(field)):
            missing.append(f"{field} (at least one item)")
    # failure_modes is optional (not every design meaningfully touches all five), but an
    # unrecognised id means the author guessed at the taxonomy rather than reading it.
    unknown = [item for item in (data.get("failure_modes") or [])
               if str(item).strip() not in FAILURE_MODES]
    if unknown:
        missing.append(
            "failure_modes has unknown ids " + json.dumps(unknown)
            + " (valid: " + ", ".join(sorted(FAILURE_MODES)) + ")")
    # novel_resources (docs/phase6_step1_authoring_scope.md section 1) is OPTIONAL at the record
    # level -- a decision with no novel resources needs no entries here at all. But once
    # present, every entry meets the same bar _valid_alternative enforces above: an entry
    # missing its justification or its alternatives_considered fails validation exactly like an
    # incomplete `alternatives` entry, rather than passing through as a lesser-checked field.
    novel_resources = data.get("novel_resources") or []
    if not isinstance(novel_resources, list):
        missing.append("novel_resources (must be a list)")
    else:
        for item in novel_resources:
            if not _valid_novel_resource(item):
                missing.append(
                    "novel_resources entry incomplete (needs resource_type, justification, "
                    "and at least one alternatives_considered item): " + json.dumps(item)
                )
    return (not missing), missing


def record_path(directory):
    return os.path.join(directory, FILENAME)


def _stamp(data, decided_by=""):
    data = dict(data)
    if not data.get("decided_at"):
        data["decided_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if decided_by:
        data["decided_by"] = decided_by
    return data


def save(path, data, decided_by=""):
    data = _stamp(data, decided_by=decided_by)
    if os.path.isdir(path):
        path = record_path(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return path


def write(directory, data, decided_by=""):
    data = _stamp(data, decided_by=decided_by)
    os.makedirs(directory, exist_ok=True)
    path = record_path(directory)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return path


def load(path):
    if os.path.isdir(path):
        path = record_path(path)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def require(data):
    ok, missing = validate(data)
    if not ok:
        raise ArchitectureDecisionIncomplete(missing)
    return data


def load_or_template(path, requirements_file="requirements.json"):
    return load(path) or template(requirements_file=requirements_file)


def set_summary(path, selected_architecture=None, decision_summary=None, decided_by=""):
    data = load_or_template(path)
    if selected_architecture is not None:
        data["selected_architecture"] = selected_architecture
    if decision_summary is not None:
        data["decision_summary"] = decision_summary
    save(path, data, decided_by=decided_by)
    return data


def add_modules(path, module_ids):
    data = load_or_template(path)
    known = {m["id"] for m in module_registry.list_modules()}
    unknown = [module_id for module_id in module_ids if module_id not in known]
    if unknown:
        raise ValueError("unknown module id(s): " + ", ".join(unknown))
    current = list(data.get("selected_modules") or [])
    for module_id in module_ids:
        if module_id not in current:
            current.append(module_id)
    data["selected_modules"] = current
    save(path, data)
    return data


def _append_unique(data, field, value):
    items = list(data.get(field) or [])
    if value not in items:
        items.append(value)
    data[field] = items


def add_list_item(path, field, value):
    if field not in {"assumptions", "risks", "validation", "rollback", "failure_modes", "sources"}:
        raise ValueError(f"unsupported list field: {field}")
    if field == "failure_modes" and str(value).strip() not in FAILURE_MODES:
        raise ValueError(
            f"unknown failure mode {value!r} (valid: {', '.join(sorted(FAILURE_MODES))})")
    data = load_or_template(path)
    _append_unique(data, field, value)
    save(path, data)
    return data


def add_alternative(path, name, decision, reason):
    data = load_or_template(path)
    alternatives = [
        item for item in (data.get("alternatives") or [])
        if _valid_alternative(item)
    ]
    entry = {"name": name, "decision": decision, "reason": reason}
    if entry not in alternatives:
        alternatives.append(entry)
    data["alternatives"] = alternatives
    save(path, data)
    return data


def add_novel_resource(path, resource_type, justification, alternatives_considered, grounding_examples=None):
    data = load_or_template(path)
    novel_resources = [
        item for item in (data.get("novel_resources") or [])
        if _valid_novel_resource(item)
    ]
    entry = {
        "resource_type": resource_type,
        "justification": justification,
        "alternatives_considered": list(alternatives_considered),
        "grounding_examples": list(grounding_examples or []),
    }
    if entry not in novel_resources:
        novel_resources.append(entry)
    data["novel_resources"] = novel_resources
    save(path, data)
    return data


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Architecture decision gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("template")
    t.add_argument("--requirements-file", default="requirements.json")
    c = sub.add_parser("check")
    c.add_argument("path")
    s = sub.add_parser("set")
    s.add_argument("path")
    s.add_argument("--architecture", required=True)
    s.add_argument("--summary", required=True)
    s.add_argument("--decided-by", default="")
    m = sub.add_parser("add-module")
    m.add_argument("path")
    m.add_argument("module_id", nargs="+")
    src = sub.add_parser("add-source")
    src.add_argument("path")
    src.add_argument("source")
    asm = sub.add_parser("add-assumption")
    asm.add_argument("path")
    asm.add_argument("assumption")
    risk = sub.add_parser("add-risk")
    risk.add_argument("path")
    risk.add_argument("risk")
    val = sub.add_parser("add-validation", help="how this design will be proven correct")
    val.add_argument("path")
    val.add_argument("validation")
    rb = sub.add_parser("add-rollback", help="how this design is undone if it fails")
    rb.add_argument("path")
    rb.add_argument("rollback")
    fm = sub.add_parser("add-failure-mode", help="TerraShark failure mode this design mitigates")
    fm.add_argument("path")
    fm.add_argument("failure_mode", choices=sorted(FAILURE_MODES))
    alt = sub.add_parser("add-alternative")
    alt.add_argument("path")
    alt.add_argument("--name", required=True)
    alt.add_argument("--decision", required=True)
    alt.add_argument("--reason", required=True)
    nr = sub.add_parser("add-novel-resource")
    nr.add_argument("path")
    nr.add_argument("--resource-type", required=True)
    nr.add_argument("--justification", required=True)
    nr.add_argument("--alternative-considered", dest="alternatives_considered", action="append", required=True)
    nr.add_argument("--grounding-example", dest="grounding_examples", action="append", default=[])
    args = ap.parse_args(argv)

    if args.cmd == "template":
        print(json.dumps(template(args.requirements_file), indent=2))
        return 0
    if args.cmd == "check":
        data = load(args.path)
        if data is None:
            print(f"[architecture] no record at {args.path}", flush=True)
            return 2
        ok, missing = validate(data)
        if ok:
            print("[architecture] complete")
            return 0
        print("[architecture] INCOMPLETE - unanswered:")
        for item in missing:
            print(f"  - {item}")
        return 2
    try:
        if args.cmd == "set":
            data = set_summary(args.path, args.architecture, args.summary, decided_by=args.decided_by)
        elif args.cmd == "add-module":
            data = add_modules(args.path, args.module_id)
        elif args.cmd == "add-source":
            data = add_list_item(args.path, "sources", args.source)
        elif args.cmd == "add-assumption":
            data = add_list_item(args.path, "assumptions", args.assumption)
        elif args.cmd == "add-risk":
            data = add_list_item(args.path, "risks", args.risk)
        elif args.cmd == "add-validation":
            data = add_list_item(args.path, "validation", args.validation)
        elif args.cmd == "add-rollback":
            data = add_list_item(args.path, "rollback", args.rollback)
        elif args.cmd == "add-failure-mode":
            data = add_list_item(args.path, "failure_modes", args.failure_mode)
        elif args.cmd == "add-alternative":
            data = add_alternative(args.path, args.name, args.decision, args.reason)
        elif args.cmd == "add-novel-resource":
            data = add_novel_resource(
                args.path, args.resource_type, args.justification,
                args.alternatives_considered, args.grounding_examples,
            )
        else:
            return 1
    except ValueError as exc:
        print(f"[architecture] REFUSED - {exc}")
        return 2
    ok, missing = validate(data)
    status = "complete" if ok else "incomplete"
    print(f"[architecture] updated ({status})")
    if missing:
        for item in missing:
            print(f"  - {item}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
