"""
Phase 6 Step 5 (docs/phase6_step5_teardown_scope.md section 1.1) -- the regression-baseline
harness. Decided by the user: Option B (the catalog stays the real composition source; nothing
retires or relocates on the strength of this harness alone). This harness still needs to exist
and run, under either option, because it is what turns "the authored-content path is
capability-equivalent to the catalog-copy path" from an argument into a checked fact, per module.

What this proves, precisely (section 0 of the scope doc): NOT that a generator can reinvent a
module from a sentence -- there is no generator. It proves that routing a module's own real
resource/data content through the NEW authored-file composition path (`synthesizer.synthesize
(authored_content=...)`) produces a real `terraform plan` equivalent to the plan produced by the
OLD catalog-copy path (`shutil.copytree` + a `module "x" { source = "./modules/<id>" }` block),
for the SAME content. Equivalence is checked at the (type, local name, action) level -- never
byte-identical HCL (the two paths lay files out differently by construction: a module
subdirectory vs. flat root files) and never full attribute equality (both paths can carry
`after_unknown` computed values whose exact shape isn't the point here; G2/G5/G6/G9 already
separately verify content correctness -- this harness verifies STRUCTURAL equivalence, that the
same resources with the same actions come out of both paths).

Real, disclosed limits (section 1.2 of the scope doc), not silently assumed away:
- Proves equivalence for ONE resolved, one-shot composition of a module's resources only -- not
  true `variable`/`output` reusability (no catalog module is composed more than once per run
  today anyway, so this is a real but currently-inapplicable gap).
- Two modules cannot be planned standalone with dummy credentials at all, by either path
  (`networking-vpc`'s `data.aws_availability_zones` call, `orchestrator-stepfunctions`'s
  `aws_sfn_state_machine` validation call) -- both real, disclosed, pre-existing gaps found
  while building tests/test_rego_gate.py's own 16-module G6 regression test, carried forward
  here rather than re-litigated.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

import architecture_decision as archdec
import modules as module_registry
import schema_lint
import synthesizer
import test_destructive_change_gate as dcg
import test_rego_gate as g6test
import toolpath

TERRAFORM = toolpath.find_tool("terraform")

pytestmark = pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")

_CANNOT_PLAN_STANDALONE = g6test._CANNOT_PLAN_STANDALONE
_DUMMY_AWS_PROVIDER = g6test._DUMMY_AWS_PROVIDER
_DUMMY_DATABRICKS_PROVIDER = g6test._DUMMY_DATABRICKS_PROVIDER
_uses_databricks = g6test._uses_databricks
_strip_caller_identity = g6test._strip_caller_identity

# compose()'s own generic root variables (synthesizer._VARIABLES) -- carrying a catalog module's
# own same-named variable over verbatim into the NEW path's root would be a duplicate
# declaration; these are already declared (and, except name_prefix/owner, already defaulted) by
# compose() itself, regardless of which/how-many modules are actually selected.
_COMPOSE_STANDARD_VARIABLES = {
    "name_prefix", "owner", "environment", "region", "tags", "run_id", "daily_data_gb",
}

_DUMMY_VERSIONS = '''terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}
'''

_LOCALS_BLOCK_RE = re.compile(r'locals\s*\{')


def _extract_locals_blocks(content):
    """A module's own `locals { ... }` block(s) (unnamed, unlike variable/resource -- schema_
    lint.iter_hcl_blocks only yields resource/data blocks, and dcg._iter_top_level_blocks needs a
    quoted name, neither covers this shape) -- carried over verbatim, same as variable blocks:
    plain computed values, no gate needed, but the decomposed resource text's own local.*
    references need SOMETHING to declare them."""
    blocks = []
    for m in _LOCALS_BLOCK_RE.finditer(content):
        start = m.end()
        depth = 1
        i = start
        while depth > 0 and i < len(content):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
            i += 1
        blocks.append(f"locals {{\n{content[start:i - 1]}\n}}\n")
    return blocks


_DUMMY_VERSIONS_WITH_DATABRICKS = '''terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    databricks = {
      source  = "databricks/databricks"
      version = ">= 1.0"
    }
  }
}
'''


def _run_tf(dst, *args, timeout=120):
    result = subprocess.run([TERRAFORM, f"-chdir={dst}", *args],
                            capture_output=True, text=True, timeout=timeout)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def _real_plan_resource_changes(dst):
    """init/plan/show for whatever HCL is already written into dst. Returns (resource_changes,
    error) -- error is None on success."""
    rc, out = _run_tf(dst, "init", "-input=false")
    if rc != 0:
        return None, f"init failed: {out.strip()[:2000]}"
    rc, out = _run_tf(dst, "plan", "-out=tfplan", "-input=false")
    if rc != 0:
        return None, f"plan failed: {out.strip()[:2000]}"
    result = subprocess.run([TERRAFORM, f"-chdir={dst}", "show", "-json", "tfplan"],
                            capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return None, f"show failed: {result.stderr.strip()[:2000]}"
    return json.loads(result.stdout).get("resource_changes", []), None


def _plan_signature(resource_changes):
    """(type, local name, sorted actions) per resource -- the structural equivalence bar (module
    docstring). Ignores `address`/`module_address` entirely so a module-wrapped OLD-path plan
    and a flat-file NEW-path plan compare fairly."""
    return {
        (rc["type"], rc["name"], tuple(sorted(rc["change"]["actions"])))
        for rc in resource_changes
    }


def _old_path_plan(module_id, tmp_path):
    """The catalog-copy path: shutil.copytree + a real module block, dummy credentials, real
    plan. Mirrors compose()'s own copy mechanism directly rather than going through
    compose()/_render_main()'s cross-module wiring ladder (docs/phase6_scope.md's own table:
    that ladder is catalog-ID-keyed and out of scope for what this harness tests)."""
    src = os.path.join(dcg.MODULES_DIR, module_id)
    main_tf = open(os.path.join(src, "main.tf"), encoding="utf-8").read()
    dst = tmp_path / "old"
    dst_modules = dst / "modules" / module_id
    shutil.copytree(src, dst_modules)

    patched = _strip_caller_identity(main_tf)
    if patched != main_tf:
        (dst_modules / "main.tf").write_text(patched, encoding="utf-8")

    var_assignments = "\n".join(
        f"  {line.strip()}" for line in dcg._required_variable_lines(main_tf)
    )
    (dst / "main.tf").write_text(
        f'module "under_test" {{\n  source = "./modules/{module_id}"\n{var_assignments}\n}}\n',
        encoding="utf-8",
    )
    versions = _DUMMY_VERSIONS
    providers = _DUMMY_AWS_PROVIDER
    if _uses_databricks(main_tf):
        # Real bug found running this harness, not assumed: a root-level `provider "databricks"
        # {}` block with no matching root-level `required_providers` entry makes Terraform infer
        # the DEFAULT hashicorp/databricks namespace for that provider LOCAL NAME at the root
        # (confirmed live: "provider registry.terraform.io does not have a provider named
        # registry.terraform.io/hashicorp/databricks") -- even though the CHILD module (databricks-
        # workspace) already declares the real databricks/databricks source itself. The root's own
        # provider config needs the same required_providers declaration, matching exactly what
        # compose()'s own _render_versions() already does conditionally for composed output.
        versions = _DUMMY_VERSIONS_WITH_DATABRICKS
        providers += _DUMMY_DATABRICKS_PROVIDER
    (dst / "versions.tf").write_text(versions, encoding="utf-8")
    (dst / "_test_providers.tf").write_text(providers, encoding="utf-8")

    return _real_plan_resource_changes(dst)


def _new_path_plan(module_id, tmp_path):
    """The authored-content path: decompose the module's real resource/data blocks, route them
    through synthesizer.synthesize(authored_content=...) exactly as Step 1 built it, real plan.
    variable/output blocks are carried over verbatim as plain root-level HCL (not through
    novel_resources -- that mechanism gates RESOURCE/DATA content, not variable declarations,
    which need no gate) so the decomposed resource text's own var.* references still resolve."""
    src = os.path.join(dcg.MODULES_DIR, module_id)
    main_tf = open(os.path.join(src, "main.tf"), encoding="utf-8").read()
    patched = _strip_caller_identity(main_tf)

    blocks = list(schema_lint.iter_hcl_blocks(patched))
    assert blocks, f"{module_id}: no resource/data blocks found to decompose"

    novel_resources = []
    authored_content = {}
    for kind, type_name, block_name, body in blocks:
        key = type_name if kind == "resource" else f"data.{type_name}"
        header = f'{kind} "{type_name}" "{block_name}"' if kind == "resource" \
            else f'data "{type_name}" "{block_name}"'
        content = f"{header} {{\n{body}\n}}\n"
        if key not in authored_content:
            novel_resources.append({
                "resource_type": key,
                "justification": "Step 5 regression-baseline harness -- real catalog content "
                                  "routed through the authoring path for equivalence proof.",
                "alternatives_considered": ["none -- this is the module's own real content"],
                "grounding_examples": [module_id],
            })
            authored_content[key] = content
        else:
            # A module declaring more than one resource of the same type (e.g. two
            # aws_s3_bucket_public_access_block blocks) -- append into the same authored file,
            # since authored_content is keyed one entry per type, matching the real mechanism's
            # own contract rather than inventing a new one for this harness.
            authored_content[key] += content

    decision = dict(archdec.template(), selected_modules=[], novel_resources=novel_resources,
                    selected_architecture="teardown regression harness",
                    decision_summary="Step 5 harness -- proving path equivalence, not a real "
                                     "architecture decision.",
                    alternatives=[{"name": "n/a", "decision": "rejected", "reason": "harness"}],
                    assumptions=["harness-only"], risks=["none -- test fixture"],
                    sources=["docs/phase6_step5_teardown_scope.md"])

    # Calls the REAL fail-closed validation Step 1 built (schema_lint.gate_content() per entry,
    # the same function synthesize() itself calls) -- but composes directly via compose(), not
    # the full synthesize()/select_modules() path, deliberately: select_modules() would run
    # match_modules() against this harness's own synthetic request text and pull in unrelated
    # catalog picks (plus the always-added governance-observability module), contaminating the
    # very equivalence comparison this harness exists to make. This harness tests the authoring/
    # composition path itself, not requirement-driven module selection, which is untouched here.
    authored_resources = synthesizer._validate_novel_resources(decision, authored_content)

    dst = tmp_path / "new"
    dst.mkdir()
    compose_result = synthesizer.compose(
        [], f"harness-{module_id}", str(dst), owner="teardown-harness",
        request=f"harness:{module_id}", authored_resources=authored_resources,
    )

    # compose() always renders versions.tf/providers.tf/variables.tf itself, regardless of
    # present_ids being empty -- real, no-dummy-credential AWS provider, and its own generic
    # variable set (name_prefix/owner/environment/region/tags/run_id/daily_data_gb, all with
    # defaults except name_prefix/owner, already supplied via terraform.tfvars by compose()
    # itself). Overwrite providers.tf/versions.tf with dummy-credentialed equivalents (can't add
    # a second `provider "aws"` block -- Terraform rejects duplicate provider configs), and only
    # carry over the module's OWN variables that aren't already in compose()'s generic set (a
    # duplicate `variable "name_prefix"` declaration is rejected the same way).
    out_dir = compose_result["out_dir"]
    versions = _DUMMY_VERSIONS
    providers = _DUMMY_AWS_PROVIDER
    if _uses_databricks(main_tf):
        versions = _DUMMY_VERSIONS_WITH_DATABRICKS
        providers += _DUMMY_DATABRICKS_PROVIDER
    with open(os.path.join(out_dir, "versions.tf"), "w", encoding="utf-8") as f:
        f.write(versions)
    with open(os.path.join(out_dir, "providers.tf"), "w", encoding="utf-8") as f:
        f.write(providers)

    var_blocks = "\n".join(
        f'variable "{name}" {{\n{body}\n}}\n'
        for name, body in dcg._iter_top_level_blocks(main_tf, "variable")
        if name not in _COMPOSE_STANDARD_VARIABLES
    )
    locals_blocks = "\n".join(_extract_locals_blocks(main_tf))
    with open(os.path.join(out_dir, "_module_vars.tf"), "w", encoding="utf-8") as f:
        f.write(var_blocks + "\n" + locals_blocks)

    var_assignments = "\n".join(
        line for line in dcg._required_variable_lines(main_tf)
        if line.strip().split(" ", 1)[0] not in _COMPOSE_STANDARD_VARIABLES
    )
    with open(os.path.join(out_dir, "terraform.tfvars"), "a", encoding="utf-8") as f:
        f.write("\n" + var_assignments + "\n")

    return _real_plan_resource_changes(out_dir)


