"""
schema_lint.py (G2) checks a module's actual resource/data-source attribute references against
the REAL, LIVE provider schema before the pin CLI accepts it -- docs/g2_scope.md. Unlike
schema_watch.py (a diff engine, no-ops without a prior snapshot), this is a single-point check
against live-schema-now on every call, no baseline required. Unit tests below use synthetic
schema fixtures (fast, hermetic, no network); the tests at the bottom are real-terraform proof
against the live AWS and Databricks providers -- including the exact `.name` -> `.region`
deprecation on `data.aws_region` that motivated the whole generation-time-authoring pivot, and
a real Databricks deprecation (`databricks_mws_credentials.account_id`) proving G2's fetch/
reduce machinery works against both tracked providers, not just AWS.
"""
import json
import os

import pytest

import modules as module_registry
import module_provenance
import schema_lint
import toolpath

TERRAFORM = toolpath.find_tool("terraform")


# ---------------------------------------------------------------------------
# iter_hcl_blocks(): brace-depth-aware resource/data block extraction
# ---------------------------------------------------------------------------

def test_iter_hcl_blocks_finds_resource_and_data():
    content = (
        'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n'
        'data "aws_iam_policy_document" "d" {\n  statement {}\n}\n'
    )
    blocks = list(schema_lint.iter_hcl_blocks(content))
    assert [(k, t, n) for k, t, n, _ in blocks] == [
        ("resource", "aws_s3_bucket", "b"),
        ("data", "aws_iam_policy_document", "d"),
    ]


def test_iter_hcl_blocks_is_brace_depth_aware():
    content = 'resource "aws_s3_bucket" "b" {\n  versioning {\n    enabled = true\n  }\n}\n'
    blocks = list(schema_lint.iter_hcl_blocks(content))
    assert len(blocks) == 1
    assert "versioning" in blocks[0][3]
    assert "enabled = true" in blocks[0][3]


# ---------------------------------------------------------------------------
# _scan_body(): top-level + one-level-nested attribute assignment extraction
# ---------------------------------------------------------------------------

def test_scan_body_extracts_top_level_assignment():
    attrs, unparseable = schema_lint._scan_body('bucket = "x"\n')
    assert attrs == {"bucket"}
    assert unparseable == []


def test_scan_body_excludes_meta_arguments():
    attrs, _ = schema_lint._scan_body('count = 2\nbucket = "x"\nfor_each = var.x\n')
    assert attrs == {"bucket"}


def test_scan_body_recurses_one_level_into_nested_block_with_dotted_prefix():
    attrs, _ = schema_lint._scan_body('versioning {\n  enabled = true\n}\n')
    assert attrs == {"versioning.enabled"}


def test_scan_body_treats_assignment_style_map_as_a_plain_attribute_not_a_block():
    attrs, _ = schema_lint._scan_body('tags = {\n  Team = "x"\n}\n')
    assert attrs == {"tags"}


def test_scan_body_flags_dynamic_block_as_unparseable_and_does_not_descend():
    attrs, unparseable = schema_lint._scan_body(
        'dynamic "ingress" {\n  for_each = var.rules\n  content {\n    port = each.value.port\n  }\n}\n'
    )
    assert attrs == set()
    assert unparseable == ["dynamic:ingress"]


def test_scan_body_terminates_on_an_inline_empty_nested_block():
    # Real, severe bug found dogfooding modules/dq-great-expectations/main.tf's
    # `aws_s3_bucket_lifecycle_configuration` rule (a completely valid, real Terraform
    # pattern: `filter {}` with no filter criteria). A block that opens AND closes on the
    # same physical line advanced the line index to its OWN index instead of the next line,
    # re-entering the same line forever -- an actual infinite loop, not just a slow case
    # (reproduced directly: hung 6+ hours before this fix). pytest-timeout isn't a dependency
    # here, so this asserts on the *result* being reachable at all -- if this regresses, the
    # test itself hangs forever rather than failing cleanly, which is exactly why the real
    # dogfood run against all 16 modules (not just unit fixtures) is part of G2's proof bar.
    attrs, unparseable = schema_lint._scan_body(
        'bucket = "x"\nrule {\n  id = "y"\n  filter {}\n  expiration {\n    days = 90\n  }\n}\n'
    )
    assert attrs == {"bucket", "rule.id", "rule.expiration.days"}
    assert unparseable == []


