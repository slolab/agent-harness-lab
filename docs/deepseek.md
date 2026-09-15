# DeepSeek browser harness

AHL runs the official `@deepseek-ai/dsh` Web profile in Docker. This integration
supports OpenRouter, local or delegated skills, persisted sessions, and native
version-3 trace summaries. Native terminal/headless interfaces and MCP wiring
are outside this integration.

## Configure and launch

```yaml
harness:
  name: deepseek
  parameters:
    port: 3080
provider: openrouter
model: qwen/qwen3.7-flash
```

Set `OPENROUTER_API_KEY` in `.env`, then run `uv run ahl up --name browser-test`.
Inside the shell, run the printed `ahl-deepseek --port 3080` command. Open the
printed token URL. DSH exchanges the token for its native browser cookie and
redirects to the clean URL. Choose `/workspace` and start a Standard session.

The model ID is used exactly as configured. The native model picker may also
show DeepSeek's shipped models; this AHL release only authenticates OpenRouter.
Native search remains installed but requires `DEEPSEEK_API_KEY`, which AHL does
not inject. Invoking it without that credential fails. No alternate search
backend or network restrictions are added.

### Ports and shutdown

The configured integer port must be in `1..65535`. Docker publishes
`127.0.0.1:P:Q`, where `Q` is 3081 (3082 if `P` is 3081). The image's small Node
launcher listens on `Q`, then starts DSH on container `127.0.0.1:P`. Raw TCP
relaying preserves native cookies, headers, streaming and WebSocket frames.
The launch URL already has the correct host port.

An occupied host port fails Docker startup. An occupied relay or native port
fails launcher startup. Ctrl-C forwards SIGINT; container shutdown forwards
SIGTERM. The launcher closes its sockets and returns the child's status; a
child ignoring termination is killed after five seconds. Exit the AHL shell
to stop the container and write the trace.

## State, skills, and resume

| Host directory under `runs/<id>/` | Container path | Purpose |
|---|---|---|
| `deepseek/home` | `/root/.dsh` | Native config, browser authentication, sessions, mounted skills |
| `deepseek/agents` | `/root/.agents` | Delegated global skills |
| `workspace` | `/workspace` | Existing AHL workspace copy or explicit live mount |

`DSH_HOME` points to `/root/.dsh`. AHL seeds three existing Cordis rows:
`llm-pi-ai`, `agent-default-model`, and `session-persistence-jsonl`. Persistence
uses uncompressed `session.v3.jsonl`. AHL refreshes its owned provider/model
fields in the patch and existing native `settings.yaml` on each launch,
including resume, while preserving unrelated rows and settings.

Run `uv run ahl up --resume browser-test`, start the printed command, and select
the saved session. Native session-specific model selections survive; after
changing the configured default, use the model picker to change an existing
session's explicit selection. Malformed patch/settings documents fail clearly
instead of being overwritten.

Local `install: mount` skills are read-only mounts at
`/root/.dsh/skills/<name>`. Local `copy` and remote `npx` installs use
`npx skills --agent universal --global`, which DSH discovers under
`/root/.agents/skills`. Plugin-expanded skills use the same paths. MCP
capabilities retain AHL's warning-and-skip behavior.

## Version pinning

The CLI is pinned to **0.1.5-rc.1** with Node **22.20.0**. The committed npm lock
also pins its dependency graph, including the 0.1.5-rc.2 DSH dependencies
selected by the CLI's published ranges. Builds use `npm ci --omit=dev` and
retain optional native packages for Linux arm64 and amd64. Upgrading requires
rechecking the actual installed persistence, settings, and browser contracts.

## Native trace summary

The authoritative records remain under
`deepseek/home/sessions/<project>/<encoded-id>/session.v3.jsonl`.
See [observability](observability.md) for the normalized conventions.

- Only v3 plaintext generations are read. Older retained generations are not
  added again; unsupported-only, malformed, and unreadable logs produce warnings.
- A seeded fork contributes events after its last inherited end-seed marker.
  An ordinary resume marker retains earlier own work. Duplicate `(session ID,
  sequence)` events are counted once; retries sharing a turn/step stay distinct.
- Assistant messages use top-level usage. Uncommitted attempts use their final
  stream usage chunk. Usage-bearing compaction summaries also contribute.
- Four base counters map native input/output/cache-write/cache-read fields.
  Reasoning is already included in output. `total_tokens` preserves the reported
  total separately. Missing counters propagate `null`, including missing cache
  values; TTL breakdown is unknown. Failed attempts, interrupted messages, and
  settlements with missing base counters count as incomplete.
- Human prompts and injected context are separate. Reasoning is separate from
  assistant text. Native tool calls/results are paired by session and call ID;
  missing results remain visible. Nested PTC details stay in raw logs.

Trace totals describe readable recorded settlements, not a provider invoice.
Warnings must be considered when logs are missing or unsupported. Resume
reparses native logs; it never adds the previous trace's totals.

## Validation

Local acceptance on 2026-09-15 passed both image builds and native browser
authentication/WebSocket checks, plus live Qwen prompts through all three
OpenRouter routes and an actual AHL DeepSeek resume with mounted/delegated skills.
The free Gemma route returned an upstream rate limit; the configured Qwen fallback
succeeded.

Automated tests cover routing, config reconciliation, skill wiring, trace
boundaries, and real Node relay sockets/process cleanup. Run:

```bash
uv run pytest
uv run ruff check .
docker build --platform linux/arm64 -t agent-harness-lab:deepseek -f docker/deepseek.Dockerfile docker
docker build --platform linux/amd64 -t agent-harness-lab:deepseek-amd64 -f docker/deepseek.Dockerfile docker
```

For a browser smoke test, check the token URL and subsequent clean URL,
unauthenticated HTTP/WebSocket rejection, hostile Host/Origin rejection,
authenticated WebSocket connection, a tiny prompt with `pwd`, skill discovery,
then exit and resume. Exercise default/alternate ports, including 3081, and an
occupied port. Use `google/gemma-4-31b-it:free`, falling back to
`qwen/qwen3.7-flash` if unavailable. Record provider failures separately from
integration failures and never copy real credentials into test fixtures.

Primary references: [official harness](https://www.deepseek.com/harness/en/),
[provider configuration](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/llm/llm-pi-ai),
[browser authentication](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/client/connection),
and [persistence](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/session/session-persistence-jsonl).
