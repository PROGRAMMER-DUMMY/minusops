"""
schema_lint.py — G2, the pre-write schema linter (docs/g2_scope.md).

Gates `module_provenance.py`'s `pin` CLI action: before a maintainer's hand-edited (or
MCP-assisted) module HCL is accepted as a new pinned version, this checks the module's actual
resource/data-source type and attribute references against the REAL, LIVE provider schema
(`terraform providers schema -json`) -- not a cached snapshot, not a prior baseline. This is
what would have caught the exact class of break that motivated the generation-time-authoring
pivot: `data.aws_region.name` deprecated in favor of `.region` on the AWS provider (verified
live against the real, currently-resolving provider: 6.54.0 carries `name: deprecated=true`,
`region` does not -- this repo's own modules already use the post-break `.region` form).

Unlike schema_watch.py (a DIFF engine: old snapshot vs. new, no-ops without a prior snapshot),
this is a single-point validity check against live-schema-now, on every single lint call, with
no first-run pass and no missing-baseline skip. If the live schema can't be fetched or parsed,
that is itself a blocking finding -- never a silent "nothing to check" the way schema_watch's
own `_diff()` legitimately no-ops without history to diff against. The one place this module
DOES look at history is the schema-shape WARN signal (see `_shape_warning` below), and that is
deliberately never part of the blocking core: a first-ever pin simply has nothing to compare
the WARN against, which is not the same thing as skipping a required check.

Reuses schema_watch.py's fetch/reduce machinery wholesale (`_fetch_schema`, `used_types`,
`_PROVIDER_PREFIX`, `_PROVIDER_SOURCE`) rather than re-implementing it. Everything below that
line -- HCL attribute-reference extraction, unknown/deprecated/type-mismatch classification,
the blocking verdict -- is net-new; schema_watch.py has no equivalent (it only ever diffs a
resource's schema `version` int and deprecated-attribute *names* between two snapshots, never
looks at what a module's own HCL actually sets or reads).
"""
import hashlib
import json
import os
import re
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
import modules as module_registry  # noqa: E402
import module_provenance  # noqa: E402
from schema_watch import (  # noqa: E402
    _fetch_schema, _PROVIDER_PREFIX, _PROVIDER_SOURCE,
)

# Terraform meta-arguments: valid on every resource/data block regardless of provider schema,
# never real provider attributes -- must never be checked against a provider's attribute set.
_META_ARGS = {"count", "for_each", "provider", "depends_on", "lifecycle"}

_BLOCK_START = re.compile(r'(resource|data)\s+"([A-Za-z0-9_]+)"\s+"([A-Za-z0-9_]+)"\s*\{')
_TOP_LEVEL_ASSIGN = re.compile(r'^[ \t]*([A-Za-z0-9_]+)[ \t]*=(?!=)')
_TOP_LEVEL_BLOCK = re.compile(r'^[ \t]*([A-Za-z0-9_]+)[ \t]*\{')
_DYNAMIC_BLOCK = re.compile(r'^[ \t]*dynamic\s+"([A-Za-z0-9_]+)"[ \t]*\{')


def _matching_brace(content, start):
    """Index just past the `{` at `start`'s matching `}`, brace-depth aware (same technique
    tests/test_destructive_change_gate.py's _iter_top_level_blocks already uses)."""
    depth = 1
    i = start
    while depth > 0 and i < len(content):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
        i += 1
    return i


def iter_hcl_blocks(content):
    """Yield (kind, type_name, block_name, body) for top-level resource/data blocks."""
    for m in _BLOCK_START.finditer(content):
        kind, type_name, block_name = m.group(1), m.group(2), m.group(3)
        end = _matching_brace(content, m.end())
        yield kind, type_name, block_name, content[m.end():end - 1]


