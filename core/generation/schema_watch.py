"""
schema_watch.py — CI provider schema-diff watch (docs/project_plan.md Phase E addendum,
"self-updating knowledge" tooling, scoped down from a larger enterprise-expansion proposal to
exactly this: pure detection/reporting, no new provisioning surface).

While MinusOps is stopped waiting on external unblocks (a real account for the live create+
destroy test, a second tenant for Phase 3, Autoresearch's BAA), the AWS and Databricks provider
surface keeps moving underneath the already-pinned modules. This fetches the real, live
`terraform providers schema -json` for each tracked provider, reduces it to just the
resource/data-source types MinusOps' modules actually reference (parsed straight out of
modules/*/main.tf, not guessed), and diffs it against the last committed snapshot:

  - a used type disappears from the live schema         -> finding: removed
  - a used type's schema `version` integer changes       -> finding: schema_version_bump
  - a used type gains a newly `deprecated` attribute      -> finding: deprecated
  - an unused type newly matches MinusOps' own module vocabulary -> informational note only,
    never a finding (worth investigating later, not "fail loudly")

Every fact this reports comes from a real `terraform init` + `terraform providers schema -json`
run against the exact version constraints already live in synthesizer.py -- nothing here is
guessed or carried over from any external planning document.

`run_provider()` writes recent-changes/<provider>/schema-snapshot.json (replaced each run) and,
once a prior snapshot exists to diff against, recent-changes/<provider>/<timestamp>.json (the
diff report), then appends one summary record into the shared .agents/logs/audit.jsonl chain
via audit_logger.log_audit_event -- same "one continuous chain" invariant audit_chain.py already
establishes elsewhere in this codebase. This is a detector and reporter only: it never touches
plan_gate.py's apply path, never blocks a deploy, and never provisions anything.
"""
import argparse
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
import modules as module_registry  # noqa: E402
import synthesizer  # noqa: E402
import audit_logger  # noqa: E402
import toolpath  # noqa: E402

_PROVIDER_PREFIX = {"aws": "aws_", "databricks": "databricks_"}
_PROVIDER_SOURCE = {"aws": "hashicorp/aws", "databricks": "databricks/databricks"}
_RESOURCE_DECL = re.compile(r'^\s*(resource|data)\s+"([A-Za-z0-9_]+)"\s+"', re.MULTILINE)


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _now_stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _version_constraint(provider):
    """Read the version constraint straight out of synthesizer.py's own templates -- one
    source of truth for the pin, never a re-typed literal."""
    block = synthesizer._VERSIONS_HEADER if provider == "aws" else synthesizer._DATABRICKS_VERSION
    # Anchor to the constraint immediately following `source = "..."`, not a bare `version =`
    # search -- _VERSIONS_HEADER also contains `required_version = ">= 1.5"` earlier in the
    # string, and a naive search matches that "version" substring first.
    match = re.search(r'source\s*=\s*"[^"]+"\s*\n\s*version\s*=\s*"([^"]+)"', block)
    if not match:
        raise RuntimeError(f"could not find a version constraint for {provider!r} in synthesizer.py")
    return match.group(1)


def used_types(modules_dir, provider):
    """(kind, type_name) pairs actually declared by modules/*/main.tf, restricted to one
    provider's naming prefix."""
    prefix = _PROVIDER_PREFIX[provider]
    found = set()
    for main_tf in glob.glob(os.path.join(modules_dir, "*", "main.tf")):
        with open(main_tf, encoding="utf-8") as f:
            text = f.read()
        for kind, type_name in _RESOURCE_DECL.findall(text):
            if type_name.startswith(prefix):
                found.add((kind, type_name))
    return found


