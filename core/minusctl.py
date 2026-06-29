"""
Operator-facing CLI for MinusOps.

This is a thin safe wrapper around the repo tools. It does not run Terraform,
cloud CLIs, or mutating commands. Commands either create local run files, inspect
local artifacts, or print the next safe command to run.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_chain  # noqa: E402
import demo  # noqa: E402
import plan_inspector  # noqa: E402
import runs  # noqa: E402
import source_guard  # noqa: E402
import workflow  # noqa: E402


def _json_or_text(data, as_json, text):
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        print(text)


def _latest_run_or_exit():
    run = runs.latest_run()
    if not run:
        raise SystemExit("no run workspaces found")
    return run


def _run_by_id_or_latest(run_id=None):
    if not run_id or run_id == "latest":
        return _latest_run_or_exit()
    for item in runs.list_runs():
        if item.get("run_id") == run_id or item.get("run_id", "").startswith(run_id):
            return item
    raise SystemExit(f"run not found: {run_id}")


def _terraform_dir(args):
    if getattr(args, "dir", None):
        return args.dir
    run = _run_by_id_or_latest(getattr(args, "run", None))
    return run["terraform_dir"]


def _format_run(run):
    return "\n".join([
        f"run        : {run['run_id']}",
        f"blueprint  : {run.get('blueprint', '-')}",
        f"cloud      : {run.get('cloud', '-')}",
        f"terraform  : {run.get('terraform_dir', '-')}",
        f"reports    : {run.get('reports_dir', '-')}",
    ])


def _print_runs(as_json=False):
    items = runs.list_runs()
    if as_json:
        print(json.dumps(items, indent=2))
        return
    if not items:
        print("no runs")
        return
    for item in items:
        print(f"{item['run_id']}\t{item.get('blueprint', '-')}\t{item.get('terraform_dir', '-')}")


def _report_id(args):
    return "latest" if getattr(args, "latest", False) else args.report


def _print_report_command(command, args):
    if command == "list":
        plan_inspector._print_list()
        return
    report_id = _report_id(args)
    if not report_id:
        raise SystemExit("--report or --latest is required")
    _, manifest, plan = plan_inspector.load_report(report_id)
    if command == "status":
        print(json.dumps(plan_inspector.source_status(report_id), indent=2))
    elif command == "services":
        plan_inspector._print_services(plan)
    elif command == "resources":
        plan_inspector._print_resources(plan)
    elif command == "roles":
        plan_inspector._print_roles(plan)
    elif command == "files":
        plan_inspector._print_files(report_id)
    elif command == "diff":
        print("\n".join(plan_inspector.diff_source(report_id)))
    else:
        raise SystemExit(f"unknown report command: {command}")


def _run_reports(run):
    reports = []
    root = Path(run["reports_dir"])
    if root.exists():
        for path in root.iterdir():
            manifest = path / "manifest.json"
            plan = path / "plan.json"
            if not path.is_dir() or not manifest.exists():
                continue
            try:
                meta = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            reports.append({
                "id": path.name,
                "generated_at": meta.get("generated_at", ""),
                "path": str(path),
                "has_plan_json": plan.exists(),
                "manifest": meta,
            })
    reports.sort(key=lambda item: item.get("generated_at", ""), reverse=True)
    return reports


def _next_steps(run):
    tf_dir = run["terraform_dir"]
    guard = source_guard.status(tf_dir)
    reports = _run_reports(run)
    lines = [
        "Safe next steps",
        f"run        : {run['run_id']}",
        f"terraform  : {tf_dir}",
        f"source     : {guard['status']}",
    ]
    if guard["status"] == "STALE":
        lines.append("review diff : python core/minusctl.py guard diff --run " + run["run_id"])
    if not reports:
        lines.extend([
            "verify     : python core/plan_gate.py verify --dir " + tf_dir,
            "plan       : python core/plan_gate.py plan --dir " + tf_dir,
            "reports    : none yet",
        ])
    else:
        lines.extend([
            "latest rpt : " + reports[0]["id"],
            "inspect    : python core/minusctl.py reports services --latest",
            "drift      : python core/minusctl.py reports diff --latest",
        ])
    lines.append("blocked    : do not apply until a reviewed plan hash is approved")
    return {"run": run, "source": guard, "reports": reports, "text": "\n".join(lines)}


def _read_workflow(run):
    path = Path(run["root"]) / "workflow.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _generated_files(run):
    manifest = Path(run["terraform_dir"]) / "minus-generated.json"
    if not manifest.exists():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data.get("files", [])


def _latest_report_details(reports):
    if not reports:
        return {}
    item = reports[0]
    if not item.get("has_plan_json"):
        return {"id": item["id"], "path": item["path"]}
    try:
        report_path = Path(item["path"])
        manifest = json.loads((report_path / "manifest.json").read_text(encoding="utf-8"))
        plan = json.loads((report_path / "plan.json").read_text(encoding="utf-8"))
    except Exception:
        return {"id": item["id"], "path": item["path"], "error": "report could not be loaded"}
    services = {
        service: len(rows)
        for service, rows in plan_inspector.services(plan).items()
    }
    roles = plan_inspector.iam_roles(plan)
    return {
        "id": item["id"],
        "path": item["path"],
        "generated_at": manifest.get("generated_at"),
        "template": manifest.get("template"),
        "counts": manifest.get("counts", {}),
        "cost": manifest.get("cost", {}),
        "services": services,
        "iam_role_count": len(roles.get("roles", [])),
        "iam_policy_count": len(roles.get("policies", [])),
        "source_status": _report_source_status(Path(item["path"]), manifest),
    }


def _report_source_status(report_path, manifest):
    source_dir = manifest.get("dir")
    hash_path = report_path / "source_hashes.json"
    if not source_dir or not hash_path.exists():
        return {"status": "UNKNOWN", "stale": False, "reason": "source snapshot unavailable"}
    try:
        saved = json.loads(hash_path.read_text(encoding="utf-8"))
        current = plan_inspector.source_hashes(source_dir)
    except Exception as exc:
        return {"status": "UNKNOWN", "stale": False, "reason": str(exc)}
    changed = sorted(k for k in saved if k in current and saved[k] != current[k])
    missing = sorted(k for k in saved if k not in current)
    added = sorted(k for k in current if k not in saved)
    stale = bool(changed or missing or added)
    return {
        "status": "STALE" if stale else "CURRENT",
        "stale": stale,
        "changed": changed,
        "missing": missing,
        "added": added,
    }


def _check(name, ok, severity, detail, fix):
    return {
        "name": name,
        "ok": bool(ok),
        "severity": severity,
        "detail": detail,
        "fix": fix,
    }


def _readiness(run):
    tf_dir = Path(run["terraform_dir"])
    reports = _run_reports(run)
    source = source_guard.status(tf_dir)
    generated_files = _generated_files(run)
    latest = _latest_report_details(reports)
    latest_path = Path(latest["path"]) if latest.get("path") else None
    required_tf = [
        "main.tf", "provider.tf", "variables.tf", "kms.tf", "s3.tf", "iam.tf",
        "glue.tf", "step_functions.tf", "athena.tf", "monitoring.tf", "outputs.tf",
    ]
    required_report_files = ["architecture.svg", "plan.pdf", "cost.pdf", "report.html"]
    checks = [
        _check(
            "terraform directory exists",
            tf_dir.exists() and tf_dir.is_dir(),
            "blocker",
            str(tf_dir),
            "Generate a run with `python core/minusctl.py create ... --generate`.",
        ),
        _check(
            "generated manifest exists",
            (tf_dir / "minus-generated.json").exists(),
            "blocker",
            "minus-generated.json",
            "Regenerate the Terraform workspace through the governed workflow.",
        ),
        _check(
            "source baseline exists",
            (tf_dir / ".minus" / "baseline.json").exists(),
            "blocker",
            ".minus/baseline.json",
            "Run `python core/minusctl.py guard baseline --run " + run["run_id"] + "` after reviewing the generated source.",
        ),
        _check(
            "source is current",
            source.get("status") == "CURRENT",
            "blocker",
            source.get("status", "UNKNOWN"),
            "Run `python core/minusctl.py guard diff --run " + run["run_id"] + "` and reconcile manual edits.",
        ),
        _check(
            "core Terraform files present",
            all((tf_dir / name).exists() for name in required_tf),
            "blocker",
            ", ".join(name for name in required_tf if not (tf_dir / name).exists()) or "all present",
            "Regenerate the Terraform workspace or restore missing files.",
        ),
        _check(
            "report exists",
            bool(reports),
            "warning",
            reports[0]["id"] if reports else "none",
            "Run `python core/plan_gate.py verify --dir <terraform-dir>` then `python core/plan_gate.py plan --dir <terraform-dir>`.",
        ),
        _check(
            "latest report has required visuals",
            bool(latest_path) and all((latest_path / name).exists() for name in required_report_files),
            "warning",
            ", ".join(name for name in required_report_files if not latest_path or not (latest_path / name).exists()) or "all present",
            "Regenerate the report after planning, then inspect the dashboard report links.",
        ),
        _check(
            "report source snapshot current",
            not latest or latest.get("source_status", {}).get("status") == "CURRENT",
            "warning",
            latest.get("source_status", {}).get("status", "no report"),
            "Run `python core/minusctl.py reports diff --latest` and regenerate the plan if files changed.",
        ),
        _check(
            "cost evidence is BCM-backed",
            bool(latest.get("cost", {}).get("ok")),
            "warning",
            latest.get("cost", {}).get("pricing_source", "BCM Pricing Calculator API required"),
            "Review BCM payloads, resolve REVIEW_REQUIRED usage fields, approve BCM estimate creation, then regenerate report.",
        ),
        _check(
            "safe package can be written",
            bool(run.get("root")) and Path(run["root"]).exists(),
            "info",
            run.get("root", "-"),
            "Run `python core/minusctl.py package --run " + run["run_id"] + "`.",
        ),
    ]
    blockers = [item for item in checks if not item["ok"] and item["severity"] == "blocker"]
    warnings = [item for item in checks if not item["ok"] and item["severity"] == "warning"]
    score = max(0, 100 - len(blockers) * 20 - len(warnings) * 7)
    status = "READY" if not blockers and not warnings else "BLOCKED" if blockers else "NEEDS_EVIDENCE"
    return {
        "status": status,
        "score": score,
        "run": run,
        "source": source,
        "reports": [{k: v for k, v in item.items() if k != "manifest"} for item in reports],
        "latest_report": latest,
        "generated_files": generated_files,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
    }


def _format_readiness(readiness):
    lines = [
        "Enterprise readiness",
        f"status     : {readiness['status']}",
        f"score      : {readiness['score']}/100",
        f"run        : {readiness['run'].get('run_id', '-')}",
        f"terraform  : {readiness['run'].get('terraform_dir', '-')}",
        "",
        "Checks:",
    ]
    for item in readiness["checks"]:
        marker = "OK" if item["ok"] else item["severity"].upper()
        lines.append(f"- {marker}: {item['name']} ({item['detail']})")
        if not item["ok"]:
            lines.append(f"  fix: {item['fix']}")
    return "\n".join(lines)


def _package_markdown(package):
    run = package["run"]
    latest = package.get("latest_report") or {}
    lines = [
        "# MinusOps Enterprise Run Package",
        "",
        "## Run",
        "",
        f"- Run ID: `{run.get('run_id', '-')}`",
        f"- Blueprint: `{run.get('blueprint', '-')}`",
        f"- Cloud: `{run.get('cloud', '-')}`",
        f"- Terraform directory: `{run.get('terraform_dir', '-')}`",
        f"- Reports directory: `{run.get('reports_dir', '-')}`",
        f"- Readiness: `{package.get('readiness', {}).get('status', '-')}` ({package.get('readiness', {}).get('score', '-')}/100)",
        "",
        "## Request",
        "",
        package.get("request") or "-",
        "",
        "## Source Status",
        "",
        f"- Status: `{package['source'].get('status', '-')}`",
        f"- Changed files: `{len(package['source'].get('changed', []))}`",
        f"- Missing files: `{len(package['source'].get('missing', []))}`",
        f"- Added files: `{len(package['source'].get('added', []))}`",
        "",
        "## Generated Terraform Files",
        "",
    ]
    files = package.get("generated_files") or []
    lines.extend([f"- `{name}`" for name in files] or ["- No generated file manifest found."])
    lines.extend(["", "## Latest Report", ""])
    if latest:
        lines.extend([
            f"- Report ID: `{latest.get('id', '-')}`",
            f"- Path: `{latest.get('path', '-')}`",
            f"- Generated at: `{latest.get('generated_at', '-')}`",
            f"- Plan counts: `{latest.get('counts', {})}`",
            f"- Source status: `{latest.get('source_status', {}).get('status', '-')}`",
        ])
        services = latest.get("services") or {}
        if services:
            lines.extend(["", "### Services", ""])
            lines.extend([f"- {name}: {count}" for name, count in services.items()])
        lines.extend([
            "",
            "### Cost Evidence",
            "",
            f"- Status: `{latest.get('cost', {}).get('ok', False)}`",
            f"- Source: `{latest.get('cost', {}).get('pricing_source', 'BCM Pricing Calculator API required')}`",
        ])
    else:
        lines.append("- No report exists yet. Run `python core/plan_gate.py plan --dir <terraform-dir>` after verification.")
    lines.extend([
        "",
        "## Safe Next Steps",
        "",
    ])
    lines.extend([f"- `{line}`" for line in package["next"]["text"].splitlines()])
    lines.extend([
        "",
        "## Readiness Checks",
        "",
    ])
    readiness = package.get("readiness", {})
    for item in readiness.get("checks", []):
        marker = "OK" if item.get("ok") else item.get("severity", "issue").upper()
        lines.append(f"- **{marker}** `{item.get('name')}`: {item.get('detail')}")
        if not item.get("ok"):
            lines.append(f"  - Fix: {item.get('fix')}")
    lines.extend([
        "",
        "## Blocked Actions",
        "",
        "- Do not run `terraform apply`, `terraform destroy`, mutating cloud CLI commands, or mutating git commands until the exact plan hash is reviewed and approved.",
        "- Do not publish enterprise cost totals unless AWS BCM Pricing Calculator API evidence exists.",
        "",
    ])
    return "\n".join(lines)


def _build_package(run):
    workflow_record = _read_workflow(run)
    source = source_guard.status(run["terraform_dir"])
    reports = _run_reports(run)
    readiness = _readiness(run)
    package = {
        "run": run,
        "request": run.get("request") or workflow_record.get("resolution", {}).get("query", ""),
        "workflow": workflow_record,
        "source": source,
        "generated_files": _generated_files(run),
        "reports": [{k: v for k, v in item.items() if k != "manifest"} for item in reports],
        "latest_report": _latest_report_details(reports),
        "next": _next_steps(run),
        "readiness": readiness,
    }
    package["markdown"] = _package_markdown(package)
    return package


def _write_package(run):
    package = _build_package(run)
    root = Path(run["root"])
    md_path = root / "enterprise-package.md"
    json_path = root / "enterprise-package.json"
    md_path.write_text(package["markdown"] + "\n", encoding="utf-8")
    json_data = {k: v for k, v in package.items() if k != "markdown"}
    json_path.write_text(json.dumps(json_data, indent=2) + "\n", encoding="utf-8")
    package["paths"] = {"markdown": str(md_path), "json": str(json_path)}
    return package


def _prove(run):
    """
    End-to-end evidence harness: prove the offline governance chain works on this environment
    (generate -> report artifacts -> audit-chain integrity -> readiness), then report exactly
    which AWS-gated steps remain (real BCM estimate, real gated apply) with the credential
    posture. Writes evidence.md + evidence.json — a hand-off artifact.
    """
    import toolpath  # noqa: E402
    tf_dir = run["terraform_dir"]
    reports = _run_reports(run)
    latest = reports[0] if reports else None
    latest_path = latest["path"] if latest else None
    audit_path = os.path.join(os.getcwd(), ".agents", "logs", "audit.jsonl")
    chain = audit_chain.chain_status(audit_path)
    audit_ok = chain["intact"]            # chained segment intact (tolerates a pre-chaining legacy prefix)
    audit_errors = chain["errors"]
    readiness = _readiness(run)
    terraform = toolpath.find_tool("terraform")
    aws = toolpath.find_tool("aws")
    try:
        from providers.base import get_provider
        posture = get_provider().credential_posture()
    except Exception:
        posture = {"connected": False, "type": "unknown"}

    def artifact(name):
        return bool(latest_path) and os.path.exists(os.path.join(latest_path, name))

    checks = [
        _check("run workspace generated", os.path.isdir(tf_dir), "blocker", tf_dir, "minusctl create ... --generate"),
        _check("deploy report present", bool(reports), "blocker", latest["id"] if latest else "none", "plan_gate plan"),
        _check("architecture.svg", artifact("architecture.svg"), "warning", "-", "regenerate report"),
        _check("plan.pdf", artifact("plan.pdf"), "warning", "-", "regenerate report"),
        _check("cost.pdf", artifact("cost.pdf"), "warning", "-", "regenerate report"),
        _check("audit chain intact", audit_ok, "blocker",
               (f"ok ({chain['chained_count']} chained"
                + (f", {chain['legacy_count']} legacy pre-chain" if chain["legacy_count"] else "") + ")")
               if audit_ok else f"{len(audit_errors)} error(s)", "investigate audit.jsonl"),
        _check("terraform available", bool(terraform), "warning", terraform or "not found", "install terraform"),
        _check("aws CLI available", bool(aws), "info", aws or "not found", "install aws cli"),
    ]
    blockers = [c for c in checks if not c["ok"] and c["severity"] == "blocker"]
    next_aws = []
    if not posture.get("connected"):
        next_aws.append("Authenticate (aws sso login) to run the real BCM estimate and gated apply.")
    elif posture.get("type") in ("long_term", "root"):
        next_aws.append(f"Use a temporary session (SSO / assumed MFA role) — current is {posture.get('type')}; apply refuses static keys.")
    next_aws += [
        "Real per-service cost: bcm prepare --derive ... then bcm run (BCM prices it).",
        "Real deploy: plan_gate verify -> plan -> approve -> apply against this run's terraform dir.",
    ]
    evidence = {
        "run": run.get("run_id"),
        "offline_chain_proven": not blockers,
        "readiness": readiness["status"], "readiness_score": readiness["score"],
        "audit_chain_ok": audit_ok,
        "audit_chained_records": chain["chained_count"], "audit_legacy_records": chain["legacy_count"],
        "terraform_available": bool(terraform), "aws_available": bool(aws),
        "aws_connected": bool(posture.get("connected")), "credential_type": posture.get("type"),
        "checks": checks, "blockers": blockers, "next_aws_steps": next_aws,
    }
    root = Path(run["root"])
    (root / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# MinusOps Evidence Bundle", "",
        f"- Run: `{run.get('run_id')}`",
        f"- Offline governance chain proven: **{not blockers}**",
        f"- Readiness: **{readiness['status']}** ({readiness['score']}/100)",
        f"- Audit chain intact: **{audit_ok}**",
        f"- Terraform: {bool(terraform)} · AWS CLI: {bool(aws)} · AWS connected: "
        f"{bool(posture.get('connected'))} ({posture.get('type')})",
        "", "## Checks", "",
    ]
    for c in checks:
        lines.append(f"- {'OK' if c['ok'] else c['severity'].upper()}: {c['name']} ({c['detail']})")
    lines += ["", "## Remaining AWS-gated steps", ""] + [f"- {s}" for s in next_aws]
    (root / "evidence.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    evidence["paths"] = {"markdown": str(root / "evidence.md"), "json": str(root / "evidence.json")}
    return evidence


def main(argv=None):
    ap = argparse.ArgumentParser(description="MinusOps safe operator CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create", help="resolve request and optionally generate Terraform")
    create.add_argument("request")
    create.add_argument("--cloud", default=None)
    create.add_argument("--input", action="append", default=[], help="Blueprint input as name=value")
    create.add_argument("--generate", action="store_true")
    create.add_argument("--json", action="store_true")

    run_cmd = sub.add_parser("runs", help="list or show run workspaces")
    run_cmd.add_argument("action", choices=["list", "latest"])
    run_cmd.add_argument("--json", action="store_true")

    guard = sub.add_parser("guard", help="inspect generated source drift")
    guard.add_argument("action", choices=["status", "diff", "baseline", "refresh"])
    guard.add_argument("--dir", help="Terraform source directory")
    guard.add_argument("--run", default="latest", help="Run id or prefix; default latest")
    guard.add_argument("--label", default="manual", help="Baseline label for baseline/refresh")
    guard.add_argument("--json", action="store_true")

    reports = sub.add_parser("reports", help="inspect plan reports")
    reports.add_argument("action", choices=["list", "status", "services", "resources", "roles", "files", "diff"])
    reports.add_argument("--report", help="Report hash prefix")
    reports.add_argument("--latest", action="store_true")

    nxt = sub.add_parser("next", help="print safe next steps for a run")
    nxt.add_argument("--run", default="latest")
    nxt.add_argument("--json", action="store_true")

    pkg = sub.add_parser("package", help="write an enterprise handoff package for a run")
    pkg.add_argument("--run", default="latest")
    pkg.add_argument("--json", action="store_true")

    ready = sub.add_parser("readiness", help="score enterprise presentation readiness for a run")
    ready.add_argument("--run", default="latest")
    ready.add_argument("--json", action="store_true")
    ready.add_argument("--strict", action="store_true", help="Exit non-zero unless status is READY")

    prove_cmd = sub.add_parser("prove", help="run the end-to-end evidence harness for a run")
    prove_cmd.add_argument("--run", default="latest")
    prove_cmd.add_argument("--json", action="store_true")

    audit_cmd = sub.add_parser("audit", help="verify the tamper-evident audit chain")
    audit_cmd.add_argument("action", choices=["verify"])
    audit_cmd.add_argument("--path", default=os.path.join(os.getcwd(), ".agents", "logs", "audit.jsonl"))
    audit_cmd.add_argument("--json", action="store_true")

    demo_cmd = sub.add_parser("demo", help="generate a no-cloud demo run/report")
    demo_cmd.add_argument("name", choices=["governed-data-pipeline"])
    demo_cmd.add_argument("--owner", default="data-platform")
    demo_cmd.add_argument("--daily-data-gb", type=float, default=50)
    demo_cmd.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "create":
        record = workflow.resolve_to_run(
            args.request,
            cloud=args.cloud,
            inputs=workflow.parse_input(args.input),
            generate=args.generate,
        )
        _json_or_text(record, args.json, workflow.format_result(record))
        return 0 if record.get("ok") else 2

    if args.cmd == "runs":
        if args.action == "list":
            _print_runs(args.json)
            return 0
        run = _latest_run_or_exit()
        _json_or_text(run, args.json, _format_run(run))
        return 0

    if args.cmd == "guard":
        tf_dir = _terraform_dir(args)
        if args.action in {"baseline", "refresh"}:
            result = source_guard.write_baseline(tf_dir, label=args.label)
            _json_or_text(result, args.json, f"baseline written: {tf_dir}")
        elif args.action == "status":
            result = source_guard.status(tf_dir)
            _json_or_text(result, args.json, json.dumps(result, indent=2))
        elif args.action == "diff":
            print("\n".join(source_guard.diff(tf_dir)))
        return 0

    if args.cmd == "reports":
        _print_report_command(args.action, args)
        return 0

    if args.cmd == "next":
        result = _next_steps(_run_by_id_or_latest(args.run))
        _json_or_text(result, args.json, result["text"])
        return 0

    if args.cmd == "package":
        result = _write_package(_run_by_id_or_latest(args.run))
        text = "\n".join([
            "Enterprise package written",
            f"markdown : {result['paths']['markdown']}",
            f"json     : {result['paths']['json']}",
        ])
        _json_or_text({k: v for k, v in result.items() if k != "markdown"}, args.json, text)
        return 0

    if args.cmd == "readiness":
        result = _readiness(_run_by_id_or_latest(args.run))
        _json_or_text(result, args.json, _format_readiness(result))
        if args.strict and result["status"] != "READY":
            return 2
        return 0

    if args.cmd == "prove":
        result = _prove(_run_by_id_or_latest(args.run))
        text = "\n".join([
            "Evidence bundle written",
            f"offline chain proven : {result['offline_chain_proven']}",
            f"readiness            : {result['readiness']} ({result['readiness_score']}/100)",
            f"audit chain intact   : {result['audit_chain_ok']}",
            f"aws connected        : {result['aws_connected']} ({result['credential_type']})",
            f"markdown             : {result['paths']['markdown']}",
        ])
        _json_or_text(result, args.json, text)
        return 0 if result["offline_chain_proven"] else 2

    if args.cmd == "audit":
        ok, errors = audit_chain.verify(args.path)
        result = {"path": args.path, "ok": ok, "errors": errors}
        text = (f"[audit] chain OK: {args.path}" if ok
                else f"[audit] CHAIN INTEGRITY FAILURE: {args.path}\n" + "\n".join(f"  - {e}" for e in errors))
        _json_or_text(result, args.json, text)
        return 0 if ok else 1

    if args.cmd == "demo":
        result = demo.governed_data_pipeline(owner=args.owner, daily_data_gb=args.daily_data_gb)
        text = "\n".join([
            "[DEMO] governed AWS data pipeline",
            f"run       : {result['run']['run_id']}",
            f"terraform : {result['run']['terraform_dir']}",
            f"report    : {result['report_dir']}",
        ])
        _json_or_text(result, args.json, text)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
