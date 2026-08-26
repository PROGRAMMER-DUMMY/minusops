"""
CI/CD pipeline synthesis: 4-lane pre-merge validation, and the reusable feed factory.

Emits the CI/CD half of a governed pipeline the same way `synthesizer.py` emits the
Terraform half -- into a run workspace, for a human to review before it reaches a repo.
Two engines are supported because enterprises do not all get to choose: GitHub Actions with
OIDC federation, and declarative Jenkins for shops with private-VPC runners behind a
firewall. Both drive the *same* `plan_gate.py` commands, so the governance path does not
fork per CI engine -- only the wrapper does.

Three properties are deliberate and should survive edits:

1. `pull_request`, never `pull_request_target`. The latter runs with the base repo's
   secrets against the fork's code, handing any fork author a write-capable OIDC role.
   Fork PRs get the static lanes and no plan; that is the correct trade.
2. No static credentials in either engine. GitHub assumes a role via OIDC; Jenkins uses the
   agent's ambient instance profile / IRSA. A generated pipeline that asks for an
   `AWS_SECRET_ACCESS_KEY` teaches the operator to store one.
3. Onboarding a feed is one YAML file. `feeds/*.yaml` is discovered into a matrix, so
   adding a vendor never means writing another workflow -- which is how CI directories
   become forty near-identical files that drift apart.

Templates use `__TOKEN__` placeholders rather than str.format or string.Template, because
GitHub Actions expressions are themselves `${{ ... }}` and would collide with both.

Depends on: nothing (stdlib only)
Shells out to: nothing
Used by: nothing yet -- CLI entry point; wire into `minusctl` when the engine choice is
    captured by the requirements gate
"""
import argparse
import os
import re
import sys

GITHUB = "github"
JENKINS = "jenkins"
ENGINES = (GITHUB, JENKINS)

DEFAULT_TF_DIR = "terraform"
DEFAULT_REGION = "us-east-1"
DEFAULT_TF_VERSION = "1.10.5"


# --- 4-lane pre-merge validation ------------------------------------------------------

_PR_WORKFLOW = '''name: "Pre-merge validation (4 lanes)"

# Four independent lanes converge on one merge gate. They run in parallel because a
# reviewer who waits eleven minutes for lane 4 to reveal a lint error stops reading lanes
# 1-3 carefully.
#
# `pull_request`, NOT `pull_request_target`: pull_request_target runs with this repo's
# secrets against the fork's code, which would hand any fork author the OIDC role below.
# Fork PRs therefore get lanes 1, 2 and 4 plus the static half of lane 3, and no plan.

on:
  pull_request:
    paths:
      - "__TF_DIR__/**"
      - "src/**"
      - "feeds/**"
      - "contracts/**"
      - ".github/workflows/pre-merge.yml"
  workflow_dispatch:

permissions:
  contents: read
  id-token: write        # OIDC only; no static keys anywhere in this file
  pull-requests: write

# One validation per PR. Two concurrent plans on one directory race for the plan-gate's
# pending record and the loser's approval is silently voided by the winner.
concurrency:
  group: premerge-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  lane1-migration:
    name: "Lane 1 - DDL/DML migration dry-run"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - name: Compile models without executing them
        run: |
          if [ -f src/dbt/dbt_project.yml ]; then
            pip install --quiet dbt-core dbt-athena-community
            dbt compile --project-dir src/dbt --target staging
          else
            echo "No dbt project; nothing to dry-run."
          fi

  lane2-contracts:
    name: "Lane 2 - Data contracts"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - name: Validate schema contracts
        run: |
          if [ -d tests/contracts ]; then
            pip install --quiet pytest
            pytest tests/contracts -q
          else
            echo "No contract tests present."
          fi

  lane3-terraform:
    name: "Lane 3 - Terraform plan + AST security scan"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # Reuses the existing composite action rather than re-implementing plan+scan+cost.
      # Two copies of the review logic drift, and the copy in the newer file wins by
      # accident rather than by decision.
      - uses: ./.github/actions/pr-reviewer
        with:
          tf_dir: "__TF_DIR__"
          aws_region: "__REGION__"
          role_to_assume: ${{ vars.MINUSOPS_PLAN_ROLE_ARN }}

  lane4-unit:
    name: "Lane 4 - PySpark / DAG unit tests"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install --quiet pytest
      - name: Unit tests
        run: |
          if [ -d tests/unit ]; then pytest tests/unit -q; else echo "No unit tests."; fi

  merge-gate:
    name: "Merge gate"
    runs-on: ubuntu-latest
    needs: [lane1-migration, lane2-contracts, lane3-terraform, lane4-unit]
    # `needs` already fails this job if any lane fails. The explicit check exists for the
    # skipped case: a lane that never ran is not a lane that passed.
    steps:
      - name: Assert every lane succeeded
        run: |
          for result in "${{ needs.lane1-migration.result }}" \\
                        "${{ needs.lane2-contracts.result }}" \\
                        "${{ needs.lane3-terraform.result }}" \\
                        "${{ needs.lane4-unit.result }}"; do
            if [ "$result" != "success" ]; then
              echo "::error::Lane result '$result' is not success - blocking merge."
              exit 1
            fi
          done
          echo "All four lanes passed."
'''