def _scan_body(body, prefix=""):
    """Depth-aware scan of a block body for top-level `attr = ...` assignments and one level of
    nested-block attributes (dotted, matching schema_watch._deprecated_attrs' own prefix
    convention). Returns (set_attrs: set[str], unparseable: list[str]).

    A `dynamic "name" { ... }` block's actual emitted attributes depend on evaluating its
    for_each expression -- not resolvable statically, so it is never descended into and is
    always reported as unparseable rather than silently skipped (skipping would be exactly the
    fail-open shape Probe A found and closed in G5: "couldn't parse -> treated as nothing to
    check" is not the same as "confirmed nothing to check")."""
    set_attrs = set()
    unparseable = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        dyn = _DYNAMIC_BLOCK.match(line)
        if dyn:
            unparseable.append(f"{prefix}dynamic:{dyn.group(1)}")
            # Skip the whole dynamic block's body -- its content is not a real attribute scan.
            block_start_offset = sum(len(l) + 1 for l in lines[:i]) + line.index("{")
            end = _matching_brace(body, block_start_offset + 1)
            # +1: body[:end].count("\n") gives the line index the closing "}" lands ON, not the
            # next unprocessed line -- for a block that opens AND closes on the same physical
            # line (an inline empty block, e.g. `filter {}`), that's this very line's own
            # index, which would re-enter this branch on the identical line forever. Confirmed
            # by reproducing it directly: dogfooding modules/dq-great-expectations/main.tf's
            # `filter {}` (an aws_s3_bucket_lifecycle_configuration rule with no filter
            # criteria, valid real Terraform) hung indefinitely before this fix.
            i = body[:end].count("\n") + 1
            continue
        block_m = _TOP_LEVEL_BLOCK.match(line)
        assign_m = _TOP_LEVEL_ASSIGN.match(line)
        if block_m and block_m.group(1) not in _META_ARGS:
            name = block_m.group(1)
            block_start_offset = sum(len(l) + 1 for l in lines[:i]) + line.index("{")
            end = _matching_brace(body, block_start_offset + 1)
            nested_body = body[block_start_offset + 1:end - 1]
            nested_attrs, nested_unparseable = _scan_body(nested_body, prefix=f"{prefix}{name}.")
            set_attrs |= nested_attrs
            unparseable += nested_unparseable
            # +1 -- see the identical comment above; the same inline-empty-block hang applies
            # to any nested block, not just `dynamic`.
            i = body[:end].count("\n") + 1
            continue
        if assign_m and assign_m.group(1) not in _META_ARGS:
            set_attrs.add(f"{prefix}{assign_m.group(1)}")
            # A multi-line literal -- a list/map (`tags = {`) or a function call wrapping one
            # (`event_pattern = jsonencode({`) -- must be skipped wholesale -- its inner lines
            # (e.g. `source = [...]` inside the jsonencode(...) payload) are JSON keys inside
            # this attribute's own value, not sibling top-level Terraform attributes of this
            # block. Triggered on ANY unbalanced RHS, not just one starting with a bracket --
            # `jsonencode(` starts with a letter, so a bracket-only check misses it entirely.
            rhs = line[assign_m.end():]
            if rhs.strip() and not _balanced(rhs):
                j = i + 1
                while j < len(lines) and not _balanced(rhs):
                    rhs += "\n" + lines[j]
                    j += 1
                i = j - 1
        i += 1
    return set_attrs, unparseable


