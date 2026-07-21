# syntax=docker/dockerfile:1
ARG CLAUDE_CODE_VERSION=latest
ARG NODE_VERSION=22.20.0
ARG UV_VERSION=latest

FROM node:${NODE_VERSION}-bookworm-slim AS build
ARG CLAUDE_CODE_VERSION
RUN npm install -g --prefix /opt/claude "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM node:${NODE_VERSION}-bookworm-slim AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        iproute2 \
        iptables \
        python3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/
# `uv tool install` links executables here (e.g. for editable-installed local
# packages — see ahl.packages); put it on PATH so they're runnable directly.
ENV PATH="/root/.local/bin:${PATH}"

COPY --from=build /opt/claude /opt/claude
RUN ln -s /opt/claude/bin/claude /usr/local/bin/claude

COPY init-firewall.sh /usr/local/bin/init-firewall.sh
RUN chmod +x /usr/local/bin/init-firewall.sh \
    && mkdir -p /workspace

WORKDIR /workspace