# --- Feed factory ---------------------------------------------------------------------

_FEED_FACTORY = '''name: "Feed factory (reusable)"

# One reusable workflow for every vendor feed. Onboarding a feed is a YAML file in feeds/,
# never another copy of this file -- forty near-identical workflows drift apart and the
# difference is only discovered during an incident.

on:
  workflow_call:
    inputs:
      feed_file:
        description: "Path to the feed config, e.g. feeds/payer_feed_01.yaml"
        required: true
        type: string
      environment:
        description: "dev | staging | prod"
        required: false
        default: "dev"
        type: string

permissions:
  contents: read
  id-token: write

jobs:
  feed:
    name: "${{ inputs.feed_file }}"
    runs-on: ubuntu-latest
    environment: ${{ inputs.environment }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }

      - name: Read feed config
        id: feed
        run: python core/generation/cicd.py read-feed --file "${{ inputs.feed_file }}" --github-output

      # Region and role come from repository variables, never from the feed file. A feed
      # config is edited by whoever onboards a vendor; a role ARN in it is an escalation
      # path disguised as configuration.
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ vars.MINUSOPS_PLAN_ROLE_ARN }}
          aws-region: __REGION__

      - name: Verify and plan through the deploy gate
        run: |
          minusctl gate verify --dir "${{ steps.feed.outputs.tf_dir }}"
          minusctl gate plan   --dir "${{ steps.feed.outputs.tf_dir }}"

      # No apply here on purpose. Applying belongs behind the environment protection rule
      # and the two-person check in plan_gate, not inside a per-feed loop that a matrix
      # can fan out across every vendor at once.
'''


_FEED_DISPATCH = '''name: "Feeds"

# Discovers feeds/*.yaml and fans the reusable factory across them. Adding a vendor means
# adding one YAML file and nothing else.

on:
  pull_request:
    paths: ["feeds/**"]
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

jobs:
  discover:
    runs-on: ubuntu-latest
    outputs:
      feeds: ${{ steps.list.outputs.feeds }}
      any: ${{ steps.list.outputs.any }}
    steps:
      - uses: actions/checkout@v4
      - id: list
        run: python core/generation/cicd.py list-feeds --github-output

  feed:
    needs: discover
    if: needs.discover.outputs.any == 'true'
    strategy:
      fail-fast: false        # one bad vendor config must not hide the others
      matrix:
        feed_file: ${{ fromJson(needs.discover.outputs.feeds) }}
    uses: ./.github/workflows/feed-factory.yml
    with:
      feed_file: ${{ matrix.feed_file }}
    secrets: inherit
'''


_FEED_EXAMPLE = '''# Onboarding a vendor feed: copy this file, change the values, open a PR.
# No workflow file is needed -- feeds-dispatch.yml discovers this automatically.
#
# Deliberately absent: any role ARN, secret, or account id. Those come from repository
# variables so that editing a feed config can never widen access.

feed_id: "__FEED_ID__"
domain: "domain-analytics"
source_s3_prefix: "inbound/payers/vendor_a/"
schedule_cron: "0 8 * * ? *"          # daily 08:00 UTC
schema_contract: "contracts/payers/v1_schema.json"
compute_engine: "glue-spark-4.0"
max_worker_capacity: 4
timeout_minutes: 120                   # FinOps circuit breaker; see PRD s11
cost_center: "CC-0000"
owner_role: "payer-reconciliation-lead"   # role alias, never a personal email
tf_dir: "__TF_DIR__"
'''