def extract_references(content, declared_blocks):
    """For every declared (kind, type_name, block_name), find `type.name.attr` (or
    `data.type.name.attr`) reference chains anywhere else in the file. An index/splat access
    (`type.name[0].attr`, `type.name[*].attr` -- the standard way to wire an optional,
    count-based resource's output into another resource, real and common in this repo's own
    modules) is fully resolvable: whatever is inside the brackets only selects *which
    instance*, never *which attribute* -- the attribute name after the bracket is exactly as
    statically known as it would be without the bracket, so it is extracted the same way. Only
    a bracket with nothing meaningful after it (the whole indexed object used as-is, with no
    attribute narrowed) has nothing to check -- not a finding, just nothing to verify there.
    Returns {(kind, type_name, block_name): set(attr names)}, plus a list of unparseable
    references (currently always empty -- see the dynamic-block case in _scan_body for the
    real unparseable case this repo's HCL actually produces: a `dynamic` block's attributes
    depend on evaluating its for_each, which is genuinely not statically resolvable)."""
    referenced = {}
    unparseable = []
    for kind, type_name, block_name, _ in declared_blocks:
        key = (kind, type_name, block_name)
        base = f"data.{type_name}.{block_name}" if kind == "data" else f"{type_name}.{block_name}"
        pattern = re.compile(re.escape(base) + r'(?:\[[^\[\]]*\])?(\.[A-Za-z0-9_]+)?')
        for m in pattern.finditer(content):
            attr = m.group(1)
            if attr:
                referenced.setdefault(key, set()).add(attr[1:])
    return referenced, unparseable


def _schema_type_family(type_repr):
    """Terraform's schema JSON encodes a scalar type as a bare string ("string") and a
    collection as a nested list (["list", "string"], ["map", "number"], ["object", {...}]).
    Reduce either shape to one coarse family name, or None if unrecognized (never guessed)."""
    if isinstance(type_repr, str):
        return type_repr if type_repr in ("string", "bool", "number") else None
    if isinstance(type_repr, list) and type_repr:
        head = type_repr[0]
        if head in ("list", "set", "map", "object"):
            return head
    return None


def _infer_literal_shape(value_text):
    """Best-effort literal shape of a `attr = <value_text>` RHS: only classifies clearly
    unambiguous literals (list/map/string/bool/number). Anything else -- a variable reference,
    function call, interpolation, ternary -- returns None deliberately: type-checking a dynamic
    expression without evaluating Terraform is out of scope, and forcing a verdict there would
    be a real false-positive risk, not a fail-closed win. None means "skip the type-mismatch
    check for this attribute", never a finding by itself."""
    v = value_text.strip().rstrip(",")
    if not v:
        return None
    if v.startswith("["):
        return "list"
    if v.startswith("{"):
        return "map"
    if v.startswith('"') and v.endswith('"') and '${' not in v:
        return "string"
    if v in ("true", "false"):
        return "bool"
    if re.fullmatch(r"-?\d+(\.\d+)?", v):
        return "number"
    return None


def _extract_assigned_values(body, prefix=""):
    """Parallel to _scan_body but keeps the raw RHS text per attribute, for the type-mismatch
    check. Recurses into nested blocks with the same dotted-prefix convention _scan_body and
    _reduce_full/_walk_attributes both use (`versioning.enabled`, not bare `enabled`) -- an
    earlier version of this function only skipped a nested block's header line without
    descending, so a nested attribute's value got attributed to whatever *other*, unrelated
    top-level attribute happened to share its bare name, which could type-check it against the
    wrong schema entry entirely."""
    values = {}
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _DYNAMIC_BLOCK.match(line):
            block_start_offset = sum(len(l) + 1 for l in lines[:i]) + line.index("{")
            end = _matching_brace(body, block_start_offset + 1)
            # +1: see the identical fix (and the real reproduced hang) in _scan_body.
            i = body[:end].count("\n") + 1
            continue
        block_m = _TOP_LEVEL_BLOCK.match(line)
        assign_m = _TOP_LEVEL_ASSIGN.match(line)
        if block_m and block_m.group(1) not in _META_ARGS:
            name = block_m.group(1)
            block_start_offset = sum(len(l) + 1 for l in lines[:i]) + line.index("{")
            end = _matching_brace(body, block_start_offset + 1)
            nested_body = body[block_start_offset + 1:end - 1]
            values.update(_extract_assigned_values(nested_body, prefix=f"{prefix}{name}."))
            i = body[:end].count("\n") + 1
            continue
        if assign_m and assign_m.group(1) not in _META_ARGS:
            name = f"{prefix}{assign_m.group(1)}"
            rhs = line[assign_m.end():]
            # Multi-line literal (list/map spanning several lines, or a function call wrapping
            # one, e.g. jsonencode({...})): fold forward to the closing bracket at this line's
            # depth so _infer_literal_shape sees the whole value. Triggered on any unbalanced
            # RHS, not just one starting with a bracket -- see the matching comment in
            # _scan_body for why a bracket-only check misses the jsonencode(...) case.
            if rhs.strip() and not _balanced(rhs):
                j = i + 1
                while j < len(lines) and not _balanced(rhs):
                    rhs += "\n" + lines[j]
                    j += 1
                i = j - 1
            values[name] = rhs
        i += 1
    return values


