"""
Package a generated run into a domain repository (PRD-ARCH-2026-005, FR-03/FR-04).

MinusOps synthesizes into `runs/<run-id>/`; the team that operates the pipeline works in
their own repository. Export is the handover: it copies the four deployable directories
(`terraform/`, `dags/`, `scripts/`, `configs/`) into `<target-repo>/<dest-dir>/` and, on
request, writes a per-pipeline GitHub Actions workflow into `<target-repo>/.github/workflows/`.

What is deliberately NOT copied is as much of the contract as what is. `reports/`, `bcm/`
and `run.json` are control-plane artifacts; shipping them would couple the domain team to a
tool they do not run. What lands is plain Terraform that `terraform init && terraform apply`
handles with no MinusOps runtime present (NFR-01).

This command only copies files locally -- it never touches AWS and never runs Terraform. The
mutating path stays where it has always been, behind `plan_gate`. What it does do is write
into a repository the operator named, so `--dest-dir` is treated as hostile input: it is
joined onto the repo root and a `../` in it would write beside the repository instead of
inside it.

Per-directory the copy REPLACES rather than merges. A resource dropped from the run has to
disappear from the domain repo; a leftover `.tf` file is one `terraform apply` away from
recreating infrastructure the architecture no longer declares.

Depends on: core/generation/cicd.py (render_pipeline_workflow),
    core/governance/audit_logger.py (lazily, so an offline import stays side-effect free)
Shells out to: nothing
Used by: core/reporting/minusctl.py (`minusctl export`), tests/test_export.py
"""
import argparse
import json
import os
import re
import shutil
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "governance", "reporting"):
    _p = os.path.join(_CORE_DIR, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)
import cicd  # noqa: E402

# The directories a domain team can actually deploy. Everything else in a run workspace is
# control-plane evidence and stays here.
EXPORT_DIRS = ("terraform", "dags", "scripts", "configs")

AUDIT_DIR = os.path.join(os.getcwd(), ".agents", "logs")

# The pipeline name becomes a directory name and a workflow filename. Anything outside this
# set can forge a path under .github/workflows/.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _safe_name(value):
    if not value or not _SAFE_NAME.match(value) or value in (".", ".."):
        raise ValueError(f"unsafe pipeline name: {value!r}")
    return value


def _resolve_dest(target_repo, dest_dir):
    """Absolute destination, proven to sit inside `target_repo`.

    `os.path.join` silently discards the root when the second argument is absolute, and
    `..` walks out of it, so both are checked against the resolved real path rather than
    by inspecting the string."""
    root = os.path.realpath(target_repo)
    dest = os.path.realpath(os.path.join(root, dest_dir))
    if dest != root and not dest.startswith(root + os.sep):
        raise ValueError(f"--dest-dir must stay inside the target repo: {dest_dir!r}")
    if dest == root:
        raise ValueError("--dest-dir must name a subdirectory, not the repo root")
    return root, dest


def _copy_tree(src, dest):
    """Replace `dest` with `src`. Returns the files written, relative to `dest`."""
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    written = []
    for base, _dirs, files in os.walk(dest):
        for name in files:
            written.append(os.path.relpath(os.path.join(base, name), dest))
    return written


def export_run(run_root, target_repo, dest_dir=None, generate_workflow=False,
               pipeline_name=None, region=cicd.DEFAULT_REGION):
    """
    Copy a run's deployable directories into a domain repository.

    Returns a manifest: pipeline name, absolute destination, repo-relative paths of every
    file written, and the workflow path when one was generated.
    """
    run_root = os.path.abspath(run_root)
    if not os.path.isdir(run_root):
        raise ValueError(f"run workspace not found: {run_root}")
    if not os.path.isdir(target_repo):
        raise ValueError(f"target repo not found: {target_repo}")

    tf_dir = os.path.join(run_root, "terraform")
    if not os.path.isdir(tf_dir) or not os.listdir(tf_dir):
        raise ValueError(
            f"{run_root} has no generated Terraform to export -- synthesize the run first")

    dest_dir = dest_dir or f"pipelines/{os.path.basename(run_root)}"
    root, dest = _resolve_dest(target_repo, dest_dir)
    pipeline_name = _safe_name(pipeline_name or os.path.basename(dest))
    rel_dest = os.path.relpath(dest, root)

    copied = []
    for sub in EXPORT_DIRS:
        src = os.path.join(run_root, sub)
        if not os.path.isdir(src):
            continue
        for name in _copy_tree(src, os.path.join(dest, sub)):
            copied.append(os.path.join(rel_dest, sub, name))

    workflow_path = None
    if generate_workflow:
        workflows = os.path.join(root, ".github", "workflows")
        os.makedirs(workflows, exist_ok=True)
        workflow_path = os.path.join(workflows, f"{pipeline_name}-deploy.yml")
        with open(workflow_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(cicd.render_pipeline_workflow(
                pipeline_name, dest_dir=rel_dest.replace(os.sep, "/"), region=region))

    manifest = {
        "run": run_root,
        "pipeline_name": pipeline_name,
        "target_repo": root,
        "dest_dir": dest,
        "copied": sorted(copied),
        "workflow": workflow_path,
    }
    _audit(manifest)
    return manifest


def _audit(manifest):
    """NFR-03. Never lets a logging problem fail the export -- the files are already on
    disk by this point, so raising here would report a failure that did not happen."""
    try:
        import audit_logger
        audit_logger.log_audit_event(
            "export",
            f"{manifest['pipeline_name']} -> {manifest['target_repo']} "
            f"({len(manifest['copied'])} file(s))",
            AUDIT_DIR)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] export not audited: {exc}", file=sys.stderr)


def format_manifest(manifest):
    lines = [
        f"exported {manifest['pipeline_name']} -> {manifest['dest_dir']}",
        f"  files    : {len(manifest['copied'])}",
    ]
    if manifest["workflow"]:
        lines.append(f"  workflow : {manifest['workflow']}")
    lines.append("  next     : review the diff in the domain repo, then commit and open a PR")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Export a generated run into a domain repository")
    ap.add_argument("--run", required=True, help="run workspace directory or run id")
    ap.add_argument("--target-repo", required=True, help="path to the domain repository")
    ap.add_argument("--dest-dir", help="destination inside the repo, e.g. pipelines/clickstream")
    ap.add_argument("--pipeline-name", help="defaults to the last segment of --dest-dir")
    ap.add_argument("--generate-workflow", action="store_true",
                    help="also write .github/workflows/<pipeline>-deploy.yml")
    ap.add_argument("--region", default=cicd.DEFAULT_REGION)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    run_root = args.run
    if not os.path.isdir(run_root):
        import runs as runs_mod
        record = runs_mod.get_run(args.run)
        if not record:
            print(f"[ERR] no such run: {args.run}", file=sys.stderr)
            return 1
        run_root = record["root"]

    try:
        manifest = export_run(run_root, args.target_repo, dest_dir=args.dest_dir,
                              generate_workflow=args.generate_workflow,
                              pipeline_name=args.pipeline_name, region=args.region)
    except ValueError as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2) if args.json else format_manifest(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
