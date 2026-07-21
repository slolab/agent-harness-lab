# syntax=docker/dockerfile:1
ARG NODE_VERSION=22.20.0
ARG UV_VERSION=latest

FROM debian:bookworm-slim AS build
ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/root
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl tar unzip \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    curl -fsSL https://antigravity.google/cli/install.sh | bash -s -- --dir /usr/local/bin; \
    test -x /usr/local/bin/agy

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM node:${NODE_VERSION}-bookworm-slim AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bsdextrautils \
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

COPY --from=build /usr/local/bin/agy /usr/local/bin/agy

COPY init-firewall.sh /usr/local/bin/init-firewall.sh
RUN chmod +x /usr/local/bin/init-firewall.sh \
    && mkdir -p /workspace

WORKDIR /workspace