def _balanced(text):
    return text.count("[") + text.count("{") <= text.count("]") + text.count("}")


def _object_fields(type_repr):
    """Terraform's newer NestedType attribute encoding represents what used to be a repeatable
    `block_types` entry as a plain ATTRIBUTE whose `type` is (optionally wrapped in list/set/
    map) an `["object", {field: field_type, ...}]` shape -- e.g. aws_route_table's `route` is a
    top-level attribute of type `["set", ["object", {"cidr_block": "string", "gateway_id":
    "string", ...}]]`, not a block_types entry at all, even though the real HCL syntax for it
    is still the traditional repeatable `route { cidr_block = ... }` block (valid Terraform
    sugar for populating a collection-of-objects attribute). Returns the field dict, or None if
    `type_repr` isn't (or doesn't wrap) an object shape -- verified live: an earlier version of
    this function only ever looked at block_types, missing `route`/`ingress`/`egress` and
    similar NestedType-encoded fields entirely, producing false-positive unknown_attribute
    findings against real, valid, already-pinned modules/networking-vpc/main.tf HCL."""
    if isinstance(type_repr, list) and len(type_repr) == 2:
        head, inner = type_repr
        if head == "object" and isinstance(inner, dict):
            return inner
        if head in ("list", "set", "map"):
            return _object_fields(inner)
    return None


def _walk_attributes(block, prefix=""):
    """Recursively walk attributes + nested block_types to arbitrary depth (matching
    schema_watch._deprecated_attrs' own recursion -- real schemas nest more than one level,
    e.g. aws_iam_policy_document's statement.principals.type, or aws_s3_bucket_server_side_
    encryption_configuration's rule.apply_server_side_encryption_by_default.sse_algorithm;
    stopping at one level, as an earlier version of this function did, produced false-positive
    unknown_attribute findings against real, valid, already-pinned module HCL). Also descends
    into object-shaped attributes (NestedType encoding, see _object_fields) the same way --
    two different real schema shapes for the same underlying idea (a nested, named group of
    fields), both must resolve to the same dotted-attribute-path lookup regardless of which
    shape a given resource happens to use.

    NestedType object fields carry no per-field deprecation info in this JSON encoding (unlike
    classic block_types, which do) -- a field synthesized this way is only ever checked for
    existence/rough type family, never flagged deprecated. That is a real, narrower scope than
    the block_types path, disclosed here rather than silently assumed equivalent."""
    attrs = {}
    if not isinstance(block, dict):
        return attrs
    raw_attributes = block.get("attributes")
    if isinstance(raw_attributes, dict):
        for name, attr in raw_attributes.items():
            if not isinstance(attr, dict):
                continue
            attrs[f"{prefix}{name}"] = {"type": attr.get("type"), "deprecated": bool(attr.get("deprecated"))}
            fields = _object_fields(attr.get("type"))
            if fields:
                attrs.update(_walk_object_fields(fields, prefix=f"{prefix}{name}."))
    raw_block_types = block.get("block_types")
    if isinstance(raw_block_types, dict):
        for bname, btype in raw_block_types.items():
            if not isinstance(btype, dict):
                continue
            nested_block = btype.get("block")
            attrs.update(_walk_attributes(nested_block if isinstance(nested_block, dict) else {},
                                           prefix=f"{prefix}{bname}."))
    return attrs