# --- Jenkins --------------------------------------------------------------------------

_JENKINSFILE = """// Declarative Jenkins pipeline for MinusOps-governed infrastructure.
//
// Runs the SAME plan_gate.py commands as the GitHub Actions path. The governance logic
// lives in Python, not in the CI engine, so switching engines cannot quietly change what
// is enforced.
//
// Credentials: the agent's IAM instance profile (EC2) or IRSA (EKS) supplies ambient STS
// credentials. No withCredentials block, no static keys -- plan_gate rejects long-term
// AKIA keys in production anyway, so storing them would only produce a late failure.

pipeline {
    agent { label '__AGENT_LABEL__' }

    options {
        timestamps()
        timeout(time: 2, unit: 'HOURS')
        disableConcurrentBuilds()   // two plans on one dir race for the pending record
    }

    environment {
        AWS_REGION = '__REGION__'
        TF_DIR     = '__TF_DIR__'
    }

    stages {
        stage('Pre-merge validation (4 lanes)') {
            parallel {
                stage('Lane 1 - Migration dry-run') {
                    steps {
                        sh '''
                          if [ -f src/dbt/dbt_project.yml ]; then
                            dbt compile --project-dir src/dbt --target staging
                          else
                            echo "No dbt project; nothing to dry-run."
                          fi
                        '''
                    }
                }
                stage('Lane 2 - Data contracts') {
                    steps {
                        sh 'if [ -d tests/contracts ]; then pytest tests/contracts -q; else echo "none"; fi'
                    }
                }
                stage('Lane 3 - Terraform plan + AST scan') {
                    steps {
                        sh 'minusctl gate verify --dir "$TF_DIR"'
                        sh 'minusctl gate plan   --dir "$TF_DIR"'
                    }
                }
                stage('Lane 4 - Unit tests') {
                    steps {
                        sh 'if [ -d tests/unit ]; then pytest tests/unit -q; else echo "none"; fi'
                    }
                }
            }
        }

__ARTIFACT_STAGE__
        stage('Deploy to dev') {
            when { branch 'develop' }
            steps {
                sh 'minusctl gate run --dir "$TF_DIR" --mode gatekeeper --policy-mode dev'
            }
        }

        stage('Staging gate - business sign-off') {
            when { branch 'staging' }
            input {
                message 'Approve deployment to staging?'
                submitter '__STAGING_APPROVERS__'
            }
            steps {
                sh 'minusctl gate run --dir "$TF_DIR" --mode gatekeeper --policy-mode production'
            }
        }

        stage('Production gate - two-person rule') {
            when { branch 'main' }
            // Jenkins `submitter` records who clicked, but it is NOT the two-person check.
            // plan_gate re-verifies planner != approver against STS caller identity, which
            // a CI button cannot forge. Removing this input would not weaken that; removing
            // --policy-mode production would.
            input {
                message 'Authorize production release?'
                submitter '__PROD_APPROVERS__'
            }
            steps {
                sh 'minusctl gate run --dir "$TF_DIR" --mode gatekeeper --policy-mode production'
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: '.agents/logs/audit.jsonl', allowEmptyArchive: true
        }
    }
}
"""


# --- Rendering ------------------------------------------------------------------------

_TOKEN = re.compile(r"__([A-Z][A-Z0-9_]*)__")

# A pipeline name becomes a directory, a workflow filename, a YAML `name:` value and -- the
# one that matters -- the `paths:` filter deciding which subtree the workflow deploys
# DNS-label shape: lowercase, digits, hyphens, 63 max.
_PIPELINE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _valid_name(value):
    """Return `value` if it is a safe pipeline name, else raise.

    Refuses rather than sanitises. Silently rewriting `My Pipeline` to `my-pipeline` would
    put the workflow on a path the caller did not ask for, and `paths:` filters are how a
    monorepo keeps one pipeline's deploy from firing on another's commits.
    """
    if not isinstance(value, str) or not _PIPELINE_NAME.match(value):
        raise ValueError(
            f"unsafe pipeline name: {value!r} -- must match {_PIPELINE_NAME.pattern} "
            "(lowercase letters, digits and hyphens, starting alphanumeric, 63 max)")
    return value


