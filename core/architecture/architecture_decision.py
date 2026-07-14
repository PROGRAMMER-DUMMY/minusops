"""
Architecture decision gate.

Requirements say what must be built. This record says why a particular architecture and module
set was selected after research. Production synthesis is bound to this file so keyword matching
cannot silently become a recommendation engine.
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
    if not _nonempty_list(data.get("selected_modules")):
        missing.append("selected_modules (at least one module id)")
    alternatives = data.get("alternatives") or []
    if not (isinstance(alternatives, list) and any(_valid_alternative(item) for item in alternatives)):
        missing.append("alternatives (at least one named choice with decision and reason)")
    for field in ("assumptions", "risks", "sources"):
        if not _nonempty_list(data.get(field)):
            missing.append(f"{field} (at least one item)")
    # novel_resources (docs/phase6_step1_authoring_scope.md section 1) is additive and OPTIONAL
    # at the record level -- a decision with no novel resources needs no entries here at all.
    # But once present, every entry is held to the same completeness bar _valid_alternative
    # already enforces above: an incomplete entry (missing justification, or no
    # alternatives_considered answered) fails validation exactly like an incomplete
    # `alternatives` entry does, rather than silently passing through as a lesser-checked field.
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
    if field not in {"assumptions", "risks", "sources"}:
        raise ValueError(f"unsupported list field: {field}")
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