def _fetch_schema(provider, workdir):
    source = _PROVIDER_SOURCE[provider]
    versions_tf = f'''terraform {{
  required_providers {{
    {provider} = {{
      source  = "{source}"
      version = "{_version_constraint(provider)}"
    }}
  }}
}}
'''
    os.makedirs(workdir, exist_ok=True)
    with open(os.path.join(workdir, "versions.tf"), "w", encoding="utf-8") as f:
        f.write(versions_tf)

    terraform = toolpath.find_tool("terraform")
    if terraform is None:
        raise RuntimeError("terraform CLI not found on PATH")

    init = subprocess.run([terraform, f"-chdir={workdir}", "init", "-input=false"],
                           capture_output=True, text=True)
    if init.returncode != 0:
        raise RuntimeError(f"terraform init failed:\n{init.stdout}\n{init.stderr}")

    result = subprocess.run([terraform, f"-chdir={workdir}", "providers", "schema", "-json"],
                             capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"terraform providers schema -json failed:\n{result.stdout}\n{result.stderr}")

    data = json.loads(result.stdout)
    schema = data["provider_schemas"][f"registry.terraform.io/{source}"]

    lock_path = os.path.join(workdir, ".terraform.lock.hcl")
    resolved_version = None
    if os.path.exists(lock_path):
        with open(lock_path, encoding="utf-8") as f:
            lock_text = f.read()
        vmatch = re.search(
            rf'provider "registry\.terraform\.io/{re.escape(source)}"\s*\{{\s*version\s*=\s*"([^"]+)"',
            lock_text)
        resolved_version = vmatch.group(1) if vmatch else None

    return schema, resolved_version


def _deprecated_attrs(block, prefix=""):
    """Recursively walk attributes + nested block_types for anything marked deprecated --
    verified live against real `terraform providers schema -json` output (attribute-level
    `deprecated`/`deprecation_message`, not a resource-level flag)."""
    found = []
    for name, attr in (block.get("attributes") or {}).items():
        if attr.get("deprecated"):
            found.append(f"{prefix}{name}")
    for name, block_type in (block.get("block_types") or {}).items():
        nested = block_type.get("block") or {}
        found.extend(_deprecated_attrs(nested, prefix=f"{prefix}{name}."))
    return sorted(found)


def _reduce(schema, used):
    resource_schemas = schema.get("resource_schemas", {})
    data_schemas = schema.get("data_source_schemas", {})
    reduced = {}
    for kind, type_name in used:
        table = resource_schemas if kind == "resource" else data_schemas
        entry = table.get(type_name)
        if entry is None:
            continue
        reduced[f"{kind}:{type_name}"] = {
            "kind": kind,
            "version": entry.get("version", 0),
            "deprecated_attributes": _deprecated_attrs(entry.get("block") or {}),
        }
    return reduced


def _diff(old_snapshot, reduced, used_keys):
    """Findings are scoped to types MinusOps still references *right now* -- a type dropped
    from a module between runs is simply no longer tracked, not reported as 'removed'."""
    if old_snapshot is None:
        return []
    old_types = old_snapshot.get("resource_types", {})
    findings = []
    for key in sorted(used_keys):
        old_entry = old_types.get(key)
        if old_entry is None:
            continue
        new_entry = reduced.get(key)
        if new_entry is None:
            findings.append({"finding": "removed", "type": key,
                              "detail": "no longer present in the live provider schema"})
            continue
        if new_entry["version"] != old_entry.get("version"):
            findings.append({"finding": "schema_version_bump", "type": key,
                              "old_version": old_entry.get("version"),
                              "new_version": new_entry["version"]})
        newly_deprecated = sorted(
            set(new_entry["deprecated_attributes"]) - set(old_entry.get("deprecated_attributes", [])))
        if newly_deprecated:
            findings.append({"finding": "deprecated", "type": key, "attributes": newly_deprecated})
    return findings


def _vocab_tokens():
    """Reuse the module registry's own capability vocabulary (satisfies + services phrases) as
    the relevance filter for 'new resource worth investigating' -- same tokenizer
    (modules.py's match_modules already uses this), not a second heuristic."""
    tokens = set()
    for m in module_registry.MODULES:
        for phrase in m["satisfies"] + m["services"]:
            tokens |= module_registry._tokens(phrase)
    return tokens