def _fill(template, **tokens):
    """Substitute `__TOKEN__` placeholders in ONE pass.

    Single-pass because the previous sequential `str.replace` fed each replacement back in
    as input to the next: a value containing `__REGION__` was rewritten by the later region
    pass. One regex sweep means a replacement is output and never input again.

    `__TOKEN__` is kept over `string.Template` deliberately. These templates already carry
    two dollar dialects -- GitHub's `${{ ... }}` (20 occurrences) and shell `$TF_DIR` /
    `$result` -- and `string.Template` would make every `$` significant, adding a third that
    is distinguished from the other two only by brace count and case. `__TOKEN__` shares no
    syntax with either, which is why no rendered output has ever collided.
    """
    values = {key.upper(): str(value) for key, value in tokens.items()}
    return _TOKEN.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def render_pr_workflow(tf_dir=DEFAULT_TF_DIR, region=DEFAULT_REGION):
    """The 4-lane pre-merge workflow."""
    return _fill(_PR_WORKFLOW, tf_dir=tf_dir, region=region)


def render_feed_factory(region=DEFAULT_REGION):
    """The reusable `workflow_call` template every feed shares."""
    return _fill(_FEED_FACTORY, region=region)


def render_feed_dispatch():
    """Matrix dispatcher that discovers feeds/*.yaml."""
    return _FEED_DISPATCH


def render_feed_example(feed_id="payer-reconciliation-01", tf_dir=DEFAULT_TF_DIR):
    return _fill(_FEED_EXAMPLE, feed_id=feed_id, tf_dir=tf_dir)


def render_jenkinsfile(tf_dir=DEFAULT_TF_DIR, region=DEFAULT_REGION,
                       agent_label="aws-data-engineer-runner",
                       staging_approvers="bi-analysts,domain-leads",
                       prod_approvers="platform-lead,secops-approvers",
                       artifact_repo=None):
    """The declarative Jenkins pipeline. `artifact_repo` adds one publish stage.

    Artifactory's steps are plugin-provided, so they are emitted only when asked for --
    see the comment inside `_JENKINS_ARTIFACT[ARTIFACTORY]`.
    """
    _check_repo(artifact_repo)
    stage = _JENKINS_ARTIFACT[artifact_repo] if artifact_repo else ""
    return _fill(_JENKINSFILE, tf_dir=tf_dir, region=region, agent_label=agent_label,
                 staging_approvers=staging_approvers, prod_approvers=prod_approvers,
                 artifact_stage=stage)


# --- Exported per-pipeline deploy workflow (PRD-ARCH-2026-005, FR-04) ------------------
#
# This one is not for THIS repo. It ships inside a domain repository alongside the
# exported pipeline, where eight sibling pipelines share one `.github/workflows/`. The
# `paths:` filter is the whole point: without it a commit to any pipeline runs a plan
# against every other pipeline's state.
#
# `${{ }}` is GitHub's own syntax and collides with str.format/Template, which is why this
# file interpolates `__TOKEN__` placeholders through `_fill` instead.

_PIPELINE_WORKFLOW = """name: Deploy __PIPELINE__ pipeline

# Generated by MinusOps (core/generation/cicd.py) via `minusctl export`.
# Owned by the domain team from here on -- it needs no MinusOps runtime to run.

on:
  push:
    branches: [main, dev, uat]
    paths:
      - '__DEST_DIR__/**'
      - '.github/workflows/__PIPELINE__-deploy.yml'
  pull_request:
    branches: [main]
    paths:
      - '__DEST_DIR__/**'

permissions:
  # OIDC federation is the only AWS credential this workflow has. No static key is
  # generated here, and none should be added: a long-term key in a repo secret outlives
  # every person who could have rotated it.
  id-token: write
  contents: read

concurrency:
  group: __PIPELINE__-deploy-${{ github.ref }}
  cancel-in-progress: false

env:
  TF_DIR: __DEST_DIR__/terraform
  AWS_REGION: __REGION__

jobs:
  deploy:
    runs-on: ubuntu-latest
    # dev -> test -> uat -> prod. The GitHub Environment carries the approvers and the
    # per-tier role ARN, so promotion is a branch merge rather than a changed secret.
    environment: "${{ github.ref_name == 'main' && 'prod' || github.ref_name }}"
    steps:
      - uses: actions/checkout@v4

      - name: Assume the deployment role
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: "${{ secrets.AWS_DEPLOY_ROLE_ARN }}"
          role-session-name: "__PIPELINE__-deploy"
          aws-region: "${{ env.AWS_REGION }}"

__ARTIFACT_STAGE__
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "__TF_VERSION__"

      - name: terraform init
        run: terraform init -input=false
        working-directory: "${{ env.TF_DIR }}"

      - name: terraform validate
        run: terraform validate
        working-directory: "${{ env.TF_DIR }}"

      - name: terraform plan
        run: terraform plan -input=false -lock-timeout=5m -out=tfplan__TF_ARTIFACT_VAR__
        working-directory: "${{ env.TF_DIR }}"

      # Apply on push only. A pull_request from a fork runs HCL the fork author wrote;
      # planning it is a read of the account, applying it is a write.
      - name: terraform apply
        if: github.event_name == 'push'
        run: terraform apply -input=false -auto-approve tfplan
        working-directory: "${{ env.TF_DIR }}"
"""


