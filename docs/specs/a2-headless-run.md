# A2: Headless `ahl run` for Claude Code and OpenCode

Repo: slolab/agent-harness-lab · Needs: A1 · Roadmap decisions: 1, 2, 5, 7, 10, 11 ([roadmap](https://github.com/slolab/biotope-bench/blob/main/docs/roadmap.md))

## Goal

`ahl run` executes a harness session without a human. It takes one or more prompt files, runs each as a turn in the same native session, and exits with a meaningful code. It leaves the raw harness output, a normalized trace in one schema for every harness, and a `result.json` with status, reason, timings and the OpenRouter cost of each turn. Claude Code and OpenCode get headless drivers. `ahl up` keeps working as the interactive shell.

## Scope

- The `ahl run` command, its run-directory layout and its exit codes.
- Unique container names, for `ahl run` and `ahl up`.
- Returning ownership of the run directory to the calling user after `ahl run`.
- An optional headless-driver part of the adapter protocol.
- Drivers for Claude Code and OpenCode.
- A normalized trace (`trace.jsonl`), its schema and its token definitions.
- `result.json`, including cost from the OpenRouter key's usage.
- Mapping web-tool denials onto OpenCode's permissions.
- OpenRouter provider routing for OpenCode.

## Non-goals

- Codex (A3), and headless drivers for Gemini, agy, Claude Science and DeepSeek.
- Parallel sessions, retries and batch orchestration. Callers such as biotope-bench do these.
- Budget caps. Per-turn wall-clock timeouts are in scope; spending limits are not.
- Parsing traces live while a session runs.
- Restricting network egress.
- Truncating trace content. AHL writes full tool outputs.
- Fixing file ownership after `ahl up`.

## Design and interfaces

### Command

```
ahl run -c CONFIG --turn FILE [--turn FILE ...]
        [--env-file PATH] [--runs-dir DIR] [--name NAME]
        [--timeout SECONDS] [--build/--no-build]
```

- `--turn` is repeatable and required at least once. Turn *n* is the *n*-th file. AHL delivers the file's bytes unchanged.
- `--timeout` applies to each turn. The default is 3600.
- `--env-file`, `--runs-dir`, `--name` and `--build` behave as in `ahl up` (A1). Command-line paths, including `--turn`, resolve against the current working directory (A1).
- A `--name` whose run directory already exists is a usage error (exit 2).

**Exit codes**

| Code | Run status | Meaning |
|---|---|---|
| 0 | `completed` | Every turn completed |
| 1 | `failed` | The harness failed in a turn, including provider errors. Later turns are skipped |
| 2 | — | Config or usage error, including a harness without a headless driver. No `result.json` is written |
| 3 | `error` | Infrastructure error. Later turns are skipped |
| 124 | `timeout` | A turn exceeded `--timeout`. Later turns are skipped |
| 130 | `interrupted` | Interrupted with Ctrl-C. Later turns are skipped |

**Turn outcome.** The first matching rule decides:

- The turn exceeded `--timeout`: `timeout`, exit 124.
- Ctrl-C: `interrupted`, exit 130.
- `docker exec` exited 125, 126 or 127, the Docker daemon reported an error, or the container is no longer running: `error` with reason `infra`, exit 3. Image build and container start failures are also `infra`.
- The driver reports the turn as failed: `failed`, exit 1. The reason code is `provider_error` when the driver finds a provider or API error in the harness output (HTTP 429 or 5xx, rate limit). This detection is best effort and driver-specific. Otherwise the code is `harness_exit`, `harness_reported_error` or `no_assistant_output`, as the driver reports.

### Execution

1. Prepare the run exactly as `ahl up` does: config, run dir, workspace, seeding, permissions, capabilities, packages. Copy every turn file to `turns/<n>/prompt.md`.
2. Start the container detached (`sleep infinity`), then run setup commands with `docker exec`.
3. For each turn, run the driver's command with `docker exec -i`. AHL writes `turns/<n>/prompt.md` to its stdin, and captures stdout to `turns/<n>/stdout.jsonl` and stderr to `turns/<n>/stderr.log`.
   - A turn that exceeds the timeout is killed.
   - Turn 2 onwards resume the native session id returned by turn 1.
   - After a turn that did not complete, the remaining turns are not run.
4. Teardown runs in every terminal state, including failure, timeout, infrastructure error and Ctrl-C:
   - finish the key-usage read after the last started turn (see "Key usage");
   - `docker exec <container> chown -R <uid>:<gid>` over every read-write mount under the run directory, with the calling user's uid and gid, so the caller can delete the run directory without root. On macOS this is harmless. If the container is already gone, AHL prints a warning instead;
   - remove the container.
5. Parse traces and write `result.json`, in every terminal state except exit 2.

### Container name

- `ahl run` and `ahl up` name the container `ahl-<run id>-<8 random hex chars>`, and record the name in `session.json` as `container`.
- A container left over from an earlier invocation never blocks a new run.

### Run directory

```
<run dir>/
  session.json        as for ahl up, plus "mode": "headless"
  result.json
  trace.json          existing native parse (unchanged format)
  trace.jsonl         normalized events (schema below)
  turns/<n>/prompt.md       copy of the turn file, for every turn
  turns/<n>/stdout.jsonl    raw harness stdout, for every started turn
  turns/<n>/stderr.log      for every started turn
  workspace/ …        as today
```

`session.json` also gains, for `ahl up` and `ahl run`:

- `container`: the container name;
- `model_parameters`: `{"unsupported": [...]}`, listing configured `model.parameters` keys the harness could not apply. Empty when all were applied.

### Driver protocol

This is an optional part of `HarnessAdapter`, defined in `src/ahl/harnesses/base.py`. Adapters without a driver make `ahl run` exit 2 with a message naming the harness. The exact method names are left to the implementer. The driver must provide:

- the command for a turn, given the session id to resume (`None` for turn 1). The prompt arrives on stdin;
- the extra environment for headless mode;
- the native session id, extracted from a turn's output or from harness state;
- the turn's status (`completed` or `failed`) and, when failed, the reason code, from its exit code and output;
- the parser from the harness's native files to `trace.jsonl` events.

### Claude Code driver

- **Command:** `claude -p --output-format stream-json --verbose --model <model> --disallowedTools AskUserQuestion` plus permission bypass, and `--resume <session id>` from turn 2 onwards. The prompt arrives on stdin.
  - `--bare` is **not** used, because it disables skills.
- **Model aliases:** for `provider: openrouter`, set `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL` to the configured model, alongside the existing variables. Without this, background and subagent calls could go to a different model.
- **Running as root:** containers run as root, and Claude Code refuses `--dangerously-skip-permissions` as root outside a recognised sandbox.
  - First try `IS_SANDBOX=1`.
  - If that does not work, use managed-settings allow rules covering every tool with `--permission-mode dontAsk`.
  - Either way, the result must be a session that runs tools without prompts. Record which mechanism was used in the PR.
- **Session id:** from the `system/init` or `result` event.
- **Status:** a turn is `completed` when the exit code is 0 and the `result` event has `is_error: false`. A non-zero exit gives `harness_exit`. Exit 0 with `is_error: true` gives `harness_reported_error`. Either becomes `provider_error` when the output shows a provider or API error.
- **Provider routing:** Claude Code cannot pass OpenRouter's `provider` body field. A configured `model.parameters.provider` prints a warning and is listed in `model_parameters.unsupported`.

### OpenCode driver

- **Command:** `opencode run --format json -m openrouter/<model>`, plus `--session <session id>` from turn 2 onwards. The prompt comes from stdin. If the pinned version does not read its message from stdin, the driver passes stdin as one argument inside the container (`sh -c 'exec opencode run … "$(cat)"'`).
- **Permissions:** the seeded `opencode.json` sets each permission key of the pinned version explicitly, because behaviour on `ask` varies between versions. The adapter keeps that list of keys.
  - All keys are `allow`, except `question` (the ask-the-user tool), which is `deny` in `ahl run`.
  - A web deny from `permissions.deny` sets `webfetch` and `websearch` to `deny`. `session.json` lists them under `permissions.applied`.
  - This seeding applies to `ahl up` as well. OpenCode web denials become supported there too.
- **Small model:** `small_model` is set to the run's model, so title and summary calls do not go to a second model.
- **Provider routing:** `model.parameters.provider` is copied verbatim into `provider.openrouter.models["<model>"].options.provider` in `opencode.json`. Example: `{"order": ["deepinfra"], "allow_fallbacks": false}`.
- **Status:** a turn is `failed` if the exit code is non-zero (`harness_exit`), an `error` event appears in stdout (`harness_reported_error`), or the turn produced no assistant message (`no_assistant_output`). Any of these becomes `provider_error` when the error shows a provider or API error. Otherwise the turn is `completed`.
- **Usage source:** `opencode.db`, not stdout. `run --format json` can exit before its final `step_finish` event (opencode#26855). The current parser reads only session-level input and output totals from the `session` table. A2 extends it to read the `message` and `part` tables, with per-message tokens including cache reads and writes.

### Normalized trace (`trace.jsonl`)

One JSON object per line, in order. The schema is a JSON Schema file at `src/ahl/schemas/trace_event.schema.json`, documented in `docs/trace-schema.md`. Unknown values are `null`, never guessed.

**Source per harness.** Captured stdout is kept but is not the trace source.

| Harness | Source |
|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` in the run's Claude home, including subagent files |
| OpenCode | `opencode.db`, `message` and `part` tables |
| Codex (A3) | rollout JSONL files |

**Fields common to every event:**

| Field | Type | Meaning |
|---|---|---|
| `seq` | int | Position in the file, starting at 0 |
| `type` | string | `message`, `tool_call`, `usage` or `error` |
| `session` | string or null | Native session id |
| `agent` | string | `"main"`, or a subagent id |
| `turn` | int or null | AHL turn number (1-based) where attributable |
| `ts` | string or null | ISO-8601 timestamp |

- `turn` is assigned from each turn's `started_at` and `ended_at` in `result.json`, by the event's timestamp. An event without a timestamp takes the turn of the event before it.
- `agent` is `"main"` for the main session. Events from a subagent's own file (Claude) or child session (OpenCode) carry that subagent's id.

**Fields by event type:**

| Type | Fields |
|---|---|
| `message` | `role` (`user`, `assistant` or `system`), `text` (string), `reasoning` (string or null) |
| `tool_call` | `id` (string or null), `tool` (native tool name), `input` (object or string), `output` (string or null, full length), `is_error` (bool or null). Optional: `output_truncated` (bool), `output_original_length` (int) |
| `usage` | `model` (string or null), `response_id` (string or null), `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens` (each int or null), `cost_usd` (number or null, as the harness reports it) |
| `error` | `message` (string) |

- AHL never truncates and never writes `output_truncated` or `output_original_length`. The fields exist so that shortened copies of a trace made by callers still validate.
- Each API response is counted once in the `usage` events. Claude responses are deduplicated as the existing Claude parser already does. OpenCode writes one `usage` event per assistant message, from the message's tokens, with `response_id` set to the OpenCode message id. One message can span several API calls; its tokens are not also counted from its parts.
- The first `user` message of each turn contains that turn's prompt text with leading and trailing whitespace stripped. Harnesses may trim or wrap the prompt, so this is containment, not equality.

**Token definitions.** Every parser converts to these, and `docs/trace-schema.md` documents them together with each harness's conversion:

| Field | Meaning |
|---|---|
| `input_tokens` | Input tokens not served from cache and not written to cache |
| `cache_read_tokens` | Input tokens served from cache |
| `cache_write_tokens` | Input tokens written to cache |
| `output_tokens` | Output tokens as billed |
| `reasoning_tokens` | The reported reasoning subset of `output_tokens`, or null |

Where a harness or provider counts cached tokens inside its input figure, the adapter subtracts them.

### `result.json`

```json
{
  "run_id": "…", "harness": "opencode", "provider": "openrouter", "model": "deepseek/deepseek-v4.1-flash",
  "status": "failed",
  "reason": {"code": "harness_exit", "message": "opencode exited with code 1"},
  "session_id": "…",
  "turns": [
    {
      "index": 1, "prompt_file": "turns/1/prompt.md", "prompt_sha256": "…",
      "started_at": "…", "ended_at": "…", "wall_clock_seconds": 812.4,
      "exit_code": 0, "status": "completed", "reason": null, "session_id": "…",
      "key_usage": {
        "before": {"usd": 12.3456, "at": "…"},
        "after": {"usd": 12.4012, "at": "…", "settled": true},
        "delta_usd": 0.0556
      }
    },
    {
      "index": 2, "prompt_file": "turns/2/prompt.md", "prompt_sha256": "…",
      "started_at": "…", "ended_at": "…", "wall_clock_seconds": 398.3,
      "exit_code": 1, "status": "failed", "session_id": "…",
      "reason": {"code": "harness_exit", "message": "opencode exited with code 1"},
      "key_usage": {
        "before": {"usd": 12.4012, "at": "…"},
        "after": {"usd": 12.4187, "at": "…", "settled": true},
        "delta_usd": 0.0175
      }
    },
    {
      "index": 3, "prompt_file": "turns/3/prompt.md", "prompt_sha256": "…",
      "started_at": null, "ended_at": null, "wall_clock_seconds": null,
      "exit_code": null, "status": "skipped", "session_id": null, "key_usage": null,
      "reason": {"code": "skipped_after_failure", "message": "turn 2 failed"}
    }
  ],
  "totals": {
    "wall_clock_seconds": 1210.7, "cost_usd_key_delta": 0.0731,
    "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0
  },
  "warnings": []
}
```

- **Status.** Turn `status` is one of `completed`, `failed`, `timeout`, `error`, `interrupted` or `skipped`. Run `status` is one of the first five.
- **Reason.** Every turn and the run carry `reason`: `null` when completed, otherwise `{"code": "...", "message": "..."}`. The `message` is free text.

  | Code | Status |
  |---|---|
  | `harness_exit` | `failed`: the harness exited non-zero |
  | `harness_reported_error` | `failed`: the harness exited 0 but reported an error |
  | `provider_error` | `failed`: the harness output shows a provider or API error |
  | `no_assistant_output` | `failed`: the turn produced no assistant message |
  | `timeout` | `timeout` |
  | `infra` | `error` |
  | `interrupted` | `interrupted` |
  | `skipped_after_failure` | `skipped`: an earlier turn did not complete |

- Every turn is listed. Turns that did not run have `status: skipped`; their fields other than `index`, `prompt_file`, `prompt_sha256`, `status` and `reason` are `null`.
- The run's `status` and `reason` are those of the first turn that did not complete, or `completed` and `null`. A failure before the first turn (image build, container start) makes the run `error` with reason `infra`, and every turn `skipped`.
- `warnings` lists non-fatal problems as `{"code": "...", "turn": n, "message": "..."}`, for example `usage_not_updated`. `turn` is null for run-level warnings.

**Key usage** (only for `provider: openrouter`, otherwise `null`):

- Read `data.usage` from `GET https://openrouter.ai/api/v1/key`, using the run's key, before each turn.
- After each started turn, in every terminal state including failure, timeout and Ctrl-C, read it every 10 s for up to 120 s.
  - Settled: the value is greater than `before` and two consecutive reads agree. Then `settled: true` and `delta_usd` is `after - before`.
  - If the value never exceeds `before`: `delta_usd: null`, `settled: false`, warning `usage_not_updated`.
  - If it exceeds `before` but never settles: `settled: false`, and `delta_usd` uses the last read.
  - A second Ctrl-C during this wait stops it, with `settled: false`.
- If a read fails, `delta_usd` is `null` and a `usage_read_failed` warning is recorded. The run does not fail because of it.
- `totals.cost_usd_key_delta` is the sum of the started turns' deltas, and `null` if any of them is `null`.
- Token totals are sums over the `usage` events in `trace.jsonl`. A total is `null` if any `usage` event has `null` in that field, and 0 if there are no `usage` events.

## Acceptance criteria

- **AC-1** (unit) `ahl run` accepts the flags above with the stated defaults. A relative `--turn` path resolves against the working directory. Exit codes 0, 1, 2, 3, 124 and 130 occur for the matching situations, each with the run status from the table. A `--name` whose run directory exists exits 2. Docker is stubbed at the subprocess boundary.
- **AC-2** (unit) Infrastructure versus harness failure, with `docker exec` stubbed:
  - `docker exec` exit 125, 126 or 127, a daemon error, and a container that is no longer running each give exit 3 and reason `infra`;
  - a harness exit code 1 gives exit 1 and reason `harness_exit`;
  - a harness exit with an HTTP 429 error in its recorded output gives exit 1 and reason `provider_error`.
- **AC-3** (unit) In every terminal state (completed, failed, timeout, error, interrupted), the run directory contains `session.json` (with `"mode": "headless"` and `container`), `result.json`, `trace.json`, `trace.jsonl`, `turns/<n>/prompt.md` for every turn, and `turns/<n>/{stdout.jsonl,stderr.log}` for each started turn. After exit 2, no `result.json` exists.
- **AC-4** (unit) `result.json` status and reason:
  - every turn and the run carry `status` and `reason`, with `reason` null exactly when the status is `completed`, and otherwise a code from the table;
  - in a three-turn run whose turn 2 fails, turn 3 is listed with `status: skipped` and reason `skipped_after_failure`, and the run has turn 2's status and reason;
  - SIGINT during a turn gives exit 130, `status: interrupted` and reason `interrupted` for that turn and the run, and `result.json` is written.
- **AC-5** (docker) Timeout: a stub test driver whose turn outlasts `--timeout 5` makes `ahl run` exit 124. The container named in `session.json` no longer exists, and `result.json` shows `status: timeout` and reason `timeout` for that turn and for the run.
- **AC-6** (docker) File ownership: after an `ahl run` whose stub driver writes files into the workspace and the harness home, every file and directory under the run directory is owned by the calling user's uid and gid, and `rm -rf <run dir>` as that user succeeds. Tested on Linux.
- **AC-7** (unit, docker) Container names:
  - unit tier: `ahl run` and `ahl up` name the container `ahl-<run id>-` plus 8 hex characters, record it in `session.json` as `container`, and two invocations with the same `--name` in different runs directories get different names;
  - docker tier: an `ahl run --name r` killed with SIGKILL during a turn leaves its container running. A second `ahl run --name r` into another runs directory exits 0, and the first container still exists.
- **AC-8** (unit) A harness without a headless driver makes `ahl run` exit 2 with a message naming the harness. `ahl up` behaves as before and the existing tests pass, with one stated change: the OpenCode case of `tests/test_permissions.py::test_unsupported_policy_warns_launches_and_records_gap` changes, because OpenCode now applies web denials.
- **AC-9** (unit) Claude driver:
  - the turn-1 command contains `-p`, `--output-format stream-json`, `--model <model>` and `--disallowedTools AskUserQuestion`, omits `--bare`, and has no prompt text in its arguments;
  - the turn-2 command adds `--resume <id>`;
  - with `provider: openrouter`, the environment sets all four model-alias variables to the configured model;
  - a config with `model.parameters.provider` prints a warning, and `session.json` has `model_parameters.unsupported` equal to `["provider"]`.
- **AC-10** (unit) OpenCode seeding:
  - the seeded config sets every key in the adapter's permission list explicitly. In `ahl run` all are `allow` except `question`, which is `deny`;
  - with `permissions.deny: [websearch, webfetch]` it sets `webfetch` and `websearch` to `deny`, and `session.json` lists both as applied;
  - `small_model` equals the run's model;
  - `model.parameters.provider` appears verbatim in the config's OpenRouter model options, and `model_parameters.unsupported` is empty.
- **AC-11** (unit) Driver status rules, on recorded outputs:
  - Claude: exit 0 with `is_error: false` is `completed`; exit 0 with `is_error: true` is `failed` with `harness_reported_error`;
  - OpenCode: a non-zero exit gives `harness_exit`; exit 0 with an `error` event gives `harness_reported_error`; exit 0 with no assistant message in `opencode.db` gives `no_assistant_output`; otherwise `completed`.
- **AC-12** (live) A Claude Code two-turn smoke run on `anthropic/claude-sonnet-5` meets all of the following:
  - turn 1's prompt contains a random nonce and asks the agent to remember it without writing it to disk, and to create a file with a tool. Turn 2 asks for the nonce;
  - turn 2's assistant output contains the nonce;
  - both turns report the same session id;
  - both turns are `completed`, with no permission prompt or refusal while running as root;
  - every `usage` event names the same model;
  - `totals.cost_usd_key_delta` > 0.
- **AC-13** (live) An OpenCode two-turn smoke run on `deepseek/deepseek-v4.1-flash` with a provider pin meets all of the following:
  - the same nonce test as AC-12 passes;
  - both turns report the same session id;
  - `totals.cost_usd_key_delta` > 0;
  - the token totals are non-zero and equal the sums of the per-message tokens in `opencode.db`.
- **AC-14** (unit) Parsing the fixtures committed from the AC-12 and AC-13 runs:
  - every line of `trace.jsonl` validates against the JSON Schema;
  - the first `user` message of each turn contains that turn's prompt text with leading and trailing whitespace stripped;
  - `tool_call` events carry their outputs;
  - for one response per harness with cached input, the `usage` event's token fields equal values computed by hand under the token definitions.
- **AC-15** (unit) Key-usage logic, tested with HTTP and the clock mocked:
  - the delta is computed correctly;
  - settling stops when the value exceeds `before` and two consecutive reads agree;
  - a value that never exceeds `before` within 120 s gives `delta_usd: null`, `settled: false` and warning `usage_not_updated`;
  - a failed read gives `delta_usd: null` and a warning, not a failed run;
  - failed, timed-out and interrupted turns still get `key_usage.after`;
  - `totals.cost_usd_key_delta` is `null` when one turn's delta is `null`;
  - a non-OpenRouter provider gives `key_usage: null`.
- **AC-16** (live) For both harnesses, a prompt that tells the agent to ask the user a clarifying question completes within the timeout without waiting for input, and `result.json` shows the turn as completed or failed, not timed out.
- **AC-17** (unit) `README.md` documents `ahl run`, its exit codes, its status and reason codes, and its run directory. `docs/trace-schema.md` documents the trace schema and the token definitions. A test checks that the schema file and the document list the same event types and fields.

## Test plan

- The unit tier stubs Docker at the `subprocess` boundary, like the `docker_stub` fixture in `tests/test_permissions.py`.
- AC-5, AC-6 and AC-7 use a test-only stub driver registered through monkeypatch, so the docker tier exercises the real container lifecycle without calling a model.
- The `docker` and `live` markers are registered by A1 and deselected by default.
- The live tier (`@pytest.mark.live`) needs `OPENROUTER_API_KEY`. It records its raw outputs as fixtures under `tests/fixtures/headless/<harness>/`: the Claude project JSONL files, OpenCode's `opencode.db` as the SQLite file, stdout, stderr and `result.json`. The unit tier then replays those fixtures. A unit test asserts that no fixture file contains `sk-or-`.

## Risks and open questions

- **Claude as root.** Whether `IS_SANDBOX=1` is honoured is unverified; the design above names the fallback.
- **Claude through OpenRouter:**
  - `result.total_cost_usd` is a local estimate and unreliable for non-Anthropic model ids. That is why cost comes from the key.
  - OpenRouter guarantees Claude Code compatibility only for Anthropic models, which matches roadmap decision 1.
- **OpenRouter usage lag.** Key usage may update with a delay. The settling rule is a best effort, and the raw readings are kept so callers can recompute.
- **Provider error detection** reads harness output and is best effort. A provider error it misses is reported as `harness_exit` or `harness_reported_error`.
- **OpenCode prompt as argument.** If the pinned OpenCode does not read stdin, the prompt becomes one argument, and Linux limits a single argument to 128 KiB.
- **OpenCode headless quirks** differ between versions. Pin the version (A1) and test against that version only.

## Evidence required in the PR

- The Claude root mechanism used.
- How the OpenCode driver receives the prompt (stdin or argument), and the permission keys listed for the pinned OpenCode version.
- The AC-12, AC-13 and AC-16 commands, with abridged `result.json` and cost.
- Output of `uv run pytest -m docker` covering AC-5 to AC-7.
- The fixture files committed.
