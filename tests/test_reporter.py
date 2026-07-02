"""
Golden tests for the deploy report — the product surface that had no coverage.

The architecture SVG is a BINDING cross-tool contract (docs/architecture_svg_spec.md):
one plan must always yield the same structure. These tests enforce the hard
requirements so the shipping code can never silently drift from the spec again.
They also lock the report manifest shape and the plan-hash agreement with the gate.
"""
import json
import xml.etree.ElementTree as ET

import plan_gate
import reporter

SVG_NS = "{http://www.w3.org/2000/svg}"

# A representative plan spanning every tier + the security band.
PLAN = {
    "format_version": "1.2",
    "variables": {"owner": {"value": "data-platform"}},
    "resource_changes": [
        {"address": 'aws_s3_bucket.zone["bronze"]', "type": "aws_s3_bucket",
         "name": "zone", "change": {"actions": ["create"]}},
        {"address": "aws_glue_job.bronze_to_silver", "type": "aws_glue_job",
         "name": "bronze_to_silver", "change": {"actions": ["create"]}},
        {"address": "aws_sfn_state_machine.pipeline", "type": "aws_sfn_state_machine",
         "name": "pipeline", "change": {"actions": ["create"]}},
        {"address": "aws_cloudwatch_metric_alarm.fail", "type": "aws_cloudwatch_metric_alarm",
         "name": "fail", "change": {"actions": ["create"]}},
        {"address": "aws_iam_role.glue_role", "type": "aws_iam_role",
         "name": "glue_role", "change": {"actions": ["create"]}},
        {"address": "aws_kms_key.pipeline", "type": "aws_kms_key",
         "name": "pipeline", "change": {"actions": ["create"]}},
    ],
    "output_changes": {},
}

REQUIRED_GROUP_IDS = [
    "bg", "titlebar", "edges",
    "tier-sources", "tier-storage", "tier-compute",
    "tier-orchestration", "tier-observability",
    "band-security", "legend",
]


TEMPLATE_GENERIC = "generic-stack"


def _svg():
    # Generic grid layout (tier columns) — used for the structural contract tests.
    rows, _ = reporter.summarize(PLAN)
    return reporter.build_svg(rows, TEMPLATE_GENERIC, "aws", "abc123def456", "2026-06-28 12:00 UTC")


def _pipeline_svg(findings=None):
    # Known-blueprint flow/topology layout (spec v2 §9).
    rows, _ = reporter.summarize(PLAN)
    return reporter.build_svg(rows, "aws-data-pipeline-standard", "aws", "abc123def456",
                              "2026-06-28 12:00 UTC", findings=findings)


def _group_ids_in_order(root):
    return [el.attrib["id"] for el in root.iter(SVG_NS + "g") if "id" in el.attrib]


def test_svg_is_wellformed_and_has_fixed_viewbox():
    svg = _svg()
    root = ET.fromstring(svg)  # raises if not well-formed XML
    assert root.tag == SVG_NS + "svg"
    assert root.attrib["viewBox"] == "0 0 1280 760"      # hard requirement #1
    assert root.attrib["width"] == "100%"
    assert root.attrib["role"] == "img"


def test_svg_title_and_desc_are_first_children():
    root = ET.fromstring(_svg())
    children = list(root)
    assert children[0].tag == SVG_NS + "title"
    assert children[1].tag == SVG_NS + "desc"


def test_svg_has_the_nine_named_groups_in_order():
    root = ET.fromstring(_svg())
    ids = _group_ids_in_order(root)
    # All required groups present, in the spec's document order.
    positions = [ids.index(gid) for gid in REQUIRED_GROUP_IDS]
    assert positions == sorted(positions), f"groups out of order: {ids}"
    for gid in REQUIRED_GROUP_IDS:
        assert gid in ids, f"missing required group: {gid}"


def test_every_node_carries_address_and_action():
    root = ET.fromstring(_svg())
    nodes = [el for el in root.iter(SVG_NS + "g") if el.attrib.get("class") == "node"]
    assert nodes, "expected at least one resource node"
    addresses = set()
    for node in nodes:
        assert node.attrib.get("data-address"), "node missing data-address"
        assert node.attrib.get("data-action"), "node missing data-action"
        addresses.add(node.attrib["data-address"])
    # Every managed resource in the plan appears as a node.
    for change in PLAN["resource_changes"]:
        assert change["address"] in addresses


