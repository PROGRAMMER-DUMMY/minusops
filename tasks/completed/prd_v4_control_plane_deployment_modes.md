# Product Requirements Document (PRD) — Control Plane Deployment Modes, Containerization & Operator Onboarding Guide (v4.0)

| Attribute | Details |
| :--- | :--- |
| **Document ID** | PRD-ARCH-2026-004 (Revision 4.0 — Multi-Mode Control Plane & Enterprise EKS Hosting) |
| **Status** | APPROVED ARCHITECTURE SPECIFICATION |
| **Lead Architect** | Matt (Principal Cloud Architect & Governance Lead) |
| **Author** | MinusOps Autonomous Architecture Engine |
| **Target Components** | `deploy/`, `Dockerfile`, `docs/OPERATOR_ONBOARDING_GUIDE.md`, `.agents/skills/grill-me/SKILL.md` |
| **Target Clouds** | Local Workstation, GitHub Actions / Jenkins, AWS EKS, AWS ECS Fargate |
| **Date** | 2026-08-21 |

---

## 1. Executive Summary & Problem Statement

Enterprises adopting MinusOps require flexibility in **how and where the Control Plane itself is hosted and operated**:
1. **Mode 1 (Local Operator CLI):** Individual platform engineers driving synthesis and audits from laptops via `minusctl`.
2. **Mode 2 (CI/CD Automated Gate):** GitHub Actions or Jenkins private VPC runners running automated 4-lane PR checks and plan-bound deployments via AWS OIDC STS AssumeRole.
3. **Mode 3 (Self-Hosted Enterprise Container / AWS EKS / ECS Fargate):** A 24/7 centralized web console (`dashboard_app.py`) running inside corporate VPCs with AWS IRSA (IAM Roles for Service Accounts), ALB ingress, and SSO authentication.

To make enterprise adoption frictionless, MinusOps must provide:
* Ready-to-deploy container assets (`Dockerfile`, Kubernetes/EKS manifests, ECS Fargate modules).
* A comprehensive, step-by-step **Operator Onboarding & Getting Started Guide** (`docs/OPERATOR_ONBOARDING_GUIDE.md`).
* Expansion of the `grill-me` skill to interrogate the organization's **Control Plane Hosting Topology (Pillar 14)**.

---

