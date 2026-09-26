# syntax=docker/dockerfile:1
ARG NODE_VERSION=22.20.0
ARG UV_VERSION=latest

FROM node:${NODE_VERSION}-bookworm-slim AS build
ARG HARNESS_VERSION
RUN npm install -g --prefix /opt/codex "@openai/codex@${HARNESS_VERSION}"

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM node:${NODE_VERSION}-bookworm-slim AS runtime
ARG HARNESS_VERSION
LABEL ahl.harness=codex ahl.harness.version=${HARNESS_VERSION:?required}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        python3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/
ENV PATH="/root/.local/bin:${PATH}"

COPY --from=build /opt/codex /opt/codex
RUN ln -s /opt/codex/bin/codex /usr/local/bin/codex

COPY init-firewall.sh /usr/local/bin/init-firewall.sh
RUN chmod +x /usr/local/bin/init-firewall.sh \
    && mkdir -p /workspace

WORKDIR /workspace
