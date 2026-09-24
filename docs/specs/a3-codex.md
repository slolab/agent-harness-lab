# A3: Codex harness

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
- OpenRouter provider routing for Codex.

## Design and interfaces

### Config

- `harness: codex` requires `provider: openrouter` and an explicit `model`. Any other provider is rejected with a `ConfigError`.
- Add `codex` to `SUPPORTED_HARNESSES`, the OpenRouter harness allow-list and the adapter registry.
- A configured `model.parameters.provider` prints a warning and is listed in `session.json` under `model_parameters.unsupported` (A2).

### Image

- `src/ahl/images/codex.Dockerfile` installs `@openai/codex` at an exact version.
- It follows the conventions of the other harness images: Node 22, uv, git, python3, `init-firewall.sh`.
- It carries the labels `ahl.harness=codex` and `ahl.harness.version=<version>` (A1).

### Seeding

- A per-run home at `<run dir>/codex/`, mounted at `/root/.codex` (`CODEX_HOME`). It holds `config.toml`:
  - `model` and `model_provider = "openrouter"`;
  - `[model_providers.openrouter]` with `base_url = "https://openrouter.ai/api/v1"` and `wire_api = "responses"`;
  - authentication read from `OPENROUTER_API_KEY`, using the method that avoids "Unknown model" fallback metadata (a command-based auth reading the environment);
  - approvals and sandbox set so that headless runs never prompt. The container is the sandbox.
- Exact key names follow the pinned Codex version's documentation. Record them in the PR.
- **Stateless API.** OpenRouter's Responses API keeps no state and rejects `store: true` and `previous_response_id`, so Codex must send the full context each time. Current Codex versions have no config key for this. The requirement is behavioural: no request is rejected because of `store` or `previous_response_id`. The spike and AC-7 check it.

### Skills and permissions

- **Skills:** mount-mode skills go into the native user-skill directory of the pinned Codex version. Current docs name `~/.agents/skills` and treat `~/.codex/skills` as legacy; the spike confirms which directory the pinned version reads. `skills.py` gets Codex's agent identifier for delegated installs.
- **Web denial:** `permissions.deny` containing `websearch` or `webfetch` disables Codex's web-search tool in `config.toml`. `session.json` lists the operations applied. If Codex has no separate fetch tool, `webfetch` is recorded as applied through the same switch; say which in the PR.
- **Web search when allowed:** Codex's web search is a hosted tool. The spike checks whether it works through OpenRouter's Responses API. If it does not, the README says that Codex has no web search through OpenRouter, and the PR records the evidence.

### Headless driver

- The prompt arrives on stdin (A2), passed to Codex as `-`.
- **Turn 1:** `codex exec --json --skip-git-repo-check <bypass flags> -m <model> -`.
- **Turn 2 onwards:** `codex exec resume <session id> -` with the same `--json`, `--skip-git-repo-check`, bypass and model flags. Flag placement around `resume` has changed between releases; the adapter follows the pinned version, and the PR records the full turn-2 command.
- **Session id:** taken from the `thread.started` event.
- **Status:** a turn is `completed` when the exit code is 0 and no `turn.failed` or `error` event ends the turn. A non-zero exit gives `harness_exit`. A `turn.failed` or `error` event with exit 0 gives `harness_reported_error`. Either becomes `provider_error` when the error shows a provider or API error (A2).

### Trace

- Parse the rollout files under `CODEX_HOME/sessions/**/rollout-*.jsonl` into the native `trace.json`, and into `trace.jsonl` using A2's schema. Captured stdout is kept but is not the trace source.
- **Usage:** `token_count` records carry the cumulative `total_token_usage` and the per-call `last_token_usage`, but no response id, and they can repeat. One `usage` event is written per change in `total_token_usage`, for example from the difference of consecutive totals. `response_id` is `null`. The implementer documents the method in `docs/trace-schema.md`.
- **Token definitions (A2):** in these records `cached_input_tokens` is part of `input_tokens`. The parser writes `input_tokens` minus `cached_input_tokens` as `input_tokens`, `cached_input_tokens` as `cache_read_tokens`, `null` as `cache_write_tokens`, and `reasoning_output_tokens` as `reasoning_tokens`.
- **Turn prompt:** Codex writes the environment context and AGENTS.md instructions as user-role items before the prompt. The first `user` message of a turn comes from the `user_message` event.

