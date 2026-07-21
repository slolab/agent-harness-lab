# syntax=docker/dockerfile:1
ARG GEMINI_CLI_VERSION=latest
ARG NODE_VERSION=22.20.0
ARG UV_VERSION=latest

FROM node:${NODE_VERSION}-bookworm-slim AS build
ARG GEMINI_CLI_VERSION
RUN npm install -g --prefix /opt/gemini "@google/gemini-cli@${GEMINI_CLI_VERSION}"

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

COPY --from=build /opt/gemini /opt/gemini
RUN ln -s /opt/gemini/bin/gemini /usr/local/bin/gemini

COPY init-firewall.sh /usr/local/bin/init-firewall.sh
RUN chmod +x /usr/local/bin/init-firewall.sh \
    && mkdir -p /workspace

WORKDIR /workspace
