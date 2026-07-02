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

# --- Data-pipeline profile (additive; enforced only for data workloads) ------
# Functional fields map to the analytics reference architecture's six layers; the
# non-functional fields map to the Well-Architected Data Analytics Lens pillars.
# See memory `aws-reference-architectures-for-design`.
DATA_FR = ["sources", "storage_zones", "transforms", "catalog", "consumption"]      # ingestion..consumption
DATA_NFR = ["data_quality", "freshness_sla", "data_volume", "governance", "orchestration"]
DATA_FIELDS = DATA_FR + DATA_NFR
# What each field grounds to (for the record / audit / grill-me prompts).
DATA_FIELD_GROUNDING = {
    "sources": "Ingestion layer — what data comes in and over which protocol",
    "storage_zones": "Storage layer — raw/cleaned/curated (or bronze/silver/gold) zones",
    "transforms": "Processing layer — validation/clean/normalize/enrich steps",
    "catalog": "Cataloging layer — metadata catalog / schema registry strategy",
    "consumption": "Consumption layer — how data is queried/served (SQL, BI, ML)",
    "data_quality": "WA Operational Excellence BP 1.1 — source data-quality validation",
    "freshness_sla": "WA Reliability/Performance — freshness/latency SLA",
    "data_volume": "WA Performance — data volume and growth (scale)",
    "governance": "WA Security — access control, lineage, PII/sensitivity",
    "orchestration": "WA Reliability BP 6.x — scheduling/triggering + failure handling",
}
_DATA_SIGNALS = ("data", "pipeline", "lakehouse", "lake house", "etl", "elt",
                 "analytics", "warehouse", "streaming", "ingest", "medallion")


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
        "data_pipeline": {k: "" for k in DATA_FIELDS},
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


def is_data_pipeline(data):
    """Heuristic: does this record describe a data workload? (system_class/goal signal, or a
    populated data_pipeline block). Used to decide whether the data-pipeline profile applies."""
    data = data or {}
    text = (str(data.get("system_class", "")) + " " + str(data.get("goal", ""))).lower()
    if any(sig in text for sig in _DATA_SIGNALS):
        return True
    dp = data.get("data_pipeline") or {}
    return any(str(v).strip() for v in dp.values())


def validate_data_pipeline(data):
    """Return (ok, missing) for the data-pipeline FR/NFR profile. Each field is answered by a
    value or an explicit 'deferred: <reason>'. Only meaningful for data workloads."""
    dp = (data or {}).get("data_pipeline") or {}
    missing = [f"data_pipeline.{f}" for f in DATA_FIELDS if not str(dp.get(f, "")).strip()]
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
    dc = sub.add_parser("data-check", help="validate the data-pipeline FR/NFR profile (data workloads)")
    dc.add_argument("path")
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
    if args.cmd == "data-check":
        data = load(args.path)
        if data is None:
            print(f"[requirements] no record at {args.path}", flush=True)
            return 2
        if not is_data_pipeline(data):
            print("[requirements] not a data workload — data-pipeline profile not required")
            return 0
        ok, missing = validate_data_pipeline(data)
        if ok:
            print("[requirements] data-pipeline profile complete")
            return 0
        print("[requirements] data-pipeline profile INCOMPLETE — unanswered:")
        for m in missing:
            field = m.split(".", 1)[-1]
            print(f"  - {m}  ({DATA_FIELD_GROUNDING.get(field, '')})")
        return 2
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
