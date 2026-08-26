"""
CI/CD synthesis (Phase 4): 4-lane pre-merge validation, feed factory, Jenkins parity.

The properties asserted here are the ones whose absence is silent. A workflow missing its
merge gate still runs and still goes green; a `pull_request_target` trigger still works and
is strictly more convenient; a static-key credential block is easier than OIDC. Nothing
fails loudly when these regress, so they are pinned.

Fast: renders text, writes to tmp_path. No network, no Terraform, no CI runner.

Depends on: core/generation/cicd.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import json
import os

import cicd


# --- The trade-offs that must not be silently reversed --------------------------------

def test_pr_workflow_uses_pull_request_not_pull_request_target():
    """pull_request_target runs with the base repo's secrets against the fork's code,
    handing any fork author the OIDC role. Comments are stripped first because the file
    explains why the trigger is wrong and a naive search trips over its own rationale."""
    directives = "\n".join(line for line in cicd.render_pr_workflow().splitlines()
                           if not line.lstrip().startswith("#"))
    assert "pull_request_target" not in directives
    assert "pull_request:" in directives


def test_no_generated_pipeline_asks_for_static_aws_keys():
    """A generated pipeline that requests AWS_SECRET_ACCESS_KEY teaches the operator to
    store one. plan_gate rejects long-term keys in production, so this would fail late."""
    for rendered in (cicd.render_pr_workflow(), cicd.render_feed_factory(),
                     cicd.render_feed_dispatch(), cicd.render_jenkinsfile()):
        assert "AWS_SECRET_ACCESS_KEY" not in rendered
        assert "AWS_ACCESS_KEY_ID" not in rendered


def test_github_lanes_request_oidc_token_permission():
    workflow = cicd.render_pr_workflow()
    assert "id-token: write" in workflow


def test_merge_gate_requires_all_four_lanes():
    """`needs` alone fails on failure but passes on *skipped*. A lane that never ran is not
    a lane that passed, so the gate re-checks each result explicitly."""
    workflow = cicd.render_pr_workflow()
    for lane in ("lane1-migration", "lane2-contracts", "lane3-terraform", "lane4-unit"):
        assert lane in workflow, f"{lane} missing"
        assert f"needs.{lane}.result" in workflow, f"{lane} result not checked by the gate"


def test_lane3_reuses_the_existing_reviewer_action():
    """Two copies of plan+scan+cost drift, and the newer copy wins by accident."""
    assert "./.github/actions/pr-reviewer" in cicd.render_pr_workflow()


def test_feed_config_carries_no_role_arn_or_account_id():
    """A feed config is edited by whoever onboards a vendor. A role ARN in it is an
    escalation path disguised as configuration."""
    example = cicd.render_feed_example()
    assert "arn:aws:iam" not in example
    assert "role-to-assume" not in example
    assert "owner_role" in example and "@" not in example  # role alias, never an email


def test_feed_factory_plans_but_never_applies():
    """Applying inside a per-feed matrix fans an apply across every vendor at once."""
    factory = cicd.render_feed_factory()
    assert "minusctl gate plan" in factory
    assert "minusctl gate apply" not in factory


def test_jenkins_and_github_drive_the_same_governance_commands():
    """The governance logic lives in Python, not the CI engine. Switching engines must not
    quietly change what is enforced."""
    jenkins = cicd.render_jenkinsfile()
    assert "minusctl gate verify" in jenkins
    assert "minusctl gate plan" in jenkins
    assert "--policy-mode production" in jenkins


def test_jenkins_production_stage_uses_production_policy_mode():
    jenkins = cicd.render_jenkinsfile()
    prod = jenkins.split("Production gate", 1)[1]
    assert "--policy-mode production" in prod


# --- Feed discovery -------------------------------------------------------------------

def test_parse_feed_reads_flat_config_and_ignores_comments():
    parsed = cicd.parse_feed(
        "# a comment\n"
        'feed_id: "payer-01"\n'
        "\n"
        "max_worker_capacity: 4   # trailing comment\n"
        "empty_value:\n"
    )
    assert parsed["feed_id"] == "payer-01"
    assert parsed["max_worker_capacity"] == "4"
    assert "empty_value" not in parsed, "a key with no value is not configuration"


def test_feed_listing_is_sorted_and_empty_is_not_an_error(tmp_path):
    """An unstable matrix order makes two identical commits produce differently-named
    checks, which breaks required-check configuration."""
    assert cicd.list_feed_files(str(tmp_path / "missing")) == []
    feeds = tmp_path / "feeds"
    feeds.mkdir()
    for name in ("z_vendor.yaml", "a_vendor.yaml", "notes.txt"):
        (feeds / name).write_text("feed_id: x\n", encoding="utf-8")
    listed = [os.path.basename(p) for p in cicd.list_feed_files(str(feeds))]
    assert listed == ["a_vendor.yaml", "z_vendor.yaml"], "non-YAML must be excluded"


def test_dispatch_matrix_consumes_the_listing_shape():
    """The dispatcher does fromJson on this output; a bare string would fan out per
    character."""
    assert json.loads(json.dumps(["feeds/a.yaml"])) == ["feeds/a.yaml"]
    assert "fromJson(needs.discover.outputs.feeds)" in cicd.render_feed_dispatch()
    assert "fail-fast: false" in cicd.render_feed_dispatch()


# --- Writing --------------------------------------------------------------------------

def test_generate_writes_github_scaffold(tmp_path):
    written = cicd.write_cicd(str(tmp_path), engine=cicd.GITHUB)
    names = {os.path.relpath(p, str(tmp_path)).replace("\\", "/") for p in written}
    assert names == {
        ".github/workflows/pre-merge.yml",
        ".github/workflows/feed-factory.yml",
        ".github/workflows/feeds-dispatch.yml",
        "feeds/payer-reconciliation-01.yaml",
    }


def test_generate_never_overwrites_an_edited_workflow(tmp_path):
    """Re-synthesising a run must not discard a workflow an operator has reviewed."""
    cicd.write_cicd(str(tmp_path), engine=cicd.GITHUB)
    target = tmp_path / ".github" / "workflows" / "pre-merge.yml"
    target.write_text("# hand-edited\n", encoding="utf-8")
    second = cicd.write_cicd(str(tmp_path), engine=cicd.GITHUB)
    assert second == []
    assert target.read_text(encoding="utf-8") == "# hand-edited\n"


def test_jenkins_engine_writes_only_a_jenkinsfile(tmp_path):
    written = cicd.write_cicd(str(tmp_path), engine=cicd.JENKINS)
    assert [os.path.basename(p) for p in written] == ["Jenkinsfile"]


def test_unknown_engine_is_refused(tmp_path):
    try:
        cicd.write_cicd(str(tmp_path), engine="gitlab")
    except ValueError as exc:
        assert "gitlab" in str(exc)
    else:
        raise AssertionError("an unsupported engine must fail closed, not emit nothing")


# --- PRD v11 Step 1: name validation and single-pass substitution ----------------------
#
# `_fill` replaced each token sequentially with str.replace, over values nobody validated.
# Two consequences, both demonstrated before this was fixed:
#   render_pipeline_workflow("__REGION__")     -> the NAME became "us-east-1", because the
#                                                 later `region` pass rewrote text the
#                                                 earlier `pipeline` pass had injected.
#   render_pipeline_workflow('evil"\nname: x') -> a second top-level `name:` key in the YAML.
#
# The second matters most under FR-01.3: `paths:` filters decide which subtree a workflow
# deploys, so a forged one is the monorepo crosstalk that requirement exists to prevent.

import pytest


def test_pipeline_name_must_be_a_safe_slug():
    for bad in ('a"b', "a\nname: hijacked", "../../evil", "Upper", "under_score",
                "dot.ted", "-leading", "", "x" * 64, "sp ace"):
        with pytest.raises(ValueError):
            cicd.render_pipeline_workflow(bad)


def test_pipeline_name_accepts_the_names_people_actually_use():
    for good in ("clickstream", "payer-reconciliation-01", "a", "x" * 63):
        assert cicd.render_pipeline_workflow(good)


def test_a_value_naming_another_token_is_not_re_substituted():
    """Single-pass: a token's replacement text is output, never input to a later pass.

    Asserted against `_fill` rather than the public renderer on purpose. The name validator
    now refuses `__REGION__` before it can reach the template, so a test at the public
    surface would pass whether or not the substitution itself was fixed -- it would be
    proving the validator twice and the mechanism never. These are two independent
    defences and each needs its own test.
    """
    out = cicd._fill("[__PIPELINE__] in __REGION__", pipeline="__REGION__",
                     region="eu-west-1")

    assert out == "[__REGION__] in eu-west-1"


def test_substitution_leaves_github_and_shell_expressions_intact():
    """The templates carry three dollar dialects -- `${{ actions }}`, `$SHELL`, and none of
    ours. A substitution change that ate any of them would produce a workflow that runs and
    silently does the wrong thing."""
    pr = cicd.render_pr_workflow()
    jenkins = cicd.render_jenkinsfile()

    assert pr.count("${{") == 6
    assert "$result" in pr
    assert '"$TF_DIR"' in jenkins


def test_no_unsubstituted_tokens_survive_any_render():
    import re
    for text in (cicd.render_pr_workflow(), cicd.render_jenkinsfile(),
                 cicd.render_feed_factory(), cicd.render_feed_dispatch(),
                 cicd.render_pipeline_workflow("clickstream")):
        assert not re.findall(r"__[A-Z][A-Z0-9_]*__", text)


# --- PRD v11 Step 2 (FR-02): immutable artifact staging --------------------------------

def test_artifact_repo_must_be_one_we_can_actually_emit():
    with pytest.raises(ValueError):
        cicd.render_pipeline_workflow("clickstream", artifact_repo="nexus")
    with pytest.raises(ValueError):
        cicd.render_jenkinsfile(artifact_repo="nexus")


def test_no_artifact_repo_means_no_artifact_stage():
    """Default stays exactly what it was. An artifact stage nobody asked for is a build
    step that fails on a repo with nothing to package."""
    assert "__ARTIFACT" not in cicd.render_pipeline_workflow("clickstream")
    assert "rtUpload" not in cicd.render_jenkinsfile()


def test_jfrog_steps_appear_only_for_artifactory():
    """rtUpload/rtPublishBuildInfo are Artifactory-PLUGIN steps. On a controller without
    that plugin they are a parse error, so emitting them unconditionally would break every
    Jenkins user who does not run Artifactory."""
    artifactory = cicd.render_jenkinsfile(artifact_repo="artifactory")
    ecr = cicd.render_jenkinsfile(artifact_repo="ecr")

    assert "rtUpload" in artifactory and "rtPublishBuildInfo" in artifactory
    assert "rtUpload" not in ecr and "rtPublishBuildInfo" not in ecr


def test_every_artifact_repo_publishes_an_immutable_commit_tagged_uri():
    """FR-02.1/02.3: the tag is the git SHA, and the resulting URI is handed to Terraform.
    A `latest` tag would make 'build once, deploy many' a lie."""
    for repo in cicd.ARTIFACT_REPOS:
        gh = cicd.render_pipeline_workflow("clickstream", artifact_repo=repo)
        assert "ARTIFACT_URI" in gh, repo
        assert ":latest" not in gh, repo
        assert "sha256" in gh.lower(), repo


def test_artifact_stages_never_carry_a_static_credential():
    """NFR-04. The whole point of OIDC is undone by one hardcoded key."""
    def _directives(text):
        # Comments are stripped first: both templates EXPLAIN why AKIA keys are refused,
        # and a naive search trips over the rationale rather than a real credential.
        return "\n".join(line for line in text.splitlines()
                         if not line.lstrip().startswith(("#", "//")))

    for repo in cicd.ARTIFACT_REPOS:
        for text in (cicd.render_pipeline_workflow("clickstream", artifact_repo=repo),
                     cicd.render_jenkinsfile(artifact_repo=repo)):
            lowered = _directives(text).lower()
            assert "aws_secret_access_key" not in lowered, repo
            assert "akia" not in lowered, repo
            assert "aws_access_key_id" not in lowered, repo


def test_rendered_github_workflows_are_valid_yaml():
    """AC-01. String assertions cannot prove a workflow parses -- this is the same gap that
    let 46 green tests pass over unparseable HCL in v8."""
    yaml = pytest.importorskip("yaml")

    docs = [cicd.render_pr_workflow(), cicd.render_feed_factory(),
            cicd.render_feed_dispatch(), cicd.render_pipeline_workflow("clickstream")]
    docs += [cicd.render_pipeline_workflow("clickstream", artifact_repo=r)
             for r in cicd.ARTIFACT_REPOS]

    for text in docs:
        loaded = yaml.safe_load(text)
        assert isinstance(loaded, dict) and "jobs" in loaded
