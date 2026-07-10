import os
import sys
import re
import argparse
import subprocess

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)

try:
    from intent_resolver import resolve as resolve_intent, format_resolution
except ImportError:
    resolve_intent = None
    format_resolution = None

# Intent Dictionary mapping user keywords to target execution scripts
INTENT_MAPPING = {
    "HEALTH": {
        "keywords": ["health", "status", "uptime", "check", "alive", "running", "fine", "ok", "diagnostic"],
        "script": "health_checker.py",
        "default_args": [],
        "description": "Running environment diagnostics and verifying AWS connection status..."
    },
    "DEPLOY": {
        "keywords": ["deploy", "apply", "build", "push", "infrastructure", "release", "provision"],
        "script": os.path.join("..", "governance", "plan_gate.py"),
        "default_args": ["run"],
        "dir_flag": "--dir",  # generic engine: caller must say WHICH terraform dir
        "description": "Running the plan-bound deploy gate (verify -> plan -> approve -> apply)..."
    },
    "OPTIMIZE": {
        "keywords": ["optimize", "bug", "security", "vulnerability", "audit", "scan", "compliance", "fault", "speed", "performance"],
        "script": "optimize_analyzer.py",
        "default_args": [],
        "dir_flag": "--source-dir",  # generic engine: caller must say WHICH dir to scan
        "description": "Scanning infrastructure configurations for cost, security, and performance gaps..."
    },
    "BUDGET": {
        "keywords": ["budget", "forecast", "estimate cost", "calculate cost", "pricing", "how much will", "projected cost"],
        "script": os.path.join("..", "cost", "budget_calculator.py"),
        "default_args": [],
        "description": "Returning cost guidance — reportable totals require the AWS BCM Pricing Calculator API..."
    },
    "FINOPS": {
        "keywords": ["finops", "anomaly", "anomalies", "correlate", "cloudtrail", "root cause", "who owns",
                     "owner", "overspend", "spike", "spend breakdown", "actual cost", "why did", "went up"],
        "script": "finops_agent.py",
        "default_args": [],
        "description": "Analyzing live AWS spend, surfacing cost anomalies, and correlating root causes..."
    }
}

def classify_intent(query):
    if resolve_intent:
        resolution = resolve_intent(query)
        if resolution["intent"] in ("REQUIREMENTS", "BLUEPRINT", "ASK_CLARIFICATION"):
            return resolution["intent"]

    query_clean = query.strip().lower()
    
    # Calculate keyword matches score for each intent
    scores = {}
    for intent, config in INTENT_MAPPING.items():
        score = 0
        for kw in config["keywords"]:
            if re.search(r'\b' + re.escape(kw) + r'\b', query_clean):
                score += 2
            elif kw in query_clean:
                score += 1
        scores[intent] = score

    # Find the highest scoring intent
    best_intent = max(scores, key=scores.get)
    if scores[best_intent] > 0:
        return best_intent
    return "UNKNOWN"

def dispatch_task(intent, query, target_dir=None):
    if intent in ("REQUIREMENTS", "BLUEPRINT", "ASK_CLARIFICATION"):
        resolution = resolve_intent(query)
        print(format_resolution(resolution))
        return intent in ("REQUIREMENTS", "BLUEPRINT")

    config = INTENT_MAPPING[intent]
    print(f"\n[DISPATCHER] Vague Query: \"{query}\"")
    print(f"[DISPATCHER] Classified Intent: {intent}")
    print(f"[DISPATCHER] Action: {config['description']}")

    # All tools live alongside the dispatcher in core/.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(script_dir, config["script"])

    if not os.path.exists(script_path):
        print(f"[ERR] Script not found: {script_path}", file=sys.stderr)
        return False

    run_args = list(config["default_args"])

    # Intents that act on a Terraform directory need an explicit target — this is a
    # workload-agnostic engine, so there is no bundled default to fall back to.
    dir_flag = config.get("dir_flag")
    if dir_flag:
        if not target_dir:
            print(f"[ERR] The {intent} intent needs a target directory. "
                  f"Re-run with --dir <terraform-dir>.", file=sys.stderr)
            return False
        run_args += [dir_flag, target_dir]

    # Construct and run python command
    cmd = [sys.executable, script_path] + run_args
    try:
        res = subprocess.run(cmd, check=True)
        return res.returncode == 0
    except subprocess.CalledProcessError as e:
        print(f"[ERR] Dispatched task returned an execution error: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Natural Language Intent Classifier & Task Dispatcher")
    parser.add_argument("query", help="Vague natural language operator query")
    parser.add_argument("--dir", help="Target Terraform directory for DEPLOY / OPTIMIZE intents")

    args = parser.parse_args()
    intent = classify_intent(args.query)

    if intent == "UNKNOWN":
        print(f"\n[DISPATCHER] Could not confidently classify intent for query: \"{args.query}\"")
        sys.exit(1)

    success = dispatch_task(intent, args.query, target_dir=args.dir)
    sys.exit(0 if success else 1)
