# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Agent Harness Lab (AHL) is a development/debug environment for testing skills and MCP servers against real agent harnesses (Gemini CLI, OpenCode, Claude Code, Claude Science, Antigravity/`agy`) inside a Docker sandbox, driven by hand through an interactive shell. Key-based harnesses receive the selected provider key; Claude Code and Claude Science authenticate through an interactive Claude-account login and receive no Anthropic API key. **It is not a tool for driving a persistent real project** — the goal is isolated, repeatable, debuggable runs for people building capabilities, so the same starting state can be replayed across harnesses or capability versions (see "Workspace is a template" below). It is the first slice of a larger system described in `docs/specs.md`: an emulator-style lab for developing and evaluating our own MCP servers and skills against real harnesses, with isolation, observability, and (eventually) record/replay and step-debugging. Read `docs/specs.md` before any architectural change. `TODO` tracks the immediate next steps.

**Key architectural decision: no wire-level proxy.** Every harness already writes a full local record of its own LLM turns and tool calls (chat/session JSONL, `logs.json`, etc.) under its own home/config directory. AHL gets observability by mounting that directory and parsing it after (or during) a run — not by intercepting traffic between the harness and its provider or its MCP servers. Don't propose an LLM-plane or MCP-plane proxy/gateway; that approach was deliberately rejected as overkill. The corollary is that deterministic replay and live pause/mutate (described in `docs/specs.md` FR-F/FR-G) no longer have an obvious mechanism now that nothing sits on the wire — that's flagged as an open question in the spec, not solved.

## Commands

```bash
uv sync --extra dev       # install deps incl. ruff/pytest (uv-managed venv)
uv run ahl up             # build image (if needed) + launch sandbox shell for the configured harness
uv run ahl up --no-build  # skip the docker build step
uv run ruff check .       # lint (no [tool.ruff] config block yet)
uv run pytest             # unit tests — pure functions/classes over paths and dicts, no Docker needed
```

Setup before first run:
```bash
cp config.example.yaml config.yaml   # untracked, local selection of harness/provider/model/workspace
cp .env.example .env                  # untracked, secrets only (the one matching provider API key)
```

## Architecture

Single Python package, `src/ahl/`, exposed via the `ahl` console script (Typer app in `cli.py`).

- **`config.py`** — loads `config.yaml` + `.env` into a `RunConfig`. Introduces the `Named` pattern used for `harness`, `provider`, and `model`: each accepts either a bare string or a `{name, parameters}` mapping in YAML. `PROVIDER_KEY_ENV` maps provider name → host env var holding its API key; this is the single source of truth for which secret a provider needs.
- **`capabilities.py`** — `Capability` value object + `parse_capabilities()` for the `capabilities:` list in `config.yaml` (skill/MCP bundles to preinstall — see `docs/capability-format.md`). Local `mount` skills retain adapter-owned read-only native-directory mounts for hot reload; local `copy` and remote `npx` skills are delegated.
- **`skills.py`** — harness-agnostic delegated skill wiring. It maps the configured AHL harness to the corresponding `vercel-labs/skills` agent identifier, mounts local copy sources under `/opt/ahl-skill-sources/`, and emits global non-interactive `npx skills add` setup commands. Claude Science is excluded and keeps its adapter-owned ZIP upload flow.
- **`packages.py`** — `Package` value object + `parse_packages()`/`wire_packages()` for the `packages:` list in `config.yaml`: a local (not-yet-published) Python CLI checkout to preinstall, e.g. a tool a skill shells out to that's being developed alongside it. Not agent-facing and harness-agnostic (unlike `capabilities.py`) — wiring is identical regardless of which harness's container it runs in, so `cli.py` calls it directly rather than through `HarnessAdapter`. `wire_packages()` resolves each `Package` to a `(host_path, container_path)` mount under `/opt/ahl-packages/<name>` plus a `uv tool install --editable <container_path>` setup command, run once before the harness shell starts.
- **`workspace.py`** — `Workspace` value object + `parse_workspace()`/`resolve_workspace()` for `workspace:` in `config.yaml`. Same `mount`/`copy` vocabulary as `capabilities.py`/`packages.py`, but **`copy` is the default** here (not `mount`) — see "Workspace is a template" below. `resolve_workspace(run_dir, workspace)` returns the actual host directory to mount: the template itself for `mount`, or a fresh `run_dir/workspace/` (a copy of the template, or empty if none was given) for `copy`. Called once in `cli.py` before `docker_run_args`, which takes that resolved directory as an explicit `workspace_dir` param rather than reading `config.workspace` itself.
- **`docker.py`** — harness-agnostic Docker plumbing only: `image_name()`, `dockerfile_path()`, `docker_run_args()` (assembles the `docker run` invocation — always `-it`, always mounts workspace at `/workspace`, runs `init-firewall.sh` then either `bash` directly, or `sh -c "<setup_commands> && exec bash"` when delegated skills or `packages:` setup commands run first). Takes two separate volume lists: `extra_volumes` (read-write — harness state the harness itself writes into at runtime, e.g. seeded config/data dirs) and `readonly_volumes` (mounted `:ro` — live skill/package sources and local skill sources copied by the delegated installer).
- **`harnesses/`** — one adapter per harness, each implementing the `HarnessAdapter` Protocol (`harnesses/base.py`): `build_env`, `seed`, `wire_capabilities`, `parse_trace`, `start_command`, `start_hints`, `docker_args`. Everything specific to a harness — env vars, auth/config seeding, capability wiring, native log/session parsing, launch command, and exceptional Docker runtime flags — lives in that harness's own module (`harnesses/{gemini,opencode,claude,claude_science,agy}.py`), not spread across shared dispatch tables. `harnesses/base.py` holds only what's genuinely shared (the Protocol, `google_env()` used by both `gemini`/`agy`, the MCP-unsupported warning). `harnesses/__init__.py` is the registry (`get_adapter(name)`) — the single place a new harness gets added. `seed`/`wire_capabilities` write into a per-run subdirectory they own (e.g. `runs/<id>/gemini/`) and return the `(host_path, container_path)` volume mounts for it; `parse_trace` reads that same subdirectory back into a normalized dict, or returns `None` when trace capture is explicitly unsupported (`claude-science`, `agy`).
- **`cli.py`** — the `up` command orchestrates: load config → create `runs/<timestamp>-<harness>/` → build image → `get_adapter(harness)` → adapter seed/live mounts + delegated skill wiring + package wiring → assemble docker args → run setup commands and the interactive shell → on exit, parse and write `trace.json` when supported.

