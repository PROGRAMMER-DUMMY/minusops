"""
Enterprise intent resolver.

Turns short user requests into safe, reviewable product decisions. It does not
generate Terraform and never deploys. Its job is to map intent to approved
blueprints and identify the minimum inputs needed before generation.

Depends on: core/generation/blueprints.py, core/providers/base.py (both imported
    relative-first, falling back to flat imports for the sys.path-injected CLI layout)
Shells out to: nothing
Used by: core/generation/workflow.py, tests/test_intent_resolver.py, tests/test_create_intent.py
"""

import argparse
import json
import os
import re
import sys

try:
    from .blueprints import list_blueprints, match_blueprints, validate_blueprints
    from ..providers.base import active_cloud
except ImportError:
    from blueprints import list_blueprints, match_blueprints, validate_blueprints
    from providers.base import active_cloud


CREATE_TERMS = (
    "create",
    "build",
    "set up",
    "setup",
    "provision",
    "generate",
    "scaffold",
    "make",
)

INFRA_TERMS = (
    "pipeline",
    "infrastructure",
    "terraform",
    "stack",
    "lakehouse",
    "analytics",
    "etl",
    "glue",
    "athena",
    "s3",
    "redshift",
    "databricks",
    "emr",
)


def _contains_term(query, terms):
    return any(re.search(r"\b" + re.escape(term) + r"\b", query) for term in terms)


# Words that make a phrase a QUESTION about existing infrastructure rather than a request to
# build some. Checked first, because "what does my pipeline cost" contains an infra term but is
# emphatically not a creation request.
_INTERROGATIVE_TERMS = (
    "what", "why", "how much", "how many", "when", "who", "which",
    "show", "list", "inspect", "check", "status", "health", "cost of", "failed", "failing",
    "debug", "diagnose", "explain",
)

# Verbs that act on infrastructure that ALREADY EXISTS. "deploy this infrastructure" names a
# deploy operation, not a request to design something new -- without this veto the loosened
# noun-phrase rule below would route every deploy request into the requirements gate.
_OPERATIONAL_TERMS = (
    "deploy", "apply", "destroy", "teardown", "tear down", "rollback", "roll back",
    "migrate", "optimize", "optimise", "scan", "audit", "upgrade", "patch", "restart",
)


def is_creation_request(query):
    """An infra noun phrase IS a creation request unless it reads as a question.

    Previously this required a create VERB *and* an infra NOUN, so
    `minusctl create "governed AWS data pipeline"` classified as OPERATION and silently
    created nothing while printing a success-shaped message. The user already typed
    `create`; making them repeat the verb inside the argument is a trap, and a silent
    no-op is the worst failure shape for an agent-driven CLI.

    Over-triggering is the risk this guards against in the other direction -- asking ABOUT a
    pipeline must not provision one -- so interrogatives veto, and an infra term is still
    required (a bare "make it faster" is not a creation request either).
    """
    normalized = " ".join((query or "").lower().split())
    if not normalized:
        return False
    if not _contains_term(normalized, INFRA_TERMS):
        return False
    # An explicit create verb settles it, even alongside other words.
    if _contains_term(normalized, CREATE_TERMS):
        return True
    # Otherwise a bare infra noun phrase counts, unless it reads as a question about, or an
    # operation on, infrastructure that already exists.
    if _contains_term(normalized, _INTERROGATIVE_TERMS):
        return False
    if _contains_term(normalized, _OPERATIONAL_TERMS):
        return False
    return True


def _missing_inputs(blueprint):
    return [
        item for item in blueprint["required_inputs"]
        if item.get("default") is None
    ]


def resolve(query, cloud=None):
    """
    Resolve a natural-language request into a safe action.

    Return shape is stable for CLI, tests, and future agent integrations.
    """
    cloud = (cloud or active_cloud()).strip().lower()
    creation = is_creation_request(query)

    if creation:
        return {
            "intent": "REQUIREMENTS",
            "query": query,
            "cloud": cloud,
            "confidence": "high",
            "blueprint": None,
            "missing_inputs": [],
            "recommendation": (
                "Create a requirements-first run. Do not generate Terraform until "
                "requirements and an architecture decision are recorded."
            ),
            "next_safe_actions": [
                "Write a requirements.json skeleton into the run workspace.",
                "Gather functional and non-functional requirements.",
                "Research candidate architectures against official provider documentation.",
                "Record architecture_decision.json before Terraform generation.",
                "Only then synthesize Terraform and enter the deploy gate.",
            ],
        }

    return {
        "intent": "OPERATION",
        "query": query,
        "cloud": cloud,
        "confidence": "none",
        "blueprint": None,
        "missing_inputs": [],
        "recommendation": "Use the direct tool path.",
        "next_safe_actions": [],
    }


def format_resolution(result):
    lines = [
        "[RESOLVER] Enterprise intent resolution",
        f"  query      : {result['query']}",
        f"  intent     : {result['intent']}",
        f"  cloud      : {result['cloud']}",
        f"  confidence : {result['confidence']}",
        f"  recommend  : {result['recommendation']}",
    ]

    blueprint = result.get("blueprint")
    if blueprint:
        lines.extend([
            f"  blueprint  : {blueprint['id']}",
            f"  summary    : {blueprint['summary']}",
            "  controls   :",
        ])
        lines.extend(f"    - {control}" for control in blueprint["controls"])
        if result["missing_inputs"]:
            lines.append("  required inputs:")
            lines.extend(
                f"    - {item['name']}: {item['prompt']} ({item['reason']})"
                for item in result["missing_inputs"]
            )
    elif result.get("available_blueprints"):
        lines.append("  available blueprints:")
        lines.extend(f"    - {blueprint_id}" for blueprint_id in result["available_blueprints"])

    if result["next_safe_actions"]:
        lines.append("  next safe actions:")
        lines.extend(f"    - {action}" for action in result["next_safe_actions"])

    return os.linesep.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Resolve short enterprise intent into requirements-first creation paths")
    parser.add_argument("query", nargs="?", help="Natural language user request")
    parser.add_argument("--cloud", default=None, help="Cloud filter, defaults to MINUS_CLOUD")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--list-blueprints", action="store_true", help="List registered blueprints")
    parser.add_argument("--validate-blueprints", action="store_true", help="Validate blueprint registry schema")
    args = parser.parse_args(argv)

    if args.validate_blueprints:
        errors = validate_blueprints()
        if args.json:
            print(json.dumps({"ok": not errors, "errors": errors}, indent=2))
        elif errors:
            print("[RESOLVER] Blueprint validation failed")
            for blueprint_id, messages in errors.items():
                print(f"  {blueprint_id}:")
                for message in messages:
                    print(f"    - {message}")
        else:
            print("[RESOLVER] Blueprint registry OK")
        return 0 if not errors else 1

    if args.list_blueprints:
        blueprints = list_blueprints(args.cloud)
        if args.json:
            print(json.dumps({"blueprints": blueprints}, indent=2))
        else:
            print("[RESOLVER] Registered blueprints")
            for blueprint in blueprints:
                print(f"  - {blueprint['id']} ({blueprint['cloud']}): {blueprint['summary']}")
        return 0

    if not args.query:
        parser.error("query is required unless --list-blueprints or --validate-blueprints is used")

    result = resolve(args.query, cloud=args.cloud)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(format_resolution(result))
    return 0 if result["intent"] != "ASK_CLARIFICATION" else 2


if __name__ == "__main__":
    sys.exit(main())