# --- Immutable artifact staging --------------------------------------------------------
#
# "Build once, deploy many" only holds if the thing deployed to prod is byte-identical to
# the thing proven in UAT. That means the tag is the git SHA and never `latest`, and the
# resulting URI is passed INTO Terraform rather than resolved again at apply time -- a tag
# resolved twice can resolve to two different images.
#
# Publishing is credential-free in every variant: GitHub uses the OIDC session already
# assumed above, and Jenkins uses the controller's configured `rtServer` / instance profile.
# A repo needing a static token is a repo we do not emit.

ARTIFACTORY, ECR, CODEARTIFACT, S3 = "artifactory", "ecr", "codeartifact", "s3"
ARTIFACT_REPOS = (ARTIFACTORY, ECR, CODEARTIFACT, S3)

_GH_ARTIFACT_COMMON = """
      - name: Build the immutable artifact
        run: |
          set -euo pipefail
          mkdir -p dist
          if [ -f pyproject.toml ]; then python -m build --wheel --outdir dist; fi
          if [ -f src/dbt/dbt_project.yml ]; then tar -czf "dist/dbt-${GITHUB_SHA}.tar.gz" src/dbt; fi
          ls -1 dist
      - name: Record the artifact digest
        id: digest
        run: |
          set -euo pipefail
          # sha256 of every built file, so the published metadata names exactly what shipped.
          sha256sum dist/* | tee dist/SHA256SUMS
          echo "sha256=$(sha256sum dist/* | sha256sum | cut -d' ' -f1)" >> "$GITHUB_OUTPUT"
"""

_GH_ARTIFACT_PUBLISH = {
    ARTIFACTORY: """      - name: Publish to JFrog Artifactory
        run: |
          set -euo pipefail
          jf rt upload "dist/*" "__PIPELINE__-generic/__PIPELINE__/${GITHUB_SHA}/"
          jf rt build-publish "__PIPELINE__" "${GITHUB_RUN_NUMBER}"
          echo "ARTIFACT_URI=__PIPELINE__-generic/__PIPELINE__/${GITHUB_SHA}/" >> "$GITHUB_ENV"
""",
    ECR: """      - name: Publish to Amazon ECR
        run: |
          set -euo pipefail
          REGISTRY="$(aws sts get-caller-identity --query Account --output text).dkr.ecr.${AWS_REGION}.amazonaws.com"
          aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "$REGISTRY"
          docker build -t "$REGISTRY/__PIPELINE__:${GITHUB_SHA}" .
          docker push "$REGISTRY/__PIPELINE__:${GITHUB_SHA}"
          echo "ARTIFACT_URI=$REGISTRY/__PIPELINE__:${GITHUB_SHA}" >> "$GITHUB_ENV"
""",
    CODEARTIFACT: """      - name: Publish to AWS CodeArtifact
        run: |
          set -euo pipefail
          aws codeartifact login --tool twine --domain "${CODEARTIFACT_DOMAIN}" --repository "${CODEARTIFACT_REPO}"
          twine upload --repository codeartifact dist/*
          echo "ARTIFACT_URI=codeartifact://${CODEARTIFACT_DOMAIN}/__PIPELINE__/${GITHUB_SHA}" >> "$GITHUB_ENV"
""",
    S3: """      - name: Publish to the versioned S3 artifact bucket
        run: |
          set -euo pipefail
          aws s3 cp dist/ "s3://${ARTIFACT_BUCKET}/__PIPELINE__/${GITHUB_SHA}/" --recursive
          echo "ARTIFACT_URI=s3://${ARTIFACT_BUCKET}/__PIPELINE__/${GITHUB_SHA}/" >> "$GITHUB_ENV"
""",
}

