# deploy Context Index

Deployment assets for **Mode 3** of the control plane: the Dash console running 24/7 inside a
cluster. Modes 1 (local CLI) and 2 (CI gate) need nothing from this directory — Mode 1 runs
`minusctl` directly and Mode 2's pipeline files are synthesized by
[`cicd.py`](../core/generation/cicd.py) into a run workspace.

The container image itself is the repo-root [`Dockerfile`](../Dockerfile), which serves Modes
2 and 3 from one build. Its default entrypoint is `minusctl`; the deployment below overrides
`command` to run the console instead.

---

## Kubernetes / EKS manifests (`deploy/k8s/`)

### [`serviceaccount.yaml`](./k8s/serviceaccount.yaml)
- **Purpose:** binds the pod to an IAM role via IRSA, so no static AWS key exists anywhere.
- **Failure mode it prevents:** without the `eks.amazonaws.com/role-arn` annotation the pod
  silently falls back to the EKS **node** role, shared by every workload on that node. AWS
  calls keep working, with the wrong identity and more permission than intended, and the
  audit trail names the node.
- **Operator input:** `<account-id>`. The role's trust policy must pin `sub` to this exact
  namespace and service account, or any pod in the cluster can assume it.

### [`deployment.yaml`](./k8s/deployment.yaml)
- **Purpose:** two replicas of the console, hardened.
- **Security context:** `runAsNonRoot`, `runAsUser: 10001` (must match the UID in the
  Dockerfile — a mismatch fails at startup with a path error rather than a cause),
  `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`, all capabilities dropped.
- **Writable scratch is not optional.** `readOnlyRootFilesystem` plus Terraform is a
  contradiction without it: `terraform init` writes `.terraform/` and plans write to disk.
  Two `emptyDir` volumes cover `/tmp` and `/home/minusops`. Remove them and the pod starts
  cleanly, then fails on first use.
- **Resources:** CPU and memory requested, **memory only** limited. A CPU limit means CFS
  throttling mid-plan; the request already guarantees the share. Memory is limited so a
  runaway plan is OOM-killed rather than evicting its neighbours.
- **`MINUS_DASH_TOKEN` comes from a `secretKeyRef`.** A literal is a literal in git and in
  `kubectl get deployment -o yaml`.
- **Scaling caveat:** the console is read-mostly. The deploy gate's state is file-backed and
  locked, so two pods planning the same directory contend. Scale the console, not applies.

### [`service.yaml`](./k8s/service.yaml)
- ClusterIP on 8050, never a LoadBalancer. The only route in is the internal ALB.

### [`ingress.yaml`](./k8s/ingress.yaml)
- AWS ALB, `scheme: internal`, TLS terminated at the ALB with an ACM certificate.
- **The single most consequential line in this directory** is the scheme. Internet-facing
  publishes live AWS account identity and spend, and nothing else in the stack objects.

---

## Code Hygiene Audit

- **Dead code:** None.
- **Unwired:** ECS Fargate is named as a supported runtime in PRD v4 but has no task
  definition here; only the EKS path is built.
- **Duplication:** None. One image serves Modes 2 and 3 rather than a second Dockerfile.
- **Mismatches:** Placeholders (`<account-id>`, `<region>`, certificate ARN, hostname) are
  intentional and must be replaced before `kubectl apply`.

---

## Tests

[`tests/test_deployment_modes.py`](../tests/test_deployment_modes.py) — YAML validity, the
IRSA annotation, replica count, security context, the writable-scratch contradiction,
requests and limits, probes, token-from-Secret, ClusterIP, and the internal ALB scheme.
