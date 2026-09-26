# Agent Harness Lab

A sandbox for testing skills and MCP servers against real agent harnesses
(Gemini CLI, OpenCode, Claude Code, Claude Science, Antigravity/`agy`, DeepSeek Harness) in
Docker — isolated and repeatable, not for driving a persistent project.
Key-based harnesses receive only their selected provider key. Claude Code with
`provider: anthropic` and Claude Science use a manual Claude-account login. Observability comes
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

For `harness: claude` or `harness: claude-science` with `provider: anthropic`,
no `.env` file or Anthropic API key is required.

Then launch the sandbox:

```bash
uv run ahl up
```

This builds the harness image and drops you into an interactive shell at
`/workspace`. Run the harness yourself (e.g. `gemini`, `opencode`, `claude`).
Exit the shell to stop.

### Using AHL from another repository

The config, env file and runs directory do not need to live in the AHL
checkout. Relative paths inside the config (`workspace`, `capabilities[].path`,
`packages[].path`, `mounts[].path`) resolve against the config file's
directory. Command-line paths resolve against the working directory.

```bash
ahl build --harness opencode            # or: ahl build -c path/to/config.yaml
ahl up -c path/to/config.yaml --env-file path/to/.env --runs-dir path/to/runs
```

- `ahl build` builds `agent-harness-lab:<harness>` from the image files shipped
  inside the installed package. It needs no provider key and reads no env file;
  with `-c` it reads only `harness` and `harness_version`. `ahl up` builds the
  same way unless you pass `--no-build`. Builds skip BuildKit's default
  attestations, so a rebuild without changes keeps the image ID.
- `ahl up` refuses a Claude Code, OpenCode or DeepSeek image whose
  `ahl.harness.version` label is not an exact version, or differs from the
  config's `harness_version`. Rebuild it with `ahl build -c CONFIG`.
- `--env-file PATH` loads that file, and its keys win over shell variables.
  A missing file is an error. Without the flag, `ahl up` reads `.env` next to
  the config, and shell variables win over it.
- `--runs-dir DIR` puts the run at `DIR/<run id>`; `--name` and `--resume`
  look inside `DIR`. The default is `runs/` next to the config.

## Headless runs

```bash
ahl run -c config.yaml --turn build.md --turn review.md \
  [--env-file PATH] [--runs-dir DIR] [--name NAME] [--timeout SECONDS] [--build/--no-build] [--quiet]
```

`ahl run` sends each `--turn` file, byte for byte on stdin, as one turn of a
single native session and exits. Claude Code and OpenCode have headless
drivers; other harnesses are a usage error. `--timeout` applies to each turn
(default 3600). `--env-file`, `--runs-dir`, `--name`, `--build` and relative
paths behave as in `ahl up`; a relative `--turn` resolves against the working
directory, and a `--name` whose run directory exists is a usage error. After a
turn that does not complete, the remaining turns are skipped.

No session can wait for input: tools run without permission prompts, the
ask-user tool is denied, and with OpenRouter every Claude Code model alias
resolves to the configured model.

While a turn runs, AHL prints a condensed view of the harness's streamed output
to stderr, one line per item, marked with its agent: `[agent] text:` for
assistant text, `tool:` for a tool call with its input, `subagent:` for a
subagent start, and `error:`. Long items are shortened to one line. Claude Code
streams its subagents' messages, marked with the id of the tool call that
started them. OpenCode streams only the main session, so a subagent shows as
its start, with the child session id; its tool calls appear in `trace.jsonl`
after the turn. `--quiet` prints only the per-turn summary lines and warnings.
The run directory is the same either way.

| Exit | Run `status` | Reason codes | When |
|---|---|---|---|
| 0 | `completed` | – | Every turn completed |
| 1 | `failed` | `harness_exit`, `harness_reported_error`, `no_assistant_output`, `provider_error` | The harness failed in a turn |
| 2 | – | – | Config or usage error; no `result.json` |
| 3 | `error` | `infra` | Image build or container start failed, `docker exec` exited 125–127, the Docker daemon reported an error, or the container is gone |
| 124 | `timeout` | `timeout` | A turn exceeded `--timeout` |
| 130 | `interrupted` | `interrupted` | Ctrl-C |

When several apply, precedence is timeout, interrupted, error, failed.
`harness_exit`: the harness exited non-zero. `harness_reported_error`: it
exited 0 but reported an error. `no_assistant_output`: the turn produced no
assistant message. `provider_error`: the output shows a provider or API error
such as HTTP 429 or 5xx (best effort). Skipped turns have
`skipped_after_failure`.

Ctrl-C (SIGINT) interrupts the image build, the container start, a turn and
the key-usage wait after a turn, and no further turn starts. Stopping the
harness, recording the turn, handing files to the caller, removing the
container and writing the run directory always finish. The turn that was
running, or whose usage wait was running, ends as `interrupted` unless it timed
out, and later turns are `skipped`. A Ctrl-C before turn 1 marks turn 1, and
one during cleanup marks the last turn that ran.

Every terminal state except exit 2 leaves:

