"""
Plan Explorer for generated Terraform reports.

Reads artifacts/reports/<plan-hash>/ or runs/<run-id>/reports/<plan-hash>/ manifest.json, plan.json, source_hashes.json,
and optional source_snapshot/ files. Provides human-readable inspection commands
for services, resources, IAM roles, file ownership, source drift, and diffs.
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path


WORKSPACE = Path(os.getcwd())
REPORTS = WORKSPACE / "artifacts" / "reports"
SKIP_DIRS = {".terraform", ".git", "__pycache__"}
SOURCE_SUFFIXES = {".tf", ".tfvars", ".py", ".md", ".yaml", ".yml", ".json"}
SOURCE_NAMES = {"README.md"}


SERVICE_PREFIXES = [
    ("Amazon S3", "aws_s3_"),
    ("AWS KMS", "aws_kms_"),
    ("AWS Glue", "aws_glue_"),
    ("AWS Step Functions", "aws_sfn_"),
    ("Amazon Athena", "aws_athena_"),
    ("Amazon CloudWatch", "aws_cloudwatch_"),
    ("AWS Budgets", "aws_budgets_"),
    ("AWS IAM", "aws_iam_"),
]


FILE_HINTS = [
    ("aws_s3_", "s3.tf"),
    ("aws_kms_", "kms.tf"),
    ("aws_iam_", "iam.tf"),
    ("aws_glue_", "glue.tf"),
    ("aws_s3_object", "scripts.tf"),
    ("aws_sfn_", "step_functions.tf"),
    ("aws_athena_", "athena.tf"),
    ("aws_cloudwatch_", "monitoring.tf"),
    ("aws_budgets_", "monitoring.tf"),
]


def _rel(path):
    try:
        return str(Path(path).resolve().relative_to(WORKSPACE.resolve())).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _hash_file(path):
    return _sha256_bytes(Path(path).read_bytes())


def iter_source_files(source_dir):
    root = Path(source_dir)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = set(path.parts)
        if parts & SKIP_DIRS:
            continue
        if path.name in {"tfplan", ".terraform.lock.hcl"}:
            continue
        if path.suffix in SOURCE_SUFFIXES or path.name in SOURCE_NAMES:
            yield path


def source_hashes(source_dir):
    root = Path(source_dir)
    hashes = {}
    for path in iter_source_files(root):
        rel = str(path.relative_to(root)).replace("\\", "/")
        hashes[rel] = _hash_file(path)
    return hashes


def write_source_snapshot(source_dir, report_dir):
    source_dir = Path(source_dir)
    report_dir = Path(report_dir)
    snapshot_dir = report_dir / "source_snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for path in iter_source_files(source_dir):
        rel = path.relative_to(source_dir)
        target = snapshot_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        target.write_bytes(data)
        hashes[str(rel).replace("\\", "/")] = _sha256_bytes(data)
    (report_dir / "source_hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    return hashes


def report_roots():
    roots = []
    if REPORTS.exists():
        roots.append(REPORTS)
    runs_root = WORKSPACE / "runs"
    if runs_root.exists():
        roots.extend(sorted(r for r in runs_root.glob("*/reports") if r.is_dir()))
    return roots


def iter_reports():
    reports = []
    for root in report_roots():
        for item in root.iterdir():
            if item.is_dir() and (item / "manifest.json").exists() and (item / "plan.json").exists():
                try:
                    manifest = json.loads((item / "manifest.json").read_text(encoding="utf-8"))
                except Exception:
                    manifest = {}
                reports.append((item, manifest))
    reports.sort(key=lambda pair: pair[1].get("generated_at", ""), reverse=True)
    return reports


def latest_report_id():
    reports = iter_reports()
    if not reports:
        raise FileNotFoundError("no reports found")
    return reports[0][0].name


def find_report(report_id):
    report_id = str(report_id)
    if report_id == "latest":
        report_id = latest_report_id()
    candidates = []
    for root in report_roots():
        direct = root / report_id
        if direct.is_dir():
            candidates.append(direct)
        candidates.extend(sorted(root.glob(report_id + "*")))
    # Several runs can share a plan-hash; prefer a COMPLETE report (manifest + plan), and
    # among those the most recently generated, so a stale/partial dir never shadows it.
    complete = [c for c in candidates if (c / "manifest.json").exists() and (c / "plan.json").exists()]
    if complete:
        complete.sort(key=lambda c: (c / "manifest.json").stat().st_mtime, reverse=True)
        return complete[0]
    if candidates:
        return candidates[0]
    raise FileNotFoundError(f"report not found: {report_id}")


def load_report(report_id):
    report_dir = find_report(report_id)
    manifest = json.loads((report_dir / "manifest.json").read_text(encoding="utf-8"))
    plan = json.loads((report_dir / "plan.json").read_text(encoding="utf-8"))
    return report_dir, manifest, plan


def resource_rows(plan):
    rows = []
    for change in plan.get("resource_changes", []):
        actions = change.get("change", {}).get("actions", ["no-op"])
        action = (
            "delete" if "delete" in actions and "create" not in actions else
            "create" if actions == ["create"] else
            "update" if "update" in actions or set(actions) == {"create", "delete"} else
            "no-op"
        )
        after = change.get("change", {}).get("after") or {}
        rows.append({
            "address": change.get("address", change.get("type", "unknown")),
            "type": change.get("type", "unknown"),
            "name": change.get("name", ""),
            "action": action,
            "after": after,
            "owner_file": owner_file_for_type(change.get("type", "")),
        })
    return sorted(rows, key=lambda row: row["address"])


def service_for_type(rtype):
    for service, prefix in SERVICE_PREFIXES:
        if rtype.startswith(prefix):
            return service
    return "Other"


def owner_file_for_type(rtype):
    for prefix, filename in FILE_HINTS:
        if rtype.startswith(prefix):
            return filename
    return "main.tf"


def services(plan):
    result = {}
    for row in resource_rows(plan):
        svc = service_for_type(row["type"])
        result.setdefault(svc, []).append(row)
    return dict(sorted(result.items()))


def iam_roles(plan):
    rows = resource_rows(plan)
    policies = [r for r in rows if r["type"] in {"aws_iam_policy", "aws_iam_role_policy"}]
    attachments = [r for r in rows if r["type"] == "aws_iam_role_policy_attachment"]
    roles = []
    for role in [r for r in rows if r["type"] == "aws_iam_role"]:
        after = role.get("after") or {}
        role_name = after.get("name") or role["name"]
        attached = []
        for item in attachments:
            item_after = item.get("after") or {}
            if item_after.get("role") == role_name or item_after.get("role") == role["name"]:
                attached.append(item["address"])
        roles.append({
            "address": role["address"],
            "name": role_name,
            "assume_role_policy": after.get("assume_role_policy", ""),
            "policy_attachments": attached,
        })
    return {"roles": roles, "policies": policies, "attachments": attachments}


def source_status(report_id):
    report_dir, manifest, _ = load_report(report_id)
    explicit_stale = bool(manifest.get("stale_after_terraform_change"))
    source_dir = manifest.get("dir")
    hash_path = report_dir / "source_hashes.json"
    if not source_dir or not hash_path.exists():
        return {
            "status": "STALE" if explicit_stale else "UNKNOWN",
            "stale": explicit_stale,
            "reason": manifest.get("stale_reason") or "source snapshot unavailable",
            "changed": [],
            "missing": [],
            "added": [],
        }
    saved = json.loads(hash_path.read_text(encoding="utf-8"))
    current = source_hashes(source_dir)
    changed = sorted(k for k in saved if k in current and saved[k] != current[k])
    missing = sorted(k for k in saved if k not in current)
    added = sorted(k for k in current if k not in saved)
    stale = explicit_stale or bool(changed or missing or added)
    return {
        "status": "STALE" if stale else "CURRENT",
        "stale": stale,
        "reason": manifest.get("stale_reason") if explicit_stale else "",
        "changed": changed,
        "missing": missing,
        "added": added,
    }


def diff_source(report_id):
    report_dir, manifest, _ = load_report(report_id)
    source_dir = manifest.get("dir")
    if not source_dir:
        return ["source directory not recorded in manifest"]
    snapshot = report_dir / "source_snapshot"
    if not snapshot.exists():
        return ["source snapshot unavailable"]
    status = source_status(report_id)
    names = sorted(set(status["changed"] + status["missing"] + status["added"]))
    if not names:
        return ["no source drift detected"]
    output = []
    source_root = Path(source_dir)
    for name in names:
        old_path = snapshot / name
        new_path = source_root / name
        old = old_path.read_text(encoding="utf-8", errors="replace").splitlines() if old_path.exists() else []
        new = new_path.read_text(encoding="utf-8", errors="replace").splitlines() if new_path.exists() else []
        output.extend(difflib.unified_diff(old, new, fromfile=f"snapshot/{name}", tofile=f"current/{name}", lineterm=""))
    return output


def _print_services(plan):
    for service, rows in services(plan).items():
        print(f"{service}: {len(rows)}")
        for row in rows:
            print(f"  - {row['address']} ({row['action']}, {row['owner_file']})")


def _print_resources(plan):
    for row in resource_rows(plan):
        print(f"{row['address']} | {row['type']} | {row['action']} | {row['owner_file']}")


def _print_roles(plan):
    data = iam_roles(plan)
    print("IAM roles:")
    for role in data["roles"]:
        print(f"- {role['address']}")
        print(f"  name: {role['name']}")
        if role["policy_attachments"]:
            print("  attachments:")
            for item in role["policy_attachments"]:
                print(f"    - {item}")
    print("\nIAM policies:")
    for policy in data["policies"]:
        print(f"- {policy['address']}")


def _print_files(report_id):
    report_dir, manifest, _ = load_report(report_id)
    print(f"report: {_rel(report_dir)}")
    print(f"terraform dir: {manifest.get('dir', '-')}")
    for item in sorted((report_dir).iterdir()):
        if item.name == "source_snapshot":
            continue
        print(f"- {item.name}")


def _print_list():
    reports = iter_reports()
    if not reports:
        print("no reports")
        return
    for report_dir, manifest in reports:
        counts = manifest.get("counts", {})
        print(
            f"{report_dir.name}\t{manifest.get('template', '-')}\t"
            f"+{counts.get('create', 0)}/~{counts.get('update', 0)}/-{counts.get('delete', 0)}\t"
            f"{_rel(report_dir)}"
        )


def main():
    parser = argparse.ArgumentParser(description="Inspect generated Terraform plan reports")
    parser.add_argument("command", choices=["list", "status", "services", "resources", "roles", "files", "diff"])
    parser.add_argument("--report", help="Report hash prefix, e.g. 45a0c4c79ed9")
    parser.add_argument("--latest", action="store_true", help="Use the newest report")
    args = parser.parse_args()

    if args.command == "list":
        _print_list()
        return 0
    report_id = "latest" if args.latest else args.report
    if not report_id:
        parser.error("--report or --latest is required for this command")
    report_dir, manifest, plan = load_report(report_id)
    if args.command == "status":
        print(json.dumps(source_status(report_id), indent=2))
    elif args.command == "services":
        _print_services(plan)
    elif args.command == "resources":
        _print_resources(plan)
    elif args.command == "roles":
        _print_roles(plan)
    elif args.command == "files":
        _print_files(report_id)
    elif args.command == "diff":
        print("\n".join(diff_source(report_id)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
