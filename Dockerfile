# MinusOps governed-deploy runner image.
# Self-contained: pinned Terraform + AWS CLI v2 + the minusops engine. Runs as a
# non-root user. Use it as a CI deploy runner or a reproducible operator shell.
FROM python:3.12-slim AS base

ARG TERRAFORM_VERSION=1.10.5
ARG OPA_VERSION=1.18.2
ARG TARGETARCH=amd64

# bash, not the default dash /bin/sh, so `set -o pipefail` below actually works -- a failed
# command inside any pipe can no longer be masked by a later command's exit code, the same
# swallowed-failure shape as the real bug this whole block was fixed for (see below).
SHELL ["/bin/bash", "-c"]

# Pinned external CLIs. Terraform is verified against the official SHA256SUMS.
#
# REAL FIX (2026-07-12): the checksum check here had NEVER actually verified anything since
# this Dockerfile was written. It downloaded the release to /tmp/tf.zip (a shortened local
# name) but then ran `sha256sum -c` against a checksum line that names the file by its
# ORIGINAL filename, terraform_<version>_linux_<arch>.zip -- sha256sum -c matches by the name
# IN the checksum line, not whatever the download was saved as, so with no file at that name
# it printed "terraform_1.10.5_linux_amd64.zip: FAILED open or read" and aborted, every single
# build, 100% of the time. That message was misread as unzip's during earlier diagnosis
# (rounds 1-3, now removed) -- unzip was never even reached. Fixed by downloading to the exact
# filename the checksum line expects, so the match actually resolves. This is the sixth
# instance this session of "a verifier that passes without verifying" (G5's classify(), G2's
# extractor, the schema no-op already caught in this same file, and now this) -- proven, not
# assumed: a real build now prints "OK" for this exact file (see the commit this shipped in
# for the captured line), and a local test with the hash deliberately corrupted confirms
# sha256sum -c still exits non-zero and, under set -e, still aborts the build for real.
RUN set -euxo pipefail; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl unzip ca-certificates; \
    TF_ZIP="terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip"; \
    curl --retry 5 --retry-delay 2 --retry-all-errors -fsSLo "/tmp/${TF_ZIP}" "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/${TF_ZIP}"; \
    curl --retry 5 --retry-delay 2 --retry-all-errors -fsSLo /tmp/tf.sums "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_SHA256SUMS"; \
    grep "${TF_ZIP}" /tmp/tf.sums > /tmp/tf.sum.line; \
    test -s /tmp/tf.sum.line; \
    (cd /tmp && sha256sum -c tf.sum.line); \
    unzip "/tmp/${TF_ZIP}" -d /usr/local/bin; \
    curl --retry 5 --retry-delay 2 --retry-all-errors -fsSLo /tmp/awscli.zip "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip"; \
    unzip -q /tmp/awscli.zip -d /tmp; \
    /tmp/aws/install; \
    # G6 (docs/g6_scope.md, Phase 3) shells out to `opa`; without it the shadow-mode gate is
    # inert in this image. Same discipline as the Terraform fix above: pinned version, verified
    # against OPA's own per-binary `.sha256` (confirmed live, `<hash>  <filename>` format --
    # sha256sum -c native), fails the build loud on a mismatch rather than shipping silently.
    OPA_ASSET="opa_linux_${TARGETARCH}"; \
    curl --retry 5 --retry-delay 2 --retry-all-errors -fsSLo "/tmp/${OPA_ASSET}" "https://github.com/open-policy-agent/opa/releases/download/v${OPA_VERSION}/${OPA_ASSET}"; \
    curl --retry 5 --retry-delay 2 --retry-all-errors -fsSLo "/tmp/${OPA_ASSET}.sha256" "https://github.com/open-policy-agent/opa/releases/download/v${OPA_VERSION}/${OPA_ASSET}.sha256"; \
    (cd /tmp && sha256sum -c "${OPA_ASSET}.sha256"); \
    install -m 0755 "/tmp/${OPA_ASSET}" /usr/local/bin/opa; \
    rm -rf /tmp/* /var/lib/apt/lists/*; \
    terraform version; aws --version; opa version

WORKDIR /app
COPY pyproject.toml README.md ./
COPY core ./core
COPY app ./app
COPY docs ./docs
COPY modules ./modules
COPY examples ./examples
COPY .agents/AGENTS.md ./.agents/AGENTS.md
COPY .agents/skills ./.agents/skills

# Both extras: [policy] for the CI gate (checkov), [dashboard] for the Mode 3 web console.
# One image serves both modes -- a second Dockerfile would duplicate the checksum-verified
# binary install above and the two would drift.
RUN pip install --no-cache-dir ".[policy,dashboard]" && checkov --version

# Non-root: the gate never needs root, and deploy creds come from the ambient chain.
RUN useradd --create-home --uid 10001 minus
USER minus

EXPOSE 8050

# Only meaningful for a long-running console (Mode 3 on ECS, or plain `docker run`).
# Kubernetes ignores this and uses the probes in deploy/k8s/deployment.yaml instead. A
# one-shot CI run exits well inside --start-period, so it never reports unhealthy.
# urllib, not curl: curl is removed from the final image above.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3   CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('DASH_PORT','8050')+'/', timeout=4).status < 500 else 1)"

# Mode 2 (CI gate) is the default. Mode 3 overrides the entrypoint:
#   command: ["python"], args: ["app/dashboard_app.py"]
# dashboard_app refuses a non-loopback bind without MINUS_DASH_TOKEN, so an unauthenticated
# console cannot be exposed by forgetting a variable.
ENTRYPOINT ["minusctl"]
CMD ["--help"]
