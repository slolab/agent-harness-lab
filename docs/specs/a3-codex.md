# A3: Codex harness

Status: draft
Repo: slolab/agent-harness-lab · Needs: A2 · Roadmap decisions: 1, 2, 10, 11 ([roadmap](https://github.com/slolab/biotope-bench/blob/main/docs/roadmap.md))

## Goal

AHL supports OpenAI's Codex CLI as a harness, authenticated through OpenRouter. It works interactively (`ahl up`) and headlessly (`ahl run`), with skills, web-tool denial, the normalized trace and key-based cost, on the same terms as Claude Code and OpenCode after A2.

## Scope

- A `codex` harness:
  - Dockerfile, adapter and registry entry;
  - config validation and seeding;
  - skill wiring;
  - native trace parsing;
  - a headless driver;
  - web-search denial.

## Non-goals

- Codex with non-OpenAI models. By roadmap decision 1, Codex runs only `openai/*` models.
- Codex through a direct OpenAI key or a ChatGPT login.
- MCP wiring, which is still unimplemented for every harness.

## Design and interfaces

### Config

- `harness: codex` requires `provider: openrouter` and an explicit `model`. Any other provider is rejected with a `ConfigError`.
- Add `codex` to `SUPPORTED_HARNESSES`, the OpenRouter harness allow-list and the adapter registry.

### Image

- `src/ahl/images/codex.Dockerfile` installs `@openai/codex` at an exact version.
- It follows the conventions of the other harness images: Node 22, uv, git, python3, `init-firewall.sh`.
- It carries the labels `ahl.harness=codex` and `ahl.harness.version=<version>` (A1).

### Seeding

- A per-run home at `<run dir>/codex/`, mounted at `/root/.codex` (`CODEX_HOME`). It holds `config.toml`:
  - `model` and `model_provider = "openrouter"`;
  - `[model_providers.openrouter]` with `base_url = "https://openrouter.ai/api/v1"` and `wire_api = "responses"`;
  - authentication read from `OPENROUTER_API_KEY`, using the method that avoids "Unknown model" fallback metadata (a command-based auth reading the environment);
  - response storage disabled. OpenRouter's Responses API is stateless and rejects `store: true` and `previous_response_id`, so Codex must send the full context each time;
  - approvals and sandbox set so that headless runs never prompt. The container is the sandbox.
- Exact key names follow the pinned Codex version's documentation. Record them in the PR.

### Skills and permissions

- **Skills:** mount-mode skills go into Codex's native skill directory under `CODEX_HOME`. `skills.py` gets Codex's agent identifier for delegated installs.
- **Web denial:** `permissions.deny` containing `websearch` or `webfetch` disables Codex's web-search tool in `config.toml`. `session.json` lists the operations applied. If Codex has no separate fetch tool, `webfetch` is recorded as applied through the same switch; say which in the PR.

### Headless driver

- **Turn 1:** `codex exec --json --skip-git-repo-check <bypass flags> -m <model>` with the prompt.
- **Turn 2 onwards:** `codex exec resume <session id>` with the prompt.
- **Session id:** taken from the `thread.started` event.
- **Status:** a turn is `completed` when the exit code is 0 and no `turn.failed` or `error` event ends the turn.

### Trace

- Parse the rollout files under `CODEX_HOME/sessions/**/rollout-*.jsonl` into the native `trace.json`, and into `trace.jsonl` using A2's schema.
- `usage` events come from `token_count` records, deduplicated per response.

## Acceptance criteria

- **AC-1** (unit) `harness: codex` with `provider: openrouter` and a model loads. Any other provider, or a missing model, raises a `ConfigError` naming the problem.
- **AC-2** (unit) `codex.Dockerfile` pins `@openai/codex` to an exact version and sets both AHL labels.
- **AC-3** (unit) The seeded `config.toml` selects the OpenRouter provider with `wire_api = "responses"`, takes its credential from `OPENROUTER_API_KEY` without writing the key to disk, disables response storage, and sets non-prompting approval and sandbox modes. With a web deny, web search is disabled and `session.json` lists the applied operations.
- **AC-4** (unit) The turn-1 command is `codex exec --json` with the configured model. The turn-2 command resumes the session id taken from the `thread.started` event in a recorded fixture.
- **AC-5** (unit) Mount-mode skills are mounted under Codex's native skill directory, and `skills.py` maps `codex` to its installer agent identifier.
- **AC-6** (unit) Rollout fixtures committed from the AC-7 run parse into `trace.jsonl` lines that validate against A2's schema. The first `user` message of each turn equals its prompt file.
- **AC-7** (live) A two-turn `ahl run` on `openai/gpt-6-sol` meets all of the following:
  - turn 1 creates a file with a tool, and turn 2 changes it;
  - both turns share one session id;
  - `totals.cost_usd_key_delta` > 0;
  - a separate one-turn run with a mounted fixture skill shows the agent reading that skill.
- **AC-8** (unit) `README.md` and `config.example.yaml` document the Codex harness. A test asserts `codex` appears in both.

## Test plan

- Unit tests follow the existing adapter tests in `tests/test_harnesses.py` and `tests/test_openrouter.py`.
- The live tier records fixtures under `tests/fixtures/headless/codex/`.

## Risks and open questions

- **Stateless Responses API.** OpenRouter's Responses API keeps no state between requests. Start with a live spike: one prompt, one tool call, one resumed turn. If Codex cannot work through OpenRouter, stop and report `BLOCKED on AC-7` with the evidence. Do not switch to a different API, provider or harness.
- **Codex config keys change between releases.** Pin the version and test against it only.

## Evidence required in the PR

- The pinned Codex version and the `config.toml` keys used.
- The AC-7 commands with abridged `result.json` and cost.
- The spike result.
