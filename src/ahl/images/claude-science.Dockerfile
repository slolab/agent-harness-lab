# syntax=docker/dockerfile:1
ARG UV_VERSION=latest

FROM --platform=linux/amd64 ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# Claude Science currently ships only for x64 glibc Linux. Ubuntu 24.04 also
# supplies bubblewrap >= 0.8.0, which the app requires for code execution.
FROM --platform=linux/amd64 ubuntu:24.04 AS runtime
LABEL ahl.harness=claude-science
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        bubblewrap \
        ca-certificates \
        curl \
        iproute2 \
        iptables \
        python3 \
        socat \
    && rm -rf /var/lib/apt/lists/* \
    && bwrap --version

COPY --from=uv /uv /uvx /usr/local/bin/
ENV PATH="/root/.local/bin:${PATH}"

# Anthropic's installer verifies the downloaded release checksum and installs
# the claude-science binary under ~/.local/bin.
RUN curl -fsSL https://claude.ai/install-claude-science.sh | bash \
    && claude-science --version

COPY init-firewall.sh /usr/local/bin/init-firewall.sh
RUN chmod +x /usr/local/bin/init-firewall.sh \
    && mkdir -p /workspace /root/.claude-science

EXPOSE 8000 8001
WORKDIR /workspace