def _read_companion_assets(module_id):
    """Every non-main.tf file under a real catalog module's directory, keyed by its relative
    path -- the exact shape authored_content's module form's `assets` map expects (docs/
    phase7_item1_module_unit_scope.md)."""
    src = os.path.join(dcg.MODULES_DIR, module_id)
    assets = {}
    for root, _dirs, files in os.walk(src):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, src).replace(os.sep, "/")
            if rel == "main.tf":
                continue
            with open(full, "rb") as f:
                assets[rel] = f.read()
    return assets


def _module_args_from_required_variables(main_tf):
    """dcg._required_variable_lines()'s own `  name = value` placeholder lines, parsed into a
    dict -- reused directly (not re-derived) so the MODULE-form path is given the exact same
    values the OLD catalog-copy path already uses for its own required-variable assignments
    (_old_path_plan below), which is what makes the plan-equivalence comparison fair."""
    args = {}
    for line in dcg._required_variable_lines(main_tf):
        name, _, value = line.strip().partition(" = ")
        args[name] = value
    return args


def _new_module_form_plan(module_id, tmp_path):
    """The authored-content MODULE form (docs/phase7_item1_module_unit_scope.md, approved): the
    module's entire real main.tf routed through synthesizer.compose(authored_resources=...) as
    ONE module-shaped unit -- its own directory (authored_modules/<module_id>/), its own
    variable/output namespace, its real companion asset files copied alongside it -- instead of
    _new_path_plan()'s flat per-type decomposition, which has no directory for `path.module` to
    resolve against. Exists only for the modules the flat form structurally cannot reproduce;
    see the (now closed) entries this replaces in _NEW_PATH_KNOWN_BLOCKERS below."""
    src = os.path.join(dcg.MODULES_DIR, module_id)
    main_tf = open(os.path.join(src, "main.tf"), encoding="utf-8").read()
    patched = _strip_caller_identity(main_tf)

    decision = dict(archdec.template(), selected_modules=[], novel_resources=[{
        "resource_type": module_id,
        "justification": "Phase 7 Item 1 harness -- real catalog module content routed through "
                          "the module-shaped authored path for equivalence proof.",
        "alternatives_considered": ["none -- this is the module's own real content"],
        "grounding_examples": [module_id],
    }], selected_architecture="teardown regression harness",
        decision_summary="Phase 7 Item 1 harness -- proving path equivalence, not a real "
                         "architecture decision.",
        alternatives=[{"name": "n/a", "decision": "rejected", "reason": "harness"}],
        assumptions=["harness-only"], risks=["none -- test fixture"],
        sources=["docs/phase7_item1_module_unit_scope.md"])

    authored_content = {module_id: {
        "content": patched,
        "assets": _read_companion_assets(module_id),
        "module_args": _module_args_from_required_variables(main_tf),
    }}
    authored_resources = synthesizer._validate_novel_resources(decision, authored_content)

    dst = tmp_path / "new_module_form"
    dst.mkdir()
    compose_result = synthesizer.compose(
        [], f"harness-{module_id}", str(dst), owner="teardown-harness",
        request=f"harness:{module_id}", authored_resources=authored_resources,
    )

    out_dir = compose_result["out_dir"]
    versions = _DUMMY_VERSIONS
    providers = _DUMMY_AWS_PROVIDER
    if _uses_databricks(main_tf):
        versions = _DUMMY_VERSIONS_WITH_DATABRICKS
        providers += _DUMMY_DATABRICKS_PROVIDER
    with open(os.path.join(out_dir, "versions.tf"), "w", encoding="utf-8") as f:
        f.write(versions)
    with open(os.path.join(out_dir, "providers.tf"), "w", encoding="utf-8") as f:
        f.write(providers)

    return _real_plan_resource_changes(out_dir)