def test_scan_body_handles_multiple_siblings_after_a_nested_block():
    attrs, _ = schema_lint._scan_body(
        'versioning {\n  enabled = true\n}\nbucket = "x"\n'
    )
    assert attrs == {"versioning.enabled", "bucket"}


# ---------------------------------------------------------------------------
# extract_references(): type.name.attr chains, index/splat access -> unparseable
# ---------------------------------------------------------------------------

def test_extract_references_finds_resource_and_data_chains():
    content = (
        'resource "aws_s3_bucket" "b" {}\n'
        'data "aws_iam_policy_document" "d" {}\n'
        'resource "aws_s3_bucket_policy" "p" {\n'
        '  bucket = aws_s3_bucket.b.bucket\n'
        '  policy = data.aws_iam_policy_document.d.json\n'
        '}\n'
    )
    declared = [("resource", "aws_s3_bucket", "b", ""), ("data", "aws_iam_policy_document", "d", "")]
    referenced, unparseable = schema_lint.extract_references(content, declared)
    assert referenced[("resource", "aws_s3_bucket", "b")] == {"bucket"}
    assert referenced[("data", "aws_iam_policy_document", "d")] == {"json"}
    assert unparseable == []


def test_extract_references_resolves_attribute_after_index_access():
    # aws_subnet.s[0].id -- an optional, count-based resource's output wired elsewhere. What's
    # inside the brackets only selects which instance, never which attribute; `.id` is exactly
    # as statically known as it would be without the index. Real, common pattern (this repo's
    # own databricks-workspace module wires databricks_metastore.this[0].id this way) -- must
    # resolve, not block.
    content = (
        'resource "aws_subnet" "s" {}\n'
        'output "first_id" {\n  value = aws_subnet.s[0].id\n}\n'
    )
    declared = [("resource", "aws_subnet", "s", "")]
    referenced, unparseable = schema_lint.extract_references(content, declared)
    assert referenced[("resource", "aws_subnet", "s")] == {"id"}
    assert unparseable == []


def test_extract_references_resolves_attribute_after_splat_access():
    content = (
        'resource "aws_subnet" "s" {}\n'
        'output "all_ids" {\n  value = aws_subnet.s[*].id\n}\n'
    )
    declared = [("resource", "aws_subnet", "s", "")]
    referenced, unparseable = schema_lint.extract_references(content, declared)
    assert referenced[("resource", "aws_subnet", "s")] == {"id"}


def test_extract_references_bracket_with_no_trailing_attribute_is_not_a_finding():
    # The whole indexed object used as-is (e.g. passed to a function), with no attribute
    # narrowed -- nothing to check, not a finding, not unparseable.
    content = (
        'resource "aws_subnet" "s" {}\n'
        'output "whole" {\n  value = aws_subnet.s[0]\n}\n'
    )
    declared = [("resource", "aws_subnet", "s", "")]
    referenced, unparseable = schema_lint.extract_references(content, declared)
    assert referenced == {}
    assert unparseable == []


# ---------------------------------------------------------------------------
# _infer_literal_shape() / _schema_type_family(): best-effort, conservative
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ('["a", "b"]', "list"),
    ('{ foo = "bar" }', "map"),
    ('"hello"', "string"),
    ("true", "bool"),
    ("42", "number"),
    ("3.14", "number"),
    ("var.something", None),
    ("aws_s3_bucket.b.arn", None),
    ('"${var.x}"', None),
])
def test_infer_literal_shape(text, expected):
    assert schema_lint._infer_literal_shape(text) == expected


@pytest.mark.parametrize("type_repr,expected", [
    ("string", "string"),
    ("bool", "bool"),
    ("number", "number"),
    (["list", "string"], "list"),
    (["set", "string"], "set"),
    (["map", "string"], "map"),
    (["object", {"a": "string"}], "object"),
    (None, None),
    ("dynamic", None),
])
def test_schema_type_family(type_repr, expected):
    assert schema_lint._schema_type_family(type_repr) == expected


# ---------------------------------------------------------------------------
# _reduce_full(): full attribute table (not just deprecated names + version)
# ---------------------------------------------------------------------------

