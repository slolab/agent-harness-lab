# A3: Codex harness

Repo: slolab/agent-harness-lab · Needs: A2 · Roadmap decisions: 1, 2, 10, 11 ([roadmap](https://github.com/slolab/biotope-bench/blob/main/docs/roadmap.md))

## Goal and why

By roadmap decision 1, `openai/*` models run in OpenAI's own harness, Codex, and by decision 2 every model goes through one OpenRouter key. AHL has no Codex harness. After A3, AHL runs the Codex CLI through OpenRouter, interactively (`ahl up`) and headlessly (`ahl run`), with skills, web-tool denial, the normalized trace and key-based cost on the same terms as Claude Code and OpenCode after A2.

## Scope

- A `codex` harness: image, config validation, seeding, skill wiring, web-search denial, a headless driver and trace parsing.
- A live spike before the rest of the work: a resumed turn through OpenRouter that must recall a nonce, web search through OpenRouter, and the skill directory the installed version reads.

## Non-goals

- Codex with non-OpenAI models, a direct OpenAI key or a ChatGPT login.
- OpenRouter provider routing for Codex, and MCP wiring, which no harness has yet.
- Switching to another API, provider or harness if Codex fails through OpenRouter. The implementer reports `BLOCKED on AC-n` instead.

## Interfaces

- **Config.** `harness: codex` requires `provider: openrouter` and an explicit `model`; anything else is a config error naming the problem. A configured `model.parameters.provider` prints a warning and is listed in `model_parameters.unsupported` (A2).
- **Image.** `agent-harness-lab:codex` follows A1's harness-version rule: `harness_version` pins Codex, otherwise the image installs the release current at build time, and no Dockerfile hard-codes a version. It carries `ahl.harness=codex`, and `session.json` records the installed Codex version (A1).
- **Run interfaces.** `ahl run` with Codex produces A2's exit codes, run directory, `result.json`, `session.json` fields and `trace.jsonl`. Codex `usage` events carry the `response_id` and `cache_write_tokens` that Codex reports, following A2's token definitions; a field Codex does not report is null.
- **Stateless API.** OpenRouter's Responses API keeps no state between requests. No Codex request may be rejected because of `store` or `previous_response_id`, and a resumed turn still has the earlier turns' context.
- **Web.** `permissions.deny` with `websearch` or `webfetch` disables Codex's web search, and `session.json` lists the operations under `permissions.applied`. If Codex has no separate fetch tool, `webfetch` is covered by the same switch; the PR says which. With the web allowed, web search either works through OpenRouter or the README says it does not.
- **Skills.** Mount-mode skills go into the user-skill directory that the installed Codex version reads. Delegated installs use Codex's agent identifier in the skills installer.

## Acceptance criteria

- **AC-1** (unit) A config with `harness: codex`, `provider: openrouter` and a model, as documented in `config.example.yaml`, loads through the real config loader. Any other provider, or a missing model, is a config error naming the problem.
- **AC-2** (unit, docker) The Codex Dockerfile hard-codes no Codex version. With `harness_version` set, the image installs exactly that version; without it, the current release. `session.json` records the installed version (docker: `codex --version` in the image).
- **AC-3** (unit) Seeded Codex state selects OpenRouter's Responses API, takes the credential from `OPENROUTER_API_KEY` without writing the key to disk, and lets headless runs execute tools without approval prompts. A web deny disables web search and is listed in `permissions.applied`. `model.parameters.provider` gives a warning and `model_parameters.unsupported: ["provider"]`.
- **AC-4** (unit) Headless driver, on recorded output: turn 2 resumes the session id reported in turn 1, and both turns use the configured model. Exit 0 is `completed` unless the turn ends in failure; a non-zero exit is `harness_exit`; exit 0 with a `turn.failed` event, or with an `error` event not followed by the turn completing, is `harness_reported_error`. A transient `error`, such as a stream reconnect, after which the turn completes, does not fail the turn.
- **AC-5** (unit) Mount-mode skills land in the installed version's user-skill directory, and delegated installs name Codex's installer agent.
- **AC-6** (unit) On fixtures recorded by AC-7: every `trace.jsonl` line validates against A2's schema; each turn's first `user` message contains its prompt, stripped of leading and trailing whitespace; each API response yields exactly one `usage` event, even where Codex repeats its usage records; for one response with cached input, the token fields follow A2's definitions. No fixture contains `sk-or-`.
- **AC-7** (live) A two-turn `ahl run` on `openai/gpt-6-sol`. Turn 1 gives a random nonce to remember without writing it to disk, and asks for a file created with a tool; turn 2 asks for the nonce. Turn 2's assistant output contains the nonce, both turns share one session id, no 400 error about `store` or `previous_response_id` appears in stderr or the trace, and `totals.cost_usd_key_delta` > 0.
- **AC-8** (live) A one-turn `ahl run` with a mounted fixture skill whose `SKILL.md` holds a marker string, and a prompt that asks for that skill, produces an assistant message containing the marker.

## Freedom to operate

`config.toml` keys and how the credential is supplied; the exact `codex exec` flags and their placement around `resume`; how the prompt reaches Codex; how rollout files are parsed and repeated usage records deduplicated; test layout and fixtures.

## Design sketch (non-binding)

- Per-run `CODEX_HOME` at `<run dir>/codex/` holding `config.toml`: an `openrouter` model provider with `base_url = "https://openrouter.ai/api/v1"` and `wire_api = "responses"`, a command-based auth that reads the environment (avoids "Unknown model" fallback metadata), approvals and sandbox off. The container is the sandbox.
- Turn 1 `codex exec --json --skip-git-repo-check <bypass> -m <model> -`; later turns `codex exec resume <id> -` with the same flags. Session id from `thread.started`.
- Trace from `CODEX_HOME/sessions/**/rollout-*.jsonl`, not stdout. `token_count` records carry cumulative and per-call usage but no response id, and repeat; emit one `usage` event per change in the cumulative total. `cached_input_tokens` is inside `input_tokens`, so subtract it; `reasoning_output_tokens` becomes `reasoning_tokens`. The turn's prompt is the `user_message` event, not the environment and AGENTS.md items before it.
- Current docs name `~/.agents/skills` and treat `~/.codex/skills` as legacy.

## Risks and open questions

- **Stateless Responses API.** Codex may assume server-side state that OpenRouter does not keep. The spike shows this early: one prompt with a nonce, one tool call, one resumed turn. If Codex cannot work through OpenRouter, report `BLOCKED on AC-7` with the evidence.
- Codex config keys and flags change between releases. Record the version the tests ran against; a caller that needs stability pins it with `harness_version`.

## Evidence required in the PR

- The spike result, the Codex version tested, the `config.toml` keys, and the full turn-1 and turn-2 commands.
- The skill directory, whether web search works through OpenRouter, and whether `webfetch` is covered by the web-search switch.
- The deduplication method, also documented in `docs/trace-schema.md`, and the AC-7 and AC-8 commands with abridged `result.json` and cost.
- `README.md` and `config.example.yaml` document the Codex harness.
