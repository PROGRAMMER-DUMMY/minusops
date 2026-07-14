"""
destructive_change_gate.py is the plan-JSON classifier deciding autonomous-ship-on-green vs.
staged/guarded path. Three kinds of proof here, all real, neither mocked at the classifier level:

1. Action-shape fixtures: real `terraform show -json` output from a local-only resource
   (hashicorp/random's random_id, no cloud credentials, no cost) forced through create, delete,
   and BOTH replace orderings (destroy-then-create and create_before_destroy) -- reproducing the
   live probe that grounded destructive_change_gate.py's docstring before any code was written.
1b. docs/g5_autonomy_boundary_scope.md (Phase 6 Step 0, 2026-07-14): the autonomy boundary
   inverted from allowlist-of-danger (fail-OPEN on any type STATEFUL_RESOURCE_TYPES/
   IAM_RESOURCE_TYPES don't name) to AUTO_SHIP_ELIGIBLE_TYPES, a reviewed allowlist of types
   confirmed safe (fail-CLOSED on anything unreviewed). The gap this closed was verified live,
   manually, against the unmodified classifier before any code changed -- see the tests in that
   section for the permanent regression lock.
2. All-16-modules baseline: real Terraform plan JSON (via `terraform test -json -verbose`'s
   `test_plan` event, which carries the same resource_changes shape as `terraform show -json`)
   for every module in modules/*/main.tf under mock_provider -- proving today's catalog only
   ever proposes creates, the regression baseline the classifier exists to protect. Extended
   (section 1b's fix) to also assert no real module's real plan produces an
   `unreviewed_resource_type` finding -- the "nothing that should auto-ship today regresses"
   half of that fix's own proof bar, checked against real plans, not just hand-built fixtures.

The all-16-modules run itself caught a real classifier bug on its first pass (not a fixture
issue): dq-great-expectations' plan includes a data source read (data.aws_iam_policy_document.dq,
actions=["read"]) that was misclassified as a non_create_action finding. Data source reads are
not resource changes and must never be treated as destructive -- classify() now filters to
mode == "managed" before evaluating actions (see test_data_source_reads_are_never_findings below
for the direct regression, in addition to the real-module case that found it).
"""
import json
import os
import re
import shutil
import subprocess

import pytest

import destructive_change_gate as gate
import toolpath

TERRAFORM = toolpath.find_tool("terraform")
pytestmark = pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")

_REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
MODULES_DIR = os.path.join(_REPO_ROOT, "modules")


def _cached_plugin_dir():
    runs_dir = os.path.join(_REPO_ROOT, "runs")
    if not os.path.isdir(runs_dir):
        return None
    for entry in sorted(os.listdir(runs_dir), reverse=True):
        candidate = os.path.join(runs_dir, entry, "terraform", ".terraform", "providers")
        if os.path.isdir(candidate):
            return candidate
    return None


def _run(args, cwd, env=None):
    full_env = dict(os.environ)
    cache = _cached_plugin_dir()
    if cache:
        full_env["TF_PLUGIN_CACHE_DIR"] = cache
    if env:
        full_env.update(env)
    return subprocess.run([TERRAFORM, f"-chdir={cwd}", *args],
                           capture_output=True, text=True, env=full_env)


# ---------------------------------------------------------------------------
# 1. Action-shape fixtures: real plan JSON from a local-only resource
# ---------------------------------------------------------------------------

_RANDOM_MAIN_TF = '''terraform {
  required_providers {
    random = {
      source  = "hashicorp/random"
      version = ">= 3.0"
    }
  }
}

resource "random_id" "probe" {
  byte_length = 4
  lifecycle {
    create_before_destroy = %s
  }
}
'''


@pytest.fixture
def random_workdir(tmp_path):
    d = tmp_path / "random-probe"
    d.mkdir()
    return d