def test_titlebar_shows_template_cloud_and_hash():
    svg = _svg()
    assert "generic-stack" in svg
    assert "abc123def456" in svg
    assert "· plan abc123def456 ·" in svg


def test_bespoke_nonconformant_diagram_is_gone():
    # The old hand-drawn pipeline diagram used Segoe-UI styling, a 720 viewBox, and
    # swimlanes with no data-address. None of that may survive.
    svg = _svg()
    assert "Segoe UI" not in svg
    assert "viewBox=\"0 0 1280 720\"" not in svg
    assert 'class="swim"' not in svg
    assert not hasattr(reporter, "build_pipeline_svg")


def test_dataflow_svg_is_wellformed_layered_and_carries_data_address():
    plan = {
        "resource_changes": [
            {"address": 'aws_s3_bucket.zone["bronze"]', "type": "aws_s3_bucket", "name": "zone",
             "module_address": "module.storage", "mode": "managed", "change": {"actions": ["create"]}},
            {"address": "aws_glue_job.dq", "type": "aws_glue_job", "name": "dq",
             "module_address": "module.compute", "mode": "managed", "change": {"actions": ["create"]}},
            {"address": "aws_athena_workgroup.this", "type": "aws_athena_workgroup", "name": "this",
             "module_address": "module.query", "mode": "managed", "change": {"actions": ["create"]}},
            {"address": "aws_iam_role.glue", "type": "aws_iam_role", "name": "glue",
             "module_address": "module.compute", "mode": "managed", "change": {"actions": ["create"]}},
        ],
        "configuration": {"root_module": {"module_calls": {}}},
    }
    rows, _ = reporter.summarize(plan)
    svg = reporter.build_dataflow_svg(rows, "terraform", "aws", "abc123def456", "2026-07-01 12:00 UTC",
                                      findings=[], plan=plan)
    root = ET.fromstring(svg)                         # raises if not well-formed XML
    assert root.attrib["viewBox"].startswith("0 0 1280")
    assert "STORAGE &amp; PROCESSING" in svg          # ampersand is escaped (renders standalone)
    assert "SECURITY &amp; MONITORING" in svg
    nodes = [el for el in root.iter(SVG_NS + "g") if el.attrib.get("class") == "node"]
    assert nodes and all(n.attrib.get("data-address") for n in nodes)


def test_dataflow_icon_embedding_is_sanitized(tmp_path):
    # Icon files are operator-supplied (untrusted). Active content must never reach the
    # emitted SVG — a hostile icon falls back to the built-in generic glyph.
    (tmp_path / "s3.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<script>alert(1)</script>'
        '<rect width="64" height="64" onload="alert(2)"/>'
        '<a href="javascript:alert(3)"><circle r="9"/></a>'
        '<image href="https://evil.example/x.svg"/></svg>', encoding="utf-8")
    plan = {"resource_changes": [
        {"address": 'aws_s3_bucket.zone["bronze"]', "type": "aws_s3_bucket", "name": "zone",
         "mode": "managed", "change": {"actions": ["create"]}}], "output_changes": {}}
    rows, _ = reporter.summarize(plan)
    svg = reporter.build_dataflow_svg(rows, "t", "aws", "h", "ts", plan=plan, icons_dir=str(tmp_path))
    ET.fromstring(svg)                       # still well-formed
    low = svg.lower()
    assert "<script" not in low and "onload" not in low
    assert "javascript:" not in low and "evil.example" not in low


