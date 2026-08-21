"""
address_churn.py -- catch the regeneration that quietly destroys data.

A Terraform resource's identity is its ADDRESS. Regeneration that restructures a workspace --
moving a bucket into a module, renaming it, re-keying a for_each -- produces a different
address for the same real-world object, and Terraform reads that as destroy + create. On S3,
RDS, or a KMS key that is data loss, and the plan report shows it as an ordinary
delete-then-create with nothing indicating the two lines are the same thing.

Terraform already solves this: a `moved { from = ... to = ... }` block re-keys state with no
destroy. So this detects rename-SHAPED churn and requires that block, rather than inventing a
parallel mechanism.

"Rename-shaped" = a delete and a create, same resource type, in the same plan, whose
identifying attributes match. Identity comparison is what keeps this honest in both
directions: without it, every delete+create pair would look like a rename (waving through
real deletions), and a genuinely different bucket would be mistaken for a move.

Blocking is scoped to stateful types only. Renaming an IAM role recreates it -- disruptive,
but nothing is lost -- so that is advisory. A gate that blocks on the harmless case trains
operators to bypass it.

Depends on: plan_reader, destructive_change_gate (STATEFUL_RESOURCE_TYPES)
Shells out to: nothing -- it reads .tf text and already-parsed plan JSON
Used by: core/governance/plan_gate.py
"""
import os
import re

import plan_reader
from destructive_change_gate import STATEFUL_RESOURCE_TYPES

# The attribute that names the real-world object, per type. Compared instead of the whole
# object because a rename legitimately changes tags, ARNs, and computed fields.
_IDENTITY_ATTRS = ("bucket", "name", "id", "identifier", "namespace_name", "table_name",
                   "key_id", "domain_name", "cluster_identifier", "function_name")

_MOVED_RE = re.compile(
    r"moved\s*\{[^}]*?\bfrom\s*=\s*([A-Za-z0-9_.\[\]\"-]+)[^}]*?\bto\s*=\s*([A-Za-z0-9_.\[\]\"-]+)[^}]*?\}",
    re.S)