def _real_plan_json(workdir, byte_length, create_before_destroy, apply_first):
    (workdir / "main.tf").write_text(
        _RANDOM_MAIN_TF % ("true" if create_before_destroy else "false"), encoding="utf-8")
    init = _run(["init", "-input=false"], workdir)
    assert init.returncode == 0, init.stdout + init.stderr

    if apply_first:
        apply = _run(["apply", "-auto-approve"], workdir)
        assert apply.returncode == 0, apply.stdout + apply.stderr
        (workdir / "main.tf").write_text(
            (_RANDOM_MAIN_TF % ("true" if create_before_destroy else "false"))
            .replace("byte_length = 4", f"byte_length = {byte_length}"),
            encoding="utf-8")

    plan = _run(["plan", "-out=tfplan", "-input=false"], workdir)
    assert plan.returncode in (0, 2), plan.stdout + plan.stderr  # 2 = changes present, not an error
    show = _run(["show", "-json", "tfplan"], workdir)
    assert show.returncode == 0, show.stdout + show.stderr
    return json.loads(show.stdout)


def test_real_create_only_plan_is_autonomous_eligible(random_workdir):
    plan = _real_plan_json(random_workdir, byte_length=4, create_before_destroy=False, apply_first=False)
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is True
    assert result["findings"] == []


def test_real_delete_plan_is_not_autonomous_eligible(random_workdir):
    _real_plan_json(random_workdir, byte_length=4, create_before_destroy=False, apply_first=True)
    (random_workdir / "main.tf").write_text('''terraform {
  required_providers {
    random = { source = "hashicorp/random", version = ">= 3.0" }
  }
}
''', encoding="utf-8")
    plan_run = _run(["plan", "-out=tfplan2", "-input=false"], random_workdir)
    assert plan_run.returncode in (0, 2), plan_run.stdout + plan_run.stderr
    show = _run(["show", "-json", "tfplan2"], random_workdir)
    plan = json.loads(show.stdout)

    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["actions"] == ["delete"]


def test_real_replace_destroy_first_is_not_autonomous_eligible(random_workdir):
    plan = _real_plan_json(random_workdir, byte_length=8, create_before_destroy=False, apply_first=True)
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    finding = result["findings"][0]
    assert finding["actions"] == ["delete", "create"]
    assert finding["replace_paths"] == [["byte_length"]]


def test_real_replace_create_before_destroy_is_not_autonomous_eligible(random_workdir):
    # The exact ordering asymmetry the whole gate design is built around: a naive classifier
    # that only recognized ["delete", "create"] would let this real, empirically-different
    # ordering through as if it were safe.
    plan = _real_plan_json(random_workdir, byte_length=16, create_before_destroy=True, apply_first=True)
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["actions"] == ["create", "delete"]


