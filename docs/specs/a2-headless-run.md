# A2: Headless `ahl run` for Claude Code and OpenCode

Repo: slolab/agent-harness-lab · Needs: A1 · Roadmap decisions: 1, 2, 5, 7, 10, 11 ([roadmap](https://github.com/slolab/biotope-bench/blob/main/docs/roadmap.md))

## Goal and why

Callers such as biotope-bench run many harness sessions unattended and compare them across harnesses. `ahl up` needs a human at the shell. After A2, `ahl run` runs one or more prompt files as turns of one native session and exits with a code a script can act on. It leaves the raw harness output, a trace in one schema for every harness, and a `result.json` with status, reason, timings, tokens and the OpenRouter cost of each turn. Claude Code and OpenCode get headless drivers. `ahl up` stays the interactive shell.

## Scope

- `ahl run`: flags, exit codes, run directory, `result.json` with key-based cost, and new `session.json` fields. Unique container names, also for `ahl up`, and teardown in every terminal state.
- A normalized `trace.jsonl` with a JSON Schema and token definitions.
- Headless drivers for Claude Code and OpenCode, including OpenCode web denial and OpenRouter provider routing.

## Non-goals

- Codex (A3), and headless drivers for Gemini, agy, Claude Science and DeepSeek.
- Parallel sessions, retries, batch orchestration and budget caps. Callers do these.
- Parsing traces while a session runs, restricting egress, truncating trace content, and fixing file ownership after `ahl up`.

## Interfaces

**Command.** `ahl run -c CONFIG --turn FILE [--turn FILE …] [--env-file PATH] [--runs-dir DIR] [--name NAME] [--timeout SECONDS] [--build/--no-build]`

- Turn *n* is the *n*-th `--turn` file, delivered to the harness byte for byte. `--timeout` applies to each turn; the default is 3600.
- `--env-file`, `--runs-dir`, `--name`, `--build` and path resolution behave as in `ahl up` (A1). A `--name` whose run directory exists, and a harness without a headless driver, are usage errors (exit 2).

**Exit codes.** After a turn that does not complete, the remaining turns are skipped. When several rows apply, precedence is timeout, interrupted, error, failed.

| Exit | Run `status` | Reason codes | When |
|---|---|---|---|
| 0 | `completed` | — | Every turn completed |
| 1 | `failed` | `harness_exit`, `harness_reported_error`, `no_assistant_output`, `provider_error` | The harness failed in a turn |
| 2 | — | — | Config or usage error. No `result.json` is written |
| 3 | `error` | `infra` | Image build or container start failed, `docker exec` exited 125–127, the Docker daemon reported an error, or the container is gone |
| 124 | `timeout` | `timeout` | A turn exceeded `--timeout` |
| 130 | `interrupted` | `interrupted` | Ctrl-C (SIGINT) |

`harness_exit`: the harness exited non-zero. `harness_reported_error`: it exited 0 but reported an error. `no_assistant_output`: the turn produced no assistant message. `provider_error`: the output shows a provider or API error such as HTTP 429 or 5xx. Provider-error detection is best effort; a missed one is reported under the other codes.

**Run directory.** Written in every terminal state except exit 2. Its files end up owned by the calling user; if the container is already gone, AHL prints a warning instead.

```
<run dir>/
  session.json   result.json   trace.jsonl   trace.json (native parse, format unchanged)
  turns/<n>/prompt.md                            copy of every turn file
  turns/<n>/stdout.jsonl, turns/<n>/stderr.log   raw output of every started turn
  <harness home>/  workspace/                    native harness state, and the workspace, as today
```

**`session.json`** keeps A1's fields, adds `"mode": "headless"` in `ahl run`, and adds for both `ahl run` and `ahl up`:

- `container`: the Docker container name, unique per invocation. AHL removes the container in every terminal state, and a leftover container never blocks a new run. If AHL is killed with SIGKILL, the container may keep running, and `docker rm -f <container>` removes it.
- `permissions.applied`: the existing field, now also listing OpenCode's web denials.
- `model_parameters.unsupported`: configured `model.parameters` keys the harness cannot apply, e.g. `["provider"]`; empty when all were applied. Each also prints a warning; the run goes on.

**`result.json`**

```json
{"run_id": "…", "harness": "opencode", "provider": "openrouter", "model": "deepseek/deepseek-v4.1-flash",
 "status": "failed", "reason": {"code": "harness_exit", "message": "opencode exited with code 1"}, "session_id": "…",
 "turns": [
   {"index": 1, "prompt_file": "turns/1/prompt.md", "prompt_sha256": "…", "started_at": "…", "ended_at": "…",
    "wall_clock_seconds": 812.4, "exit_code": 0, "status": "completed", "reason": null, "session_id": "…",
    "key_usage": {"before": {"usd": 12.3456, "at": "…"}, "after": {"usd": 12.4012, "at": "…", "settled": true}, "delta_usd": 0.0556}},
   {"index": 2, "…": "…", "exit_code": 1, "status": "failed", "reason": {"code": "harness_exit", "message": "…"}},
   {"index": 3, "prompt_file": "turns/3/prompt.md", "prompt_sha256": "…", "status": "skipped",
    "reason": {"code": "skipped_after_failure", "message": "turn 2 failed"}, "started_at": null, "…": null}],
 "totals": {"wall_clock_seconds": 1210.7, "cost_usd_key_delta": 0.0731, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0},
 "warnings": []}
```

- Turn `status` is `completed`, `failed`, `timeout`, `error`, `interrupted` or `skipped`. The run's `status` and `reason` are those of the first turn that did not complete, or `completed` and null. A failure before turn 1 makes the run `error` with `infra`, and every turn `skipped`.
- `reason` is null exactly when the status is `completed`, otherwise `{code, message}` with a code from the table, or `skipped_after_failure` for a skipped turn. `message` is free text. Every turn is listed; a skipped turn has null in every field except `index`, `prompt_file`, `prompt_sha256`, `status` and `reason`.
- `key_usage` (only for `provider: openrouter`, otherwise null) holds readings of the key's cumulative usage before and after each started turn, in every terminal state. After a turn, AHL waits a bounded time for the usage to settle: risen above `before`, with two consecutive readings equal.
  - Settled: `settled: true` and `delta_usd = after − before`. Risen but not settled, or the wait ended by a second Ctrl-C: `settled: false`, delta from the last reading.
  - Never rose: `delta_usd: null`, `settled: false`, warning `usage_not_updated`. A failed read: `delta_usd: null`, warning `usage_read_failed`. Neither fails the run.
- `totals.cost_usd_key_delta` sums the started turns' deltas, and is null if any is null. Each token total sums that field over the `usage` events in `trace.jsonl`; it is null if any event has null there, and 0 without `usage` events.
- `warnings` entries are `{code, turn, message}`; `turn` is null for run-level warnings.

**`trace.jsonl`.** One event per line. The JSON Schema ships in the package as `ahl/schemas/trace_event.schema.json`, and `docs/trace-schema.md` documents it. Unknown values are null, never guessed. Every event has `seq` (int from 0), `type`, `session` (native session id or null), `agent` (`"main"` or a subagent id), `turn` (AHL turn from 1, or null) and `ts` (ISO-8601 or null).

| Type | Fields |
|---|---|
| `message` | `role` (`user`, `assistant`, `system`), `text`, `reasoning` (string or null) |
| `tool_call` | `id` (string or null), `tool` (native name), `input` (object or string), `output` (string or null), `is_error` (bool or null). Optional: `output_truncated` (bool), `output_original_length` (int) |
| `usage` | `model`, `response_id` (string or null), `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens` (int or null), `cost_usd` (number or null, as the harness reports it) |
| `error` | `message` |

- `turn` comes from the turn windows in `result.json` by timestamp; an event without one takes the previous event's turn. Each API response is counted once across `usage` events, subagents included. The first `user` message of a turn contains that turn's prompt, stripped of leading and trailing whitespace.
- AHL writes full tool outputs and never writes `output_truncated` or `output_original_length`. They exist so that callers' shortened copies still validate.
- **Token definitions.** `input_tokens`: input neither read from nor written to cache. `cache_read_tokens`, `cache_write_tokens`: input read from, or written to, cache. `output_tokens`: output as billed, reasoning included, also where the harness stores reasoning separately, as OpenCode does. `reasoning_tokens`: the reported reasoning subset of `output_tokens`, or null. `docs/trace-schema.md` documents each harness's conversion.

## Acceptance criteria

- **AC-1** (unit) With Docker stubbed, `ahl run` takes the flags above with their defaults, and a relative `--turn` resolves against the working directory. Each exit code arises in its situation with its run status and reason: `docker exec` exit 125–127, a daemon error and a vanished container each give 3 and `infra`; a harness exit 1 gives `harness_exit`; a recorded HTTP 429 gives `provider_error`. A taken `--name` and a harness without a driver give exit 2 and no `result.json`.
- **AC-2** (unit) In every terminal state other than exit 2, the run directory holds the files above, and `session.json` has `mode`, `container` and `model_parameters`. Status and reason follow the rules above. A three-turn run whose turn 2 fails lists turn 3 as `skipped` with `skipped_after_failure`, and the run takes turn 2's status and reason. SIGINT during a turn gives exit 130, `interrupted` for that turn and the run, and a written `result.json`.
- **AC-3** (unit, docker) Containers. Unit: `ahl run` and `ahl up` record the container name in `session.json`; two invocations with the same `--name` in different runs directories get different names; the container is removed in every terminal state. Docker, with a stub driver that calls no model: a turn outlasting `--timeout 5` exits 124 with `timeout` for the turn and the run, and the container named in `session.json` is gone. An `ahl run --name r` killed with SIGKILL mid-turn leaves its container running; a second `ahl run --name r` into another runs directory exits 0, and `docker rm -f` on the first run's recorded name removes the leftover.
- **AC-4** (docker) After an `ahl run` whose stub driver writes into the workspace and the harness home, every file and directory under the run directory belongs to the calling user's uid and gid, and `rm -rf` of it as that user succeeds. Tested on Linux.
- **AC-5** (unit) Key usage, with HTTP and the clock mocked: a settled delta is correct; the wait ends once the value has risen and settled; a value that never rises within the bound gives null, `settled: false` and `usage_not_updated`; a failed read gives null and `usage_read_failed` and the run still succeeds; failed, timed-out and interrupted turns get `after`; one null delta makes `totals.cost_usd_key_delta` null; a non-OpenRouter provider gives `key_usage: null`.
- **AC-6** (unit) Driver setup, checked on the generated commands and seeded config:
  - both harnesses: no session can wait for input, since tools run without permission prompts and the ask-user tool (Claude `AskUserQuestion`, OpenCode `question`) is denied; turn 2 resumes turn 1's session id;
  - Claude: skills stay enabled; with `provider: openrouter` every model alias resolves to the configured model; `model.parameters.provider` gives a warning and `model_parameters.unsupported: ["provider"]`;
  - OpenCode: the main and small model are the configured provider's model id, as `ahl up` uses it. With `provider: openrouter`, `model.parameters.provider` reaches OpenRouter's routing options verbatim and `unsupported` is empty; with another provider, e.g. `anthropic`, no OpenRouter routing is set;
  - OpenCode `permissions.deny: [websearch, webfetch]` is applied in `ahl run` and `ahl up` and listed in `permissions.applied`. Existing `ahl up` tests pass, except the OpenCode case of `test_unsupported_policy_warns_launches_and_records_gap`, which changes for this reason.
- **AC-7** (unit) Driver status on recorded outputs. Claude: exit 0 with a successful result is `completed`; exit 0 with an error result is `harness_reported_error`. OpenCode: non-zero exit gives `harness_exit`; exit 0 with an error event gives `harness_reported_error`; exit 0 with no assistant message gives `no_assistant_output`; otherwise `completed`.
- **AC-8** (unit) On fixtures recorded by AC-9: every `trace.jsonl` line validates against the schema; each turn's first `user` message contains its prompt; `tool_call` events carry their outputs; for one cached response per harness, the token fields equal values computed by hand under the definitions; one OpenCode response with non-zero reasoning has `output_tokens` including it. No fixture contains `sk-or-`. The schema file and `docs/trace-schema.md` list the same event types and fields.
- **AC-9** (live) Two-turn smoke runs: Claude Code on `anthropic/claude-sonnet-5`, and OpenCode on `deepseek/deepseek-v4.1-flash` with a provider pin. Turn 1 gives a random nonce to remember without writing it to disk, and asks for a file created with a tool; turn 2 asks for the nonce. For each: turn 2's assistant output contains the nonce; both turns report one session id and are `completed`; `totals.cost_usd_key_delta` > 0. Claude runs as root with no permission prompt or refusal, and every `usage` event names the same model. OpenCode's token totals are non-zero and equal the per-message sums in its database after the reasoning conversion.
- **AC-10** (live) For both harnesses, the AC-9 scenario plus an instruction to ask the user a clarifying question ends within `--timeout` without waiting for input, and `trace.jsonl` has an `assistant` message for the turn. `completed` passes. `failed` passes only when the reason is the denial of the ask-user tool; `provider_error`, `infra` and startup failures fail the test.

## Freedom to operate

The driver protocol and its method names; how the prompt reaches the harness; the exact harness flags, environment and permission keys; which native files feed the trace and how they are parsed and deduplicated; the key-usage poll interval and bound; container name format; test layout, stub drivers and fixture format.

## Design sketch (non-binding)

- Run like `ahl up`, but start the container detached and `docker exec -i` each turn with the prompt on stdin. Teardown after every terminal state: final key read, `chown -R` of the run directory's read-write mounts to the caller from inside the container, container removal. Container name `ahl-<run id>-<8 hex>`.
- Claude: `claude -p --output-format stream-json --verbose --model …`, `--disallowedTools AskUserQuestion`, `--resume` from turn 2, no `--bare` (it disables skills). Root: try `IS_SANDBOX=1`, else managed-settings allow rules with `--permission-mode dontAsk`. Aliases: `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`. Trace from the project JSONL files, subagents included.
- OpenCode: `opencode run --format json -m <model_id(config)>`, `--session` from turn 2. Set every permission key of the pinned version explicitly, since `ask` behaviour varies. Usage from `opencode.db` (`message` and `part` tables), one `usage` event per assistant message, not from stdout (opencode#26855). Native output excludes reasoning.
- Key usage: `GET https://openrouter.ai/api/v1/key` `data.usage`, e.g. every 10 s for up to 120 s.

## Risks and open questions

- Whether Claude Code honours `IS_SANDBOX=1` as root is unverified. Its `total_cost_usd` is unreliable for non-Anthropic ids, which is why cost comes from the key. OpenRouter usage may lag; the settling rule is best effort, and raw readings are kept so callers can recompute.
- If OpenCode cannot read the prompt from stdin, an argument is limited to 128 KiB on Linux. OpenCode's headless quirks vary by version; test only the pinned one.

## Evidence required in the PR

- The Claude root mechanism, how OpenCode receives the prompt, and the OpenCode permission keys used.
- The AC-9 and AC-10 commands with abridged `result.json` and cost, the committed fixtures, and `uv run pytest -m docker` output for AC-3 and AC-4.
- `README.md` documents `ahl run`, its exit codes, status and reason codes, and its run directory.
