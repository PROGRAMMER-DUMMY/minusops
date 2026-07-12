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
# TEMPORARY DIAGNOSTIC (2026-07-12), round 3: rounds 1-2 proved the download is completely
# healthy (HTTP 200, all 27714924 bytes) and ruled out resource ceiling (13GB/16GB memory free,
# 89G/145G disk free at the checksum layer) -- so the original silent death is a command
# failing inside the chain, not the runner reaping it. Splitting into separate RUN layers
# introduced its own artifact (files under /tmp did not persist across a RUN-layer boundary in
# this environment) that masked the real question, so this reverts to the single-RUN structure
# that actually ships, instrumented INLINE (no layer crossed) with a sentinel echoing the exit
# code after every command -- the last sentinel to print pins the death to the very next
# command, which set -euxo pipefail then stops on for real instead of swallowing.
RUN set -euxo pipefail; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl unzip ca-certificates; \
    echo "DIAG: which unzip -> $(which unzip)"; \
    unzip -v; \
    echo "DIAG unzip_check=$?"; \
    TF_URL="https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip"; \
    curl -fsSL -o /tmp/tf.zip "$TF_URL"; \
    echo "DIAG curl_tf_zip=$?"; \
    ls -l /tmp/tf.zip; \
    echo "DIAG ls_tf_zip=$?"; \
    curl -fsSL -o /tmp/tf.sums "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_SHA256SUMS"; \
    echo "DIAG curl_tf_sums=$?"; \
    grep "terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip" /tmp/tf.sums > /tmp/tf.sum.line; \
    echo "DIAG grep=$?"; \
    test -s /tmp/tf.sum.line; \
    echo "DIAG test_s=$?"; \
    (cd /tmp && sha256sum -c tf.sum.line); \
    echo "DIAG sha256sum=$?"; \
    unzip -t /tmp/tf.zip; \
    echo "DIAG unzip_test=$?"; \
    unzip /tmp/tf.zip -d /usr/local/bin; \
    echo "DIAG unzip_extract=$?"; \
    curl -fsSL -o /tmp/awscli.zip "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip"; \
    echo "DIAG curl_awscli=$?"; \
    unzip -q /tmp/awscli.zip -d /tmp; \
    echo "DIAG unzip_awscli=$?"; \
    /tmp/aws/install; \
    echo "DIAG aws_install=$?"; \
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
