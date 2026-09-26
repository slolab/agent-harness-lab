# CLAUDE.md

This file guides Claude Code and other coding agents working in this repository. `AGENTS.md` is a symlink to it.

## What this is

Agent Harness Lab (AHL) is a development/debug environment for testing skills and MCP servers against real agent harnesses (Gemini CLI, OpenCode, Claude Code, Claude Science, Antigravity/`agy`, DeepSeek Harness, Codex) inside a Docker sandbox, driven by hand through an interactive shell. Key-based harnesses receive the selected provider key; Claude Code with `provider: anthropic` and Claude Science use account login; Claude Code also supports OpenRouter bearer credentials, and Codex runs only through OpenRouter. **It is not a tool for driving a persistent real project** — the goal is isolated, repeatable, debuggable runs for people building capabilities, so the same starting state can be replayed across harnesses or capability versions (see "Workspace is a template" below). It is the first slice of a larger system described in `docs/specs.md`: an emulator-style lab for developing and evaluating our own MCP servers and skills against real harnesses, with isolation, observability, and (eventually) record/replay and step-debugging. Read `docs/specs.md` before any architectural change. Planned work lives in GitHub issues and in milestone specs under `docs/specs/`.

**Key architectural decision: no wire-level proxy.** Every harness already writes a full local record of its own LLM turns and tool calls (chat/session JSONL, `logs.json`, etc.) under its own home/config directory. AHL gets observability by mounting that directory and parsing it after (or during) a run — not by intercepting traffic between the harness and its provider or its MCP servers. Don't propose an LLM-plane or MCP-plane proxy/gateway; that approach was deliberately rejected as overkill. The corollary is that deterministic replay and live pause/mutate (described in `docs/specs.md` FR-F/FR-G) no longer have an obvious mechanism now that nothing sits on the wire — that's flagged as an open question in the spec, not solved.

## Commands

```bash
uv sync --extra dev       # install deps incl. ruff/pytest (uv-managed venv)
uv run ahl up             # build image (if needed) + launch sandbox shell for the configured harness
uv run ahl up --no-build  # skip the docker build step
uv run ahl build --harness claude  # build one image from the package's image files (or -c CONFIG)
uv run ahl up -c ../elsewhere/config.yaml --env-file ../elsewhere/.env --runs-dir ../elsewhere/runs
uv run ahl run -c config.yaml --turn t1.md --turn t2.md  # headless turns of one session (claude, codex, opencode)
uv run ruff check .       # lint (no [tool.ruff] config block yet)
uv run pytest             # unit tests — no Docker needed; relay tests use Node.js and local sockets
uv run pytest -m docker   # tests that build images or start containers (local only)
uv run pytest -m live     # tests that call a real provider and spend credit (local only, never CI)
```

Setup before first run:
```bash
cp config.example.yaml config.yaml   # untracked, local selection of harness/provider/model/workspace
cp .env.example .env                  # untracked, secrets only (the one matching provider API key)
```

## Architecture

Single Python package, `src/ahl/`, exposed via the `ahl` console script (Typer app in `cli.py`).

