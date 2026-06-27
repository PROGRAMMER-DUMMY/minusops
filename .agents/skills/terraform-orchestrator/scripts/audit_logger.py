import os
import sys
import json
import datetime
import argparse
import getpass

def log_audit_event(action, details, log_dir):
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "audit.jsonl")

    # Gather system metadata
    username = getpass.getuser()
    timestamp = datetime.datetime.utcnow().isoformat() + "Z"
    
    event = {
        "timestamp": timestamp,
        "operator": username,
        "action": action,
        "details": details,
        "status": "RECORDED"
    }

    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
        print(f"[AUDIT] Event successfully logged: {action}")
        return True
    except Exception as e:
        print(f"[ERR] Failed to write to audit log: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit Logger for Agentic DevOps")
    parser.add_argument("--action", required=True, help="Action name being executed")
    parser.add_argument("--details", required=True, help="Detailed description of the action")
    parser.add_argument("--log-dir", default=os.path.join(os.getcwd(), ".agents", "logs"), help="Path to audit logs directory")

    args = parser.parse_args()
    success = log_audit_event(args.action, args.details, args.log_dir)
    sys.exit(0 if success else 1)