def _new_resources_of_interest(schema, used_keys, vocab_tokens, old_snapshot):
    """Informational only, never a finding. Requires a prior snapshot's `all_type_names` as the
    'already seen' baseline -- without one, every type looks 'new' and the signal is noise."""
    if old_snapshot is None:
        return []
    already_seen = set(old_snapshot.get("all_type_names", []))
    interesting = []
    for kind, table_name in (("resource", "resource_schemas"), ("data", "data_source_schemas")):
        for type_name in schema.get(table_name, {}):
            key = f"{kind}:{type_name}"
            if key in used_keys or type_name in already_seen:
                continue
            tokens = set(re.findall(r"[a-z0-9]+", type_name.lower()))
            if tokens & vocab_tokens:
                interesting.append(type_name)
    return sorted(interesting)


def run_provider(provider, recent_changes_dir=None, log_dir=None):
    if provider not in _PROVIDER_PREFIX:
        raise ValueError(f"unknown provider: {provider!r} (tracked: {sorted(_PROVIDER_PREFIX)})")

    recent_changes_dir = recent_changes_dir or os.path.join(module_registry.output_root(), "recent-changes")
    log_dir = log_dir or os.path.join(module_registry.output_root(), ".agents", "logs")
    provider_dir = os.path.join(recent_changes_dir, provider)
    os.makedirs(provider_dir, exist_ok=True)
    snapshot_path = os.path.join(provider_dir, "schema-snapshot.json")

    old_snapshot = None
    if os.path.exists(snapshot_path):
        with open(snapshot_path, encoding="utf-8") as f:
            old_snapshot = json.load(f)

    workdir = tempfile.mkdtemp(prefix=f"schema-watch-{provider}-")
    try:
        schema, resolved_version = _fetch_schema(provider, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    used = used_types(module_registry.MODULES_DIR, provider)
    used_keys = {f"{kind}:{type_name}" for kind, type_name in used}
    reduced = _reduce(schema, used)
    all_type_names = sorted(
        set(schema.get("resource_schemas", {})) | set(schema.get("data_source_schemas", {})))

    findings = _diff(old_snapshot, reduced, used_keys)
    new_of_interest = _new_resources_of_interest(schema, used_keys, _vocab_tokens(), old_snapshot)

    generated_at = _now_iso()
    new_snapshot = {
        "provider": provider,
        "resolved_version": resolved_version,
        "generated_at": generated_at,
        "resource_types": reduced,
        "all_type_names": all_type_names,
    }
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(new_snapshot, f, indent=2)
        f.write("\n")

    if old_snapshot is not None:
        report = {
            "provider": provider,
            "generated_at": generated_at,
            "previous_version": old_snapshot.get("resolved_version"),
            "resolved_version": resolved_version,
            "used_types_tracked": sorted(used_keys),
            "findings": findings,
            "new_resources_of_interest": new_of_interest,
        }
        report_path = os.path.join(provider_dir, f"{_now_stamp()}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
            f.write("\n")

    status = "FINDINGS" if findings else "OK"
    audit_logger.log_audit_event(
        "schema_watch",
        {
            "provider": provider,
            "resolved_version": resolved_version,
            "findings_count": len(findings),
            "new_resources_of_interest_count": len(new_of_interest),
            "status": status,
            "note": "no plan-hash: this is a schema-fetch event, not a plan/apply event",
        },
        log_dir,
    )
    return findings, new_of_interest


def main(argv=None):
    ap = argparse.ArgumentParser(description="CI provider schema-diff watch")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="fetch + diff + report for one or all tracked providers")
    r.add_argument("--provider", choices=sorted(_PROVIDER_PREFIX), default=None)
    r.add_argument("--all", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "run":
        if not args.all and not args.provider:
            print("[schema_watch] specify --provider <aws|databricks> or --all", file=sys.stderr)
            return 1
        providers = sorted(_PROVIDER_PREFIX) if args.all else [args.provider]
        any_findings = False
        for provider in providers:
            try:
                findings, new_of_interest = run_provider(provider)
            except Exception as exc:
                print(f"[schema_watch] {provider}: FAILED ({exc})", file=sys.stderr)
                return 1
            if findings:
                any_findings = True
                print(f"[schema_watch] {provider}: {len(findings)} finding(s)")
                for f_ in findings:
                    print(f"  - {f_['finding']}: {f_['type']}")
            else:
                print(f"[schema_watch] {provider}: OK "
                      f"({len(new_of_interest)} new-resource note(s))")
        return 1 if any_findings else 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