- **`config.py`** — loads `config.yaml` + an env file into a `RunConfig`. Introduces the `Named` pattern used for `harness`, `provider`, and `model`: each accepts either a bare string or a `{name, parameters}` mapping in YAML. `PROVIDER_KEY_ENV` maps provider name → host env var holding its API key; this is the single source of truth for which secret a provider needs. `load_config(path, env_file)` loads an explicit env file over the shell, or `<config dir>/.env` under it; relative paths in the config resolve against the config file's directory through `resolve_config_path()`. It also parses `harness_version` (only for the harnesses in `HARNESS_NPM_PACKAGES`), `network` and `env`, and rejects `env` names that are provider key variables or that the selected adapter's `build_env()` sets. `read_config()`, `parse_harness()` and `parse_harness_version()` let `ahl build -c` read only `harness` and `harness_version`.
- **`mounts.py`** — `Mount` value object + `parse_mounts()` for the `mounts:` list: extra bind mounts, read-only unless `readonly: false`, with existing sources and absolute, unique targets other than `/workspace`. `docker_run_args` adds them as bind mounts.
- **`capabilities.py`** — `Capability` value object + `parse_capabilities()` for the `capabilities:` list in `config.yaml` (skill/MCP bundles to preinstall — see `docs/capability-format.md`). Local `mount` skills retain adapter-owned read-only native-directory mounts for hot reload; local `copy` and remote `npx` skills are delegated. A `kind: plugin` entry expands at parse time into one skill `Capability` per bundled skill; local plugins enumerate `skills/` from `path` (optional `skills:` filter), remote (`install: npx`) plugins require a `skills:` subset list installed from `source`.
- **`skills.py`** — harness-agnostic delegated skill wiring. It maps the configured AHL harness to the corresponding `vercel-labs/skills` agent identifier, mounts local copy sources under `/opt/ahl-skill-sources/`, and emits global non-interactive `npx skills add` setup commands. Claude Science is excluded and keeps its adapter-owned ZIP upload flow.
- **`packages.py`** — `Package` value object + `parse_packages()`/`wire_packages()` for the `packages:` list in `config.yaml`: a local (not-yet-published) Python CLI checkout to preinstall, e.g. a tool a skill shells out to that's being developed alongside it. Not agent-facing and harness-agnostic (unlike `capabilities.py`) — wiring is identical regardless of which harness's container it runs in, so `cli.py` calls it directly rather than through `HarnessAdapter`. `wire_packages()` resolves each `Package` to a `(host_path, container_path)` mount under `/opt/ahl-packages/<name>` plus a `uv tool install --editable <container_path>` setup command, run once before the harness shell starts. Optional `extras` become a shell-quoted `'<container_path>[a,b]'` requirement in both install modes.
- **`workspace.py`** — `Workspace` value object + `parse_workspace()`/`resolve_workspace()` for `workspace:` in `config.yaml`. Same `mount`/`copy` vocabulary as `capabilities.py`/`packages.py`, but **`copy` is the default** here (not `mount`) — see "Workspace is a template" below. `resolve_workspace(run_dir, workspace)` returns the actual host directory to mount: the template itself for `mount`, or a fresh `run_dir/workspace/` (a copy of the template, or empty if none was given) for `copy`. Called once in `cli.py` before `docker_run_args`, which takes that resolved directory as an explicit `workspace_dir` param rather than reading `config.workspace` itself.
- **`images/`** — the Dockerfiles and their build context, shipped inside the package so `ahl build` works from an installed wheel or a submodule checkout. `IMAGES_DIR` in `docker.py` locates it. No Dockerfile hard-codes a harness version, and uv is not pinned. The claude, codex, opencode and deepseek Dockerfiles require a `HARNESS_VERSION` build argument (`${HARNESS_VERSION:?required}`) and label it `ahl.harness.version`. Every image has `LABEL ahl.harness=<name>`.
- **`runs.py`** — the prepare step `ahl up` and `ahl run` share: `prepare_run()` resolves the workspace, seeds the adapter, prepares and validates permissions, warns about and records `model.parameters` keys the adapter's driver does not apply, wires capabilities, delegated skills and packages, names the container `ahl-<run id>-<8 hex>` (unique per invocation), assembles the `docker run` args, creates the mountpoints of binds nested in a writable mount as the caller, and writes `session.json` (`ahl` and `image` provenance, `container`, `permissions`, `model_parameters`, and `mode: headless` for `ahl run`). `start_container()` starts a detached container, copies `install: copy` packages in and runs setup commands.
- **`headless.py`** — `ahl run`'s turn loop: `docker exec -i` of the driver's command per turn with the prompt on stdin and raw output in `turns/<n>/`, stdout teed through the driver's `view` into a live view on stderr unless `--quiet`, `--timeout` per turn, classification (exec exit 125–127, daemon errors and a vanished container are `infra`; otherwise one ladder over the driver's `TurnReport` gives the reason code), Ctrl-C handling (`Interrupts`: SIGINT raises only while AHL waits on Docker, the harness or the key, so recording, teardown and `result.json` always finish), key usage around each turn, teardown (kill the harness, chown the writable mounts to the caller without entering nested external binds, `docker rm -f`), then `trace.json`, `trace.jsonl` and `result.json`. Exit codes follow the run status.
- **`keyusage.py`** — reads OpenRouter's `GET /api/v1/key` usage and waits, bounded, for it to settle after a turn.
- **`trace.py`** + **`schemas/trace_event.schema.json`** — the normalized `trace.jsonl`: drivers return events without `seq`/`turn`; `write_trace()` numbers them, assigns turns from the turn windows by timestamp and writes the file; `token_totals()` feeds `result.json`. The schema and `docs/trace-schema.md` must list the same fields (a unit test checks this).
- **`docker.py`** — harness-agnostic Docker plumbing only: `image_name()`, `dockerfile_path()`, `docker_run_args()` (assembles the `docker run` invocation — always `-it`, always mounts workspace at `/workspace`, runs `init-firewall.sh` then either `bash` directly, or `sh -c "<setup_commands> && exec bash"` when delegated skills or `packages:` setup commands run first). Takes two separate volume lists: `extra_volumes` (read-write — harness state the harness itself writes into at runtime, e.g. seeded config/data dirs) and `readonly_volumes` (mounted `:ro` — live skill/package sources and local skill sources copied by the delegated installer). Adds `--network`, the `mounts` and the config's `env` from `RunConfig`.
- **`harnesses/`** — one adapter per harness, each implementing the `HarnessAdapter` Protocol (`harnesses/base.py`): `build_env`, `seed`, `wire_capabilities`, `parse_trace`, `start_command`, `start_hints`, `docker_args`, plus the `permission_handler` and `driver` attributes. `driver` is a `HeadlessDriver` (container env, per-turn command, a `TurnReport` from a turn's stdout with the session id, reported error, provider-error flag and whether the assistant replied, `(agent, kind, detail)` live-view items from one streamed stdout record, normalized trace events, applied `model.parameters` keys) or `None` for harnesses `ahl run` rejects; Claude Code, Codex and OpenCode have one. Everything specific to a harness — env vars, auth/config seeding, capability wiring, native log/session parsing, launch command, and exceptional Docker runtime flags — lives in that harness's own module (`harnesses/{gemini,opencode,claude,claude_science,agy,codex}.py`), not spread across shared dispatch tables. `harnesses/base.py` holds only what's genuinely shared (the Protocol, `google_env()` used by both `gemini`/`agy`, the MCP-unsupported warning, and the readers for saved tool outputs and token counts). `harnesses/__init__.py` is the registry (`get_adapter(name)`) — the single place a new harness gets added. `seed`/`wire_capabilities` write into a per-run subdirectory they own (e.g. `runs/<id>/gemini/`) and return the `(host_path, container_path)` volume mounts for it; `parse_trace` reads that same subdirectory back into a normalized dict, or returns `None` when trace capture is explicitly unsupported (`claude-science`, `agy`).
- **`cli.py`** — the `up` command orchestrates: load config (with `--env-file`) → check that the `--name` run is new or the `--resume` run is resumable → check `network` exists → build image (unless `--no-build`) with `HARNESS_VERSION` set to the config's `harness_version`, the current npm release, or DeepSeek's lockfile version → `docker image inspect`, so a missing image, a claude/codex/opencode/deepseek image without an exact `ahl.harness.version`, or one that differs from `harness_version` fails before any run directory exists → create `<runs dir>/<timestamp>-<harness>/` (`--runs-dir`, default `<config dir>/runs`) → `prepare_run()` (`runs.py`) → run setup commands and the interactive shell → remove the container → parse and write `trace.json` when supported. The `run` command does the same checks (usage errors exit 2 without a run directory; a harness without a driver is one), but a Docker, build or missing-image failure still creates the run directory and ends as `error`/`infra` with exit 3; it then hands over to `headless.py`. The `build` command builds one image from `images/`, with `BUILDX_NO_DEFAULT_ATTESTATIONS=1` so that a cached rebuild keeps the image ID.

**Adding a new harness** means: add `src/ahl/images/<harness>.Dockerfile` with `LABEL ahl.harness=<harness>` (plus `ahl.harness.version=${HARNESS_VERSION:?required}` and an entry in `HARNESS_NPM_PACKAGES` if AHL tracks its version), add it to `SUPPORTED_HARNESSES` in `config.py`, add a new `harnesses/<harness>.py` adapter implementing `HarnessAdapter` (with a `HeadlessDriver`, or `driver = None`), register it in `harnesses/__init__.py`, and add its supported `skills` CLI agent identifier to `skills.py`. A driver's trace conversion is documented in `docs/trace-schema.md`. CLI harness images include Node.js 22.20+, npm/npx, Git, Python, and uv/uvx so delegated skills and Python-backed capabilities run consistently.

**Adding a new provider** means: add it to `PROVIDER_KEY_ENV` in `config.py`, then extend each harness adapter's `build_env()` (and `MODEL_PROVIDER`/`AUTH_TYPE` in `harnesses/opencode.py`/`harnesses/gemini.py` if those harnesses should support it) to handle the new provider name.

**Tests** (`tests/`, pytest) cover `config.py`/`capabilities.py`/`packages.py`/`workspace.py` parsing/validation, `docker.py`'s arg assembly, and each adapter's `seed`/`wire_capabilities`/`parse_trace`/registry behavior against `tmp_path` — no Docker needed. `ahl run` scenarios go through the CLI with the shared Docker stub in `tests/conftest.py` (turn outputs, key usage, clock) and harness outputs recorded under `tests/fixtures/a2/` and, for Codex, `tests/fixtures/a3/`; the Docker tier uses `tests/stub_driver.py`, a driver that calls no model. Run with `uv run pytest`.

## Key design points to preserve

- **Workspace is a template, not persistent state.** `workspace:` defaults to `install: copy` (and is optional — omitting it entirely gives an empty ephemeral workspace): the configured directory is snapshotted into `runs/<id>/workspace/` fresh on every `ahl up`, and that copy — not the template — is what's mounted read-write at `/workspace`. The template is never mutated, so the same starting state can be reused across harnesses or capability versions, and the resulting copy is itself a diffable artifact (`diff -r <template> runs/<id>/workspace`). `install: mount` is an explicit opt-in for the rarer case of wanting edits to persist into the template directly. Don't default back to `mount` — that was tried and caused exactly the contamination this design avoids.
- **Two-file config split**: `.env` is secrets-only, `config.yaml` is non-secret selection. Don't blend them.
- **Per-run directory** (`runs/<id>/`): everything seeded for/produced by one container run lives there (harness home/config dirs, the resolved `workspace/` copy, `session.json`, `trace.json`, and for `ahl run` also `result.json`, `trace.jsonl` and `turns/<n>/`). Keep new per-run artifacts under this directory rather than introducing new top-level state dirs.
- **No proxy.** Observability is built by mounting and parsing each harness's own log/session directory, not by intercepting LLM or MCP traffic. See "Key architectural decision" above.
- **Egress is currently unrestricted** (`src/ahl/images/init-firewall.sh` is a deliberate passthrough). Re-enabling default-deny egress (needs `--cap-add=NET_ADMIN`) is deferred until log-based observability lands for the remaining harnesses — don't quietly "fix" this without flagging it.
- **Tracked vs untracked**: `src/` (including `src/ahl/images/`), `docs/`, `tests/`, `.github/`, `pyproject.toml`, `uv.lock`, `config.example.yaml`, `.env.example` are tracked. `config.yaml`, `.env`, `runs/`, `projects/` are local state and stay untracked (see `.gitignore`).

## OpenRouter and DeepSeek extension points

- Validate new provider/harness combinations in `config.py` before creating a run.
  Account login is a harness/provider predicate. Preserve provider-native model
  IDs; OpenCode's OpenRouter routing prefix is separate from the native ID.
- `DeepSeekAdapter` owns its Web-only startup, loopback Docker publication,
  `.dsh`/`.agents` mounts, config reconciliation, and v3 trace normalization.
  `src/ahl/images/deepseek/ahl-deepseek.cjs` relays raw TCP to DSH's required loopback
  listener. It carries browser traffic only; provider calls remain direct.
- The DSH CLI version and transitive dependencies are fixed by
  `src/ahl/images/deepseek/package.json` and `package-lock.json`. Use `npm ci`, retain
  optional platform dependencies, and validate both Linux architectures when
  changing the lock. Native event schemas must be checked against that build.
- AHL-owned Cordis patch rows are updated by ID while other rows survive.
  DeepSeek permissions use a root plugin insertion owned by its permission handler.
  Native `settings.yaml` overrides composition; reconcile owned settings there
  on resume. Preserve session-specific native model selection and unrelated state.
- DeepSeek delegated skills use installer agent `universal`, persisted at
  `/root/.agents/skills`. Mounted skills live under `/root/.dsh/skills`.
- Keep tests at behavioral boundaries. The launcher process tests need Node.js
  and permission to bind local sockets; see `docs/deepseek.md` for live checks.

## Permissions

- `RunConfig.permissions` is a portable immutable deny policy. The CLI calls
  `adapter.permission_handler.prepare()` after seeding, validates full coverage,
  combines read-only mounts, and records the result in session metadata.
- Native mappings belong in handlers, never in CLI/Docker conditionals. Other
  adapters explicitly use `UnsupportedPermissions`. See `docs/permissions.md`.
- Claude mounts run-owned managed settings read-only. DeepSeek loads the
  image-owned global guard at host scope; native settings overrides must be
  reconciled on resume. Removing AHL rules must preserve unrelated native state.

## Development workflow

These rules apply to every agent and subagent. They are the same in `slolab/biotope-bench`; keep both copies in sync. The current milestones (A1–A3) belong to the biotope-bench roadmap, [`docs/roadmap.md`](https://github.com/slolab/biotope-bench/blob/main/docs/roadmap.md), with live status in [slolab/biotope-bench#1](https://github.com/slolab/biotope-bench/issues/1).

### Roles

| Role | Does |
|---|---|
| Orchestrator (main Claude session) | Writes specs, dispatches subagents, watches PRs, updates the tracking issue |
| Implementer (subagent) | Builds one milestone in its own worktree and opens a draft PR |
| Review agent (subagent, fresh context) | Reviews the PR against its spec and posts inline comments |
| Architect (subagent, fresh context) | Reviews the PR's code and tests for design quality, bloat and test quality, and posts inline comments |
| Finalizer (subagent) | Answers the review, gets CI green, completes the PR description, marks the PR ready |
| Fix agent (subagent) | Addresses later review comments, one round at a time |
| Vlad | Reviews specs before implementation, reviews PRs last, merges |

### Order of work

1. The orchestrator writes the spec in a PR. Vlad reviews and merges it.
2. An implementer builds the milestone and opens a draft PR.
3. One review agent and one architect review the PR in parallel. Every implementation PR gets at least one architect pass; after a large fix round the orchestrator may ask for another.
4. The finalizer answers every review thread, gets CI green and marks the PR ready.
5. The orchestrator watches the PR for comments from Vlad and from agents Vlad dispatches, and dispatches fix agents until every comment is addressed.
6. Vlad merges.

### Branches, worktrees and PRs

- Never commit to `main`. Only Vlad merges, by squash merge.
- One milestone per branch, named `<id>-<slug>`, for example `a1-portable-runs`. Work in a worktree at `../.worktrees/<repo>/<branch>`, or use the Agent tool's worktree isolation.
- A new worktree has no `.env`; before running live tests, symlink it from the main checkout with `ln -s /home/vladsam42/Projects/biotope_project/agent-harness-lab/.env .env`.
- Open a draft PR as soon as the branch has its first commit. Title it `<ID>: <title>` and fill in the PR template.
- When a bench milestone needs an AHL change, the AHL PR merges first. Bench then moves the `vendor/agent-harness-lab` submodule to a commit on AHL's `main`, never to an unmerged branch.
- Use Conventional Commits that name the milestone, for example `feat(a1): resolve paths against the config file`.

### Specs are the contract

- Every milestone has a spec in `docs/specs/<id>-<slug>.md`. Vlad approves it by merging it to `main`, and from then on it is frozen.
- Implement the spec as written. **Never weaken, drop or reinterpret an acceptance criterion to make progress.**
- The binding parts of a spec are its goal, scope, non-goals, interfaces and acceptance criteria. Its design sketch is guidance only: depart from it when the code shows a better way, and say why in the PR. That needs no spec change.
- The implementer never edits a spec. If a criterion is wrong, impossible or far harder than expected, post a PR comment starting with your role tag and `BLOCKED on AC-n`, for example `🤖 claude-implementer: BLOCKED on AC-3`, with the evidence and the options you see, and stop work on that criterion.
- The orchestrator takes the question to Vlad. Only after Vlad approves a change in a PR comment does the orchestrator commit it, in its own commit prefixed `spec-change:`, with the reason and the URL of Vlad's approving comment in the commit message.
- Findings outside the spec become new GitHub issues, not extra scope.

### Tests first

- Work test-first. Write the tests for the desired behaviour before the code, with mocks or stubs at the boundaries (subprocess, Docker, HTTP) so they run before the implementation exists. See them fail, then implement.
- Test behaviour through public interfaces, not internals. Prefer a few scenario tests that exercise an outcome end to end over many small tests of its parts. One scenario may cover several acceptance criteria. The PR description maps each criterion to the tests that cover it.
- No test bloat:
  - no phantom tests, which cannot fail or cannot pass;
  - no tests of trivial code, or of the language or a library;
  - no tests that repeat what another test already covers, including sweeps over trivial parameter variations;
  - no tests split into fragments that one integrated test would cover.
- Three tiers, selected by pytest markers:
  - **unit:** the default, runs in CI.
  - **`@pytest.mark.docker`:** needs Docker, runs locally.
  - **`@pytest.mark.live`:** spends OpenRouter credit, runs locally only, never in CI. A test that spends credit carries only `live`, even when it also needs Docker, because `-m docker` selects any test marked `docker`.
- The pytest config keeps the docker and live tiers out of the default run: `addopts = "--strict-markers -m 'not docker and not live'"`, with both markers registered. `uv run pytest` runs the unit tier, and `-m docker` or `-m live` runs the others. A marked test run by node id still needs its marker, for example `uv run pytest -m live tests/test_x.py::test_a1_ac5`. AHL has it in `pyproject.toml`.
- Never make a test pass by weakening it: no new `skip` or `xfail`, no removed assertions, no loosened tolerances, no mocking of the code under test, no expected values hard-coded into production code.
- Merging or deleting redundant, trivial or phantom tests needs no approval, as long as every behaviour stays covered. A wrong test needs Vlad's agreement before it changes. Say so in the PR and wait for his approval in a PR comment.
- A bug fix starts with a failing test that reproduces it.
- Live and Docker evidence goes in the PR description: the command, abridged output and, for live runs, the OpenRouter cost.

### Reviews and comments

- Every comment an agent posts on GitHub starts with its role tag, prefixed by its agent family. The Claude orchestrator tags itself `🤖 claude-agent:`, and its subagents use `🤖 claude-implementer:`, `🤖 claude-review-agent:`, `🤖 claude-architect:`, `🤖 claude-finalizer:` or `🤖 claude-fix-agent:`. Codex agents follow the same pattern: `🤖 codex-agent:`, `🤖 codex-review-agent:` and so on. An agent with no family convention uses the bare role, for example `🤖 review-agent:`. All agents post through Vlad's account, so a comment without a tag is Vlad's.
- Keep PR comments and descriptions concise and to the point: findings, decisions, evidence and SHAs. No praise, no summaries of what went well, no filler.
- Reply to every review thread with the fixing commit's SHA, or with the reason for not changing anything.
- The agent that fixes an agent-opened thread resolves it. Threads answered without a change stay open for Vlad, and threads Vlad opened stay open until Vlad resolves them.
- Review agents check, for every PR:
  - each acceptance criterion is implemented and has a real test;
  - for implementation PRs, the spec is unchanged: `git diff origin/main...HEAD -- docs/specs/` is empty except for `spec-change:` commits that each cite an approving comment by Vlad (an untagged comment). An unapproved spec change is blocking. Spec PRs and bootstrap PRs are exempt from this check;
  - no test was weakened (see above);
  - the evidence holds up: re-run the unit and Docker tiers locally, and check the pasted live output and cost against any run records the PR commits (in bench, `results/`). Do not re-run the live tier;
  - the code is correct and readable.
- Architects check, for every implementation PR:
  - **design:**
    - modular, maintainable structure;
    - extensible where the roadmap needs it;
    - no duplicated code;
    - no unneeded abstractions, layers, options or files;
    - efficient code. Shorter is better while it stays clear: 100 lines that one line could replace are a finding;
  - **comments:** the zero-comment policy (see Style) holds, and comment and docstring bloat is removed;
  - **tests:** the tests were designed first and there is no test bloat (see Tests first). The architect reads every test the PR adds or changes, and flags:
    - phantom, trivial, redundant and over-granular tests;
    - tests that pass around a wrong implementation, for example by mocking the code under test, or by asserting what the code happens to do instead of what the spec requires.

### Definition of done

- Every acceptance criterion is implemented and mapped to a passing test in the PR description.
- CI is green. The Docker and live tiers the spec requires have run locally, with evidence in the PR.
- The PR description lists every `spec-change:` commit under "Spec changes", because squash merge drops commit subjects.
- Docs, including this file, match the change.
- No review thread is left unanswered.
- The tracking issue shows the new status.

### Handoffs

End every working session on a PR with a comment covering what is done, what is next, blockers and open questions. The next agent starts from that comment.

## Style

- Python 3.11+, formatted and linted with ruff. Match the surrounding code.
- **Zero-comment policy.** Code explains itself through names and structure. A comment is allowed only for a non-obvious trick or decision, and says why, not what. This means:
  - no docstrings that restate the name or signature;
  - no commented-out code, section banners or change notes.
  Apply it to all code you write or touch.
- Documents use plain, compact prose: short sentences, concrete statements, no rhetorical emphasis or repeated conclusions.