## 2. The 3 Enterprise Control Plane Deployment Modes

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3 SUPPORTED MINUSOPS CONTROL PLANE HOSTING TOPOLOGIES                       │
├──────────────────────────┬──────────────────────────────────────────────────┤
│ Mode                     │ Architecture, Authentication & Runtime           │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ **Mode 1: Local CLI**    │ • Runtime: Developer Laptop / Workstation.       │
│ (Ad-Hoc Engineering)     │ • Auth: Ambient AWS CLI SSO / `aws configure`.   │
│                          │ • Command: `minusctl`   │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ **Mode 2: Automated CI** │ • Runtime: GitHub Actions or Jenkins VPC Runner. │
│ (Pipeline Automation)    │ • Auth: OIDC STS `AssumeRoleWithWebIdentity`.    │
│                          │ • Execution: `.github/workflows/deploy.yml`      │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ **Mode 3: Enterprise**   │ • Runtime: AWS EKS (Pod) or AWS ECS Fargate.     │
│ **Container / EKS**      │ • Auth: Native AWS IRSA (Zero static AWS keys).  │
│ (24/7 Web Control Plane) │ • Ingress: AWS ALB + Corporate VPN / SSO Token.  │
│                          │ • Entrypoint: `python app/dashboard_app.py`      │
└──────────────────────────┴──────────────────────────────────────────────────┘
```

---

## 3. Containerization Specification (AWS EKS & ECS Fargate)

### 3.1 Production `Dockerfile`
* **Base Image:** Minimal `python:3.11-slim-bookworm` (eliminates CVE vulnerabilities).
* **Terraform Binary:** Pre-installs pinned Terraform binary (v1.8+) and AWS CLI v2.
* **Non-Root User:** Runs as unprivileged `minusops` user (`UID 10001`) with read-only root filesystem.
* **Healthcheck:** Configured HTTP health probe at `/` (Dash UI) with 30s interval.

### 3.2 Kubernetes / EKS Deployment Manifests (`deploy/k8s/`)
* **`serviceaccount.yaml`:** Annotates the K8s ServiceAccount with `eks.amazonaws.com/role-arn: arn:aws:iam::<account>:role/MinusOpsControlPlaneRole` for native IRSA.
* **`deployment.yaml`:** 2-replica deployment with CPU/Memory requests (500m / 512Mi), readiness/liveness probes, and securityContext (readOnlyRootFilesystem, drop ALL capabilities).
* **`service.yaml`:** Internal ClusterIP service exposing port `8050`.
* **`ingress.yaml`:** AWS ALB Ingress Controller (`ingress.k8s.aws/scheme: internal`) terminating corporate TLS.

---

## 4. Grilling Session Expansion: Pillar 14 (Control Plane Host Topology)

We add **Pillar 14** to `.agents/skills/grill-me/SKILL.md`:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ PILLAR 14: CONTROL PLANE HOSTING & DRIVING TOPOLOGY                         │
├─────────────────────────────────────────────────────────────────────────────┤
│ Questions to Interrogate:                                                   │
│ 1. "How will your team drive MinusOps: Local CLI, CI/CD, or a 24/7 Web Console?"│
│ 2. "If containerized on AWS, what is the compute runtime: EKS, ECS, or App Runner?"│
│ 3. "What authentication model: AWS SSO, EKS IRSA, or OIDC GitHub Actions?"   │
│ 4. "Is non-local web console access protected by `MINUS_DASH_TOKEN` or SAML?"│
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Operator Onboarding & Getting Started Guide (`docs/OPERATOR_ONBOARDING_GUIDE.md`)

The documentation guide must provide step-by-step instructions for all three modes:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ OPERATOR ONBOARDING GUIDE STRUCTURE                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Quickstart (5-Minute Local CLI Run)                                      │
│    • Prerequisites (Python 3.10+, Terraform 1.8+, AWS CLI)                  │
│    • Running `minusctl doctor` pre-flight diagnostics                        │
│    • Synthesizing a governed pipeline: `minusctl create "<request>"`        │
├─────────────────────────────────────────────────────────────────────────────┤
│ 2. CI/CD Pipeline Integration (GitHub Actions & Jenkins)                    │
│    • Configuring OIDC IAM Role Trust Policies                               │
│    • 4-Lane Pre-Merge PR Checks (fmt, validate, scan, plan-hash)            │
│    • Feed-Factory vendor onboarding in 1 YAML file                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ 3. Containerized 24/7 Deployment on AWS EKS / ECS                           │
│    • Building & pushing the Docker container image                          │
│    • Provisioning EKS IRSA IAM Roles (`deploy/k8s/serviceaccount.yaml`)     │
│    • Applying Kubernetes manifests (`kubectl apply -f deploy/k8s/`)         │
│    • Securing the Dash UI with `MINUS_DASH_TOKEN`                           │
├─────────────────────────────────────────────────────────────────────────────┤
│ 4. Outbound Integrations Setup                                              │
│    • Wiring Slack, Teams, Outlook, Confluence & Jira via Environment Vars   │
│    • Testing dispatches with 1-line Python verification commands            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Functional Requirements (FR)

* **FR-13 (Production Dockerfile):** Hardened, non-root Docker container image bundling Python 3.11, Terraform CLI, and MinusOps control plane.
* **FR-14 (EKS / Kubernetes Deployment Manifests):** Production K8s manifests in `deploy/k8s/` supporting IRSA IAM authentication and ALB ingress.
* **FR-15 (Operator Onboarding Guide):** Comprehensive documentation in `docs/OPERATOR_ONBOARDING_GUIDE.md` covering Local CLI, CI/CD, and Containerized EKS/ECS modes.
* **FR-16 (Pillar 14 Grilling):** Grilling engine interrogates Control Plane host topology and authentication posture.

---

## 7. Verification & Acceptance Test Suite

* [ ] `tests/test_dockerfile.py`: Asserts non-root user, minimal base image, and correct entrypoints.
* [ ] `tests/test_k8s_manifests.py`: Validates YAML syntax, IRSA annotations, securityContext, and resource limits.
* [ ] `tests/test_operator_guide.py`: Validates all documentation links and code examples in `docs/OPERATOR_ONBOARDING_GUIDE.md`.
