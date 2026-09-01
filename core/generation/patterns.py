"""
Approved-pattern registry — the cache that lets the blueprint set grow from real, governed work.

When a synthesized composition is approved and deployed, capture it here: the requirements it
served and the module set that satisfied them. The next similar request can then reuse a
proven, governed composition instead of re-researching from scratch. This is how MinusOps gets
the *adaptability* of research-driven synthesis and the *reliability* of vetted recipes without
hand-authoring monolithic blueprints up front.

Stored at .minus/patterns.json (next to approvers.json) so a team can commit and share it.

Depends on: core/generation/modules.py (as module_registry)
Shells out to: nothing
Used by: nothing in core/ or app/ — CLI entrypoint (`python core/generation/patterns.py`)
    plus tests/test_patterns.py
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


# `match_modules` scores a whole-phrase hit at 3 and a single shared token at 1. The weak
# signal is right for RANKING -- it is why a near-miss still appears in the list for a human
# to see -- and wrong for SELECTION. Reuse scoring is selection: it asks "which modules would
# this request actually pull in", and one shared token ("lake" in "data lake" hitting
# `governance-lakeformation`'s "lake formation") is not an answer to that.
#
# Regression this threshold prevents (2026-08-22, PRD v8): with the weak hits included, every
# module added to the catalog grew the Jaccard denominator and pushed every stored pattern's
# reuse_score down. A catalog that grows would silently stop reusing approved compositions,
# and nothing would report it -- the scores just drift below min_overlap one by one.
_SELECTION_MIN_SCORE = 3


def _reuse_target(requirements):
    """Modules a request actually names -- at least one whole-phrase hit, not token noise."""
    return {m["id"] for m in module_registry.match_modules(
        requirements, min_score=_SELECTION_MIN_SCORE)}


def match_patterns(requirements, min_overlap=0.5):
    """
    Find prior approved patterns that fit new requirements, by overlap between the module set
    those requirements *would* select and each pattern's stored module set. Returns best-first
    with a `reuse_score`, so a near-identical request reuses a governed composition.
    """
    target = _reuse_target(requirements)
    out = []
    for p in load_patterns():
        score = _jaccard(target, p.get("modules", []))
        if score >= min_overlap:
            out.append({**p, "reuse_score": round(score, 3)})
    return sorted(out, key=lambda x: x["reuse_score"], reverse=True)


def promote_pattern(run_root=None, name=None, description="", skip_proof=False):
    """
    Promote an approved, proven architecture run to the pattern registry via Git PR.
    """
    import git_agent
    if not run_root or run_root == ".":
        try:
            import core.cli.context as context
            active = context.active_run()
            if not active:
                import runs as runs_mod
                lr = runs_mod.latest_run()
                if lr:
                    active = lr.get("run_id")
            if active:
                run_root = os.path.join("runs", active)
        except Exception:
            pass
    if not run_root:
        run_root = "."

    pr_result = git_agent.create_pattern_pull_request(
        run_root=run_root,
        pattern_name=name,
        description=description,
        skip_proof=skip_proof
    )
    # Also record in local registry
    try:
        with open(os.path.join(run_root, "architecture_decision.json"), "r", encoding="utf-8") as f:
            decision = json.load(f)
        capture_pattern(
            requirements=decision.get("decision_summary", description),
            module_ids=decision.get("selected_modules", []),
            name=name,
            plan_hash=pr_result.get("plan_hash"),
            approver=pr_result.get("operator")
        )
    except Exception:
        pass
    return pr_result


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
    p = sub.add_parser("promote")
    p.add_argument("--name", required=True, help="Pattern identifier name")
    p.add_argument("--run-root", default=".", help="Run root directory")
    p.add_argument("--description", default="", help="Pattern business rationale")
    p.add_argument("--skip-proof", action="store_true", help="Bypass UAT proving verification")
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
    if args.cmd == "promote":
        res = promote_pattern(args.run_root, args.name, args.description, skip_proof=args.skip_proof)
        print(f"[git-agent] Created PR branch: {res['branch']}")
        print(f"[git-agent] PR Title: {res['pr_title']}")
        print(f"[git-agent] Plan Hash: {res['plan_hash']}")
        print(f"[git-agent] UAT Status: {res['proving_status']}")
        return 0
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