def _walk_object_fields(fields, prefix):
    """Recurse into a NestedType object's fields to arbitrary depth -- an object field can
    itself be object-shaped (an object nested inside an object), same idea as _walk_attributes'
    own block_types recursion, just for the other schema encoding."""
    attrs = {}
    for fname, ftype in fields.items():
        attrs[f"{prefix}{fname}"] = {"type": ftype, "deprecated": False}
        nested_fields = _object_fields(ftype)
        if nested_fields:
            attrs.update(_walk_object_fields(nested_fields, prefix=f"{prefix}{fname}."))
    return attrs


def _reduce_full(schema, used_keys):
    """Like schema_watch._reduce, but keeps the full attribute table (name -> {type,
    deprecated}) for used types, not just version + deprecated-names -- schema_watch never
    needed the full set because it only ever diffs two already-reduced snapshots."""
    resource_schemas = schema.get("resource_schemas", {})
    data_schemas = schema.get("data_source_schemas", {})
    reduced = {}
    for kind, type_name in used_keys:
        table = resource_schemas if kind == "resource" else data_schemas
        entry = table.get(type_name)
        if not isinstance(entry, dict):
            reduced[(kind, type_name)] = None
            continue
        attrs = _walk_attributes(entry.get("block"))
        version = entry.get("version", 0)
        reduced[(kind, type_name)] = {"version": version if isinstance(version, int) else 0,
                                       "attributes": attrs}
    return reduced


