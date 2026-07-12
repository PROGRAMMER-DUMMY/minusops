# MinusOps governed-deploy runner image.
# Self-contained: pinned Terraform + AWS CLI v2 + the minusops engine. Runs as a
# non-root user. Use it as a CI deploy runner or a reproducible operator shell.
FROM python:3.12-slim AS base

ARG TERRAFORM_VERSION=1.10.5
ARG TARGETARCH=amd64

# bash, not the default dash /bin/sh, so `set -o pipefail` below actually works -- a failed
# command inside any pipe can no longer be masked by a later command's exit code, the same
# swallowed-failure shape the checksum no-op fix already closed once this session.
SHELL ["/bin/bash", "-c"]

# Pinned external CLIs. Terraform is verified against the official SHA256SUMS.
#
# TEMPORARY DIAGNOSTIC (2026-07-12), round 2: round 1 proved the download itself is completely
# healthy (HTTP 200, all 27714924 bytes, correct URL -- disproving "curl isn't really
# fetching"/"swallowed HTTP error"). But the build still died right after, with NO `+ ls -l`
# echo under `set -x` -- the fingerprint of the shell being KILLED outright (OOM or a BuildKit
# resource ceiling), not a command returning a normal non-zero exit. Split into separate
# layers (download / checksum / unzip) so whichever operation crosses the ceiling is isolated
# to its own failing layer, with the actual memory/disk numbers captured BEFORE anything runs,
# not guessed after the fact.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl unzip ca-certificates file; \
    rm -rf /var/lib/apt/lists/*

RUN set -euxo pipefail; \
    echo "DIAG: resource ceiling BEFORE any download"; \
    free -m; \
    df -h /; \
    ulimit -a; \
    (cat /sys/fs/cgroup/memory.max 2>/dev/null || cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null || echo "DIAG: no cgroup memory limit file found"); \
    dmesg 2>&1 | tail -20 || echo "DIAG: dmesg unavailable (unprivileged container, expected)"; \
    echo "DIAG: TERRAFORM_VERSION=${TERRAFORM_VERSION} TARGETARCH=${TARGETARCH} TARGETPLATFORM=${TARGETPLATFORM:-<unset>}"; \
    TF_URL="https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip"; \
    curl -fsSL -w "DIAG: http_code=%{http_code} size_download=%{size_download}\n" -o /tmp/tf.zip "$TF_URL"; \
    echo "DIAG: download layer completed"; \
    ls -l /tmp/tf.zip; \
    file /tmp/tf.zip; \
    df -h /tmp

RUN set -euxo pipefail; \
    echo "DIAG: resource ceiling BEFORE checksum layer"; \
    free -m; \
    df -h /; \
    dmesg 2>&1 | tail -20 || echo "DIAG: dmesg unavailable (unprivileged container, expected)"; \
    curl --retry 5 --retry-delay 2 --retry-all-errors -fsSLo /tmp/tf.sums "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_SHA256SUMS"; \
    grep "terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip" /tmp/tf.sums > /tmp/tf.sum.line; \
    test -s /tmp/tf.sum.line; \
    (cd /tmp && sha256sum -c tf.sum.line); \
    echo "DIAG: checksum layer completed"

RUN set -euxo pipefail; \
    echo "DIAG: resource ceiling BEFORE unzip layer"; \
    free -m; \
    df -h /; \
    dmesg 2>&1 | tail -20 || echo "DIAG: dmesg unavailable (unprivileged container, expected)"; \
    unzip /tmp/tf.zip -d /usr/local/bin; \
    echo "DIAG: unzip layer completed"; \
    terraform version

RUN set -euxo pipefail; \
    curl --retry 5 --retry-delay 2 --retry-all-errors -fsSLo /tmp/awscli.zip "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip"; \
    unzip -q /tmp/awscli.zip -d /tmp; \
    /tmp/aws/install; \
    rm -rf /tmp/*; \
    aws --version

WORKDIR /app
COPY pyproject.toml README.md ./
COPY core ./core
COPY app ./app
COPY docs ./docs
COPY modules ./modules
COPY examples ./examples
COPY .agents/AGENTS.md ./.agents/AGENTS.md
COPY .agents/skills ./.agents/skills

RUN pip install --no-cache-dir ".[policy]" && checkov --version

# Non-root: the gate never needs root, and deploy creds come from the ambient chain.
RUN useradd --create-home --uid 10001 minus
USER minus

ENTRYPOINT ["minusctl"]
CMD ["--help"]