## Acceptance criteria

- **AC-1** (unit) `harness: codex` with `provider: openrouter` and a model loads. Any other provider, or a missing model, raises a `ConfigError` naming the problem.
- **AC-2** (unit) `codex.Dockerfile` pins `@openai/codex` to an exact version and sets both AHL labels.
- **AC-3** (unit) Seeding:
  - the seeded `config.toml` selects the OpenRouter provider with `wire_api = "responses"`, takes its credential from `OPENROUTER_API_KEY` without writing the key to disk, and sets non-prompting approval and sandbox modes;
  - with a web deny, web search is disabled and `session.json` lists the applied operations;
  - a config with `model.parameters.provider` prints a warning, and `session.json` has `model_parameters.unsupported` equal to `["provider"]`.
- **AC-4** (unit) The turn-1 and turn-2 commands both contain `--json` and the configured model, and read the prompt from stdin (`-`). The turn-2 command resumes the session id taken from the `thread.started` event in a recorded fixture.
- **AC-5** (unit) Mount-mode skills are mounted under the native user-skill directory of the pinned Codex version, and `skills.py` maps `codex` to its installer agent identifier.
- **AC-6** (unit) Rollout fixtures committed from the AC-7 run:
  - parse into `trace.jsonl` lines that validate against A2's schema;
  - the first `user` message of each turn contains its prompt text with leading and trailing whitespace stripped;
  - repeated `token_count` records with an unchanged total produce no extra `usage` event;
  - for one response with cached input, the `usage` event's token fields follow A2's token definitions.
- **AC-7** (live) A two-turn `ahl run` on `openai/gpt-6-sol` meets all of the following:
  - turn 1's prompt contains a random nonce and asks the agent to remember it without writing it to disk, and to create a file with a tool. Turn 2 asks for the nonce;
  - turn 2's assistant output contains the nonce;
  - both turns share one session id;
  - no request is rejected because of `store` or `previous_response_id`: no such 400 error appears in stderr or the trace;
  - `totals.cost_usd_key_delta` > 0.
- **AC-8** (live) A one-turn `ahl run` with a mounted fixture skill whose `SKILL.md` holds a marker string, and a prompt that asks for that skill, produces an assistant message containing the marker.
- **AC-9** (unit) `README.md` and `config.example.yaml` document the Codex harness. A test asserts `codex` appears in both.

## Test plan

- Unit tests follow the existing adapter tests in `tests/test_harnesses.py` and `tests/test_openrouter.py`.
- The live tier records fixtures under `tests/fixtures/headless/codex/`.

## Risks and open questions

- **Stateless Responses API.** OpenRouter's Responses API keeps no state between requests. Start with a live spike: one prompt with a nonce, one tool call, one resumed turn that must reproduce the nonce. The spike also checks web search through OpenRouter and the skill directory. If Codex cannot work through OpenRouter, stop and report `BLOCKED on AC-7` with the evidence. Do not switch to a different API, provider or harness.
- **Codex config keys and flags change between releases.** Pin the version and test against it only.

## Evidence required in the PR

- The pinned Codex version and the `config.toml` keys used.
- The full turn-1 and turn-2 commands.
- The skill directory used.
- Whether web search works through OpenRouter, and whether `webfetch` is covered by the web-search switch.
- The `token_count` deduplication method.
- The AC-7 and AC-8 commands with abridged `result.json` and cost.
- The spike result.
