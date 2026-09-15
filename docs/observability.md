# Trace accounting

AHL reads each harness's persisted state when the sandbox shell exits and
writes `runs/<id>/trace.json`. Parsing does not change the native logs. The
accounting changes below apply to newly parsed traces; saved traces are not
backfilled automatically.

## Claude Code

Claude records several content blocks for a single API response. Those blocks
can repeat token usage or update an earlier cumulative snapshot. AHL groups
assistant records with a usage object by `message.id` and keeps the greatest
observed value of each counter. For example, output snapshots of `2`, `208`,
then a replayed `2` contribute **208 output tokens**.

If a response has no nonempty string ID, AHL uses the record's UUID. If neither
identity exists, each physical source line is counted separately. These
identity forms have separate namespaces. Explicit API-error placeholders
(`isApiErrorMessage: true`) are excluded; real zero-token responses count.
Only nonnegative integers are valid usage values; booleans, strings, floats,
and negative values supply no usage evidence.

Each session and the run's `totals` expose:

| Field | Meaning |
| --- | --- |
| `api_responses` | Number of unique eligible assistant responses with a usage object. |
| `incomplete_api_responses` | Responses missing a nonempty terminal `message.stop_reason` or any of the four base counters below. |
| `usage.input_tokens` | Observed input tokens. |
| `usage.output_tokens` | Observed output tokens. |
| `usage.cache_creation_input_tokens` | Observed cache-write input tokens. |
| `usage.cache_read_input_tokens` | Observed cache-read input tokens. |
| `usage.cache_creation.ephemeral_5m_input_tokens` | Cache-write tokens with a five-minute lifetime, or `null` if unknown. |
| `usage.cache_creation.ephemeral_1h_input_tokens` | Cache-write tokens with a one-hour lifetime, or `null` if unknown. |

The four base counters remain integers and sum known values, defaulting to
zero when none are known. The response counts and cache-lifetime fields are
additions to the existing format. A cache-lifetime bucket is `null` if any
contributing response lacks a valid value for it; a recorded zero is known.
An empty set of responses has zero counts and zero usage, including both
lifetime buckets. Lifetime buckets break down `cache_creation_input_tokens`;
do not add them to that counter again.

Session views include responses visible in that session's file. Run totals
use the unique union across all project JSONL files, including subagents.
Copied or forked histories can therefore appear in several session views
while contributing only once to run totals. **Summing session usage may exceed
run usage.** A later snapshot in another file can also complete a response
that remains incomplete in an individual session view.

These are observed transcript counters, not billing reconciliation. Missing
final usage cannot be reconstructed. A response without an incomplete flag
has completion and base-usage evidence, but that does not establish agreement
with a provider invoice. Text, tools, timestamps, model listing, and elapsed
time retain their previous parsing behavior.

The earlier first-seen/per-file deduplication suggestion was insufficient:
keeping the first record loses later usage updates, and deduplicating only
within a file counts copied history again in the run total. Regression cases
for both appear in [the Claude accounting tests](../tests/test_claude_accounting.py).

## Other-adapter audit

| Adapter | Current accounting behavior | Audit result |
| --- | --- | --- |
| OpenCode | Reads tokens and cost directly from SQLite's session table; joined message/part rows supply text and tool summaries. | Multiple parts do not multiply usage. Covered by `TestOpenCodeAdapter.test_parse_trace_reads_sessions_from_sqlite_db` in [the adapter tests](../tests/test_harnesses.py). No parser change needed. |
| Gemini | Retains native session records without aggregating token usage. | No equivalent aggregation to correct. |
| Antigravity (`agy`) | Trace parsing is unsupported. | No token parser to correct. |
| Claude Science | Trace parsing is unsupported. | No token parser to correct. |

## DeepSeek

DeepSeek summaries follow the [v3 native event rules](deepseek.md#native-trace-summary).
Each session exposes lineage/preset metadata, models, human and assistant
`messages`, separate `context_messages`, correlated `tool_calls`, `children`,
`timing`, `usage`, and response counts. Run totals sum each session's own events
once. Unknown usage fields propagate `null`; unlike Claude's known-value sums,
DeepSeek partial logs do not produce a complete-looking component total.
Native `totalTokens` is reported separately as `usage.total_tokens` and is never
added to the components. Inspect top-level `warnings` for unreadable, malformed,
unsupported, or ambiguous native logs.
