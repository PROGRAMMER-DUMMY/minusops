"""
Cross-platform environment diagnostics (`minusctl doctor`).

Replaces `tools/doctor.ps1`, which only ran under Windows PowerShell and so was useless
in CI containers, on macOS, and on Linux — the three places a governed deploy is most
likely to run unattended.

Every check reports one of three statuses:
  ok    — present and usable
  warn  — a feature degrades but the core governed-deploy loop still works
          (rego gate without `opa`, dashboard without `dash`, production policy mode
          without an external scanner)
  error — the core loop cannot run at all (no terraform, no cloud CLI, no credentials)

Discovery goes through `toolpath.find_tool` (which refreshes PATH from the Windows
registry first), and the credential probe goes through the provider abstraction rather
than shelling out to `aws` directly, per AGENTS.md §1.
"""
import argparse
import importlib.util
import json
import os
import platform
import subprocess
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)

import toolpath  # noqa: E402
from providers.base import get_provider  # noqa: E402
from optimize_analyzer import EXTERNAL_SCANNERS  # noqa: E402


def _check(name, status, detail, fix=""):
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def _version(path, args):
    """First line of the tool's own version output, or a short failure note."""
    try:
        result = subprocess.run([path, *args], capture_output=True, text=True, timeout=15)
    except Exception as exc:  # tool exists but won't execute (wrong arch, broken install)
        return f"(version probe failed: {exc})"
    text = (result.stdout or result.stderr or "").strip()
    return text.splitlines()[0] if text else "(no version output)"


def _cli_check(name, tool, version_args, required, fix):
    path = toolpath.find_tool(tool)
    if not path:
        return _check(name, "error" if required else "warn", f"`{tool}` not found on PATH", fix)
    return _check(name, "ok", f"{_version(path, version_args)}  [{path}]")


def _python_check():
    version = platform.python_version()
    detail = f"Python {version}  [{sys.executable}]"
    if sys.version_info < (3, 10):
        return _check("python", "warn", detail, "MinusOps targets Python 3.10+.")
    return _check("python", "ok", detail)


def _packages_check():
    missing = [pkg for pkg in ("dash", "plotly") if importlib.util.find_spec(pkg) is None]
    if missing:
        return _check("python packages", "warn", f"missing: {', '.join(missing)}",
                      "pip install -r requirements.txt (needed only for `app/dashboard_app.py`)")
    return _check("python packages", "ok", "dash, plotly importable")


def _credentials_check():
    """Connected + credential posture. Long-term keys are a warn, not an ok: an
    unattended auto-approve run holding static keys can apply real infrastructure."""
    if not toolpath.find_tool("aws"):
        return _check("cloud credentials", "error", "skipped: AWS CLI not available",
                      "Install the AWS CLI, then run `aws sso login`.")
    try:
        posture = get_provider().credential_posture()
    except Exception as exc:
        return _check("cloud credentials", "error", f"credential probe failed: {exc}",
                      "Run `aws sts get-caller-identity` and resolve the error it reports.")
    if not posture.get("connected"):
        return _check("cloud credentials", "error",
                      posture.get("error") or "no valid credentials found",
                      "Run `aws sso login` (preferred) or `aws configure`.")
    kind = posture.get("type", "unknown")
    detail = f"account {posture.get('account')} as {posture.get('arn')} ({kind})"
    if kind in ("long_term", "root"):
        return _check("cloud credentials", "warn", detail,
                      "Prefer SSO / an assumed MFA-gated role; static or root credentials "
                      "let an unattended apply mutate real infrastructure.")
    return _check("cloud credentials", "ok", detail)


def _scanner_check():
    found = [s for s in EXTERNAL_SCANNERS if toolpath.find_tool(s)]
    if found:
        return _check("policy scanners", "ok", f"available: {', '.join(found)}")
    return _check("policy scanners", "warn",
                  f"none of {', '.join(EXTERNAL_SCANNERS)} on PATH",
                  "MINUS_POLICY_MODE=production requires one of these; the native SEC scan "
                  "still runs without them.")


def diagnose():
    """Run every check. Returns {"ok": bool, "checks": [...]} — ok is False iff any error."""
    checks = [
        _python_check(),
        _cli_check("terraform", "terraform", ("version",), True,
                   "Install Terraform (winget install Hashicorp.Terraform / brew install terraform)."),
        _cli_check("aws cli", "aws", ("--version",), True,
                   "Install the AWS CLI v2 (winget install Amazon.AWSCLI / brew install awscli)."),
        _credentials_check(),
        _cli_check("opa", "opa", ("version",), False,
                   "Install OPA to enable the Rego plan gate; it degrades to warn-only without it."),
        _cli_check("tflint", "tflint", ("--version",), False,
                   "Install TFLint for provider-level lint findings in optimize_analyzer; "
                   "run `tflint --init` in a Terraform dir to add the AWS ruleset."),
        _scanner_check(),
        _packages_check(),
    ]
    return {"ok": not any(c["status"] == "error" for c in checks), "checks": checks}


_MARK = {"ok": "[OK]  ", "warn": "[WARN]", "error": "[ERR] "}


def format_result(result):
    lines = [f"MinusOps doctor - {platform.system()} {platform.release()}", ""]
    for check in result["checks"]:
        lines.append(f"{_MARK[check['status']]} {check['name']}: {check['detail']}")
        if check.get("fix"):
            lines.append(f"        fix: {check['fix']}")
    lines.append("")
    errors = [c["name"] for c in result["checks"] if c["status"] == "error"]
    lines.append("environment ready" if result["ok"] else "blocked on: " + ", ".join(errors))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Diagnose the local MinusOps environment.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result = diagnose()
    print(json.dumps(result, indent=2) if args.json else format_result(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