**Adding a new harness** means: add `docker/<harness>.Dockerfile`, add it to `SUPPORTED_HARNESSES` in `config.py`, add a new `harnesses/<harness>.py` adapter implementing `HarnessAdapter`, register it in `harnesses/__init__.py`, and add its supported `skills` CLI agent identifier to `skills.py`. CLI harness images include Node.js 22.20+, npm/npx, Git, Python, and uv/uvx so delegated skills and Python-backed capabilities run consistently.

**Adding a new provider** means: add it to `PROVIDER_KEY_ENV` in `config.py`, then extend each harness adapter's `build_env()` (and `MODEL_PROVIDER`/`AUTH_TYPE` in `harnesses/opencode.py`/`harnesses/gemini.py` if those harnesses should support it) to handle the new provider name.

**Tests** (`tests/`, pytest) cover `config.py`/`capabilities.py`/`packages.py`/`workspace.py` parsing/validation, `docker.py`'s arg assembly, and each adapter's `seed`/`wire_capabilities`/`parse_trace`/registry behavior against `tmp_path` — no Docker needed. Run with `uv run pytest`.

## Key design points to preserve

- **Workspace is a template, not persistent state.** `workspace:` defaults to `install: copy` (and is optional — omitting it entirely gives an empty ephemeral workspace): the configured directory is snapshotted into `runs/<id>/workspace/` fresh on every `ahl up`, and that copy — not the template — is what's mounted read-write at `/workspace`. The template is never mutated, so the same starting state can be reused across harnesses or capability versions, and the resulting copy is itself a diffable artifact (`diff -r <template> runs/<id>/workspace`). `install: mount` is an explicit opt-in for the rarer case of wanting edits to persist into the template directly. Don't default back to `mount` — that was tried and caused exactly the contamination this design avoids.
- **Two-file config split**: `.env` is secrets-only, `config.yaml` is non-secret selection. Don't blend them.
- **Per-run directory** (`runs/<id>/`): everything seeded for/produced by one container run lives there (harness home/config dirs, the resolved `workspace/` copy, `session.json`, `trace.json`). Keep new per-run artifacts under this directory rather than introducing new top-level state dirs.
- **No proxy.** Observability is built by mounting and parsing each harness's own log/session directory, not by intercepting LLM or MCP traffic. See "Key architectural decision" above.
- **Egress is currently unrestricted** (`docker/init-firewall.sh` is a deliberate passthrough). `TODO` sequences re-enabling default-deny egress (needs `--cap-add=NET_ADMIN`) after log-based observability lands for the remaining harnesses — don't quietly "fix" this without flagging it.
- **Tracked vs untracked**: `src/`, `docker/`, `docs/`, `pyproject.toml`, `config.example.yaml`, `.env.example` are tracked. `config.yaml`, `.env`, `runs/`, `projects/` are local state and stay untracked (see `.gitignore`).
