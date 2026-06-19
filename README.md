# Agent Harness Lab

A sandbox for testing skills and MCP servers against real agent harnesses
(Gemini CLI, OpenCode, Claude Code, Antigravity/`agy`) in Docker — isolated
and repeatable, not for driving a persistent project. The real provider key
is injected into the container and the harness talks to its provider
directly; observability comes from reading each harness's own logs, not from
a wire proxy.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync
cp config.example.yaml config.yaml
cp .env.example .env
```

Put your provider key in `.env` (matching `provider` in `config.yaml`):

```bash
GEMINI_API_KEY=...
```

Then launch the sandbox:

```bash
uv run ahl up
```

This builds the harness image and drops you into an interactive shell at
`/workspace`. Run the harness yourself (e.g. `gemini`, `opencode`, `claude`).
Exit the shell to stop.

## Configuration

Two files, clean split:

- `.env` — **secrets only** (API keys).
- `config.yaml` — **non-secret selection**.

```yaml
harness: gemini       # gemini | opencode | agy | claude
provider: gemini      # anthropic | openai | gemini | vertex
model: gemini-3.5-flash
workspace: ./projects/demo
```

| provider  | key env             |
|-----------|---------------------|
| anthropic | `ANTHROPIC_API_KEY` |
| openai    | `OPENAI_API_KEY`    |
| gemini    | `GEMINI_API_KEY`    |
| vertex    | `GOOGLE_API_KEY`    |

`harness`, `provider`, and `model` each also accept a `{name, parameters}`
mapping (e.g. Vertex needs `project`/`location`). See `config.example.yaml`
for the full annotated reference, including:

- **`workspace`** — a *template*, not a live project. By default it's
  snapshotted into `runs/<id>/workspace/` fresh on every `ahl up`, so the
  same starting state is reusable across harnesses and capability versions;
  the template itself is never touched. Opt into `install: mount` for a live
  bind-mount instead, or omit `workspace` entirely for an empty workspace.
- **`capabilities`** — skills/MCP bundles to preinstall (see
  `docs/capability-format.md`). Mounted read-only, either bind-mounted live
  (`install: mount`, hot reload) or snapshotted once (`install: copy`).
- **`packages`** — local, not-yet-published Python checkouts to preinstall
  (`uv tool install` for CLIs, `uv pip install --system` for libraries),
  for developing a tool alongside the skill that depends on it.

Runs are named: `ahl up --name my-run` uses `runs/my-run/` instead of the
default `<timestamp>-<harness>` id. `ahl up --resume my-run` continues that
run in place — same workspace, same harness home/config state (sessions,
chat history, etc.) — instead of starting a fresh `runs/<id>/`.

## Harnesses

| harness  | providers                          | launch (inside the shell)  |
|----------|-------------------------------------|----------------------------|
| gemini   | gemini, vertex                      | `gemini --skip-trust -m <model>` |
| opencode | anthropic, openai, gemini, vertex   | `opencode -m <provider/model>`   |
| claude   | anthropic                           | `claude --model <model>`         |
| agy      | vertex only (no OAuth/ADC fallback) | `agy`                             |

Auth is pre-seeded per harness before the container starts; `ahl up` prints
the exact launch command and any harness-specific hints. Add a harness by
adding `docker/<harness>.Dockerfile` and a `src/ahl/harnesses/<harness>.py`
adapter — see `CLAUDE.md`.

## Observability

Each `ahl up` writes `runs/<id>/session.json` (harness, provider, model,
workspace mode, timestamp). **gemini** and **opencode** also persist their
own session state under `runs/<id>/` and get a normalized `trace.json` on
exit (sessions, messages, tool calls); **claude** and **agy** don't have a
known log location yet.

## Isolation

The real provider key is injected as an env var; the harness reaches its
provider directly. Egress is currently **unrestricted** —
`docker/init-firewall.sh` is a permissive passthrough kept as the hook point
for re-enabling default-deny egress later. Capability/package mounts are
read-only, so an agent can use a skill or local tool but never write back
into your checkout.

## What is tracked

Tracked: `src/`, `docker/`, `docs/`, `pyproject.toml`, `config.example.yaml`,
`.env.example`. Untracked local state: `config.yaml`, `.env`, `runs/`,
`projects/`.

## Roadmap

See `TODO` and `docs/specs.md`. Next up: MCP capability wiring, better log exploration, repeatable execution of tasks by the harness
