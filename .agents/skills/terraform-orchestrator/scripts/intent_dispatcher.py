import os
import sys
import re
import argparse
import subprocess

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
        "script": "hitl_gatekeeper.py",
        "default_args": ["--plan-file", "tfplan"],
        "description": "Triggering infrastructure deployment pipeline and prompting approval gate..."
    },
    "OPTIMIZE": {
        "keywords": ["optimize", "bug", "security", "vulnerability", "audit", "scan", "compliance", "fault", "speed", "performance"],
        "script": "optimize_analyzer.py",
        "default_args": ["--source-dir", "./aws-medallion-pipeline"],
        "description": "Scanning infrastructure configurations for cost, security, and performance gaps..."
    },
    "BUDGET": {
        "keywords": ["budget", "forecast", "estimate cost", "calculate cost", "pricing", "how much will", "projected cost"],
        "script": "budget_calculator.py",
        "default_args": ["--scale", "4", "--duration", "6", "--runs-daily", "24", "--s3-gb", "200"],
        "description": "Calculating AWS pipeline monthly billing forecast based on sizing parameters..."
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

def dispatch_task(intent, query):
    config = INTENT_MAPPING[intent]
    print(f"\n[DISPATCHER] Vague Query: \"{query}\"")
    print(f"[DISPATCHER] Classified Intent: {intent}")
    print(f"[DISPATCHER] Action: {config['description']}")

    # Secure Out-Of-Workspace Bin folder (built with os.path so it resolves on any OS)
    secure_bin_dir = os.path.expanduser(os.path.join("~", ".gemini", "antigravity-cli", "scratch", "bin"))
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Map paths based on where scripts reside
    if intent == "DEPLOY":
        # Gatekeeper is in the secure bin folder outside the workspace
        script_path = os.path.join(secure_bin_dir, config["script"])
    elif intent in ["OPTIMIZE", "BUDGET"]:
        script_path = os.path.join(os.getcwd(), ".agents", "skills", "pipeline-optimizer", "scripts", config["script"])
    else:
        script_path = os.path.join(script_dir, config["script"])

    if not os.path.exists(script_path):
        print(f"[ERR] Script not found: {script_path}", file=sys.stderr)
        return False

    run_args = list(config["default_args"])

    # Construct and run python command
    cmd = ["python", script_path] + run_args
    try:
        res = subprocess.run(cmd, check=True)
        return res.returncode == 0
    except subprocess.CalledProcessError as e:
        print(f"[ERR] Dispatched task returned an execution error: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Natural Language Intent Classifier & Task Dispatcher")
    parser.add_argument("query", help="Vague natural language operator query")
    
    args = parser.parse_args()
    intent = classify_intent(args.query)
    
    if intent == "UNKNOWN":
        print(f"\n[DISPATCHER] Could not confidently classify intent for query: \"{args.query}\"")
        sys.exit(1)
        
    success = dispatch_task(intent, args.query)
    sys.exit(0 if success else 1)
