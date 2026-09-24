# CLAUDE.md

This file guides Claude Code and other coding agents working in this repository. `AGENTS.md` is a symlink to it.

## What this is

Agent Harness Lab (AHL) is a development/debug environment for testing skills and MCP servers against real agent harnesses (Gemini CLI, OpenCode, Claude Code, Claude Science, Antigravity/`agy`, DeepSeek Harness) inside a Docker sandbox, driven by hand through an interactive shell. Key-based harnesses receive the selected provider key; Claude Code with `provider: anthropic` and Claude Science use account login; Claude Code also supports OpenRouter bearer credentials. **It is not a tool for driving a persistent real project** — the goal is isolated, repeatable, debuggable runs for people building capabilities, so the same starting state can be replayed across harnesses or capability versions (see "Workspace is a template" below). It is the first slice of a larger system described in `docs/specs.md`: an emulator-style lab for developing and evaluating our own MCP servers and skills against real harnesses, with isolation, observability, and (eventually) record/replay and step-debugging. Read `docs/specs.md` before any architectural change. Planned work lives in GitHub issues and in milestone specs under `docs/specs/`.

**Key architectural decision: no wire-level proxy.** Every harness already writes a full local record of its own LLM turns and tool calls (chat/session JSONL, `logs.json`, etc.) under its own home/config directory. AHL gets observability by mounting that directory and parsing it after (or during) a run — not by intercepting traffic between the harness and its provider or its MCP servers. Don't propose an LLM-plane or MCP-plane proxy/gateway; that approach was deliberately rejected as overkill. The corollary is that deterministic replay and live pause/mutate (described in `docs/specs.md` FR-F/FR-G) no longer have an obvious mechanism now that nothing sits on the wire — that's flagged as an open question in the spec, not solved.

## Commands

```bash
uv sync --extra dev       # install deps incl. ruff/pytest (uv-managed venv)
uv run ahl up             # build image (if needed) + launch sandbox shell for the configured harness
uv run ahl up --no-build  # skip the docker build step
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

- **`config.py`** — loads `config.yaml` + `.env` into a `RunConfig`. Introduces the `Named` pattern used for `harness`, `provider`, and `model`: each accepts either a bare string or a `{name, parameters}` mapping in YAML. `PROVIDER_KEY_ENV` maps provider name → host env var holding its API key; this is the single source of truth for which secret a provider needs.
- **`capabilities.py`** — `Capability` value object + `parse_capabilities()` for the `capabilities:` list in `config.yaml` (skill/MCP bundles to preinstall — see `docs/capability-format.md`). Local `mount` skills retain adapter-owned read-only native-directory mounts for hot reload; local `copy` and remote `npx` skills are delegated. A `kind: plugin` entry expands at parse time into one skill `Capability` per bundled skill; local plugins enumerate `skills/` from `path` (optional `skills:` filter), remote (`install: npx`) plugins require a `skills:` subset list installed from `source`.
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
- **Egress is currently unrestricted** (`docker/init-firewall.sh` is a deliberate passthrough). Re-enabling default-deny egress (needs `--cap-add=NET_ADMIN`) is deferred until log-based observability lands for the remaining harnesses — don't quietly "fix" this without flagging it.
- **Tracked vs untracked**: `src/`, `docker/`, `docs/`, `tests/`, `.github/`, `pyproject.toml`, `uv.lock`, `config.example.yaml`, `.env.example` are tracked. `config.yaml`, `.env`, `runs/`, `projects/` are local state and stay untracked (see `.gitignore`).

## OpenRouter and DeepSeek extension points

- Validate new provider/harness combinations in `config.py` before creating a run.
  Account login is a harness/provider predicate. Preserve provider-native model
  IDs; OpenCode's OpenRouter routing prefix is separate from the native ID.
- `DeepSeekAdapter` owns its Web-only startup, loopback Docker publication,
  `.dsh`/`.agents` mounts, config reconciliation, and v3 trace normalization.
  `docker/deepseek/ahl-deepseek.cjs` relays raw TCP to DSH's required loopback
  listener. It carries browser traffic only; provider calls remain direct.
- The DSH CLI version and transitive dependencies are fixed by
  `docker/deepseek/package.json` and `package-lock.json`. Use `npm ci`, retain
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
