# MinusOps Operator Onboarding Guide

Getting from nothing to a governed pipeline, in whichever of the three modes fits your team.

| Mode | Who runs it | Authentication | Start here |
| :--- | :--- | :--- | :--- |
| **1. Local CLI** | Platform engineers, ad hoc | Ambient AWS SSO / `aws configure` | [Quickstart](#1-quickstart-five-minutes-locally) |
| **2. CI/CD gate** | Automated, per pull request | OIDC `AssumeRoleWithWebIdentity` | [CI/CD](#2-cicd-integration) |
| **3. Web console** | Whole org, 24/7 | EKS IRSA / ECS task role | [EKS](#3-containerized-eks-and-ecs) |

All three drive the same Python. The governance logic lives in `core/governance/plan_gate.py`,
not in the wrapper, so changing modes cannot change what is enforced.

---

## 1. Quickstart: five minutes locally

### Prerequisites

- Python 3.10 or newer
- Terraform **1.9 or newer** — the generated S3 backend uses native locking (`use_lockfile`),
  which 1.8 does not support
- AWS CLI v2, authenticated with temporary credentials (SSO or an assumed role)

### Check the environment first

```bash
python core/reporting/minusctl.py doctor
```

`doctor` distinguishes three states, and the middle one matters most:

- **ok** — present and usable
- **warn** — something degrades but the loop still runs: no `opa` (the Rego gate becomes
  advisory), no `checkov`/`trivy` (required only under `MINUS_POLICY_MODE=production`), or
  **long-term credentials**, which work fine and are exactly what you do not want an
  unattended run using
- **error** — the loop cannot run: no `terraform`, no AWS CLI, no valid credentials

Exit code follows `ok`, so it drops straight into a shell script.

### Create your first run

```bash
python core/reporting/minusctl.py create "daily payer reconciliation from S3 drops"
```

This does **not** generate infrastructure. It creates a run workspace and seeds
`requirements.json`, because generation is requirements-first by design — a vague request
silently guessed into production infrastructure is the failure this gate exists to prevent.

Then, in order:

```bash
python core/reporting/minusctl.py next --run <run-id>        # what to do next, and why
python core/reporting/minusctl.py readiness --run <run-id>   # scored against 15+ checks
```

### Deploy behind the gate

```bash
python core/governance/plan_gate.py verify  --dir runs/<run-id>/terraform
python core/governance/plan_gate.py plan    --dir runs/<run-id>/terraform
python core/governance/plan_gate.py approve --dir runs/<run-id>/terraform
python core/governance/plan_gate.py apply   --dir runs/<run-id>/terraform
```

`apply` runs only the exact plan whose SHA-256 hash was approved. Editing any `.tf` file
after `plan` — including a `terraform fmt` that only moves whitespace — changes the hash and
voids the approval. That is deliberate; re-run `plan` and `approve`.

---

## 2. CI/CD integration

### Generate the pipeline files

```bash
python core/generation/cicd.py generate --engine github --tf-dir terraform --region us-east-1
python core/generation/cicd.py generate --engine jenkins
```

GitHub mode writes a four-lane pre-merge workflow, the reusable feed factory, its matrix
dispatcher, and a seed feed config. Jenkins mode writes a declarative `Jenkinsfile` running
the same `plan_gate.py` commands.

### The OIDC trust policy

Federate; never store an access key in CI. Model the trust policy on
`examples/iam/ci-oidc-trust-policy.json` and scope `sub` to the specific repository and ref —
a wildcard there lets any branch in the repo assume the role.

Set the role ARN as a **repository variable**, not a secret in the workflow file:

```
MINUSOPS_PLAN_ROLE_ARN = arn:aws:iam::<account-id>:role/minusops-plan
```

The role for PR checks should be read-and-plan only. Applying belongs behind an environment
protection rule and the two-person check.

### Two things worth knowing before your first PR

The workflow uses `pull_request`, not `pull_request_target`. The latter runs with the base
repository's secrets against the fork's code, which hands any fork author your OIDC role. The
cost is that fork PRs get the static lanes and no plan, which is the correct trade.

The merge gate re-checks each lane's `result` rather than relying on `needs:` alone, because
`needs:` fails on failure but passes on *skipped* — and a lane that never ran is not a lane
that passed.

### Onboarding a vendor feed

Add one YAML file to `feeds/`. No workflow file is needed; the dispatcher discovers it.
Do not put a role ARN, account id, or personal email in it — that file is edited by whoever
onboards a vendor.

---

## 3. Containerized: EKS and ECS

### Build and push

```bash
docker build -t minusops:0.1.0 .
docker tag minusops:0.1.0 <account-id>.dkr.ecr.<region>.amazonaws.com/minusops:0.1.0
aws ecr get-login-password --region <region> \
  | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/minusops:0.1.0
```

One image serves Modes 2 and 3. Its default entrypoint is `minusctl` (the CI gate); the
Kubernetes deployment overrides `command` to run the console.

### The IRSA role

Create `MinusOpsControlPlaneRole` with a trust policy naming your cluster's OIDC provider and
**this exact namespace and service account**:

```json
"StringEquals": {
  "<oidc-provider>:sub": "system:serviceaccount:minusops:minusops-control-plane",
  "<oidc-provider>:aud": "sts.amazonaws.com"
}
```

Without the `sub` condition any pod in the cluster can assume the role.

### Apply the manifests

Edit the placeholders first — `<account-id>`, `<region>`, the certificate ARN, and the
hostname:

```bash
kubectl create namespace minusops
kubectl -n minusops create secret generic minusops-dashboard \
  --from-literal=token="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
kubectl apply -f deploy/k8s/serviceaccount.yaml
kubectl apply -f deploy/k8s/deployment.yaml
kubectl apply -f deploy/k8s/service.yaml
kubectl apply -f deploy/k8s/ingress.yaml
```

Generate the token; do not invent one by hand.

### Securing the console

`app/dashboard_app.py` refuses to start on a non-loopback host unless `MINUS_DASH_TOKEN` is
set, so you cannot expose an unauthenticated console by forgetting a variable. Two things are
still yours to get right:

1. `alb.ingress.kubernetes.io/scheme: internal` in `deploy/k8s/ingress.yaml`. Flipping it to
   internet-facing publishes live AWS account identity and spend, and nothing else in the
   stack will object.
2. The token comes from a Secret. A literal value in the manifest is a literal value in git
   and in `kubectl get deployment -o yaml`.

### Verify

```bash
kubectl -n minusops rollout status deployment/minusops-control-plane
kubectl -n minusops logs -l app.kubernetes.io/name=minusops --tail=50
```

If pods start and then fail on the first Terraform action, check that the `emptyDir` mounts
in `deploy/k8s/deployment.yaml` are intact — the root filesystem is read-only, and
`terraform init` needs somewhere to write.

---

## 4. Integrations

Five outbound transports live in `core/integrations/`, each with a manifest in
`.agents/subagents/` telling an agent how to drive it. All are stdlib-only and
approval-gated.

### Wiring

| Transport | Environment |
| :--- | :--- |
| Slack | `SLACK_WEBHOOK_URL` |
| Teams | Webhook URL via Secrets Manager ARN (see `configs/teams.yaml.example`) |
| Outlook | `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_FROM`, `SMTP_PASSWORD` |
| Confluence | `CONFLUENCE_BASE_URL`, `CONFLUENCE_USER`, `CONFLUENCE_API_TOKEN` |
| Jira | `JIRA_BASE_URL`, `JIRA_USER`, `JIRA_TOKEN`, `JIRA_ISSUE_TYPE` |

Prefer a Secrets Manager ARN over an environment variable in Mode 3. A webhook URL is a
bearer credential: anyone holding it can post as you.

### Test a dispatch

```bash
python -c "
import sys; sys.path.insert(0, 'core/integrations')
import slack_hook
print(slack_hook.send_slack_notification(payload={'text': 'MinusOps connectivity test'},
                                         approval_mode='gatekeeper'))
"
```

### Reading the result

Check `sent`, never `ok`:

- `{"ok": true, "sent": true}` — delivered
- `{"ok": true, "sent": false, "reason": "not_configured"} ` — **nothing was sent.** The call
  succeeded and the destination is not wired up
- `{"ok": true, "sent": false, "reason": "deduplicated"}` — suppressed as a duplicate of an
  alert delivered in the last five minutes. Slack and Teams deduplicate; Confluence, Jira and
  Outlook do not, because republishing a page or filing a second ticket is intended work
- `{"ok": false, "reason": "not_authorized"}` — approval was denied. Nothing was sent, and
  this is not a failure to retry

---

## Where to look next

| Question | File |
| :--- | :--- |
| What blocks me and why is it blocking? | `minusops_friction.md` |
| What does this directory do? | the `CONTEXT-*.md` in it, indexed by `CONTEXT-MAP.md` |
| What are the operating rules for agents? | `AGENTS.md` |
| What is the current state of the project? | `docs/PROGRESS.md` |
