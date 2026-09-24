# Trace schema

`ahl run` writes `trace.jsonl` in the run directory: one JSON event per line,
for every harness with a headless driver. The JSON Schema ships in the package
as `ahl/schemas/trace_event.schema.json` (JSON Schema 2020-12). Load it with
`importlib.resources.files("ahl") / "schemas/trace_event.schema.json"`.

Unknown values are null, never guessed. AHL writes full tool outputs.

## Common fields

| Field | Type | Meaning |
|---|---|---|
| `seq` | integer | Position in the file, from 0. |
| `type` | string | `message`, `tool_call`, `usage` or `error`. |
| `session` | string or null | Native session id of the record. |
| `agent` | string | `main`, or the subagent id for records a subagent produced. |
| `turn` | integer or null | AHL turn from 1. |
| `ts` | string or null | ISO-8601 time of the native record. |

`turn` comes from the turn windows in `result.json`: an event belongs to the
last turn whose `started_at` is not after its `ts`. An event without `ts`
takes the previous event's turn. The first `user` message of a turn contains
that turn's prompt, stripped of leading and trailing whitespace.

## Event types

### `message`

| Field | Type | Meaning |
|---|---|---|
| `role` | string | `user`, `assistant` or `system`. Text the harness injects into the conversation, such as reminders, is `system`. |
| `text` | string | The message text. |
| `reasoning` | string or null | Reasoning text the harness recorded with the message. |

### `tool_call`

| Field | Type | Meaning |
|---|---|---|
| `id` | string or null | Native tool call id. |
| `tool` | string | Native tool name, e.g. `Bash` or `bash`. |
| `input` | object or string | Tool input as recorded. |
| `output` | string or null | Tool output, or the error text of a failed call. |
| `is_error` | boolean or null | Whether the call failed. |
| `output_truncated` | boolean | Optional. Never written by AHL; lets callers mark a shortened copy. |
| `output_original_length` | integer | Optional. Never written by AHL; the length before shortening. |

### `usage`

One event per API response. Each response is counted once across `usage`
events, subagents included.

| Field | Type | Meaning |
|---|---|---|
| `model` | string or null | Model the response names. |
| `response_id` | string or null | Provider response id. |
| `input_tokens` | integer or null | Input neither read from nor written to cache. |
| `output_tokens` | integer or null | Output as billed, reasoning included. |
| `cache_read_tokens` | integer or null | Input read from cache. |
| `cache_write_tokens` | integer or null | Input written to cache. |
| `reasoning_tokens` | integer or null | The reported reasoning subset of `output_tokens`, or null. |
| `cost_usd` | number or null | Cost as the harness reports it. |

`result.json` sums each token field over the `usage` events. A total is null
if any event has null there, and 0 without `usage` events. Cost for billing
comes from the OpenRouter key (`totals.cost_usd_key_delta`), not from
`cost_usd`.

### `error`

| Field | Type | Meaning |
|---|---|---|
| `message` | string | Error text the harness recorded, such as an API error. |

## Claude Code

Source: the project JSONL files under `claude/projects/`, subagent files
included. Records repeated across files (same `uuid`) are read once.
Subagent records carry `isSidechain` and an `agentId`, which becomes `agent`.

- One assistant `message`, its `tool_call` events and one `usage` event per
  API response. Claude writes one record per content block and repeats
  cumulative usage on each; records are grouped by `message.id`, and each
  counter takes its largest value (see [trace accounting](observability.md)).
  Synthetic responses (`model: "<synthetic>"`) and API-error placeholders
  produce no `usage`; the latter become `error` events.
- `thinking` blocks become `reasoning`. User records marked `isMeta`, and
  text sent alongside tool results, are `system` messages.
- Tokens: `input_tokens` = `input_tokens`, `cache_read_tokens` =
  `cache_read_input_tokens`, `cache_write_tokens` =
  `cache_creation_input_tokens`, `output_tokens` = `output_tokens`, which
  already includes thinking, `reasoning_tokens` =
  `output_tokens_details.thinking_tokens`. `cost_usd` is null: Claude Code
  records no cost per response.
- `response_id` is `message.id`.

## OpenCode

Source: the `session`, `message` and `part` tables of `opencode/data/opencode.db`,
not stdout, which can end before the final `step_finish` event
([opencode#26855](https://github.com/anomalyco/opencode/issues/26855)).
Messages of a child session (one with a `parent_id`) take that session id as
`agent`.

- Each assistant row is one API response: one `message` from its `text` and
  `reasoning` parts, one `tool_call` per `tool` part (output from
  `state.output`, or `state.error` when the call failed) and one `usage` event.
  Rows without token counts, such as aborted responses, produce no `usage`.
  A row's `error` becomes an `error` event. Synthetic user text parts are
  `system` messages.
- Tokens: OpenCode stores input without cached tokens and output without
  reasoning. `input_tokens` = `tokens.input`, `cache_read_tokens` =
  `tokens.cache.read`, `cache_write_tokens` = `tokens.cache.write`,
  `output_tokens` = `tokens.output + tokens.reasoning`, `reasoning_tokens` =
  `tokens.reasoning`, `cost_usd` = `cost`, computed by OpenCode from its model
  catalogue.
- `response_id` is null; OpenCode does not store it. `model` is `modelID`.
- OpenCode's request for a session title is not stored as a message, so its
  tokens appear in the key delta but in no `usage` event.