# Real, named blockers found RUNNING this harness (not hypothetical, not hacked around) --
# per docs/phase6_step5_teardown_scope.md section 2: a module the current authored_content
# mechanism can't reproduce to the same bar is a disclosed blocker, not something to paper over
# by copying extra files into the flat-root composition to make the symptom disappear.
_NEW_PATH_KNOWN_BLOCKERS = {
    # compute-glue-etl and compaction-glue used to be listed here: the flat per-type
    # decomposition _new_path_plan() uses (one root file per resource type, no module
    # subdirectory) has no way to carry a companion asset file a `path.module`-relative
    # reference needs (aws_s3_object.script's `filemd5("${path.module}/scripts/....py")`).
    # CLOSED for real by Phase 7 Item 1 (docs/phase7_item1_module_unit_scope.md): the new
    # module-shaped authored_content form gives the unit its own directory, so `path.module`
    # resolves correctly -- see test_module_form_closes_the_path_module_asset_blockers below,
    # which proves plan-equivalence for both using that form. Still skipped HERE because this
    # parametrization specifically exercises the flat-decomposition path via _new_path_plan(),
    # which still can't reproduce them (unchanged, by design -- the flat form stays simple for
    # callers that don't need a module boundary).
    "compute-glue-etl": (
        "flat-decomposition path only -- see test_module_form_closes_the_path_module_asset_"
        "blockers for the real, now-passing proof via the module-shaped authored_content form."
    ),
    "compaction-glue": (
        "flat-decomposition path only -- see test_module_form_closes_the_path_module_asset_"
        "blockers for the real, now-passing proof via the module-shaped authored_content form."
    ),
    # Same real, disclosed G2 limitation already found and recorded in
    # tests/test_schema_lint.py::test_every_real_module_passes_g2_cleanly's own known-exceptions
    # table: a genuinely dynamic `dynamic "columns" { for_each = var.columns }` block that
    # schema_lint.py structurally cannot resolve statically. Since _validate_novel_resources()
    # calls the exact same gate_content(), this surfaces here identically, not a new gap.
    "table-format-iceberg": (
        "aws_glue_catalog_table.this's dynamic \"columns\" block trips the same structural G2 "
        "unparseable_reference limitation already disclosed in "
        "test_schema_lint.py::test_every_real_module_passes_g2_cleanly -- this module was never "
        "actually G2-clean (no PROVENANCE.json; never pinned)."
    ),
}