def read_moved_blocks(tf_dir):
    """Parse `moved` blocks out of the workspace's .tf files.

    Regex rather than a full HCL parser: a moved block is a fixed two-field shape, and this
    repo already gates real HCL structure through schema_lint. A missed block fails SAFE --
    the gate objects to churn the operator already declared, which is noisy but never
    silently destructive.
    """
    blocks = []
    if not os.path.isdir(tf_dir):
        return blocks
    for name in sorted(os.listdir(tf_dir)):
        if not name.endswith(".tf"):
            continue
        try:
            content = open(os.path.join(tf_dir, name), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for src, dst in _MOVED_RE.findall(content):
            blocks.append({"from": src.strip('"'), "to": dst.strip('"')})
    return blocks


def _identity(values):
    if not isinstance(values, dict):
        return None
    for attr in _IDENTITY_ATTRS:
        if values.get(attr):
            return (attr, values[attr])
    return None


def classify(plan_json, moved_blocks=()):
    resource_changes, _ = plan_reader.read_resource_changes(
        plan_json, treat_absent_as_error=False)
    managed, _ = plan_reader.managed_only(resource_changes or [])

    deletes, creates = [], []
    for rc in managed:
        actions = (rc.get("change") or {}).get("actions") or []
        if "delete" in actions and "create" not in actions:
            deletes.append(rc)
        elif "create" in actions and "delete" not in actions:
            creates.append(rc)

    declared = {(b.get("from"), b.get("to")) for b in (moved_blocks or ())}
    rename_shaped, advisory, covered = [], [], 0

    for gone in deletes:
        gone_identity = _identity((gone.get("change") or {}).get("before"))
        if gone_identity is None:
            continue
        for made in creates:
            if made.get("type") != gone.get("type"):
                continue
            if _identity((made.get("change") or {}).get("after")) != gone_identity:
                continue
            pair = {"from": gone.get("address"), "to": made.get("address"),
                    "type": gone.get("type"), "identity": list(gone_identity)}
            if (pair["from"], pair["to"]) in declared:
                covered += 1
            elif gone.get("type") in STATEFUL_RESOURCE_TYPES:
                rename_shaped.append(pair)
            else:
                advisory.append(pair)
            break

    return {
        "rename_shaped": rename_shaped,
        "advisory": advisory,
        "advisory_count": len(advisory),
        "covered_by_moved": covered,
        "blocked": bool(rename_shaped),
    }


def format_result(result):
    if not result["rename_shaped"] and not result["advisory"]:
        return "[gate] address churn: none detected"
    lines = []
    for row in result["rename_shaped"]:
        lines.append(
            f"[gate] BLOCKING: {row['from']} -> {row['to']} ({row['type']}) is a rename of the "
            f"same {row['identity'][0]}={row['identity'][1]!r}, but Terraform will DESTROY and "
            f"recreate it. Add:  moved {{ from = {row['from']}  to = {row['to']} }}")
    for row in result["advisory"]:
        lines.append(f"[gate] address churn (advisory, no data loss): {row['from']} -> {row['to']}")
    return "\n".join(lines)


_MOVED_HEADER = """# Generated by MinusOps (`address_churn.py write-moved`) -- MINUS-137 / TerraShark FM-01.
# Each block below re-keys state for a resource whose ADDRESS changed while the real-world
# object did not, so Terraform updates state instead of destroying and recreating it.
# Review every pair against the plan before applying, then delete this file once the moves
# have been applied and the addresses are stable.
"""


def render_moved(result):
    """The `moved` blocks that would clear this result's detected churn, as HCL text.

    Only the rename-shaped (stateful, blocking) pairs get a block. Advisory pairs -- an IAM
    role rename, say -- recreate harmlessly, and writing state-surgery blocks for them turns
    a no-op into a change nobody reviewed.
    """
    pairs = result.get("rename_shaped") or []
    if not pairs:
        return ""
    blocks = [_MOVED_HEADER]
    for row in pairs:
        blocks.append(
            f"# {row['type']}: same {row['identity'][0]} = {row['identity'][1]!r}\n"
            "moved {\n"
            f"  from = {row['from']}\n"
            f"  to   = {row['to']}\n"
            "}\n")
    return "\n".join(blocks)


def write_moved(tf_dir, result, filename="moved.tf"):
    """Write the rendered blocks into `tf_dir`. Returns the path, or None when there is
    nothing to write.

    Refuses to overwrite an existing file: a hand-edited moved.tf is state surgery someone
    already reviewed, and silently replacing it is exactly the churn this module exists to
    prevent.
    """
    text = render_moved(result)
    if not text:
        return None
    path = os.path.join(tf_dir, filename)
    if os.path.exists(path):
        raise FileExistsError(
            f"{path} already exists -- review and merge by hand rather than overwriting "
            "reviewed state surgery")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def main(argv=None):
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Address-churn detection and `moved {}` generation")
    ap.add_argument("action", choices=["check", "write-moved"])
    ap.add_argument("--dir", required=True, help="Terraform directory")
    ap.add_argument("--plan-json", required=True, help="path to `terraform show -json` output")
    args = ap.parse_args(argv)

    with open(args.plan_json, encoding="utf-8") as handle:
        plan_json = json.load(handle)
    result = classify(plan_json, moved_blocks=read_moved_blocks(args.dir))

    if args.action == "check":
        print(format_result(result))
        return 2 if result["blocked"] else 0

    try:
        path = write_moved(args.dir, result)
    except FileExistsError as exc:
        print(f"[churn] REFUSED - {exc}")
        return 2
    if not path:
        print("[churn] no rename-shaped churn -- nothing to write")
        return 0
    print(f"[churn] wrote {path}; re-plan to confirm the destroys are gone")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
