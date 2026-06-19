# Agent Harness Lab

Minimal lab for driving an agent harness (Gemini CLI, OpenCode, Claude Code, or Antigravity CLI) by hand inside a Docker container. The real provider key is injected into the container and the harness talks to its provider directly; observability comes from the harness's own logs in the workspace.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync
cp config.example.yaml config.yaml
cp .env.example .env
```

Put your provider key in `.env` (one key, matching `provider` in `config.yaml`):

```bash
GEMINI_API_KEY=...
```

Then launch the sandbox shell:

```bash
uv run ahl up
```

This builds the harness image and drops you into an interactive shell at
`/workspace`. Run the harness yourself (e.g. `gemini`, `opencode`, `claude`).
Exit the shell to stop.

## Configuration

Two files, clean split:

- `.env` — **secrets only** (API keys).
- `config.yaml` — **non-secret selection**. The host env var holding each
  provider's key is built in per provider (`src/ahl/config.py`).

`harness`, `provider`, and `model` each take either a bare name or a
`{name, parameters}` mapping:

```yaml
harness: gemini       # gemini | opencode | agy | claude
provider: gemini      # anthropic | openai | gemini | vertex
model: gemini-3.5-flash
workspace: ./projects/demo   # mounted at /workspace
```

Provider with parameters (Vertex express / Agent Platform key):

```yaml
provider:
  name: vertex
  parameters:
    project: your-gcp-project-id
    location: global
```

The same `{name, parameters}` form is reserved for `harness` and `model` (e.g.
thinking settings) — parameters are parsed today and consumed as adapters need
them.

| provider  | key env             |
|-----------|---------------------|
| anthropic | `ANTHROPIC_API_KEY` |
| openai    | `OPENAI_API_KEY`    |
| gemini    | `GEMINI_API_KEY`    |
| vertex    | `GOOGLE_API_KEY`    |

The selected harness picks its Dockerfile (`docker/<harness>.Dockerfile`) and
image (`agent-harness-lab:<harness>`). Add a harness by adding a Dockerfile and
an adapter in `src/ahl/harness.py`.

## Harness notes

- **opencode** ([OpenCode](https://opencode.ai/docs/)) supports all four
  providers via env keys. Model ID is `provider/model` (e.g.
  `google-vertex/gemini-3.5-flash`); bare model names are prefixed from
  `provider`. For **vertex** express keys, `options.apiKey` is pre-seeded in
  `opencode.json` (without it OpenCode tries ADC and fails). Config at
  `runs/<id>/opencode/config/`, session data at `runs/<id>/opencode/data/`.
  Launch: `opencode -m <provider/model>`.
- **gemini** (Gemini CLI) and **agy** both run on the `@google/genai` SDK. Use
  `provider: gemini` (Gemini API key) or `provider: vertex` (Vertex express).
  Launch with `gemini --skip-trust -m <model>`. Auth is pre-seeded in
  `runs/<id>/gemini/settings.json` (`vertex-ai` or `gemini-api-key`) and
  mounted at `/root/.gemini`. After the session, `runs/<id>/trace.json` parses
  `logs.json` and `tmp/workspace/chats/*.jsonl`.
- **agy** works only with Vertex express keys here (no OAuth/ADC). Prefer
  **gemini** for a plain Gemini API key.

## Isolation

The real provider key is injected into the container as an env var and the
harness reaches its provider directly. Egress is currently **unrestricted**:
`docker/init-firewall.sh` is a permissive passthrough kept as the hook point for
re-enabling default-deny egress later (it would also need
`--cap-add=NET_ADMIN`).

## Session record

Each `ahl up` writes `runs/<id>/session.json` (harness, provider, model,
workspace, timestamp). **gemini** and **opencode** harnesses also persist state
under `runs/<id>/` and write `trace.json` on exit.

## What is tracked

Tracked: `src/`, `docker/`, `docs/`, `pyproject.toml`, `config.example.yaml`,
`.env.example`. Untracked local state: `config.yaml`, `.env`, `runs/`,
`projects/`.

## Roadmap

Structured capture of harness logs (and optionally the shell), automated/scripted
driving, re-enabled egress isolation, and pre-installed / hot-reloaded MCP
servers and skills are next; not implemented yet. See `docs/specs.md`.
