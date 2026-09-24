# A2: Headless `ahl run` for Claude Code and OpenCode

Status: draft
Repo: slolab/agent-harness-lab · Needs: A1 · Roadmap decisions: 1, 2, 5, 7, 10, 11 ([roadmap](https://github.com/slolab/biotope-bench/blob/main/docs/roadmap.md))

## Goal

`ahl run` executes a harness session without a human. It takes one or more prompt files, runs each as a turn in the same native session, and exits with a meaningful code. It leaves the raw harness output, a normalized trace in one schema for every harness, and a `result.json` with status, timings and the OpenRouter cost of each turn. Claude Code and OpenCode get headless drivers. `ahl up` keeps working as the interactive shell.

## Scope

- The `ahl run` command, its run-directory layout and its exit codes.
- An optional headless-driver part of the adapter protocol.
- Drivers for Claude Code and OpenCode.
- A normalized trace (`trace.jsonl`) and its schema.
- `result.json`, including cost from the OpenRouter key's usage.
- Mapping web-tool denials onto OpenCode's permissions.
- OpenRouter provider routing for OpenCode.

## Non-goals

- Codex (A3), and headless drivers for Gemini, agy, Claude Science and DeepSeek.
- Parallel sessions, retries and batch orchestration. Callers such as biotope-bench do these.
- Budget caps. Per-turn wall-clock timeouts are in scope; spending limits are not.
- Parsing traces live while a session runs.
- Restricting network egress.

## Design and interfaces

### Command

```
ahl run -c CONFIG --turn FILE [--turn FILE ...]
        [--env-file PATH] [--runs-dir DIR] [--name NAME]
        [--timeout SECONDS] [--build/--no-build]
```

- `--turn` is repeatable and required at least once. Turn *n* is the *n*-th file. Its content is delivered to the harness byte for byte.
- `--timeout` applies to each turn. The default is 3600.
- `--env-file`, `--runs-dir`, `--name` and `--build` behave as in `ahl up` (A1).

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Every turn completed |
| 1 | The harness reported failure in a turn. Later turns are skipped |
| 2 | Config or usage error, including a harness without a headless driver |
| 3 | Infrastructure error: image build, container start or `docker exec` failure |
| 124 | A turn exceeded `--timeout`. Later turns are skipped |

### Execution

1. Prepare the run exactly as `ahl up` does: config, run dir, workspace, seeding, permissions, capabilities, packages.
2. Start the container detached (`sleep infinity`), then run setup commands with `docker exec`.
3. For each turn, run the driver's command with `docker exec -i`, capturing stdout to `turns/<n>/stdout.jsonl` and stderr to `turns/<n>/stderr.log`.
   - A turn that exceeds the timeout is killed.
   - Turn 2 onwards resume the native session id returned by turn 1.
4. Always remove the container at the end, including after failure, timeout or Ctrl-C.
5. Always parse traces and write `result.json`, in every terminal state.

### Run directory

```
<run dir>/
  session.json        as for ahl up, plus "mode": "headless"
  result.json
  trace.json          existing native parse (unchanged format)
  trace.jsonl         normalized events (schema below)
  turns/<n>/prompt.md       copy of the turn file
  turns/<n>/stdout.jsonl    raw harness stdout
  turns/<n>/stderr.log
  workspace/ …        as today
```

### Driver protocol

This is an optional part of `HarnessAdapter`, defined in `src/ahl/harnesses/base.py`. Adapters without a driver make `ahl run` exit 2 with a message naming the harness. The exact method names are left to the implementer. The driver must provide:

- the command for a turn, given the prompt's container path and the session id to resume (`None` for turn 1);
- the extra environment for headless mode;
- the native session id, extracted from a turn's output or from harness state;
- the turn's status (`completed` or `failed`) from its exit code and output.

### Claude Code driver

- **Command:** `claude -p --output-format stream-json --verbose --model <model> --disallowedTools AskUserQuestion` plus permission bypass, and `--resume <session id>` from turn 2 onwards. The prompt arrives on stdin.
  - `--bare` is **not** used, because it disables skills.
- **Model aliases:** for `provider: openrouter`, set `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL` to the configured model, alongside the existing variables. Without this, background and subagent calls could go to a different model.
- **Running as root:** containers run as root, and Claude Code refuses `--dangerously-skip-permissions` as root outside a recognised sandbox.
  - First try `IS_SANDBOX=1`.
  - If that does not work, use managed-settings allow rules covering every tool with `--permission-mode dontAsk`.
  - Either way, the result must be a session that runs tools without prompts. Record which mechanism was used in the PR.
- **Session id and status:** the session id comes from the `system/init` or `result` event. A turn is `completed` when the exit code is 0 and the `result` event has `is_error: false`.

### OpenCode driver

- **Command:** `opencode run --format json -m openrouter/<model>`, plus `--session <session id>` from turn 2 onwards, then the prompt.
- **Permissions:** the seeded `opencode.json` sets every permission explicitly, all `allow`, because behaviour on `ask` varies between versions.
  - A web deny from `permissions.deny` maps to `webfetch` and `websearch` set to `deny`.
  - `session.json` lists them under `permissions.applied`.
- **Provider routing:** `model.parameters.provider` is copied verbatim into `provider.openrouter.models["<model>"].options.provider` in `opencode.json`. Example: `{"order": ["deepinfra"], "allow_fallbacks": false}`.
  - For harnesses that cannot pass provider routing, a configured `model.parameters.provider` is recorded as unsupported in `session.json` and a warning is printed. It is not silently dropped.
- **Usage source:** read usage from `opencode.db`, as the existing parser does, not from stdout. `run --format json` can exit before its final `step_finish` event (opencode#26855).

### Normalized trace (`trace.jsonl`)

One JSON object per line, in order. The schema is a JSON Schema file at `src/ahl/schemas/trace_event.schema.json`, documented in `docs/trace-schema.md`. Unknown values are `null`, never guessed.

**Fields common to every event:**

| Field | Type | Meaning |
|---|---|---|
| `seq` | int | Position in the file, starting at 0 |
| `type` | string | `message`, `tool_call`, `usage` or `error` |
| `session` | string or null | Native session id |
| `agent` | string | `"main"`, or a subagent id |
| `turn` | int or null | AHL turn number (1-based) where attributable |
| `ts` | string or null | ISO-8601 timestamp |

**Fields by event type:**

| Type | Fields |
|---|---|
| `message` | `role` (`user`, `assistant` or `system`), `text` (string), `reasoning` (string or null) |
| `tool_call` | `id` (string or null), `tool` (native tool name), `input` (object or string), `output` (string or null, full length), `is_error` (bool or null) |
| `usage` | `model`, `response_id`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens` (each int or null), `cost_usd` (number or null, as the harness reports it) |
| `error` | `message` (string) |

- Each API response appears once in the `usage` events, deduplicated as the existing Claude parser already does.
- The first `user` message of each turn is the verbatim prompt text.

### `result.json`

```json
{
  "run_id": "…", "harness": "opencode", "provider": "openrouter", "model": "deepseek/deepseek-v4.1-flash",
  "status": "completed | failed | timeout | error",
  "session_id": "…",
  "turns": [
    {
      "index": 1, "prompt_file": "turns/1/prompt.md", "prompt_sha256": "…",
      "started_at": "…", "ended_at": "…", "wall_clock_seconds": 812.4,
      "exit_code": 0, "status": "completed", "session_id": "…",
      "key_usage": {
        "before": {"usd": 12.3456, "at": "…"},
        "after": {"usd": 12.4012, "at": "…", "settled": true},
        "delta_usd": 0.0556
      }
    }
  ],
  "totals": {
    "wall_clock_seconds": 812.4, "cost_usd_key_delta": 0.0556,
    "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0
  }
}
```

**Key usage** (only for `provider: openrouter`, otherwise `null`):

- Read `data.usage` from `GET https://openrouter.ai/api/v1/key`, using the run's key, before each turn.
- After the turn, read it again until two reads 10 s apart agree, or for at most 60 s. `settled` records which of the two happened.
- If a read fails, `delta_usd` is `null` and a warning is recorded. The run does not fail because of it.
- Token totals come from the `usage` events in `trace.jsonl`.

## Acceptance criteria

- **AC-1** (unit) `ahl run` accepts the flags above with the stated defaults, and returns exit codes 0, 1, 2, 3 and 124 for the matching situations. Docker is stubbed at the subprocess boundary.
- **AC-2** (unit) In every terminal state (completed, failed, timeout, error), the run directory contains `session.json` (with `"mode": "headless"`), `result.json`, `trace.json`, `trace.jsonl` and `turns/<n>/{prompt.md,stdout.jsonl,stderr.log}` for each started turn.
- **AC-3** (docker) Timeout: a stub test driver whose turn outlasts `--timeout 5` makes `ahl run` exit 124. No container named `ahl-<run id>` remains, and `result.json` shows `status: timeout` for that turn and for the run.
- **AC-4** (unit) A harness without a headless driver makes `ahl run` exit 2 with a message naming the harness. `ahl up` behaves as before; the existing tests pass.
- **AC-5** (unit) The Claude driver's turn-1 command contains `-p`, `--output-format stream-json`, `--model <model>` and `--disallowedTools AskUserQuestion`, and omits `--bare`. The turn-2 command adds `--resume <id>`. With `provider: openrouter`, the environment sets all four model-alias variables to the configured model.
- **AC-6** (live) A Claude Code two-turn smoke run on `anthropic/claude-sonnet-5` meets all of the following:
  - turn 1 creates a file with a tool, and turn 2 changes it;
  - both turns report the same session id;
  - no permission prompt or refusal occurs while running as root;
  - every `usage` event names the same model;
  - `totals.cost_usd_key_delta` > 0.
- **AC-7** (unit) The seeded OpenCode config sets every permission explicitly to `allow`. With `permissions.deny: [websearch, webfetch]` it sets `webfetch` and `websearch` to `deny`, and `session.json` lists both as applied. `model.parameters.provider` appears verbatim in the config's OpenRouter model options.
- **AC-8** (live) An OpenCode two-turn smoke run on `deepseek/deepseek-v4.1-flash` with a provider pin meets all of the following:
  - both turns report the same session id;
  - `totals.cost_usd_key_delta` > 0;
  - the token totals are non-zero and come from `opencode.db`.
- **AC-9** (unit) Parsing the fixtures committed from the AC-6 and AC-8 runs:
  - every line of `trace.jsonl` validates against the JSON Schema;
  - the first `user` message of each turn equals that turn's prompt file byte for byte;
  - `tool_call` events carry their outputs.
- **AC-10** (unit) Key-usage logic, tested with HTTP mocked:
  - the delta is computed correctly;
  - settling stops after two equal reads, or after 60 s with `settled: false`;
  - a failed read gives `delta_usd: null` and a warning, not a failed run;
  - a non-OpenRouter provider gives `key_usage: null`.
- **AC-11** (live) For both harnesses, a prompt that tells the agent to ask the user a clarifying question completes within the timeout without waiting for input, and `result.json` shows the turn as completed or failed, not timed out.
- **AC-12** (unit) `README.md` documents `ahl run`, its exit codes and its run directory, and `docs/trace-schema.md` documents the trace schema. A test checks that the schema file and the document list the same event types and fields.

## Test plan

- The unit tier uses a stub `docker` executable, or `subprocess` monkeypatching, following the existing `test_cli_docker_errors.py`.
- AC-3 uses a test-only stub driver registered through monkeypatch, so the docker tier exercises the real container lifecycle without calling a model.
- The live tier (`@pytest.mark.live`) needs `OPENROUTER_API_KEY`. It records its raw outputs as fixtures under `tests/fixtures/headless/<harness>/`, scrubbed of keys. The unit tier then replays those fixtures.

## Risks and open questions

- **Claude as root.** Whether `IS_SANDBOX=1` is honoured is unverified; the design above names the fallback.
- **Claude through OpenRouter:**
  - `result.total_cost_usd` is a local estimate and unreliable for non-Anthropic model ids. That is why cost comes from the key.
  - OpenRouter guarantees Claude Code compatibility only for Anthropic models, which matches roadmap decision 1.
- **OpenRouter usage lag.** Key usage may update with a delay. The settling rule is a best effort, and the raw readings are kept so callers can recompute.
- **OpenCode headless quirks** differ between versions. Pin the version (A1) and test against that version only.

## Evidence required in the PR

- The Claude root mechanism used.
- The AC-6, AC-8 and AC-11 commands, with abridged `result.json` and cost.
- The fixture files committed.
