"""
Approved-pattern registry — the cache that lets the blueprint set grow from real, governed work.

When a synthesized composition is approved and deployed, capture it here: the requirements it
served and the module set that satisfied them. The next similar request can then reuse a
proven, governed composition instead of re-researching from scratch. This is how MinusOps gets
the *adaptability* of research-driven synthesis and the *reliability* of vetted recipes without
hand-authoring monolithic blueprints up front.

Stored at .minus/patterns.json (next to approvers.json) so a team can commit and share it.
"""
import datetime
import json
import os

import modules as module_registry

WORKSPACE = os.getcwd()


def _patterns_file():
    return os.path.join(WORKSPACE, ".minus", "patterns.json")


def load_patterns():
    path = _patterns_file()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(patterns):
    path = _patterns_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(patterns, f, indent=2)
    return path


def capture_pattern(requirements, module_ids, name=None, plan_hash=None, approver=None):
    """Persist an approved composition. Returns the stored pattern."""
    valid = [m for m in module_ids if module_registry.get_module(m)]
    pattern = {
        "id": name or f"pattern-{len(load_patterns()) + 1:03d}",
        "requirements": requirements,
        "modules": valid,
        "plan_hash": plan_hash,
        "approver": approver,
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    patterns = load_patterns()
    patterns.append(pattern)
    _save(patterns)
    return pattern


def get_pattern(pattern_id):
    for p in load_patterns():
        if p.get("id") == pattern_id:
            return p
    return None


def _jaccard(a, b):
    a, b = set(a), set(b)
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_patterns(requirements, min_overlap=0.5):
    """
    Find prior approved patterns that fit new requirements, by overlap between the module set
    those requirements *would* select and each pattern's stored module set. Returns best-first
    with a `reuse_score`, so a near-identical request reuses a governed composition.
    """
    target = {m["id"] for m in module_registry.match_modules(requirements)}
    out = []
    for p in load_patterns():
        score = _jaccard(target, p.get("modules", []))
        if score >= min_overlap:
            out.append({**p, "reuse_score": round(score, 3)})
    return sorted(out, key=lambda x: x["reuse_score"], reverse=True)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Approved architecture-pattern registry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("requirements")
    c.add_argument("--module", action="append", default=[], required=True)
    c.add_argument("--name", default=None)
    c.add_argument("--plan-hash", default=None)
    c.add_argument("--approver", default=None)
    sub.add_parser("list")
    m = sub.add_parser("match")
    m.add_argument("requirements")
    args = ap.parse_args(argv)

    if args.cmd == "capture":
        p = capture_pattern(args.requirements, args.module, name=args.name,
                            plan_hash=args.plan_hash, approver=args.approver)
        print(f"captured {p['id']}: {', '.join(p['modules'])}")
        return 0
    if args.cmd == "list":
        for p in load_patterns():
            print(f"{p['id']:<16} {', '.join(p.get('modules', []))}")
        return 0
    if args.cmd == "match":
        for p in match_patterns(args.requirements):
            print(f"[{p['reuse_score']:.2f}] {p['id']:<16} {', '.join(p.get('modules', []))}")
        return 0
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