@pytest.mark.parametrize("module_id", [
    pytest.param(m["id"], marks=pytest.mark.skip(
        reason=_CANNOT_PLAN_STANDALONE.get(m["id"]) or _NEW_PATH_KNOWN_BLOCKERS.get(m["id"])))
    if m["id"] in _CANNOT_PLAN_STANDALONE or m["id"] in _NEW_PATH_KNOWN_BLOCKERS else m["id"]
    for m in module_registry.list_modules()
])
def test_authored_path_is_plan_equivalent_to_catalog_copy_path(module_id, tmp_path):
    old_rc, old_err = _old_path_plan(module_id, tmp_path)
    assert old_rc is not None, f"{module_id}: OLD (catalog-copy) path could not be planned -- {old_err}"

    new_rc, new_err = _new_path_plan(module_id, tmp_path)
    assert new_rc is not None, f"{module_id}: NEW (authored-content) path could not be planned -- {new_err}"

    old_sig = _plan_signature(old_rc)
    new_sig = _plan_signature(new_rc)
    assert new_sig == old_sig, (
        f"{module_id}: NEW path's plan is not equivalent to the OLD path's plan.\n"
        f"only in OLD: {old_sig - new_sig}\nonly in NEW: {new_sig - old_sig}"
    )


# The proof bar Phase 7 Item 1's scope doc named explicitly (docs/phase7_item1_module_unit_scope
# .md section 5): these two modules must move from a disclosed blocker to a real, unmodified
# plan-equivalence pass via the module-shaped authored_content form -- not a test-only workaround.
# table-format-iceberg is deliberately NOT included here: its blocker is G2's own dynamic-block
# limitation (a separate, larger question the module-boundary work does not touch).
@pytest.mark.parametrize("module_id", ["compute-glue-etl", "compaction-glue"])
def test_module_form_closes_the_path_module_asset_blockers(module_id, tmp_path):
    old_rc, old_err = _old_path_plan(module_id, tmp_path)
    assert old_rc is not None, f"{module_id}: OLD (catalog-copy) path could not be planned -- {old_err}"

    new_rc, new_err = _new_module_form_plan(module_id, tmp_path)
    assert new_rc is not None, (
        f"{module_id}: NEW module-shaped-unit path could not be planned -- {new_err}"
    )

    old_sig = _plan_signature(old_rc)
    new_sig = _plan_signature(new_rc)
    assert new_sig == old_sig, (
        f"{module_id}: module-shaped-unit path's plan is not equivalent to the OLD path's plan.\n"
        f"only in OLD: {old_sig - new_sig}\nonly in NEW: {new_sig - old_sig}"
    )
