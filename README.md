# Agent Harness Lab

A sandbox for testing skills and MCP servers against real agent harnesses
(Gemini CLI, OpenCode, Claude Code, Claude Science, Antigravity/`agy`) in
Docker — isolated and repeatable, not for driving a persistent project.
Key-based harnesses receive only their selected provider key; Claude Code and
Claude Science instead use a manual Claude-account login. Observability comes
from reading each harness's own logs, not from a wire proxy.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync
cp config.example.yaml config.yaml
cp .env.example .env
```

For a key-based harness, put the provider key in `.env` (matching `provider`
in `config.yaml`):

```bash
GEMINI_API_KEY=...
```

For `harness: claude` or `harness: claude-science`, no `.env` file or
Anthropic API key is required.

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
harness: gemini       # gemini | opencode | agy | claude | claude-science
provider: gemini      # anthropic | openai | gemini | vertex
model: gemini-3.5-flash
workspace: ./projects/demo
```

| provider  | key env             |
|-----------|---------------------|
| anthropic | `ANTHROPIC_API_KEY` (OpenCode only; not Claude login harnesses) |
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
  `docs/capability-format.md`). Local `install: mount` skills are bind-mounted
  read-only for hot reload. Local `install: copy` and remote `install: npx`
  skills are installed globally inside the container through `npx skills`,
  with the target agent inferred from the configured harness.
- **`packages`** — local, not-yet-published Python checkouts to preinstall
  (`uv tool install` for CLIs, `uv pip install --system` for libraries).
  `install: mount` bind-mounts read-only; `install: copy` snapshots then
  `docker cp`s into the container (writable — needed for setuptools editable).

Runs are named: `ahl up --name my-run` uses `runs/my-run/` instead of the
default `<timestamp>-<harness>` id. `ahl up --resume my-run` continues that
run in place — same workspace, same harness home/config state (sessions,
chat history, etc.) — instead of starting a fresh `runs/<id>/`.

## Harnesses

| harness        | auth/providers                      | launch (inside the shell) |
|----------------|-------------------------------------|---------------------------|
| gemini         | gemini, vertex API key              | `gemini --skip-trust -m <model>` |
| opencode       | anthropic, openai, gemini, vertex API key | `opencode -m <provider/model>` |
| claude         | Claude account login                | `claude` (or `--model <model>`) |
| claude-science | Claude account login; x64 Linux     | `claude-science serve --no-browser --host 0.0.0.0 --port 8000` |
| agy            | vertex API key only                 | `agy` |

Auth is either pre-seeded or completed interactively per harness; `ahl up`
prints the exact launch command and harness-specific hints. Add a harness by
adding `docker/<harness>.Dockerfile` and a `src/ahl/harnesses/<harness>.py`
adapter — see `CLAUDE.md`.

## Observability

Each `ahl up` writes `runs/<id>/session.json` (harness, provider, model,
workspace mode, timestamp). **gemini**, **opencode**, and **claude** persist
their own session state under `runs/<id>/` and get a normalized `trace.json`
on exit (sessions, messages, tool calls). Claude Science tracing is an
explicit non-goal for its initial harness; **agy** still has no confirmed log
location.

See [trace accounting and its limits](docs/observability.md) for Claude's
response counts, token counters, cache lifetimes, and overlapping session views.

### Claude account harnesses

For Claude Code, use `provider: anthropic`, omit `model` to use the plan
default, run `ahl up`, then start `claude` and choose Claude App account login.
No `ANTHROPIC_API_KEY` is validated or passed to Docker. Claude's
`~/.claude/projects` state is mounted only into that run, then parsed into
`trace.json` when the shell exits.

Claude Science targets x64 glibc Linux. Its image installs the official app,
bubblewrap, and socat; publishes the app and preview ports only on host
loopback; and mounts a fresh `~/.claude-science` plus the run workspace. See
[the Claude Science runbook](docs/claude-science.md) for configuration,
sign-in, skill upload, sandbox fallback, and the Linux acceptance checks.

## Isolation

For key-based harnesses, the selected provider key is injected as an env var;
Claude login harnesses receive no provider key. Egress is currently
**unrestricted** —
`docker/init-firewall.sh` is a permissive passthrough kept as the hook point
for re-enabling default-deny egress later. Local mount-mode capability and
package sources are read-only, so an agent cannot write back into your
checkout. Skills installed through `npx skills` are writable but remain
inside the ephemeral container.

## What is tracked

Tracked: `src/`, `docker/`, `docs/`, `pyproject.toml`, `config.example.yaml`,
`.env.example`. Untracked local state: `config.yaml`, `.env`, `runs/`,
`projects/`.

## Roadmap

See `TODO` and `docs/specs.md`. Next up: MCP capability wiring, better log exploration, repeatable execution of tasks by the harness