_JENKINS_ARTIFACT = {
    ARTIFACTORY: """        stage('Publish artifact (Artifactory)') {
            steps {
                script {
                    // rtUpload/rtPublishBuildInfo come from the Jenkins Artifactory plugin.
                    // They are emitted ONLY for --artifact-repo artifactory: on a controller
                    // without that plugin they are a pipeline parse error, not a skipped step.
                    def server = Artifactory.server 'minusops-artifactory'
                    def buildInfo = Artifactory.newBuildInfo()
                    rtUpload(
                        serverId: 'minusops-artifactory',
                        spec: '''{"files":[{"pattern":"dist/*","target":"generic-local/${JOB_NAME}/${GIT_COMMIT}/"}]}''',
                        buildName: env.JOB_NAME, buildNumber: env.BUILD_NUMBER)
                    rtPublishBuildInfo(serverId: 'minusops-artifactory',
                        buildName: env.JOB_NAME, buildNumber: env.BUILD_NUMBER)
                }
            }
        }
""",
    ECR: """        stage('Publish artifact (Amazon ECR)') {
            steps {
                sh '''
                  set -euo pipefail
                  REGISTRY="$(aws sts get-caller-identity --query Account --output text).dkr.ecr.${AWS_REGION}.amazonaws.com"
                  aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "$REGISTRY"
                  docker build -t "$REGISTRY/${JOB_NAME}:${GIT_COMMIT}" .
                  docker push "$REGISTRY/${JOB_NAME}:${GIT_COMMIT}"
                  sha256sum dist/* > dist/SHA256SUMS || true
                '''
            }
        }
""",
    CODEARTIFACT: """        stage('Publish artifact (CodeArtifact)') {
            steps {
                sh '''
                  set -euo pipefail
                  aws codeartifact login --tool twine --domain "${CODEARTIFACT_DOMAIN}" --repository "${CODEARTIFACT_REPO}"
                  twine upload --repository codeartifact dist/*
                  sha256sum dist/* > dist/SHA256SUMS
                '''
            }
        }
""",
    S3: """        stage('Publish artifact (versioned S3)') {
            steps {
                sh '''
                  set -euo pipefail
                  sha256sum dist/* > dist/SHA256SUMS
                  aws s3 cp dist/ "s3://${ARTIFACT_BUCKET}/${JOB_NAME}/${GIT_COMMIT}/" --recursive
                '''
            }
        }
""",
}


def _check_repo(artifact_repo):
    if artifact_repo is not None and artifact_repo not in ARTIFACT_REPOS:
        raise ValueError(
            f"unsupported artifact repository: {artifact_repo!r} "
            f"(expected one of {', '.join(ARTIFACT_REPOS)})")
    return artifact_repo


def _github_artifact_stage(artifact_repo):
    if not artifact_repo:
        return ""
    return _GH_ARTIFACT_COMMON + _GH_ARTIFACT_PUBLISH[artifact_repo]


def render_pipeline_workflow(pipeline_name, dest_dir=None, region=DEFAULT_REGION,
                             tf_version=DEFAULT_TF_VERSION, artifact_repo=None):
    """One domain-repo GitHub Actions workflow, scoped to a single exported pipeline.

    `artifact_repo` adds a build-and-publish stage ahead of Terraform and
    passes the resulting immutable URI into the plan as `-var artifact_uri=...`.
    """
    pipeline_name = _valid_name(pipeline_name)
    _check_repo(artifact_repo)
    dest_dir = (dest_dir or f"pipelines/{pipeline_name}").replace("\\", "/").strip("/")
    # dest_dir may carry separators, so it cannot use _valid_name -- but it lands in the same
    # `paths:` filter, so each segment gets the same treatment.
    for segment in dest_dir.split("/"):
        _valid_name(segment)
    # Terraform consumes the URI the build stage published rather than resolving
    # the tag itself: a tag resolved twice can resolve to two different images.
    tf_var = ' -var "artifact_uri=${{ env.ARTIFACT_URI }}"' if artifact_repo else ""
    return _fill(_PIPELINE_WORKFLOW, pipeline=pipeline_name, dest_dir=dest_dir,
                 region=region, tf_version=tf_version,
                 artifact_stage=_github_artifact_stage(artifact_repo),
                 tf_artifact_var=tf_var)


