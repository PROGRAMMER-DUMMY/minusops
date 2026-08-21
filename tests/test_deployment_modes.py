"""
Control-plane deployment assets: Dockerfile, EKS manifests, operator guide (PRD v4 WP1-5).

Every assertion here covers something that fails late and quietly. A container that runs as
root works fine until an auditor looks. A `readOnlyRootFilesystem` pod with no writable mount
starts cleanly and then fails on the first `terraform init`. A token pasted into a manifest
works exactly as well as one from a Secret, and leaks into `kubectl get -o yaml` and into git.
A guide that references a flag the CLI does not have fails at 3am, not in review.

Fast: reads files. No Docker, no cluster, no network.

Depends on: nothing (stdlib only; PyYAML used when present, which it is not required to be)
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_K8S = os.path.join(_ROOT, "deploy", "k8s")
_MANIFESTS = ("serviceaccount.yaml", "deployment.yaml", "service.yaml", "ingress.yaml")


def _read(*parts):
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _load_yaml(name):
    yaml = pytest.importorskip("yaml", reason="PyYAML is an optional dependency here")
    return yaml.safe_load(_read("deploy", "k8s", name))


# --- WP1: Dockerfile ------------------------------------------------------------------

def test_dockerfile_uses_a_pinned_slim_base():
    """`:latest` makes the image unreproducible, and the full python image ships a compiler
    toolchain the control plane never uses but a CVE scanner always finds."""
    text = _read("Dockerfile")
    # PRD v4 section 3.1 says 3.11; the image already runs 3.12. Asserting >= 3.11 rather
    # than == 3.11, because pinning a document's number over a newer working runtime is a
    # downgrade dressed as compliance.
    match = re.search(r"^FROM python:3\.(\d+)-slim", text, re.MULTILINE)
    assert match, "expected a python:3.x-slim base"
    assert int(match.group(1)) >= 11, f"python 3.{match.group(1)} predates the PRD floor"
    assert ":latest" not in text


def test_container_runs_as_an_unprivileged_user():
    """A root container that mounts a project directory can rewrite it as root on the host."""
    text = _read("Dockerfile")
    assert "10001" in text, "expected the UID the PRD specifies"
    users = re.findall(r"^USER\s+(\S+)", text, re.MULTILINE)
    assert users, "no USER directive: the image runs as root"
    assert users[-1] not in ("root", "0"), f"final USER is {users[-1]!r}"


def test_terraform_is_pinned_and_new_enough_for_the_generated_backend():
    """The synthesizer emits `use_lockfile = true`, which needs Terraform >= 1.9.0. PRD v4
    section 3.1 says 1.8+, which would fail `terraform init` on every generated backend."""
    text = _read("Dockerfile")
    match = re.search(r"TERRAFORM_VERSION=(\d+)\.(\d+)\.(\d+)", text)
    assert match, "Terraform version must be pinned explicitly, not floating"
    major, minor = int(match.group(1)), int(match.group(2))
    assert (major, minor) >= (1, 9), f"Terraform {match.group(0)} predates S3 native locking"


def test_dockerfile_declares_a_healthcheck():
    assert "HEALTHCHECK" in _read("Dockerfile")


def test_dockerfile_installs_the_dashboard_extra():
    """dash and plotly are an optional extra, deliberately kept out of the base install. The
    console image is the one place they are required."""
    assert "[dashboard]" in _read("Dockerfile")


def test_no_aws_credentials_are_baked_into_the_image():
    text = _read("Dockerfile")
    for forbidden in ("AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN"):
        assert forbidden not in text, f"{forbidden} must come from IRSA, never the image"


# --- WP2: Kubernetes manifests --------------------------------------------------------

@pytest.mark.parametrize("name", _MANIFESTS)
def test_manifest_exists_and_is_valid_yaml(name):
    doc = _load_yaml(name)
    assert isinstance(doc, dict) and doc.get("kind"), f"{name} has no kind"


def test_serviceaccount_carries_the_irsa_role_annotation():
    """Without this annotation the pod falls back to the node role, which is shared by every
    workload on the node -- the opposite of least privilege."""
    doc = _load_yaml("serviceaccount.yaml")
    annotations = doc.get("metadata", {}).get("annotations", {})
    assert "eks.amazonaws.com/role-arn" in annotations


def test_deployment_runs_two_replicas():
    assert _load_yaml("deployment.yaml")["spec"]["replicas"] == 2


def _container():
    return _load_yaml("deployment.yaml")["spec"]["template"]["spec"]["containers"][0]


def _pod_spec():
    return _load_yaml("deployment.yaml")["spec"]["template"]["spec"]


def test_pod_security_context_is_hardened():
    container = _container()
    security = container.get("securityContext", {})
    assert security.get("readOnlyRootFilesystem") is True
    assert security.get("allowPrivilegeEscalation") is False
    assert security.get("capabilities", {}).get("drop") == ["ALL"]
    pod = _pod_spec().get("securityContext", {})
    assert pod.get("runAsNonRoot") is True
    assert pod.get("runAsUser") == 10001


def test_readonly_root_filesystem_has_writable_scratch_mounted():
    """`terraform init` writes .terraform/, and plans write to disk. readOnlyRootFilesystem
    without a writable mount produces a pod that starts cleanly and fails on first use."""
    container = _container()
    mounts = {m["mountPath"] for m in container.get("volumeMounts", [])}
    assert any(p in mounts for p in ("/tmp", "/home/minusops", "/workspace")), (
        f"no writable mount for Terraform scratch; mounts are {mounts}"
    )
    volumes = _pod_spec().get("volumes", [])
    assert volumes and all("emptyDir" in v for v in volumes), (
        "scratch volumes should be emptyDir, not a persistent claim"
    )


def test_deployment_sets_both_requests_and_limits():
    """Requests alone let a runaway plan consume the node. Limits alone break scheduling."""
    resources = _container().get("resources", {})
    assert resources.get("requests", {}).get("cpu")
    assert resources.get("requests", {}).get("memory")
    assert resources.get("limits", {}).get("memory")


def test_deployment_has_liveness_and_readiness_probes():
    container = _container()
    assert container.get("readinessProbe"), "no readiness probe: traffic reaches a booting pod"
    assert container.get("livenessProbe")


def test_dashboard_token_comes_from_a_secret_not_a_literal():
    """A token in the manifest is a token in git and in `kubectl get -o yaml`."""
    env = _container().get("env", [])
    token = [e for e in env if e["name"] in ("MINUS_DASH_TOKEN", "DASH_TOKEN")]
    assert token, "MINUS_DASH_TOKEN not set: a non-loopback bind refuses to start without it"
    assert "value" not in token[0], "token must not be a literal"
    assert "secretKeyRef" in token[0].get("valueFrom", {})


def test_service_is_internal_on_the_dash_port():
    doc = _load_yaml("service.yaml")
    assert doc["spec"]["type"] == "ClusterIP", "the console must not be a LoadBalancer"
    assert doc["spec"]["ports"][0]["port"] == 8050


def test_ingress_is_internal_facing():
    """An internet-facing ALB in front of live AWS cost and account data."""
    doc = _load_yaml("ingress.yaml")
    annotations = doc.get("metadata", {}).get("annotations", {})
    scheme = " ".join(f"{k}={v}" for k, v in annotations.items())
    assert "internal" in scheme, f"ALB scheme is not internal: {annotations}"


# --- WP3: Pillar 14 -------------------------------------------------------------------

def test_grill_me_asks_about_control_plane_hosting():
    skill = _read(".agents", "skills", "grill-me", "SKILL.md")
    assert "Control plane hosting" in skill or "Control Plane Hosting" in skill
    assert "IRSA" in skill


# --- WP4: Operator guide --------------------------------------------------------------

def test_operator_guide_covers_all_three_modes():
    guide = _read("docs", "OPERATOR_ONBOARDING_GUIDE.md")
    for section in ("Quickstart", "CI/CD", "EKS", "Integrations"):
        assert section in guide, f"missing section: {section}"


def test_every_repo_path_the_guide_references_exists():
    """The v1 PRD documented `seed.py --replay-from-bronze`, a flag that does not exist. A
    runbook command that fails is discovered during an incident."""
    guide = _read("docs", "OPERATOR_ONBOARDING_GUIDE.md")
    referenced = set(re.findall(r"(?:^|[\s`(])((?:core|app|deploy|modules|tests|\.agents)/[\w./-]+)",
                                guide))
    missing = [p for p in referenced
               if not os.path.exists(os.path.join(_ROOT, p.rstrip(".,)`")))]
    assert not missing, f"guide references paths that do not exist: {sorted(missing)}"


def test_guide_never_prints_a_real_secret():
    guide = _read("docs", "OPERATOR_ONBOARDING_GUIDE.md")
    assert not re.search(r"AKIA[0-9A-Z]{16}", guide)
    assert not re.search(r"xox[bp]-[0-9A-Za-z-]{10,}", guide)
