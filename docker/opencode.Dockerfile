# syntax=docker/dockerfile:1
ARG OPENCODE_VERSION=latest
ARG UV_VERSION=latest

FROM node:22-bookworm-slim AS build
ARG OPENCODE_VERSION
RUN npm install -g --prefix /opt/opencode "opencode-ai@${OPENCODE_VERSION}"

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM node:22-bookworm-slim AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        python3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/
# `uv tool install` links executables here (e.g. for editable-installed local
# packages — see ahl.packages); put it on PATH so they're runnable directly.
ENV PATH="/root/.local/bin:${PATH}"

COPY --from=build /opt/opencode /opt/opencode
RUN ln -s /opt/opencode/bin/opencode /usr/local/bin/opencode

COPY init-firewall.sh /usr/local/bin/init-firewall.sh
RUN chmod +x /usr/local/bin/init-firewall.sh \
    && mkdir -p /workspace

WORKDIR /workspace
