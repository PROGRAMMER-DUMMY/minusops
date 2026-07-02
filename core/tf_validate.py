"""
Offline Terraform configuration validation — a non-mutating, credential-free correctness check.

`terraform validate` needs no cloud credentials and changes nothing; it catches syntax, type,
and reference errors in the generated config. The system runs it automatically after synthesis
so a composed plan is proven well-formed *before* it ever reaches the deploy gate (which then
does the credentialed, still-read-only `plan`). This is the cheapest place to catch a bad plan.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import toolpath  # noqa: E402

FILENAME = "validation.json"


def validate(dir_, timeout=180):
    """Run `terraform init -backend=false` + `terraform validate -json` (offline, no creds).

    Returns a dict: {available, ok, ...}. `ok` is None when terraform isn't installed (skipped,
    not a failure). Never raises.
    """
    tf = toolpath.find_tool("terraform")
    if not tf:
        return {"available": False, "ok": None, "reason": "terraform not found on PATH"}
    if not os.path.isdir(dir_):
        return {"available": True, "ok": False, "reason": f"terraform dir not found: {dir_}"}

    def _run(*args):
        try:
            r = subprocess.run([tf, f"-chdir={dir_}", *args],
                               capture_output=True, text=True, timeout=timeout)
            return r.returncode, r.stdout or "", r.stderr or ""
        except subprocess.TimeoutExpired:
            return 124, "", "terraform timed out"
        except FileNotFoundError:
            return 127, "", "terraform not found"

    rc, out, err = _run("init", "-backend=false", "-input=false")
    if rc != 0:
        return {"available": True, "ok": False, "phase": "init", "reason": (err or out).strip()[:2000]}

    rc, out, err = _run("validate", "-json")
    try:
        data = json.loads(out)
    except Exception:
        return {"available": True, "ok": rc == 0, "phase": "validate", "reason": (err or out).strip()[:2000]}

    diags = data.get("diagnostics", []) or []
    errors = [d for d in diags if d.get("severity") == "error"]
    warnings = [d for d in diags if d.get("severity") == "warning"]
    return {
        "available": True,
        "ok": bool(data.get("valid")) and not errors,
        "phase": "validate",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "diagnostics": [
            {"severity": d.get("severity"), "summary": d.get("summary"),
             "detail": (d.get("detail") or "")[:300]}
            for d in diags[:20]
        ],
    }


def validate_and_record(dir_, timeout=180):
    """Validate and persist the result to <dir>/validation.json for later readiness reads."""
    result = validate(dir_, timeout=timeout)
    try:
        if os.path.isdir(dir_):
            with open(os.path.join(dir_, FILENAME), "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
    except OSError:
        pass
    return result


def load(dir_):
    path = os.path.join(dir_, FILENAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _format(result):
    if not result or result.get("available") is False:
        return "[validate] skipped — terraform not on PATH (install terraform to self-check the plan)"
    if result.get("ok"):
        w = result.get("warning_count", 0)
        return f"[validate] OK — configuration is valid" + (f" ({w} warning(s))" if w else "")
    lines = [f"[validate] INVALID — {result.get('error_count', '?')} error(s) in {result.get('phase', 'validate')}"]
    if result.get("reason"):
        lines.append("  " + result["reason"].splitlines()[0])
    for d in result.get("diagnostics", []):
        if d.get("severity") == "error":
            lines.append(f"  - {d.get('summary')}")
    return "\n".join(lines)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python core/tf_validate.py <terraform-dir>", file=sys.stderr)
        return 2
    result = validate(argv[0])
    print(_format(result))
    return 0 if result.get("ok") or result.get("ok") is None else 2


if __name__ == "__main__":
    sys.exit(main())