def test_dataflow_benign_icon_still_embeds(tmp_path):
    (tmp_path / "s3.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<path id="bkt" d="M4,8 H60 L52,58 H12 Z" fill="#d95d39"/></svg>', encoding="utf-8")
    plan = {"resource_changes": [
        {"address": 'aws_s3_bucket.zone["bronze"]', "type": "aws_s3_bucket", "name": "zone",
         "mode": "managed", "change": {"actions": ["create"]}}], "output_changes": {}}
    rows, _ = reporter.summarize(plan)
    svg = reporter.build_dataflow_svg(rows, "t", "aws", "h", "ts", plan=plan, icons_dir=str(tmp_path))
    ET.fromstring(svg)
    assert 'd="M4,8 H60 L52,58 H12 Z"' in svg          # real icon path embedded
    assert '_bkt"' in svg                              # ids namespaced per node


def test_dataflow_renders_every_transform_not_just_stage_gaps():
    # 2 stages leave one slot between them; the 2 extra Glue jobs must still render.
    plan = {"resource_changes": [
        {"address": 'aws_s3_bucket.zone["bronze"]', "type": "aws_s3_bucket", "name": "zone",
         "mode": "managed", "change": {"actions": ["create"]}},
        {"address": 'aws_s3_bucket.zone["silver"]', "type": "aws_s3_bucket", "name": "zone",
         "mode": "managed", "change": {"actions": ["create"]}},
        {"address": "aws_glue_job.a", "type": "aws_glue_job", "name": "a",
         "mode": "managed", "change": {"actions": ["create"]}},
        {"address": "aws_glue_job.b", "type": "aws_glue_job", "name": "b",
         "mode": "managed", "change": {"actions": ["create"]}},
        {"address": "aws_glue_job.c", "type": "aws_glue_job", "name": "c",
         "mode": "managed", "change": {"actions": ["create"]}},
    ], "output_changes": {}}
    rows, _ = reporter.summarize(plan)
    svg = reporter.build_dataflow_svg(rows, "t", "aws", "h", "ts", plan=plan)
    root = ET.fromstring(svg)
    addrs = {n.attrib["data-address"] for n in root.iter(SVG_NS + "g")
             if n.attrib.get("class") == "node"}
    for a in ("aws_glue_job.a", "aws_glue_job.b", "aws_glue_job.c"):
        assert a in addrs, f"transform silently dropped: {a}"


def _medallion_plan(job_names):
    changes = [
        {"address": f'aws_s3_bucket.zone["{z}"]', "type": "aws_s3_bucket", "name": "zone",
         "mode": "managed", "change": {"actions": ["create"]}}
        for z in ("bronze", "silver", "gold")
    ] + [
        {"address": f"aws_glue_job.{n}", "type": "aws_glue_job", "name": n,
         "mode": "managed", "change": {"actions": ["create"]}}
        for n in job_names
    ]
    return {"resource_changes": changes, "output_changes": {}}


def _dataflow(plan):
    rows, _ = reporter.summarize(plan)
    return reporter.build_dataflow_svg(rows, "t", "aws", "h", "ts", plan=plan)


def test_dataflow_places_jobs_between_their_named_stages():
    # bronze_to_silver and silver_to_gold must land in THEIR gaps; every stage
    # boundary has a transform, so no fabricated-flow caveat is needed.
    svg = _dataflow(_medallion_plan(["bronze_to_silver", "silver_to_gold"]))
    order = [svg.index(a) for a in (
        'aws_s3_bucket.zone[&quot;bronze&quot;]', "aws_glue_job.bronze_to_silver",
        'aws_s3_bucket.zone[&quot;silver&quot;]', "aws_glue_job.silver_to_gold",
        'aws_s3_bucket.zone[&quot;gold&quot;]')]
    assert order == sorted(order), "spine order does not follow the medallion flow"
    assert "no transform in plan" not in svg


def test_dataflow_marks_stage_gap_without_transform_as_not_flow():
    # Only bronze_to_silver exists: silver -> gold has NO job, so drawing a solid
    # arrow would fabricate a flow. The gap must be visibly declared instead.
    svg = _dataflow(_medallion_plan(["bronze_to_silver"]))
    assert svg.count("no transform in plan") == 1


def test_dataflow_name_placement_beats_positional_order():
    # A single job named silver_to_gold belongs between silver and gold — the old
    # positional interleave would wrongly put it between bronze and silver.
    svg = _dataflow(_medallion_plan(["silver_to_gold"]))
    order = [svg.index(a) for a in (
        'aws_s3_bucket.zone[&quot;silver&quot;]', "aws_glue_job.silver_to_gold",
        'aws_s3_bucket.zone[&quot;gold&quot;]')]
    assert order == sorted(order)
    assert svg.count("no transform in plan") == 1     # the bronze -> silver gap


def test_dataflow_wiring_verdict_matches_conformance():
    import architecture_model
    # Orchestrator module references the compute module -> conformance says wired,
    # and the diagram must agree (solid 'orchestrates', no placeholder caveat).
    plan = {
        "resource_changes": [
            {"address": 'module.storage.aws_s3_bucket.zone["bronze"]', "type": "aws_s3_bucket",
             "name": "zone", "module_address": "module.storage", "mode": "managed",
             "change": {"actions": ["create"]}},
            {"address": 'module.storage.aws_s3_bucket.zone["silver"]', "type": "aws_s3_bucket",
             "name": "zone", "module_address": "module.storage", "mode": "managed",
             "change": {"actions": ["create"]}},
            {"address": "module.compute.aws_glue_job.etl", "type": "aws_glue_job",
             "name": "etl", "module_address": "module.compute", "mode": "managed",
             "change": {"actions": ["create"]}},
            {"address": "module.orchestrator.aws_sfn_state_machine.p", "type": "aws_sfn_state_machine",
             "name": "p", "module_address": "module.orchestrator", "mode": "managed",
             "change": {"actions": ["create"]}},
        ],
        "output_changes": {},
        "configuration": {"root_module": {"module_calls": {
            "orchestrator": {"expressions": {"glue_job_names": {
                "references": ["module.compute.glue_job_names", "module.compute"]}}},
            "compute": {"expressions": {}},
            "storage": {"expressions": {}},
        }}},
    }
    rows, _ = reporter.summarize(plan)
    svg = reporter.build_dataflow_svg(rows, "t", "aws", "h", "ts", plan=plan)
    report = architecture_model.conformance(plan)
    conf_wired = not any(f["id"] == "ARCH-ORCH-UNWIRED" for f in report["findings"])
    assert conf_wired and ">orchestrates<" in svg
    assert "not wired" not in svg

    # And the unwired case agrees the other way.
    plan["configuration"]["root_module"]["module_calls"]["orchestrator"]["expressions"] = {}
    rows, _ = reporter.summarize(plan)
    svg = reporter.build_dataflow_svg(rows, "t", "aws", "h", "ts", plan=plan)
    report = architecture_model.conformance(plan)
    assert any(f["id"] == "ARCH-ORCH-UNWIRED" for f in report["findings"])
    assert "not wired" in svg


def test_collapse_folds_config_into_one_service_node():
    rows = [
        {"address": 'aws_s3_bucket.b["x"]', "type": "aws_s3_bucket", "name": "b", "action": "create", "tier": "storage", "module": ""},
        {"address": 'aws_s3_bucket_versioning.b["x"]', "type": "aws_s3_bucket_versioning", "name": "b", "action": "create", "tier": "storage", "module": ""},
        {"address": 'aws_s3_bucket_lifecycle_configuration.b["x"]', "type": "aws_s3_bucket_lifecycle_configuration", "name": "b", "action": "create", "tier": "storage", "module": ""},
        {"address": "aws_glue_job.j", "type": "aws_glue_job", "name": "j", "action": "create", "tier": "compute", "module": ""},
    ]
    comps = reporter._collapse_components(rows)
    assert len(comps) == 2                                   # 3 s3 resources -> 1, glue -> 1
    s3 = next(c for c in comps if c["type"].startswith("aws_s3"))
    assert s3["type"] == "aws_s3_bucket" and s3["config_count"] == 2   # primary + 2 folded config


def test_generic_layout_collapses_config_resources():
    # A non-blueprint plan with bucket + 3 config resources should render ONE storage node.
    plan = {"resource_changes": [
        {"address": 'aws_s3_bucket.b', "type": "aws_s3_bucket", "name": "b", "change": {"actions": ["create"]}},
        {"address": 'aws_s3_bucket_versioning.b', "type": "aws_s3_bucket_versioning", "name": "b", "change": {"actions": ["create"]}},
        {"address": 'aws_s3_bucket_public_access_block.b', "type": "aws_s3_bucket_public_access_block", "name": "b", "change": {"actions": ["create"]}},
        {"address": 'aws_s3_bucket_lifecycle_configuration.b', "type": "aws_s3_bucket_lifecycle_configuration", "name": "b", "change": {"actions": ["create"]}},
    ], "output_changes": {}}
    rows, _ = reporter.summarize(plan)
    svg = reporter.build_svg(rows, "generic-stack", "aws", "h", "ts")
    nodes = [el for el in ET.fromstring(svg).iter(SVG_NS + "g") if el.attrib.get("class") == "node"]
    assert len(nodes) == 1   # collapsed, not a 4-card pile


def test_palette_is_restricted_to_spec_tokens():
    svg = _svg()
    # A couple of off-palette colors from the previous implementation must not appear.
    for forbidden in ("#f5efe9", "#d8c8bf", "#181411"):
        assert forbidden not in svg


# ---- v2: pipeline FLOW layout (topology, not a pile), encryption, governance overlay ----
def test_pipeline_flow_is_wellformed_and_self_contained():
    root = ET.fromstring(_pipeline_svg())
    assert root.attrib["viewBox"] == "0 0 1280 760"
    ids = {el.attrib["id"] for el in root.iter(SVG_NS + "g") if "id" in el.attrib}
    for gid in ("bg", "titlebar", "edges", "flow-runtime", "band-governance", "legend"):
        assert gid in ids, f"flow layout missing group: {gid}"
    nodes = [e for e in root.iter(SVG_NS + "g") if e.attrib.get("class") == "node"]
    assert nodes and all(n.attrib.get("data-address") for n in nodes)


def test_pipeline_flow_draws_real_anchored_edges():
    root = ET.fromstring(_pipeline_svg())
    edges = next(el for el in root.iter(SVG_NS + "g") if el.attrib.get("id") == "edges")
    assert [e for e in edges.iter(SVG_NS + "path")], "expected anchored flow edges"


def test_pipeline_flow_shows_service_components_and_zone():
    svg = _pipeline_svg()
    assert "S3 Bronze" in svg                 # collapsed service box, not a pile of configs
    assert "bronze" in svg                    # zone preserved via data-address


def test_pipeline_flow_marks_kms_encrypted_nodes():
    assert "M2.5,5" in _pipeline_svg()        # lock marker path


def test_load_and_refresh_cost_renders_per_service_line_items(tmp_path, monkeypatch):
    import json as _json
    (tmp_path / "manifest.json").write_text(_json.dumps(
        {"template": "aws-data-pipeline-standard", "cloud": "aws", "short": "abc", "generated_at": "ts"}),
        encoding="utf-8")
    (tmp_path / "bcm-estimate.json").write_text(_json.dumps({
        "estimate": {"totalCost": {"amount": "123.45", "currency": "USD"}},
        "usage_lines": {"items": [
            {"serviceCode": "AWSGlue", "usageType": "USE1-ETL-DPU-Hour", "operation": "Spark",
             "cost": {"amount": "80.00"}},
            {"serviceCode": "AmazonS3", "usageType": "USE1-TimedStorage-ByteHrs", "operation": "StandardStorage",
             "cost": {"amount": "43.45"}}]}}), encoding="utf-8")
    monkeypatch.setattr(reporter, "render_pdf", lambda *a, **k: (False, "skip"))

    cost = reporter.load_bcm_estimate(str(tmp_path))
    assert cost["ok"] and cost["monthly_total_usd"] == "123.45"
    assert cost["line_items"][0]["serviceCode"] == "AWSGlue"

    reporter.refresh_cost(str(tmp_path))
    html = (tmp_path / "cost.html").read_text(encoding="utf-8")
    assert "AWSGlue" in html and "AmazonS3" in html  # per-service breakdown rendered
    assert "BCM Pricing Calculator API" in html


def test_forecast_vs_actual_normalizes_and_computes_variance():
    line_items = [
        {"serviceCode": "AWSGlue", "cost": {"amount": "80.00"}},
        {"serviceCode": "AmazonS3", "cost": {"amount": "20.00"}},
    ]
    # Cost Explorer service names differ from BCM serviceCodes — must still line up.
    actuals = {"AWS Glue": "92.00", "Amazon Simple Storage Service": "18.00", "AWS Lambda": "5.00"}
    v = reporter.forecast_vs_actual(line_items, actuals)
    rows = {r["service"]: r for r in v["rows"]}
    assert rows["glue"]["forecast"] == 80.0 and rows["glue"]["actual"] == 92.0
    assert round(rows["glue"]["variance"], 2) == 12.0
    assert round(rows["glue"]["variance_pct"], 1) == 15.0
    assert rows["s3"]["variance"] == -2.0
    # Lambda has an actual but no forecast — variance undefined, not fabricated.
    assert rows["lambda"]["forecast"] is None and rows["lambda"]["variance"] is None
    assert v["forecast_total"] == 100.0 and v["actual_total"] == 115.0


def test_cost_report_renders_variance_when_actuals_present(tmp_path):
    import json as _json
    (tmp_path / "manifest.json").write_text(_json.dumps(
        {"template": "aws-data-pipeline-standard", "cloud": "aws", "short": "abc", "generated_at": "ts"}),
        encoding="utf-8")
    (tmp_path / "bcm-estimate.json").write_text(_json.dumps({
        "estimate": {"totalCost": {"amount": "100.00", "currency": "USD"}},
        "usage_lines": {"items": [
            {"serviceCode": "AWSGlue", "cost": {"amount": "80.00"}},
            {"serviceCode": "AmazonS3", "cost": {"amount": "20.00"}}]}}), encoding="utf-8")
    (tmp_path / "bcm-actuals.json").write_text(
        _json.dumps({"AWS Glue": "92.00", "Amazon Simple Storage Service": "18.00"}), encoding="utf-8")

    cost = reporter.load_bcm_estimate(str(tmp_path))
    assert cost["variance"] is not None
    html = reporter.build_cost_html("t", "aws", "abc", "ts", cost)
    assert "Forecast vs. actual" in html
    assert "Cost Explorer" in html


def test_cost_report_omits_variance_without_actuals(tmp_path):
    import json as _json
    (tmp_path / "manifest.json").write_text(_json.dumps(
        {"template": "t", "cloud": "aws", "short": "abc", "generated_at": "ts"}), encoding="utf-8")
    (tmp_path / "bcm-estimate.json").write_text(_json.dumps({
        "estimate": {"totalCost": {"amount": "100.00"}},
        "usage_lines": {"items": [{"serviceCode": "AWSGlue", "cost": {"amount": "100.00"}}]}}),
        encoding="utf-8")
    cost = reporter.load_bcm_estimate(str(tmp_path))
    assert cost["variance"] is None
    html = reporter.build_cost_html("t", "aws", "abc", "ts", cost)
    assert "Forecast vs. actual" not in html


def test_gate_flow_svg_is_wellformed_and_labeled():
    svg = reporter.build_gate_flow_svg()
    ET.fromstring(svg)  # valid, self-contained XML
    for label in ("verify", "plan", "approve", "apply", "REFUSED", "APPLIED"):
        assert label in svg
    assert svg.count("<svg") == 1


def test_pipeline_flow_has_posture_summary():
    rows, _ = reporter.summarize(PLAN)
    plan = dict(PLAN, variables={"owner": {"value": "data-platform"}, "region": {"value": "us-east-1"}})
    svg = reporter.build_svg(rows, "aws-data-pipeline-standard", "aws", "h", "ts", plan=plan)
    assert "DEPLOYMENT POSTURE" in svg
    assert "RESOURCES" in svg and "FINDINGS" in svg
    assert "data-platform" in svg  # owner context surfaced from plan variables


def test_pipeline_flow_governance_overlay_on_nodes():
    svg = _pipeline_svg(findings=[{"id": "COST-01", "severity": "MEDIUM", "category": "Cost",
                                   "resource": "aws_s3_bucket.zone"}])
    assert 'data-findings="COST-01"' in svg
    assert ">COST-01<" in svg                 # rendered badge on the bronze box


def test_report_bundle_manifest_and_hash(tmp_path, monkeypatch):
    # Isolate all report output under tmp and skip real browser PDF rendering.
    monkeypatch.setattr(reporter, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(reporter, "REPORTS", str(tmp_path / "artifacts" / "reports"))
    monkeypatch.setattr(reporter, "render_pdf", lambda *a, **k: (False, "pdf skipped in test"))

    tf_dir = tmp_path / "terraform"
    tf_dir.mkdir()
    (tf_dir / "main.tf").write_text('# generated\n', encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(PLAN), encoding="utf-8")

    out = reporter.generate_from_plan_json(str(tf_dir), str(plan_path), template="aws-data-pipeline-standard")
    manifest = json.loads((__import__("pathlib").Path(out) / "manifest.json").read_text(encoding="utf-8"))

    # The report's plan hash must equal the deploy gate's hash for the same plan.
    assert manifest["plan_hash"] == plan_gate.hashlib.sha256(
        json.dumps({"resource_changes": PLAN["resource_changes"], "output_changes": PLAN["output_changes"]},
                   sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    assert manifest["short"] == manifest["plan_hash"][:12]
    assert manifest["counts"]["create"] == len(PLAN["resource_changes"])
    assert "architecture.svg" in manifest["files"]
    assert manifest["template"] == "aws-data-pipeline-standard"
    # cost must remain BCM-gated (never a fabricated total)
    assert manifest["cost"]["ok"] is False
