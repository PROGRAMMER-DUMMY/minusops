# MinusOps governed-deploy runner image.
# Self-contained: pinned Terraform + AWS CLI v2 + the minusops engine. Runs as a
# non-root user. Use it as a CI deploy runner or a reproducible operator shell.
FROM python:3.12-slim AS base

ARG TERRAFORM_VERSION=1.10.5
ARG TARGETARCH=amd64

# Pinned external CLIs. Terraform is verified against the official SHA256SUMS.
# --retry: a CI docker-build failure was traced to "FAILED open or read" unzipping the
# Terraform release -- consistent with a transient truncated download, not a real corruption
# (the checksum step below would have caught a corrupted-but-complete file). --retry-all-errors
# covers a truncated/incomplete transfer that curl's default retry logic (connection-level
# errors only) wouldn't otherwise retry.
# The checksum step now writes grep's match to a file and asserts it's non-empty before
# running sha256sum -c on it, instead of piping grep straight into `sha256sum -c -`. Verified
# directly (not assumed) that this is a legibility/explicitness improvement, not a fix for a
# real gap: `sha256sum -c` on empty input already fails loudly ("no properly formatted
# checksum lines found", exit 1), which combined with this script's `set -e` already aborted
# the build correctly if grep ever matched zero lines. The explicit test -s just makes that
# intent readable and gives a clearer failure point, rather than relying on a reader to know
# sha256sum's own empty-input behavior.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl unzip ca-certificates; \
    curl --retry 5 --retry-delay 2 --retry-all-errors -fsSLo /tmp/tf.zip "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip"; \
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