def test_data_source_reads_are_never_findings():
    # Direct regression for the real bug the all-16-modules run below caught on its first pass:
    # dq-great-expectations' data.aws_iam_policy_document.dq showed actions=["read"] and was
    # misclassified as a non_create_action finding. A data source read is not a resource change.
    plan = {"resource_changes": [
        {"address": "data.aws_iam_policy_document.dq", "mode": "data",
         "type": "aws_iam_policy_document", "change": {"actions": ["read"]}},
        {"address": "aws_glue_job.this", "mode": "managed",
         "type": "aws_glue_job", "change": {"actions": ["create"]}},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is True
    assert result["findings"] == []
    assert result["resource_change_count"] == 1  # the data source read doesn't count either


def test_a_stateful_data_source_read_alongside_a_real_delete_still_blocks():
    # Confirms the mode=="managed" filter doesn't accidentally swallow a real destructive
    # action just because a data source read is also present in the same plan.
    plan = {"resource_changes": [
        {"address": "data.aws_iam_policy_document.dq", "mode": "data",
         "type": "aws_iam_policy_document", "change": {"actions": ["read"]}},
        {"address": "aws_s3_bucket.old", "mode": "managed",
         "type": "aws_s3_bucket", "change": {"actions": ["delete"]}},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    assert len(result["findings"]) == 1
    assert result["findings"][0]["address"] == "aws_s3_bucket.old"


def test_missing_mode_field_is_evaluated_not_silently_dropped():
    # The classifier denylists mode == "data" rather than allowlisting mode == "managed",
    # specifically so a plan entry with a missing/malformed `mode` field still gets evaluated
    # instead of silently vanishing from classification. Real terraform show -json always sets
    # mode explicitly, so this only matters for already-malformed input -- but a delete action
    # on a resource with no mode field must still block, not fail open. Also the exact shape of
    # tests/test_plan_gate.py's older PLAN_A/PLAN_B fixtures, which predate this module and
    # never set mode at all.
    plan = {"resource_changes": [
        {"address": "aws_s3_bucket.data", "type": "aws_s3_bucket", "change": {"actions": ["delete"]}},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["address"] == "aws_s3_bucket.data"


# ---------------------------------------------------------------------------
# Fail-closed sweep (2026-07-10 audit finding): the mode fix above was one gap found by
# accident. A systematic pass over every field classify() reads found five more of the same
# shape -- three silent fail-opens, three crashes instead of a graceful fail-closed. Each is
# locked down here with the exact malformed input that previously slipped through or crashed,
# confirmed empirically (via a real Python probe, not assumed) before this suite was written.
# Two legitimate-input tests close the loop -- fail-closed must not mean fail-ALWAYS.
# ---------------------------------------------------------------------------

def test_missing_resource_changes_key_fails_closed():
    # Before: `.get("resource_changes") or []` treated a missing key the same as a real,
    # deliberate empty plan -- autonomous_eligible=True. A plan_json this malformed isn't a
    # genuine no-op, it's unreadable, and unreadable must never mean "assumed safe."
    result = gate.classify({})
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "resource_changes_missing_or_null"


def test_null_resource_changes_fails_closed():
    result = gate.classify({"resource_changes": None})
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "resource_changes_missing_or_null"


def test_non_list_resource_changes_fails_closed_not_crashes():
    # Before: crashed with AttributeError ('dict' object has no attribute -- iterating a dict
    # yields its keys, then .get() on a string key blows up).
    result = gate.classify({"resource_changes": {"oops": 1}})
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "resource_changes_not_a_list"


def test_non_dict_plan_json_fails_closed_not_crashes():
    # Before: crashed with AttributeError ('list' object has no attribute 'get').
    result = gate.classify([{"address": "x"}])
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "plan_json_not_a_dict"


def test_non_dict_entry_in_resource_changes_fails_closed_not_crashes():
    # Before: crashed with AttributeError ('str' object has no attribute 'get').
    result = gate.classify({"resource_changes": ["oops"]})
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "malformed_resource_change_entry"


def test_missing_resource_type_fails_closed():
    # Before: a create-only entry with no `type` field couldn't match STATEFUL_RESOURCE_TYPES
    # or IAM_RESOURCE_TYPES (a real lookup just safely returns False), so it silently passed
    # through as neither stateful nor IAM -- "don't know what this is" was treated as "therefore
    # safe," the same fail-open shape as the original mode bug.
    plan = {"resource_changes": [
        {"address": "x", "mode": "managed", "change": {"actions": ["create"]}},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "missing_or_invalid_resource_type"


def test_non_string_resource_type_fails_closed_not_crashes():
    # Before: crashed with AttributeError ('int' object has no attribute 'startswith') in the
    # databricks_resources computation.
    plan = {"resource_changes": [
        {"address": "x", "mode": "managed", "type": 123, "change": {"actions": ["create"]}},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "missing_or_invalid_resource_type"


def test_missing_change_block_fails_closed():
    plan = {"resource_changes": [
        {"address": "x", "mode": "managed", "type": "aws_s3_bucket"},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "malformed_change_block"


def test_non_dict_change_block_fails_closed_not_crashes():
    # Before: crashed with AttributeError ('str' object has no attribute 'get') -- confirmed
    # empirically before this fix landed.
    plan = {"resource_changes": [
        {"address": "x", "mode": "managed", "type": "aws_s3_bucket", "change": "not-a-dict"},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "malformed_change_block"


def test_missing_actions_within_change_fails_closed():
    # Already fail-closed before this sweep (empty tuple != ("create",)) -- locked down here
    # explicitly so it can't silently regress.
    plan = {"resource_changes": [
        {"address": "x", "mode": "managed", "type": "aws_s3_bucket", "change": {}},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "non_create_action"


def test_empty_actions_list_fails_closed():
    plan = {"resource_changes": [
        {"address": "x", "mode": "managed", "type": "aws_s3_bucket", "change": {"actions": []}},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "non_create_action"


def test_fail_closed_sweep_does_not_break_a_genuine_no_op_plan():
    # The whole point of denylisting/allowlisting carefully is that fail-closed must not become
    # fail-ALWAYS: a real, well-formed plan with zero changes is genuinely safe and must still
    # classify as autonomous-eligible.
    result = gate.classify({"resource_changes": []})
    assert result["autonomous_eligible"] is True
    assert result["findings"] == []


def test_fail_closed_sweep_does_not_break_a_genuine_create_only_plan():
    plan = {"resource_changes": [
        {"address": "x", "mode": "managed", "type": "aws_glue_job", "change": {"actions": ["create"]}},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is True
    assert result["findings"] == []


# ---------------------------------------------------------------------------
# 1b. docs/g5_autonomy_boundary_scope.md -- the autonomy boundary inverted from
# allowlist-of-danger (fail-OPEN on any unrecognized type) to a reviewed allowlist of types
# confirmed SAFE to auto-ship (fail-CLOSED on anything not reviewed). STATEFUL_RESOURCE_TYPES/
# IAM_RESOURCE_TYPES alone used to be the entire gate -- a type in neither produced no finding
# at all. Confirmed live, manually, BEFORE this fix existed (recorded here, not just asserted):
# a plan containing only a create-only aws_dynamodb_table -- genuinely stateful, never declared
# anywhere in this repo's real catalog -- classified autonomous_eligible=True on the unmodified
# classifier. The tests below are the permanent regression lock for that real, confirmed gap.
# ---------------------------------------------------------------------------

def test_novel_stateful_type_now_stages_the_gap_this_fix_closes():
    """The specific case that was manually verified, live, against the unmodified classifier
    before AUTO_SHIP_ELIGIBLE_TYPES existed: aws_dynamodb_table -- not in STATEFUL_RESOURCE_TYPES,
    not in IAM_RESOURCE_TYPES (this repo's catalog has never declared it, confirmed by grep
    across modules/*/main.tf, docs/g5_autonomy_boundary_scope.md proof-bar item 4) -- used to
    classify autonomous_eligible=True. Must now stage, tagged distinctly from a known-dangerous
    finding."""
    assert "aws_dynamodb_table" not in gate.STATEFUL_RESOURCE_TYPES
    assert "aws_dynamodb_table" not in gate.IAM_RESOURCE_TYPES
    assert "aws_dynamodb_table" not in gate.AUTO_SHIP_ELIGIBLE_TYPES
    plan = {"resource_changes": [
        {"address": "aws_dynamodb_table.sessions", "mode": "managed", "type": "aws_dynamodb_table",
         "change": {"actions": ["create"]}},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "unreviewed_resource_type"


def test_a_second_genuinely_novel_type_also_stages_not_just_the_one_hardcoded_example():
    """Proves the FIX is a real default, not a special case bolted on for one type. A
    completely different, also-never-declared type (docs/g5_autonomy_boundary_scope.md proof-bar
    item 4 -- confirmed absent from the real catalog by the same grep) must land on the exact
    same fail-closed path."""
    assert "aws_secretsmanager_secret" not in gate.STATEFUL_RESOURCE_TYPES
    assert "aws_secretsmanager_secret" not in gate.IAM_RESOURCE_TYPES
    assert "aws_secretsmanager_secret" not in gate.AUTO_SHIP_ELIGIBLE_TYPES
    plan = {"resource_changes": [
        {"address": "aws_secretsmanager_secret.x", "mode": "managed", "type": "aws_secretsmanager_secret",
         "change": {"actions": ["create"]}},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "unreviewed_resource_type"


def test_default_security_group_decision_is_excluded_not_defaulted():
    """docs/g5_autonomy_boundary_scope.md's own explicitly-decided call, locked in: confirmed
    live against this repo's real modules/networking-vpc/main.tf that even the CORRECT,
    intended configuration of this type sets an unrestricted egress CIDR block -- content risk
    this classifier (type + action only) cannot see, so it stages rather than auto-ships.
    Reason is reviewed_unsafe_resource_type, not unreviewed_resource_type -- this type WAS
    reviewed; the review's answer was no."""
    assert "aws_default_security_group" in gate.REVIEWED_UNSAFE_TYPES
    assert "aws_default_security_group" not in gate.AUTO_SHIP_ELIGIBLE_TYPES
    plan = {"resource_changes": [
        {"address": "aws_default_security_group.this", "mode": "managed",
         "type": "aws_default_security_group", "change": {"actions": ["create"]}},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "reviewed_unsafe_resource_type"


def test_s3_bucket_policy_is_reviewed_unsafe_not_merely_unreviewed():
    """Section 1's own live finding, locked in: aws_s3_bucket_policy has no stateful schema
    shape but its CONTENT can grant public access (the exact case G6's SEC-07 rule exists for).
    Confirmed for real against databricks-workspace's own baseline plan (this repo's one real
    module declaring this type) during this fix's own regression proof."""
    assert "aws_s3_bucket_policy" in gate.REVIEWED_UNSAFE_TYPES
    assert "aws_s3_bucket_policy" not in gate.AUTO_SHIP_ELIGIBLE_TYPES
    plan = {"resource_changes": [
        {"address": "aws_s3_bucket_policy.root_storage_bucket", "mode": "managed",
         "type": "aws_s3_bucket_policy", "change": {"actions": ["create"]}},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is False
    assert result["findings"][0]["reason"] == "reviewed_unsafe_resource_type"


def test_databricks_type_not_in_stateful_set_is_not_double_flagged_unreviewed():
    """Real bug found running this fix's own 16-module regression proof: databricks-workspace
    declares databricks_mws_credentials, a real Databricks type absent from
    STATEFUL_RESOURCE_TYPES -- without an explicit skip, it fell through to
    unreviewed_resource_type, redundant with (and out of scope of) the existing, unconditional
    databricks_resources/reduced_assurance mechanism below, which already, correctly, never
    lets ANY databricks_* type autonomous-ship regardless of this AWS-only review."""
    assert "databricks_mws_credentials" not in gate.STATEFUL_RESOURCE_TYPES
    assert "databricks_mws_credentials" not in gate.AUTO_SHIP_ELIGIBLE_TYPES
    plan = {"resource_changes": [
        {"address": "databricks_mws_credentials.this", "mode": "managed",
         "type": "databricks_mws_credentials", "change": {"actions": ["create"]}},
    ]}
    result = gate.classify(plan)
    assert result["findings"] == []  # no unreviewed_resource_type finding for this type
    assert result["autonomous_eligible"] is False  # still never eligible -- databricks_resources
    assert result["reduced_assurance"] is True
    assert result["databricks_resources"] == ["databricks_mws_credentials.this"]


def test_random_id_stays_eligible_not_a_regression_in_the_existing_action_shape_tests():
    """random_id (hashicorp/random, zero cloud footprint) is this test file's OWN pre-existing
    stand-in for action-shape testing (section 1 above) -- reviewed and added to
    AUTO_SHIP_ELIGIBLE_TYPES as a test-utility exception, not a real-world safety judgment. This
    is the regression this fix's own first implementation attempt hit for real: adding the
    fail-closed default without this entry broke test_real_create_only_plan_is_autonomous_
    eligible, since random_id had never been reviewed either."""
    assert "random_id" in gate.AUTO_SHIP_ELIGIBLE_TYPES
    plan = {"resource_changes": [
        {"address": "random_id.probe", "mode": "managed", "type": "random_id",
         "change": {"actions": ["create"]}},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is True
    assert result["findings"] == []


def test_terraform_data_stays_eligible_not_a_regression_in_gate_e2e():
    """The second real gap this fix's own real CI run caught, not local testing (a real process
    gap in itself -- this repo's test suite was never run exhaustively against the fixed
    classifier before the first push): tests/test_gate_e2e.py's real end-to-end auto-approve
    apply test uses terraform_data (built into Terraform core, zero cloud footprint) as its
    create-only fixture. Confirmed via a repo-wide grep across every tests/*.py file that
    random_id and terraform_data are the only two non-cloud fixture types in use anywhere,
    so this exemption list is now complete, not partial."""
    assert "terraform_data" in gate.AUTO_SHIP_ELIGIBLE_TYPES
    plan = {"resource_changes": [
        {"address": "terraform_data.demo", "mode": "managed", "type": "terraform_data",
         "change": {"actions": ["create"]}},
    ]}
    result = gate.classify(plan)
    assert result["autonomous_eligible"] is True
    assert result["findings"] == []


# ---------------------------------------------------------------------------
# 2. All-16-modules baseline: real plan JSON via `terraform test -json -verbose`
# ---------------------------------------------------------------------------

def _iter_top_level_blocks(content, block_type):
    """Yield (name, body) for `block_type "name" { ... }` blocks, brace-depth aware (a single
    regex can't handle nested blocks like `variable { validation { ... } }` correctly)."""
    pattern = re.compile(rf'{block_type}\s+"([A-Za-z0-9_]+)"\s*\{{')
    for m in pattern.finditer(content):
        start = m.end()
        depth = 1
        i = start
        while depth > 0 and i < len(content):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
            i += 1
        yield m.group(1), content[start:i - 1]


_TYPE_PLACEHOLDER = [
    # Non-empty placeholders: an empty string/list satisfies Terraform's own type system but
    # not provider-side validation (e.g. MWAA requires security_group_ids >= 1 item and
    # subnet_ids >= 2; aws_s3_object rejects an empty bucket name) -- discovered for real when
    # compaction-glue/databricks-workspace/orchestrator-mwaa failed their baseline plan on the
    # first run of this suite. Two list items covers every real min-item constraint this repo's
    # modules currently declare; extend if a future module needs more.
    (re.compile(r"list\s*\("), '["placeholder-a", "placeholder-b"]'),
    (re.compile(r"set\s*\("), '["placeholder-a", "placeholder-b"]'),
    (re.compile(r"map\s*\("), "{}"),
    (re.compile(r"object\s*\("), "{}"),
    (re.compile(r"\bnumber\b"), "1"),
    (re.compile(r"\bbool\b"), "false"),
    (re.compile(r"\bstring\b"), '"placeholder"'),
]


def _placeholder_for_type(type_expr):
    for pattern, placeholder in _TYPE_PLACEHOLDER:
        if pattern.search(type_expr or ""):
            return placeholder
    return '"placeholder"'


def _placeholder_for_variable(name, type_expr):
    """Name-based override, checked before the generic type-based placeholder: a generic
    string satisfies Terraform's own type system but not a provider's attribute-level format
    validation. `aws_mwaa_environment.source_bucket_arn` rejects a non-ARN-shaped string
    (real provider-side check, confirmed live: "invalid ARN: arn: invalid prefix" against a
    plain "placeholder" value) -- unrelated to HANDOFF's synthesizer-wiring gap for this same
    variable name, which lives in synthesizer.py's cross-module composition and is never
    exercised by this standalone-module test. Scoped narrowly to *_arn-suffixed variables so
    this stays a fixture-format fix, not a broad placeholder-quality rewrite."""
    if name.endswith("_arn") and "string" in (type_expr or ""):
        return '"arn:aws:s3:::placeholder-bucket"'
    return _placeholder_for_type(type_expr)


def _required_variable_lines(main_tf_content):
    """A type-appropriate placeholder for every variable with no default -- test-fixture-only
    logic, not a real HCL type system, just enough to unblock a plan for this repo's modules."""
    lines = []
    for name, body in _iter_top_level_blocks(main_tf_content, "variable"):
        if re.search(r"^\s*default\s*=", body, re.MULTILINE):
            continue
        type_match = re.search(r"^\s*type\s*=\s*(.+)$", body, re.MULTILINE)
        placeholder = _placeholder_for_variable(name, type_match.group(1) if type_match else "")
        lines.append(f"  {name} = {placeholder}")
    return lines


_JSON_POLICY_TYPES = ("aws_iam_policy_document", "databricks_aws_assume_role_policy",
                      "databricks_aws_crossaccount_policy", "databricks_aws_bucket_policy")


def _override_data_blocks(main_tf_content):
    """A best-effort override for every data source this repo's modules are known to declare
    that mock_provider can't synthesize valid values for on its own (JSON-typed .json
    attributes, and the two data sources networking-vpc indexes into by count)."""
    blocks = []
    for data_type, name in re.findall(r'data\s+"([A-Za-z0-9_]+)"\s+"([A-Za-z0-9_]+)"', main_tf_content):
        if data_type in _JSON_POLICY_TYPES:
            # Literal JSON string, not jsonencode(...) -- override_data values are evaluated in a
            # restricted context that disallows function calls on Terraform < 1.15 (CI/Docker pin
            # is 1.10.5). Byte-identical to what jsonencode({Version=..., Statement=[]}) produces
            # (alphabetical key order, no whitespace) -- verified against the real 1.15.7 binary.
            blocks.append(f'''
override_data {{
  target = data.{data_type}.{name}
  values = {{
    json = "{{\\"Statement\\":[],\\"Version\\":\\"2012-10-17\\"}}"
  }}
}}''')
        elif data_type == "aws_region":
            blocks.append(f'''
override_data {{
  target = data.aws_region.{name}
  values = {{ region = "us-east-1" }}
}}''')
        elif data_type == "aws_availability_zones":
            blocks.append(f'''
override_data {{
  target = data.aws_availability_zones.{name}
  values = {{ names = ["us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d"] }}
}}''')
    return blocks


def _mock_providers_for(main_tf_content):
    providers = ['mock_provider "aws" {}']
    if 'source  = "databricks/databricks"' in main_tf_content or 'source = "databricks/databricks"' in main_tf_content:
        providers.append('mock_provider "databricks" {}')
    return providers


def _all_module_ids():
    return sorted(
        name for name in os.listdir(MODULES_DIR)
        if os.path.isfile(os.path.join(MODULES_DIR, name, "main.tf"))
    )


def _plan_json_events(output_text):
    for line in output_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("type") == "test_plan":
            yield evt["test_plan"]


@pytest.mark.parametrize("module_id", _all_module_ids())
def test_every_current_module_plans_as_create_only(module_id, tmp_path):
    src = os.path.join(MODULES_DIR, module_id)
    main_tf = open(os.path.join(src, "main.tf"), encoding="utf-8").read()

    dst = tmp_path / module_id
    shutil.copytree(src, dst)
    (dst / "tests").mkdir()

    var_lines = _required_variable_lines(main_tf)
    override_blocks = _override_data_blocks(main_tf)
    mock_blocks = _mock_providers_for(main_tf)

    test_hcl = "\n".join(mock_blocks) + "\n\n" + "\n".join([
        "variables {",
        *var_lines,
        "}",
    ]) + "\n" + "\n".join(override_blocks) + '''

run "baseline_plan" {
  command = plan
}
'''
    (dst / "tests" / "baseline.tftest.hcl").write_text(test_hcl, encoding="utf-8")

    init = _run(["init", "-input=false"], dst)
    assert init.returncode == 0, f"{module_id} init failed:\n{init.stdout}{init.stderr}"

    result = _run(["test", "-json", "-verbose"], dst)
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"{module_id} test run failed:\n{output}"

    plans = list(_plan_json_events(output))
    assert plans, f"{module_id}: no test_plan event found in terraform test -json output"

    for plan in plans:
        classification = gate.classify(plan)
        for finding in classification["findings"]:
            assert finding["reason"] != "non_create_action", (
                f"{module_id} produced a non-create action in its baseline plan: {finding} "
                "-- today's catalog should only ever propose creates."
            )
            # docs/g5_autonomy_boundary_scope.md's own regression requirement, proven against
            # the real 16-module catalog, not just a synthetic fixture: every resource type this
            # repo's real modules actually produce was reviewed -- into AUTO_SHIP_ELIGIBLE_TYPES
            # (still eligible), REVIEWED_UNSAFE_TYPES (a real, deliberate exclusion -- e.g.
            # databricks-workspace's own aws_s3_bucket_policy.root_storage_bucket, found by this
            # very test run and confirmed intentional, not a bug), or already, correctly, a
            # known-dangerous stateful/IAM type. `unreviewed_resource_type` specifically means
            # "the seed-list review never looked at this real type at all" -- that, and only
            # that, is the real regression this assertion exists to catch.
            assert finding["reason"] != "unreviewed_resource_type", (
                f"{module_id} produced a resource type not yet reviewed at all: {finding} -- "
                "this is a real gap in the seed-list review, not an expected staged finding."
            )
