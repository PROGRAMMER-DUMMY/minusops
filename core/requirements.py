"""
Requirements gate — generation is bound to a recorded, justified requirements set.

The plan-hash gate binds *apply* to a reviewed plan; this binds *generation* to reviewed
requirements. grill-me writes a requirements record; the synthesizer refuses to generate until
it exists and every required field has a value **or an explicit deferral**. A vague request
therefore can't be silently guessed into infrastructure — it's blocked until the requirements
are gathered and justified, and the record becomes audit evidence for what was built and why.

Required functional fields: goal, system_class, at least one functional capability.
Required non-functional axes (value or "deferred: <reason>"): latency, scale, availability,
retention, security, budget.
"""
import datetime
import json
import os

REQUIRED_NFR = ["latency", "scale", "availability", "retention", "security", "budget"]
FILENAME = "requirements.json"


class RequirementsIncomplete(Exception):
    """Raised when generation is attempted without a complete requirements record."""

    def __init__(self, missing):
        self.missing = missing
        super().__init__("requirements incomplete: " + ", ".join(missing))


def template():
    """A blank record for grill-me to fill. Non-functional axes accept 'deferred: <reason>'."""
    return {
        "goal": "",
        "system_class": "",
        "stakeholders": "",
        "functional": [],
        "non_functional": {k: "" for k in REQUIRED_NFR},
        "constraints": "",
        "gathered_by": "",
        "gathered_at": "",
    }


def is_deferred(value):
    return isinstance(value, str) and value.strip().lower().startswith("deferred")


def _answered(value):
    return bool(str(value).strip()) and (is_deferred(value) or not str(value).strip().lower().startswith("deferred"))


def validate(data):
    """Return (ok, missing). `missing` names every unanswered required field (deferral counts as
    answered). A field is unanswered if it is empty / absent."""
    missing = []
    if not isinstance(data, dict):
        return False, ["(not a requirements object)"]
    if not str(data.get("goal", "")).strip():
        missing.append("goal")
    if not str(data.get("system_class", "")).strip():
        missing.append("system_class")
    functional = data.get("functional") or []
    if not (isinstance(functional, list) and any(str(x).strip() for x in functional)):
        missing.append("functional (at least one capability)")
    nfr = data.get("non_functional") or {}
    for axis in REQUIRED_NFR:
        val = nfr.get(axis, "")
        if not str(val).strip():
            missing.append(f"non_functional.{axis}")
    return (not missing), missing


def deferred_axes(data):
    """Non-functional axes that were explicitly deferred (for the record / audit)."""
    nfr = (data or {}).get("non_functional") or {}
    return [a for a in REQUIRED_NFR if is_deferred(nfr.get(a, ""))]


def record_path(directory):
    return os.path.join(directory, FILENAME)


def write(directory, data, gathered_by=""):
    data = dict(data)
    data.setdefault("gathered_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
    if gathered_by:
        data["gathered_by"] = gathered_by
    os.makedirs(directory, exist_ok=True)
    path = record_path(directory)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
    return path


def load(path):
    if os.path.isdir(path):
        path = record_path(path)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def require(data):
    """Fail-closed: raise RequirementsIncomplete unless the record is complete. Returns the record."""
    ok, missing = validate(data)
    if not ok:
        raise RequirementsIncomplete(missing)
    return data


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Requirements gate (generation is bound to requirements)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("template")
    c = sub.add_parser("check")
    c.add_argument("path")
    args = ap.parse_args(argv)

    if args.cmd == "template":
        print(json.dumps(template(), indent=2))
        return 0
    if args.cmd == "check":
        data = load(args.path)
        if data is None:
            print(f"[requirements] no record at {args.path}", flush=True)
            return 2
        ok, missing = validate(data)
        if ok:
            deferred = deferred_axes(data)
            note = f" ({len(deferred)} deferred: {', '.join(deferred)})" if deferred else ""
            print(f"[requirements] complete{note}")
            return 0
        print("[requirements] INCOMPLETE — unanswered:")
        for m in missing:
            print(f"  - {m}")
        return 2
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