def _shape_hash(reduced):
    """Deterministic hash of {type_key: version + sorted attribute names} across every type
    this lint pass actually looked at -- the schema_hash written into PROVENANCE.json."""
    payload = {}
    for (kind, type_name), entry in sorted(reduced.items()):
        if entry is None:
            payload[f"{kind}:{type_name}"] = None
        else:
            payload[f"{kind}:{type_name}"] = {
                "version": entry["version"],
                "attributes": sorted(entry["attributes"]),
            }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def gate_module(module_id):
    """The G2 verdict for one module. Returns:

        {"blocking": bool, "findings": [...], "warnings": [...], "schema_hash": str}

    `findings` entries that make `blocking` True: schema_fetch_failed, schema_malformed,
    unknown_type, unknown_attribute, deprecated_attribute_in_use, type_mismatch,
    unparseable_reference. `warnings` (never blocking): schema_shape_changed_no_signal.
    """
    module_dir = os.path.join(module_registry.MODULES_DIR, module_id)
    main_tf_path = os.path.join(module_dir, "main.tf")
    if not os.path.isfile(main_tf_path):
        return {"blocking": True,
                "findings": [{"finding": "module_not_found", "detail": module_dir}],
                "warnings": [], "schema_hash": None}
    try:
        with open(main_tf_path, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        return {"blocking": True,
                "findings": [{"finding": "module_unreadable", "detail": str(exc)}],
                "warnings": [], "schema_hash": None}

    declared = list(iter_hcl_blocks(content))
    findings = []
    warnings = []
    all_reduced = {}

    providers_present = sorted({
        provider for provider in _PROVIDER_PREFIX
        for _, type_name, _, _ in declared
        if type_name.startswith(_PROVIDER_PREFIX[provider])
    })

    for provider in providers_present:
        prefix = _PROVIDER_PREFIX[provider]
        used_keys = {(kind, type_name) for kind, type_name, _, _ in declared
                     if type_name.startswith(prefix)}
        try:
            schema, _resolved_version = _fetch_schema(provider, os.path.join(
                module_registry.output_root(), ".agents", "schema-lint-work", provider))
        except Exception as exc:
            findings.append({"finding": "schema_fetch_failed", "provider": provider,
                              "detail": str(exc)})
            continue
        if (not isinstance(schema, dict)
                or not isinstance(schema.get("resource_schemas"), dict)
                or not isinstance(schema.get("data_source_schemas"), dict)):
            findings.append({"finding": "schema_malformed", "provider": provider,
                              "detail": "fetched schema missing or malformed resource_schemas/"
                                        "data_source_schemas"})
            continue

        reduced = _reduce_full(schema, used_keys)
        all_reduced.update(reduced)

        for kind, type_name, block_name, body in declared:
            if not type_name.startswith(prefix):
                continue
            entry = reduced.get((kind, type_name))
            if entry is None:
                findings.append({"finding": "unknown_type", "type": f"{kind}:{type_name}",
                                  "block": block_name})
                continue
            attrs = entry["attributes"]

            set_attrs, unparseable_set = _scan_body(body)
            for u in unparseable_set:
                findings.append({"finding": "unparseable_reference", "type": f"{kind}:{type_name}",
                                  "block": block_name, "detail": u})

            for attr_path in set_attrs:
                if attr_path not in attrs:
                    findings.append({"finding": "unknown_attribute", "type": f"{kind}:{type_name}",
                                      "block": block_name, "attribute": attr_path})
                elif attrs[attr_path]["deprecated"]:
                    findings.append({"finding": "deprecated_attribute_in_use",
                                      "type": f"{kind}:{type_name}", "block": block_name,
                                      "attribute": attr_path})

            values = _extract_assigned_values(body)
            for attr_path, raw in values.items():
                if attr_path not in attrs:
                    continue  # already reported as unknown_attribute above
                shape = _infer_literal_shape(raw)
                if shape is None:
                    continue
                family = _schema_type_family(attrs[attr_path]["type"])
                if family is None:
                    continue
                mismatch = (
                    (shape == "list" and family not in ("list", "set")) or
                    (shape == "map" and family not in ("map", "object")) or
                    (shape in ("string", "bool", "number") and family != shape)
                )
                if mismatch:
                    findings.append({"finding": "type_mismatch", "type": f"{kind}:{type_name}",
                                      "block": block_name, "attribute": attr_path,
                                      "literal_shape": shape, "schema_type": family})

        referenced, unparseable_ref = extract_references(content, [
            b for b in declared if b[1].startswith(prefix)
        ])
        for u in unparseable_ref:
            findings.append({"finding": "unparseable_reference", "detail": u})
        for (kind, type_name, block_name), attr_names in referenced.items():
            entry = reduced.get((kind, type_name))
            if entry is None:
                continue  # already reported as unknown_type above
            attrs = entry["attributes"]
            for attr_path in attr_names:
                if attr_path not in attrs:
                    findings.append({"finding": "unknown_attribute", "type": f"{kind}:{type_name}",
                                      "block": block_name, "attribute": attr_path,
                                      "direction": "referenced"})
                elif attrs[attr_path]["deprecated"]:
                    findings.append({"finding": "deprecated_attribute_in_use",
                                      "type": f"{kind}:{type_name}", "block": block_name,
                                      "attribute": attr_path, "direction": "referenced"})

    schema_hash = _shape_hash(all_reduced) if all_reduced else None
    if schema_hash is not None and not findings:
        # A corrupted/unreadable previous PROVENANCE.json must not crash the whole gate over a
        # non-blocking WARN signal -- functionally equivalent to "no prior record to compare",
        # which is already a legitimate no-signal case (first-ever pin), never a reason to
        # block or crash.
        try:
            previous = module_provenance.show(module_id)
        except Exception:
            previous = None
        prev_hash = (previous or {}).get("schema_hash")
        if prev_hash is not None and prev_hash != schema_hash:
            warnings.append({"finding": "schema_shape_changed_no_signal",
                              "detail": "this module's used-type schema shape changed since "
                                        "the last pin, but no specific attribute-level break "
                                        "was detected -- worth a human look, not blocking."})

    return {
        "blocking": bool(findings),
        "findings": findings,
        "warnings": warnings,
        "schema_hash": schema_hash,
    }


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="G2 pre-write schema linter")
    ap.add_argument("--module", required=True)
    args = ap.parse_args(argv)

    result = gate_module(args.module)
    print(json.dumps(result, indent=2))
    return 1 if result["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