def test_reduce_full_keeps_every_attribute_with_type_and_deprecated_flag():
    schema = {
        "resource_schemas": {
            "aws_s3_bucket": {
                "version": 1,
                "block": {
                    "attributes": {
                        "bucket": {"type": "string"},
                        "acl": {"type": "string", "deprecated": True},
                    },
                    "block_types": {
                        "versioning": {"block": {"attributes": {
                            "enabled": {"type": "bool"},
                        }}},
                    },
                },
            }
        },
        "data_source_schemas": {},
    }
    reduced = schema_lint._reduce_full(schema, {("resource", "aws_s3_bucket")})
    entry = reduced[("resource", "aws_s3_bucket")]
    assert entry["version"] == 1
    assert entry["attributes"]["bucket"] == {"type": "string", "deprecated": False}
    assert entry["attributes"]["acl"] == {"type": "string", "deprecated": True}
    assert entry["attributes"]["versioning.enabled"] == {"type": "bool", "deprecated": False}


def test_reduce_full_recurses_more_than_one_level_deep():
    # Real false positive found dogfooding: aws_iam_policy_document's statement.principals.type
    # is nested TWO levels deep (statement -> principals -> type), and aws_s3_bucket_server_
    # side_encryption_configuration's rule.apply_server_side_encryption_by_default.sse_algorithm
    # is nested three levels deep. A one-level-only walk (an earlier version of this function)
    # produced unknown_attribute findings against real, valid, already-pinned module HCL.
    schema = {
        "resource_schemas": {},
        "data_source_schemas": {
            "aws_iam_policy_document": {
                "version": 0,
                "block": {
                    "attributes": {},
                    "block_types": {
                        "statement": {"block": {
                            "attributes": {},
                            "block_types": {
                                "principals": {"block": {
                                    "attributes": {"type": {"type": "string"},
                                                   "identifiers": {"type": ["list", "string"]}},
                                    "block_types": {},
                                }},
                            },
                        }},
                    },
                },
            }
        },
    }
    reduced = schema_lint._reduce_full(schema, {("data", "aws_iam_policy_document")})
    attrs = reduced[("data", "aws_iam_policy_document")]["attributes"]
    assert "statement.principals.type" in attrs
    assert "statement.principals.identifiers" in attrs


def test_reduce_full_descends_into_nestedtype_object_attributes():
    # Real false positive found dogfooding modules/networking-vpc/main.tf: aws_route_table's
    # `route` is a top-level ATTRIBUTE of type ["set", ["object", {"cidr_block": "string",
    # "gateway_id": "string", ...}]] -- Terraform's newer NestedType encoding, not a
    # block_types entry at all -- even though the real HCL syntax for it is still the
    # traditional repeatable `route { cidr_block = ... }` block. A version of this function
    # that only ever looked at block_types missed this shape entirely.
    schema = {
        "resource_schemas": {
            "aws_route_table": {
                "version": 0,
                "block": {
                    "attributes": {
                        "route": {"type": ["set", ["object", {
                            "cidr_block": "string", "gateway_id": "string",
                        }]]},
                    },
                    "block_types": {},
                },
            }
        },
        "data_source_schemas": {},
    }
    reduced = schema_lint._reduce_full(schema, {("resource", "aws_route_table")})
    attrs = reduced[("resource", "aws_route_table")]["attributes"]
    assert "route" in attrs
    assert "route.cidr_block" in attrs
    assert "route.gateway_id" in attrs


def test_object_fields_recurses_into_nested_objects():
    fields = {"outer": ["object", {"inner": "string"}]}
    attrs = schema_lint._walk_object_fields(fields, prefix="parent.")
    assert "parent.outer" in attrs
    assert "parent.outer.inner" in attrs


def test_object_fields_returns_none_for_non_object_types():
    assert schema_lint._object_fields("string") is None
    assert schema_lint._object_fields(["list", "string"]) is None
    assert schema_lint._object_fields(None) is None


def test_reduce_full_records_none_for_a_type_absent_from_the_schema():
    schema = {"resource_schemas": {}, "data_source_schemas": {}}
    reduced = schema_lint._reduce_full(schema, {("resource", "aws_ghost")})
    assert reduced[("resource", "aws_ghost")] is None


# ---------------------------------------------------------------------------
# gate_module(): the full classification, network stubbed via _fetch_schema
# ---------------------------------------------------------------------------