```
<run dir>/
  session.json   result.json   trace.jsonl   trace.json
  turns/<n>/prompt.md                            copy of every turn file
  turns/<n>/stdout.jsonl, turns/<n>/stderr.log   raw output of every started turn
  <harness home>/  workspace/                    native harness state and the workspace
```

- `result.json` holds the run's `status` and `reason` (those of the first turn
  that did not complete), the native `session_id`, and per turn its prompt
  file and SHA-256, timings, exit code, status, reason, session id and
  `key_usage`. `totals` sums wall clock, key cost and the token fields of the
  trace's `usage` events; `warnings` lists `{code, turn, message}`.
- If the harness's native state cannot be parsed, for example an OpenCode
  database killed before its tables exist, `trace.json` is missing,
  `trace.jsonl` is empty, the token totals are null and `warnings` has
  `trace_unreadable`. The status and exit code are unaffected.
- `key_usage` (OpenRouter only, otherwise null) reads `GET /api/v1/key` before
  and after each started turn. After a turn AHL polls every 10 s for up to
  120 s until the usage has risen and two readings agree (`settled: true`).
  A usage that never rises gives `delta_usd: null` and the warning
  `usage_not_updated`; a failed read gives `usage_read_failed`. A Ctrl-C
  ends the wait after one reading (`settled: false`).
  `totals.cost_usd_key_delta` is null if any started turn's delta is null.
- `trace.jsonl` is one normalized event per line for every harness; see
  [the trace schema](docs/trace-schema.md).
- `session.json` adds `"mode": "headless"`. Both `ahl run` and `ahl up`
  record `container`, the Docker container name, and
  `model_parameters.unsupported`, the configured `model.parameters` keys the
  harness cannot apply (each also prints a warning).

Container names are `ahl-<run id>-<8 hex>`, unique per invocation, so a
leftover container never blocks a new run. AHL removes the container in every
terminal state, after stopping the harness and handing the files it wrote to
the calling user. Files inside `mounts:` binds and other external mounts keep
their owners. If AHL is killed with SIGKILL, the container keeps running;
remove it with `docker rm -f <container>` from `session.json`.

## Configuration

Two files, clean split:

- `.env` — **secrets only** (API keys).
- `config.yaml` — **non-secret selection**.

```yaml
harness: gemini       # gemini | opencode | agy | claude | claude-science | deepseek
provider: gemini      # anthropic | openai | gemini | vertex | openrouter
model: gemini-3.5-flash
workspace: ./projects/demo
```

| provider  | key env             |
|-----------|---------------------|
| anthropic | `ANTHROPIC_API_KEY` (OpenCode only; not Claude login harnesses) |
| openrouter | `OPENROUTER_API_KEY` (Claude Code, OpenCode, DeepSeek) |
| openai    | `OPENAI_API_KEY`    |
| gemini    | `GEMINI_API_KEY`    |
| vertex    | `GOOGLE_API_KEY`    |

`harness`, `provider`, and `model` each also accept a `{name, parameters}`
mapping (e.g. Vertex needs `project`/`location`). See `config.example.yaml`
for the full annotated reference, including:

- **`harness_version`** — for `claude` and `opencode` only, e.g.
  `harness_version: 2.1.273`: the image installs exactly that version.
  Without it, each build installs the current release.
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
  Optional `extras: [graph]` installs the package's extras in both modes; for
  a CLI they land in the tool's own environment.
- **`mounts`** — extra bind mounts, e.g. a dataset:
  `{path: ../datasets/unpacked, target: /workspace/data}`. The source must
  exist; the target must be absolute, unique and not `/workspace` itself.
  Mounts are read-only unless `readonly: false`.
- **`network`** — the name of an existing Docker network to join, e.g. a
  Compose project's `my-project_default`, so the harness can reach its
  services. A network that does not exist is an error.
- **`env`** — non-secret environment variables for the container, as string
  values, e.g. `NEO4J_URI: bolt://neo4j:7687`. Provider key variables and
  variables the selected harness adapter sets are rejected; keys belong in
  `.env`.

Runs are named: `ahl up --name my-run` uses `runs/my-run/` instead of the
default `<timestamp>-<harness>` id. `ahl up --resume my-run` continues that
run in place — same workspace, same harness home/config state (sessions,
chat history, etc.) — instead of starting a fresh `runs/<id>/`.

### Native web-tool permissions

Configure denials once for the run and its agents:

```yaml
permissions:
  deny: [websearch, webfetch]
```

| Harness | Native web-tool denials |
|---|---|
| Claude Code (account login or OpenRouter) | Enforced through managed settings |
| DeepSeek | Enforced by a global native tool guard |
| OpenCode | Enforced through `permission` in the seeded `opencode.json` |
| Gemini, Antigravity, Claude Science | Warning; requested denials are not applied |

Omit the block or use `deny: []` for no AHL denials. Resume installs the current
rules, including removal of old AHL denials. `session.json` records requested,
applied, and unsupported operations. Unknown operation names are configuration
errors. These rules restrict native tools; shell HTTP and network access remain
available. See [permissions and handler extensions](docs/permissions.md).