# --- Feed config parsing --------------------------------------------------------------

# A deliberately small reader rather than a YAML dependency. Feed configs are flat
# `key: value` pairs by design (see the example), and PyYAML is optional in this project --
# team_resolver already degrades gracefully without it. If a feed ever needs nesting, that
# is the signal to require PyYAML, not to grow this parser.
def parse_feed(text):
    """Flat `key: value` YAML subset -> dict. Ignores comments and blank lines."""
    config = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip() if not raw.strip().startswith("#") else ""
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip('"').strip("'")
        if value:
            config[key.strip()] = value
    return config


def load_feed(path):
    with open(path, encoding="utf-8") as handle:
        return parse_feed(handle.read())


def list_feed_files(root="feeds"):
    """Sorted feed configs. Sorted so a matrix is stable between runs -- an unstable job
    order makes two identical commits produce differently-named checks."""
    if not os.path.isdir(root):
        return []
    return sorted(os.path.join(root, name) for name in os.listdir(root)
                  if name.endswith((".yaml", ".yml")))


# --- Writing --------------------------------------------------------------------------

def write_cicd(out_dir, engine=GITHUB, tf_dir=DEFAULT_TF_DIR, region=DEFAULT_REGION,
               feed_id="payer-reconciliation-01"):
    """Write the CI/CD scaffold under `out_dir`. Returns the paths actually written.

    Never overwrites. A workflow an operator has edited is reviewed configuration, and
    re-synthesising a run must not silently discard it -- the same rule
    `synthesizer.write_project_scaffold` follows.
    """
    if engine not in ENGINES:
        raise ValueError("engine must be one of %s, got %r" % (ENGINES, engine))

    if engine == GITHUB:
        planned = {
            os.path.join(".github", "workflows", "pre-merge.yml"):
                render_pr_workflow(tf_dir, region),
            os.path.join(".github", "workflows", "feed-factory.yml"):
                render_feed_factory(region),
            os.path.join(".github", "workflows", "feeds-dispatch.yml"):
                render_feed_dispatch(),
            os.path.join("feeds", "%s.yaml" % feed_id): render_feed_example(feed_id, tf_dir),
        }
    else:
        planned = {"Jenkinsfile": render_jenkinsfile(tf_dir, region)}

    written = []
    for relative, content in sorted(planned.items()):
        target = os.path.join(out_dir, relative)
        if os.path.exists(target):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        written.append(target)
    return written


# --- CLI ------------------------------------------------------------------------------

def _emit_github_output(pairs):
    """Append key=value lines to $GITHUB_OUTPUT, or print them when running locally."""
    target = os.environ.get("GITHUB_OUTPUT")
    lines = ["%s=%s" % (key, value) for key, value in pairs.items()]
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Synthesize CI/CD pipelines.")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="write the CI/CD scaffold")
    gen.add_argument("--out-dir", default=".")
    gen.add_argument("--engine", choices=ENGINES, default=GITHUB)
    gen.add_argument("--tf-dir", default=DEFAULT_TF_DIR)
    gen.add_argument("--region", default=DEFAULT_REGION)
    gen.add_argument("--feed-id", default="payer-reconciliation-01")

    read = sub.add_parser("read-feed", help="parse one feed config")
    read.add_argument("--file", required=True)
    read.add_argument("--github-output", action="store_true")

    listing = sub.add_parser("list-feeds", help="list feed configs as a JSON matrix")
    listing.add_argument("--root", default="feeds")
    listing.add_argument("--github-output", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "generate":
        written = write_cicd(args.out_dir, args.engine, args.tf_dir, args.region, args.feed_id)
        if not written:
            print("nothing written: every target already exists (never overwritten)")
        for path in written:
            print("wrote %s" % path)
        return 0

    if args.command == "read-feed":
        config = load_feed(args.file)
        config.setdefault("tf_dir", DEFAULT_TF_DIR)
        if args.github_output:
            _emit_github_output(config)
        else:
            for key, value in sorted(config.items()):
                print("%s=%s" % (key, value))
        return 0

    import json
    files = list_feed_files(args.root)
    if args.github_output:
        _emit_github_output({"feeds": json.dumps(files),
                             "any": "true" if files else "false"})
    else:
        print(json.dumps(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
