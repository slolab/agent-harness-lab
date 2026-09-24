# syntax=docker/dockerfile:1
ARG NODE_VERSION=22.20.0
ARG UV_VERSION=latest

FROM node:${NODE_VERSION}-bookworm-slim AS build
WORKDIR /opt/deepseek
COPY deepseek/package.json deepseek/package-lock.json ./
# Keep optional platform packages (PTY, SQLite, ripgrep, etc.) on both Linux arches.
RUN npm ci --omit=dev

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM node:${NODE_VERSION}-bookworm-slim AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git python3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /usr/local/bin/
ENV PATH="/root/.local/bin:${PATH}"
COPY --from=build /opt/deepseek /opt/deepseek
COPY deepseek/ahl-deepseek.cjs /usr/local/bin/ahl-deepseek
COPY permissions/deepseek.mjs /opt/ahl/permissions/deepseek.mjs
COPY init-firewall.sh /usr/local/bin/init-firewall.sh
RUN ln -s /opt/deepseek/node_modules/.bin/dsh /usr/local/bin/dsh \
    && chmod +x /usr/local/bin/ahl-deepseek /usr/local/bin/init-firewall.sh \
    && mkdir -p /workspace
WORKDIR /workspace
