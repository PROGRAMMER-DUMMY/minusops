"""
Source baseline guard for generated Terraform workspaces.

This tool records and compares local source files only. It does not run
Terraform, cloud CLIs, git commands, or any network operation.
"""
import argparse
import difflib
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


SKIP_DIRS = {".terraform", ".git", "__pycache__", ".minus"}
SKIP_FILES = {"tfplan", ".terraform.lock.hcl"}
SOURCE_SUFFIXES = {".tf", ".tfvars", ".py", ".md", ".yaml", ".yml", ".json"}


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def metadata_dir(source_dir):
    return Path(source_dir) / ".minus"


def baseline_path(source_dir):
    return metadata_dir(source_dir) / "baseline.json"


def snapshot_dir(source_dir):
    return metadata_dir(source_dir) / "source_snapshot"


def iter_source_files(source_dir):
    root = Path(source_dir)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if set(path.relative_to(root).parts) & SKIP_DIRS:
            continue
        if path.name in SKIP_FILES:
            continue
        if path.suffix in SOURCE_SUFFIXES:
            yield path


def _relative(root, path):
    return str(Path(path).relative_to(root)).replace("\\", "/")


def source_hashes(source_dir):
    root = Path(source_dir)
    hashes = {}
    for path in iter_source_files(root):
        hashes[_relative(root, path)] = _sha256(path.read_bytes())
    return hashes


def write_baseline(source_dir, label="generated", extra=None):
    root = Path(source_dir)
    meta = metadata_dir(root)
    snap = snapshot_dir(root)
    meta.mkdir(parents=True, exist_ok=True)
    snap.mkdir(parents=True, exist_ok=True)

    hashes = {}
    for path in iter_source_files(root):
        rel = Path(_relative(root, path))
        data = path.read_bytes()
        target = snap / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[str(rel).replace("\\", "/")] = _sha256(data)

    record = {
        "created_at": _now(),
        "label": label,
        "source_dir": str(root.resolve()),
        "file_count": len(hashes),
        "hashes": hashes,
    }
    if extra:
        record["extra"] = extra
    baseline_path(root).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def load_baseline(source_dir):
    path = baseline_path(source_dir)
    if not path.exists():
        raise FileNotFoundError(f"baseline not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def status(source_dir):
    root = Path(source_dir)
    try:
        baseline = load_baseline(root)
    except FileNotFoundError:
        return {
            "status": "UNKNOWN",
            "stale": True,
            "reason": "baseline not found",
            "changed": [],
            "missing": [],
            "added": sorted(source_hashes(root).keys()) if root.exists() else [],
        }

    saved = baseline.get("hashes", {})
    current = source_hashes(root)
    changed = sorted(name for name in saved if name in current and saved[name] != current[name])
    missing = sorted(name for name in saved if name not in current)
    added = sorted(name for name in current if name not in saved)
    stale = bool(changed or missing or added)
    return {
        "status": "STALE" if stale else "CURRENT",
        "stale": stale,
        "baseline_created_at": baseline.get("created_at"),
        "baseline_label": baseline.get("label"),
        "changed": changed,
        "missing": missing,
        "added": added,
    }


def diff(source_dir):
    root = Path(source_dir)
    state = status(root)
    if state["status"] == "UNKNOWN":
        return [state["reason"]]
    names = sorted(set(state["changed"] + state["missing"] + state["added"]))
    if not names:
        return ["no source drift detected"]

    output = []
    snap = snapshot_dir(root)
    for name in names:
        old_path = snap / name
        new_path = root / name
        old = old_path.read_text(encoding="utf-8", errors="replace").splitlines() if old_path.exists() else []
        new = new_path.read_text(encoding="utf-8", errors="replace").splitlines() if new_path.exists() else []
        output.extend(difflib.unified_diff(old, new, fromfile=f"baseline/{name}", tofile=f"current/{name}", lineterm=""))
    return output


def main():
    parser = argparse.ArgumentParser(description="Detect manual edits in a generated Terraform workspace")
    parser.add_argument("command", choices=["baseline", "status", "diff", "refresh"])
    parser.add_argument("--dir", required=True, help="Terraform source directory")
    parser.add_argument("--label", default="generated", help="Baseline label for baseline/refresh")
    args = parser.parse_args()

    if args.command in {"baseline", "refresh"}:
        print(json.dumps(write_baseline(args.dir, label=args.label), indent=2))
    elif args.command == "status":
        print(json.dumps(status(args.dir), indent=2))
    elif args.command == "diff":
        print("\n".join(diff(args.dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
