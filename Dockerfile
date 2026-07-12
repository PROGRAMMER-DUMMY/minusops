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
# TEMPORARY DIAGNOSTIC (2026-07-12): a CI docker-build failure ("FAILED open or read" unzipping
# the Terraform release) is 100% reproducible every run, in well under a second total for the
# whole apt-get+curl+checksum+unzip chain -- too fast to be a real transient network blip, and
# a local download+checksum from this same machine is completely healthy (hash matches
# HashiCorp's official SHA256SUMS exactly). Leading theory: TARGETARCH/the resolved URL isn't
# what's assumed, or curl's own failure is being swallowed rather than actually stopping the
# build -- so this instruments the exact failure point instead of guessing further.
RUN set -euxo pipefail; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl unzip ca-certificates file; \
    echo "DIAG: TERRAFORM_VERSION=${TERRAFORM_VERSION} TARGETARCH=${TARGETARCH} TARGETPLATFORM=${TARGETPLATFORM:-<unset>}"; \
    TF_URL="https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip"; \
    echo "DIAG: fetching $TF_URL"; \
    curl -fsSL -w "DIAG: http_code=%{http_code} size_download=%{size_download} url_effective=%{url_effective}\n" -o /tmp/tf.zip "$TF_URL"; \
    echo "DIAG: post-download inspection"; \
    ls -l /tmp/tf.zip; \
    file /tmp/tf.zip; \
    df -h; \
    unzip -t /tmp/tf.zip || true; \
    curl --retry 5 --retry-delay 2 --retry-all-errors -fsSLo /tmp/tf.sums "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_SHA256SUMS"; \
    grep "terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip" /tmp/tf.sums > /tmp/tf.sum.line; \
    test -s /tmp/tf.sum.line; \
    (cd /tmp && sha256sum -c tf.sum.line); \
    unzip /tmp/tf.zip -d /usr/local/bin; \
    curl --retry 5 --retry-delay 2 --retry-all-errors -fsSLo /tmp/awscli.zip "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip"; \
    unzip -q /tmp/awscli.zip -d /tmp; \
    /tmp/aws/install; \
    rm -rf /tmp/* /var/lib/apt/lists/*; \
    terraform version; aws --version

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