## Harnesses

| harness        | auth/providers                      | launch (inside the shell) |
|----------------|-------------------------------------|---------------------------|
| gemini         | gemini, vertex API key              | `gemini --skip-trust -m <model>` |
| opencode       | anthropic, openai, gemini, vertex, openrouter API key | `opencode -m <provider/model>` |
| claude         | Claude account login or OpenRouter key                | `claude` (or `--model <model>`) |
| claude-science | Claude account login; x64 Linux     | `claude-science serve --no-browser --host 0.0.0.0 --port 8000` |
| deepseek       | OpenRouter API key | `ahl-deepseek --port 3080` |
| agy            | gemini or vertex API key                 | `agy` |

Auth is either pre-seeded or completed interactively per harness; `ahl up`
prints the exact launch command and harness-specific hints. Add a harness by
adding `src/ahl/images/<harness>.Dockerfile` and a `src/ahl/harnesses/<harness>.py`
adapter — see `CLAUDE.md`.

No Dockerfile hard-codes a harness version. For Claude Code and OpenCode,
`ahl build` installs the config's `harness_version`, or else the current npm
release, which it looks up before building. DeepSeek installs the version in
its npm lockfile. The version is passed as the `HARNESS_VERSION` build argument,
which these three Dockerfiles require, and recorded in the image label
`ahl.harness.version`. Gemini CLI, agy and
Claude Science install their latest release and carry no version label. uv is
not pinned. Every image carries the label `ahl.harness=<name>`.

## Observability

Each `ahl up` writes `runs/<id>/session.json` (harness, provider, model,
workspace mode, timestamp, permissions, container name, unsupported model
parameters). It also records provenance: `ahl`
(version, plus `git_sha` and `git_dirty` when AHL runs from a git checkout,
otherwise null) and `image` (name, ID and `ahl.harness.version` label of the
image the container started from). **gemini**, **opencode**, **claude**, and **deepseek** persist
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

### OpenRouter

Set `provider: openrouter`, `OPENROUTER_API_KEY` in `.env`, and an explicit
provider-native `model` such as `qwen/qwen3.7-flash`. Supported harnesses are
Claude Code, OpenCode, and DeepSeek; other combinations fail during config loading.
Model IDs are preserved exactly. OpenCode adds its outer routing prefix:
`openrouter/auto` becomes `openrouter/openrouter/auto` inside OpenCode.
For OpenCode, `model.parameters.provider` reaches OpenRouter's
[provider routing](https://openrouter.ai/docs/features/provider-routing)
verbatim, e.g. `{only: [deepinfra], quantizations: [fp8], allow_fallbacks: false}`.
Claude Code cannot apply it and warns.

Claude receives the gateway URL, bearer credential, and primary model through
its environment; plain `claude` uses that model too. When switching an existing
account-login run to OpenRouter, run `/logout` inside Claude and restart it.
AHL preserves saved credentials. OpenRouter [guarantees Claude compatibility
with Anthropic's first-party provider](https://openrouter.ai/docs/cookbook/coding-agents/claude-code-integration);
AHL does not restrict model choices.

### DeepSeek browser

```yaml
harness: deepseek
provider: openrouter
model: qwen/qwen3.7-flash
```

Run `uv run ahl up`, then the printed `ahl-deepseek --port 3080` command.
Open DSH's authenticated URL in your browser. The official Web UI retains
Standard mode and the native preset roster. Only host loopback is published.
Use `harness: {name: deepseek, parameters: {port: 4321}}` to change the port;
an occupied port is an error.

Native search requires a separate `DEEPSEEK_API_KEY`, which this integration
does not supply. Search stays configured and fails without that credential
unless `permissions.deny` disables it explicitly.
See the [DeepSeek runbook](docs/deepseek.md) for state, resume, skills, version
pinning, browser acceptance, and trace limitations.

## Isolation

For key-based harnesses, the selected provider key is injected as an env var;
Claude login harnesses receive no provider key. Egress is currently
**unrestricted** —
`src/ahl/images/init-firewall.sh` is a permissive passthrough kept as the hook point
for re-enabling default-deny egress later. Local mount-mode capability and
package sources are read-only, so an agent cannot write back into your
checkout. Skills installed through `npx skills` are writable but remain
in run-owned state when the adapter persists their native directory.

## What is tracked

Tracked: `src/` (including the image files in `src/ahl/images/`), `docs/`, `tests/`, `.github/`, `pyproject.toml`,
`uv.lock`, `config.example.yaml`, `.env.example`. Untracked local state: `config.yaml`, `.env`, `runs/`,
`projects/`.

## Roadmap

See `docs/specs.md` (product spec), `docs/specs/` (milestone specs) and the GitHub issues. Contributor and agent rules are in `CLAUDE.md` (also `AGENTS.md`). Next up: milestones A1–A3 of the [biotope-bench roadmap](https://github.com/slolab/biotope-bench/blob/main/docs/roadmap.md) (portable runs, headless `ahl run`, a Codex harness). The older backlog is in [#4](https://github.com/slolab/agent-harness-lab/issues/4).