def _stub_schema(attrs=None, deprecated=()):
    attrs = attrs or {"bucket": {"type": "string"}}
    for name in deprecated:
        attrs[name] = {**attrs.get(name, {"type": "string"}), "deprecated": True}
    return {
        "resource_schemas": {"aws_s3_bucket": {"version": 0, "block": {
            "attributes": attrs, "block_types": {},
        }}},
        "data_source_schemas": {},
    }


@pytest.fixture
def fake_module(tmp_path, monkeypatch):
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    (modules_dir / "widget").mkdir()
    monkeypatch.setattr(module_registry, "MODULES_DIR", str(modules_dir))
    monkeypatch.setattr(module_registry, "output_root", lambda: str(tmp_path))
    return modules_dir / "widget"


def _write(module_dir, content):
    (module_dir / "main.tf").write_text(content, encoding="utf-8")


def test_gate_module_clean_module_is_not_blocking(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is False
    assert result["findings"] == []
    assert result["schema_hash"] is not None


def test_gate_module_unknown_type_blocks(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_ghost_resource" "b" {}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is True
    assert result["findings"][0]["finding"] == "unknown_type"


def test_gate_module_unknown_set_attribute_blocks(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {\n  totally_made_up = "x"\n}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is True
    findings = [f for f in result["findings"] if f["finding"] == "unknown_attribute"]
    assert findings and findings[0]["attribute"] == "totally_made_up"


def test_gate_module_unknown_referenced_attribute_blocks(fake_module, monkeypatch):
    _write(fake_module,
           'resource "aws_s3_bucket" "b" {}\n'
           'output "x" {\n  value = aws_s3_bucket.b.made_up_attr\n}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is True
    findings = [f for f in result["findings"] if f["finding"] == "unknown_attribute"]
    assert findings and findings[0]["attribute"] == "made_up_attr"
    assert findings[0]["direction"] == "referenced"


def test_gate_module_deprecated_set_attribute_blocks(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {\n  acl = "private"\n}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema",
                         lambda provider, workdir: (_stub_schema(deprecated=["acl"]), "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is True
    findings = [f for f in result["findings"] if f["finding"] == "deprecated_attribute_in_use"]
    assert findings and findings[0]["attribute"] == "acl"


def test_gate_module_deprecated_referenced_attribute_blocks(fake_module, monkeypatch):
    _write(fake_module,
           'resource "aws_s3_bucket" "b" {}\n'
           'output "x" {\n  value = aws_s3_bucket.b.acl\n}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema",
                         lambda provider, workdir: (_stub_schema(deprecated=["acl"]), "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is True
    findings = [f for f in result["findings"] if f["finding"] == "deprecated_attribute_in_use"]
    assert findings and findings[0]["direction"] == "referenced"


def test_gate_module_type_mismatch_blocks(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {\n  bucket = ["not", "a", "string"]\n}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is True
    findings = [f for f in result["findings"] if f["finding"] == "type_mismatch"]
    assert findings and findings[0]["attribute"] == "bucket"


def test_gate_module_matching_type_does_not_block():
    # Regression guard for the type-mismatch check specifically: a correctly-typed literal
    # must never be flagged. Exercised via the full clean-module test above (bucket = "x",
    # schema type "string") -- this test just makes that intent explicit.
    assert schema_lint._infer_literal_shape('"x"') == "string"
    assert schema_lint._schema_type_family("string") == "string"


def test_gate_module_dynamic_expression_skips_type_check_not_a_finding(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {\n  bucket = var.bucket_name\n}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is False


def test_gate_module_unparseable_dynamic_block_blocks(fake_module, monkeypatch):
    _write(fake_module,
           'resource "aws_s3_bucket" "b" {\n'
           '  dynamic "grant" {\n    for_each = var.grants\n    content {\n      x = 1\n    }\n  }\n'
           '}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is True
    assert any(f["finding"] == "unparseable_reference" for f in result["findings"])


def test_gate_module_count_based_index_reference_resolves_and_does_not_block(fake_module, monkeypatch):
    # The real pattern found dogfooding against the actual databricks-workspace module:
    # databricks_metastore.this[0].id, wiring an optional count-based resource's output
    # elsewhere. Must resolve `.bucket` against the schema like any other reference, not block.
    _write(fake_module,
           'resource "aws_s3_bucket" "b" {\n  count = 2\n}\n'
           'output "x" {\n  value = aws_s3_bucket.b[0].bucket\n}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is False


def test_gate_module_jsonencode_wrapped_multiline_value_does_not_leak_json_keys_as_attributes(fake_module, monkeypatch):
    # Real false positive found dogfooding against compute-glue-etl: `event_pattern =
    # jsonencode({...})` spanning multiple lines was previously only recognized as multi-line
    # when the RHS *starts* with a bracket -- jsonencode(...) starts with a letter, so the
    # fold never triggered, and the JSON payload's own keys (source, detail) leaked out as if
    # they were sibling top-level Terraform attributes of the resource.
    schema = {
        "resource_schemas": {"aws_cloudwatch_event_rule": {"version": 0, "block": {
            "attributes": {"event_pattern": {"type": "string"}, "name": {"type": "string"}},
            "block_types": {},
        }}},
        "data_source_schemas": {},
    }
    _write(fake_module,
           'resource "aws_cloudwatch_event_rule" "r" {\n'
           '  name = "x"\n'
           '  event_pattern = jsonencode({\n'
           '    source = ["aws.glue"]\n'
           '    detail = {\n'
           '      state = ["FAILED"]\n'
           '    }\n'
           '  })\n'
           '}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (schema, "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is False


def test_gate_module_fetch_failure_blocks(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {}\n')

    def boom(provider, workdir):
        raise RuntimeError("terraform init failed")
    monkeypatch.setattr(schema_lint, "_fetch_schema", boom)

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is True
    assert result["findings"][0]["finding"] == "schema_fetch_failed"


def test_gate_module_malformed_schema_blocks(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: ({"nonsense": True}, "x"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is True
    assert result["findings"][0]["finding"] == "schema_malformed"


def test_gate_module_not_found_blocks():
    result = schema_lint.gate_module("does-not-exist-anywhere")
    assert result["blocking"] is True
    assert result["findings"][0]["finding"] == "module_not_found"


def test_gate_module_no_types_used_for_a_provider_is_not_a_failure(fake_module, monkeypatch):
    # No resource/data blocks at all -- nothing to check, nothing fetched, not blocking.
    _write(fake_module, 'output "noop" {\n  value = "x"\n}\n')
    calls = []
    monkeypatch.setattr(schema_lint, "_fetch_schema",
                         lambda provider, workdir: (calls.append(provider), (_stub_schema(), "6.54.0"))[1])

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is False
    assert calls == []


# ---------------------------------------------------------------------------
# Fail-closed sweep (Probe-A style, matching the discipline that closed destructive_change_
# gate.py's six gaps): every malformed/missing-input path below must return a structured
# blocking (or, for the WARN-only path, silently-skipped) result -- never crash, never a
# silent fail-open.
# ---------------------------------------------------------------------------

def test_gate_module_unreadable_file_blocks_not_crashes(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {}\n')
    # Corrupt the file to invalid UTF-8 after writing valid content -- triggers
    # UnicodeDecodeError on read, not OSError, but exercises the same catch.
    (fake_module / "main.tf").write_bytes(b'\xff\xfe resource "aws_s3_bucket" "b" {}')

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is True
    assert result["findings"][0]["finding"] == "module_unreadable"


def test_gate_module_schema_with_non_dict_resource_schemas_blocks(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema",
                         lambda provider, workdir: ({"resource_schemas": "not-a-dict",
                                                      "data_source_schemas": {}}, "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is True
    assert result["findings"][0]["finding"] == "schema_malformed"


def test_gate_module_schema_missing_data_source_schemas_blocks(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema",
                         lambda provider, workdir: ({"resource_schemas": {}}, "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is True
    assert result["findings"][0]["finding"] == "schema_malformed"


def test_walk_attributes_returns_empty_for_non_dict_block():
    assert schema_lint._walk_attributes("not-a-dict") == {}
    assert schema_lint._walk_attributes(None) == {}


def test_walk_attributes_skips_non_dict_attribute_entries():
    block = {"attributes": {"good": {"type": "string"}, "bad": "not-a-dict"}, "block_types": {}}
    attrs = schema_lint._walk_attributes(block)
    assert "good" in attrs
    assert "bad" not in attrs


def test_walk_attributes_skips_non_dict_block_type_entries():
    block = {"attributes": {}, "block_types": {"good": {"block": {"attributes": {"x": {"type": "string"}}}},
                                                 "bad": "not-a-dict"}}
    attrs = schema_lint._walk_attributes(block)
    assert "good.x" in attrs
    assert not any(k.startswith("bad") for k in attrs)


def test_reduce_full_treats_non_dict_entry_as_missing():
    schema = {"resource_schemas": {"aws_s3_bucket": "not-a-dict"}, "data_source_schemas": {}}
    reduced = schema_lint._reduce_full(schema, {("resource", "aws_s3_bucket")})
    assert reduced[("resource", "aws_s3_bucket")] is None


def test_reduce_full_defaults_non_int_version_to_zero():
    schema = {"resource_schemas": {"aws_s3_bucket": {"version": "not-an-int", "block": {}}},
              "data_source_schemas": {}}
    reduced = schema_lint._reduce_full(schema, {("resource", "aws_s3_bucket")})
    assert reduced[("resource", "aws_s3_bucket")]["version"] == 0


def test_gate_module_corrupted_previous_provenance_skips_warn_not_crash(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))

    def boom(module_id):
        raise json.JSONDecodeError("bad json", "doc", 0)
    monkeypatch.setattr(module_provenance, "show", boom)

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is False
    assert result["warnings"] == []


# ---------------------------------------------------------------------------
# Hard property (#1): no first-run pass, no missing-baseline skip -- the live fetch and every
# blocking check run identically whether or not a previous pin exists.
# ---------------------------------------------------------------------------

def test_gate_module_first_ever_lint_still_fetches_live_and_still_blocks(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {\n  totally_made_up = "x"\n}\n')
    monkeypatch.setattr(module_provenance, "show", lambda module_id: None)  # never pinned before
    fetch_calls = []

    def spy_fetch(provider, workdir):
        fetch_calls.append(provider)
        return _stub_schema(), "6.54.0"
    monkeypatch.setattr(schema_lint, "_fetch_schema", spy_fetch)

    result = schema_lint.gate_module("widget")

    assert fetch_calls == ["aws"]  # the live fetch happened -- no baseline-missing skip
    assert result["blocking"] is True
    assert result["findings"][0]["finding"] == "unknown_attribute"


def test_gate_module_warns_on_shape_change_with_no_attribute_signal(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n')
    monkeypatch.setattr(module_provenance, "show",
                         lambda module_id: {"schema_hash": "some-old-hash-that-wont-match"})
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is False
    assert result["findings"] == []
    assert len(result["warnings"]) == 1
    assert result["warnings"][0]["finding"] == "schema_shape_changed_no_signal"


def test_gate_module_no_warning_on_first_ever_pin_nothing_to_compare(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n')
    monkeypatch.setattr(module_provenance, "show", lambda module_id: None)
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is False
    assert result["warnings"] == []


# ---------------------------------------------------------------------------
# module_provenance.py CLI wiring: pin refuses on a blocking G2 verdict, never writes
# PROVENANCE.json, and never calls pin() at all.
# ---------------------------------------------------------------------------

def test_cli_pin_refuses_on_blocking_lint(fake_module, monkeypatch, capsys):
    _write(fake_module, 'resource "aws_ghost_resource" "b" {}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))
    monkeypatch.setattr(module_provenance, "_module_dir",
                         lambda module_id: str(fake_module))

    rc = module_provenance.main(["pin", "--module", "widget", "--source", "test"])

    assert rc == 1
    assert "REFUSED" in capsys.readouterr().err
    assert not (fake_module / "PROVENANCE.json").exists()


def test_cli_pin_proceeds_and_auto_fills_schema_hash_when_clean(fake_module, monkeypatch, capsys):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))
    monkeypatch.setattr(module_provenance, "_module_dir",
                         lambda module_id: str(fake_module))

    rc = module_provenance.main(["pin", "--module", "widget", "--source", "test"])

    assert rc == 0
    record = json.loads((fake_module / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert record["schema_hash"] is not None


def test_cli_pin_explicit_schema_hash_still_overrides(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))
    monkeypatch.setattr(module_provenance, "_module_dir",
                         lambda module_id: str(fake_module))

    rc = module_provenance.main(["pin", "--module", "widget", "--source", "test",
                                  "--schema-hash", "explicit-override"])

    assert rc == 0
    record = json.loads((fake_module / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert record["schema_hash"] == "explicit-override"


# ---------------------------------------------------------------------------
# CLI (schema_lint.main())
# ---------------------------------------------------------------------------

def test_schema_lint_cli_exits_1_when_blocking(fake_module, monkeypatch, capsys):
    _write(fake_module, 'resource "aws_ghost_resource" "b" {}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))

    rc = schema_lint.main(["--module", "widget"])

    assert rc == 1
    assert '"blocking": true' in capsys.readouterr().out


def test_schema_lint_cli_exits_0_when_clean(fake_module, monkeypatch):
    _write(fake_module, 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n')
    monkeypatch.setattr(schema_lint, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))

    assert schema_lint.main(["--module", "widget"]) == 0


# ---------------------------------------------------------------------------
# Real terraform integration (skipped if terraform isn't installed) -- proves the actual
# `terraform providers schema -json` call, on the REAL live provider, catches the exact break
# that motivated the pivot, on BOTH tracked providers, not just AWS.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_real_aws_catches_the_name_to_region_deprecation(fake_module, monkeypatch):
    """The exact v5->v6 class of break that motivated the generation-time-authoring pivot:
    data.aws_region's `name` attribute is deprecated in favor of `region` -- this repo's own
    modules already use the post-break `.region` form (modules/databricks-workspace/main.tf,
    modules/networking-vpc/main.tf). This fixture intentionally regresses to the pre-break
    `.name` form and must be caught against the real, live AWS provider schema -- not a mock."""
    _write(fake_module,
           'data "aws_region" "current" {}\n'
           'output "region_name" {\n  value = data.aws_region.current.name\n}\n')

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is True
    deprecated = [f for f in result["findings"] if f["finding"] == "deprecated_attribute_in_use"]
    assert any(f["attribute"] == "name" for f in deprecated)


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_real_databricks_catches_a_real_deprecation(fake_module, monkeypatch):
    """Confirms G2's fetch/reduce machinery works against the Databricks provider too, not
    just AWS (docs/g2_scope.md #3) -- proven, not disclosed. `databricks_mws_credentials`'s
    `account_id` attribute is deprecated on the real, live Databricks provider (Databricks'
    own docs: it should come from the provider instance instead) -- modules/databricks-
    workspace/main.tf already knows this and deliberately avoids setting it (see the comment
    at that resource block). This fixture intentionally sets it and must be caught."""
    _write(fake_module,
           'resource "databricks_mws_credentials" "this" {\n'
           '  account_id        = "000000000000"\n'
           '  credentials_name  = "x"\n'
           '  role_arn          = "arn:aws:iam::000000000000:role/x"\n'
           '}\n')

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is True
    deprecated = [f for f in result["findings"] if f["finding"] == "deprecated_attribute_in_use"]
    assert any(f["attribute"] == "account_id" for f in deprecated)


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_real_aws_clean_module_is_not_blocking(fake_module, monkeypatch):
    """Real-schema counterpart to the synthetic clean-module test: the post-break `.region`
    form (what this repo's real modules actually use) must not be flagged."""
    _write(fake_module,
           'data "aws_region" "current" {}\n'
           'output "region_name" {\n  value = data.aws_region.current.region\n}\n')

    result = schema_lint.gate_module("widget")

    assert result["blocking"] is False


# ---------------------------------------------------------------------------
# gate_content()/gate_module() parity (docs/phase6_step1_authoring_scope.md section 2, proof-bar
# item 3): the refactor split gate_module()'s disk-read from its linting logic into a thin
# wrapper calling gate_content() -- this proves the split changed NOTHING about the verdict,
# against every one of this repo's 16 real, currently-pinned modules, not a synthetic sample.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
@pytest.mark.parametrize("module_id", [m["id"] for m in module_registry.list_modules()])
def test_gate_content_is_byte_identical_to_gate_module_for_every_real_module(module_id):
    main_tf_path = os.path.join(module_registry.module_dir(module_id), "main.tf")
    with open(main_tf_path, encoding="utf-8") as f:
        content = f.read()

    via_module = schema_lint.gate_module(module_id)
    via_content = schema_lint.gate_content(content, module_id)

    assert via_content == via_module, (
        f"{module_id}: gate_content() diverged from gate_module() -- the disk-read/lint-logic "
        f"split changed behavior.\nvia_module={via_module}\nvia_content={via_content}"
    )
