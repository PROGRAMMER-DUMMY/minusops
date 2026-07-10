"""
module_provenance.py — pin + record provenance for a module in the catalog.

Plumbing for the "fetch live docs at module-update time, never at synthesis time" pattern
(docs/project_plan.md, Phase E addendum): a maintainer runs

    python core/generation/module_provenance.py pin --module <id> --source <str> ...

after hand-editing or MCP-assisted-updating a module under modules/<id>/. This computes a
content hash over the module's current files and writes modules/<id>/PROVENANCE.json,
recording *what* informed this version (source, provider version constraint, notes) and
*when*, and bumping the version counter. From that point the module is a static, pinned
artifact like every other module in the catalog — synthesizer.py copies its files verbatim,
it is never re-fetched or re-generated at plan/apply time.

`verify` recomputes the hash and compares it to what's recorded — drift detection for a
module whose files changed without a matching pin (e.g. a hand-edit that forgot to re-run
`pin`), the same tamper-evidence idea audit_chain.py and plan_gate.py's plan-hash already use
elsewhere in this codebase, applied to the module catalog itself.

This file does not talk to any MCP server, AWS API, or Terraform Registry — `--source` and
`--provider-version` are maintainer-supplied strings. The actual live-fetch step (Terraform
MCP / AWS MCP) is a separate, later concern that calls into `pin()` once it has fetched
content, not something this module does itself.
"""
import argparse
import datetime
import hashlib
import json
import os
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
import modules as module_registry  # noqa: E402

PROVENANCE_FILENAME = "PROVENANCE.json"


def _module_dir(module_id):
    path = os.path.join(module_registry.MODULES_DIR, module_id)
    if not os.path.isdir(path):
        raise FileNotFoundError(f"no such module directory: {path}")
    return path


def content_hash(module_dir):
    """Deterministic sha256 over every file's relative path + contents, excluding the
    provenance record itself (hashing it would make it self-referential)."""
    digest = hashlib.sha256()
    for root, dirs, files in sorted(os.walk(module_dir)):
        dirs.sort()
        for name in sorted(files):
            if name == PROVENANCE_FILENAME:
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, module_dir).replace(os.sep, "/")
            digest.update(rel.encode("utf-8"))
            with open(full, "rb") as f:
                digest.update(f.read())
    return digest.hexdigest()


def _provenance_path(module_dir):
    return os.path.join(module_dir, PROVENANCE_FILENAME)


def show(module_id):
    module_dir = _module_dir(module_id)
    path = _provenance_path(module_dir)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_UPGRADES_DIRNAME = "upgrades"


def _upgrades_dir():
    return os.path.join(module_registry.output_root(), _UPGRADES_DIRNAME)


def pin(module_id, source, provider_version=None, notes=None, schema_hash=None):
    """Record a new pinned version of module_id. Bumps `version` by 1 (starts at 1).

    `schema_hash` is optional and caller-supplied (e.g. by schema_watch.py, from that module's
    slice of a live-fetched provider schema) -- this function never talks to a live source
    itself, per the module docstring above.

    When this call is a real re-pin (content_hash changed from the previously recorded one, not
    a first-ever pin), also writes an upgrades/<module_id>-v<new_version>.json report recording
    the before/after state -- this is what makes a module version bump visible/auditable without
    touching the deploy/apply path at all.
    """
    module_dir = _module_dir(module_id)
    existing = show(module_id)
    version = (existing or {}).get("version", 0) + 1
    new_hash = content_hash(module_dir)
    pinned_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record = {
        "module_id": module_id,
        "version": version,
        "content_hash": new_hash,
        "schema_hash": schema_hash,
        "source": source,
        "provider_version": provider_version,
        "notes": notes,
        "pinned_at": pinned_at,
    }
    with open(_provenance_path(module_dir), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
        f.write("\n")

    if existing is not None and existing.get("content_hash") != new_hash:
        upgrades_dir = _upgrades_dir()
        os.makedirs(upgrades_dir, exist_ok=True)
        upgrade_report = {
            "module_id": module_id,
            "old_version": existing["version"],
            "new_version": version,
            "old_content_hash": existing.get("content_hash"),
            "new_content_hash": new_hash,
            "old_schema_hash": existing.get("schema_hash"),
            "new_schema_hash": schema_hash,
            "source": source,
            "notes": notes,
            "pinned_at": pinned_at,
        }
        report_path = os.path.join(upgrades_dir, f"{module_id}-v{version}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(upgrade_report, f, indent=2)
            f.write("\n")

    return record


def verify(module_id):
    """Return (ok, recorded_hash, current_hash). ok is False if never pinned, or if the
    module's files have changed since the last pin."""
    module_dir = _module_dir(module_id)
    record = show(module_id)
    if not record:
        return False, None, content_hash(module_dir)
    current = content_hash(module_dir)
    return record["content_hash"] == current, record["content_hash"], current


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pin + verify module catalog provenance")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pin", help="record a new pinned version of a module")
    p.add_argument("--module", required=True)
    p.add_argument("--source", required=True,
                    help="what informed this version, e.g. 'terraform-mcp-server:databricks provider docs 2026-07-08'")
    p.add_argument("--provider-version", default=None)
    p.add_argument("--notes", default=None)
    p.add_argument("--schema-hash", default=None,
                    help="hash of this module's slice of a live-fetched provider schema, "
                         "e.g. from schema_watch.py")

    s = sub.add_parser("show", help="print the recorded provenance for a module")
    s.add_argument("--module", required=True)

    v = sub.add_parser("verify", help="check the module's files haven't drifted since the last pin")
    v.add_argument("--module", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "pin":
        record = pin(args.module, args.source, args.provider_version, args.notes,
                     schema_hash=args.schema_hash)
        print(json.dumps(record, indent=2))
        return 0

    if args.cmd == "show":
        record = show(args.module)
        if record is None:
            print(f"[module_provenance] {args.module} has never been pinned.", file=sys.stderr)
            return 1
        print(json.dumps(record, indent=2))
        return 0

    if args.cmd == "verify":
        ok, recorded, current = verify(args.module)
        if not ok:
            if recorded is None:
                print(f"[module_provenance] {args.module} has never been pinned.", file=sys.stderr)
            else:
                print(f"[module_provenance] DRIFT: {args.module}'s files changed since it was "
                      f"pinned (recorded {recorded[:12]}..., current {current[:12]}...). "
                      "Re-run `pin` if this change was intentional.", file=sys.stderr)
            return 1
        print(f"[module_provenance] {args.module} matches its pinned version ({current[:12]}...).")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
