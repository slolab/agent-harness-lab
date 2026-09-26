# Native web-tool permissions

The selected `config.yaml` controls the run's native tools and its agents:

```yaml
permissions:
  deny: [websearch, webfetch]
```

This is a run-level configuration, not a machine-wide settings file. The only
supported operations are `websearch` and `webfetch`. Repeated entries collapse.
Omitting `permissions`, using `{}`, or setting `deny: []` installs no AHL denials.
Explicit nulls, incorrect types, unknown keys, and unknown operation names are
configuration errors.

| Harness | Enforcement |
|---|---|
| Claude Code | `websearch` → `WebSearch`; `webfetch` → `WebFetch` |
| Codex | `websearch` or `webfetch` sets `web_search = "disabled"` in the seeded `config.toml`; Codex has no separate fetch tool |
| DeepSeek | `websearch` → `web_search`; `webfetch` → `web_fetch` |
| OpenCode | `websearch` and `webfetch` set to `deny` under `permission` in the seeded `opencode.json` |
| Gemini, Antigravity, Claude Science | Unsupported; one warning lists unapplied operations and reasons |

Unsupported mappings continue launching and record the gap. They never claim
successful enforcement. These restrictions do not block HTTP through shell,
MCP, or other tools and do not change network isolation.

## Launch and resume

Every launch seeds native state, prepares the policy, validates its result, and
adds the handler's read-only mounts. No extra command flags are needed. AHL
records sorted policy details in `runs/<id>/session.json`:

```json
{"permissions":{"deny":["webfetch","websearch"],"applied":["webfetch","websearch"],"unsupported":{}}}
```

`ahl up --resume <id>` reapplies current rules, including removing previous AHL
denials, while keeping conversation history, workspace files, and original
session timestamps. Restart the container to apply changes; editing YAML does
not hot-reload policy. Metadata describes the installed launch policy, not
continuous runtime verification.

### Claude Code

AHL writes `permissions/claude/managed-settings.json` under the run directory
and mounts it read-only at `/etc/claude-code/managed-settings.json`. Its only
owned field is `permissions.deny`. Native user/project settings and credentials
remain separate. Both account-login and OpenRouter routes use this mechanism.
Claude applies denials ahead of normal allows; whole-tool denials also remove
tools from the model catalog. See [native permission evaluation](https://code.claude.com/docs/en/permissions)
and [managed settings precedence](https://code.claude.com/docs/en/settings).

### DeepSeek

The image ships `/opt/ahl/permissions/deepseek.mjs`. AHL inserts its
`ahl-native-permissions` plugin at the host root in `cordis.patch.yml`, outside
agent/preset scopes. The native `ctx.tools.guard` hook runs after ordinary
permission evaluation and before the tool body. This covers agent scopes and
nested dispatch; it does not add tools to Minimal mode. Tools can remain
advertised while calls return `Denied by AHL permissions: <tool>`.

Resume replaces the owned insertion without accumulating duplicate plugins.
If `settings.yaml` contains an override for this plugin, AHL also refreshes its
deny list there. Other settings and patch rows survive. An empty deny list
makes the guard inert. See the [native tools contract](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/README.md).

Native search still lacks the separate DeepSeek credential unless the operator
configures it independently. When AHL denies search, the startup hint explains
the policy instead of suggesting that a search key is required for the run.

Rebuild an existing DeepSeek image once to install the plugin (`ahl up` builds
by default). An older PR 2 image used with `--no-build` lacks this module.

## Add a handler

Implement `PermissionHandler.prepare(run_dir, config, policy)` and assign it
to the adapter's `permission_handler`. Return `PermissionSetup` with:

- Read-only artifact mounts, if any.
- Applied canonical operation names.
- Unsupported requested operations mapped to explanatory reasons.

Every requested operation must appear exactly once across applied/unsupported;
extra operations and empty reasons fail validation before launch. Let I/O and
implementation errors fail rather than silently returning unsupported status.

A test implementation can install an artifact without any CLI changes:

```python
class FixturePermissions:
    def prepare(self, run_dir, config, policy):
        path = run_dir / "fixture-policy.json"
        path.write_text(json.dumps(sorted(policy.deny)))
        return PermissionSetup([(path, "/fixture-policy.json")], policy.deny, {})

class FixtureAdapter:
    permission_handler = FixturePermissions()
    # The remaining HarnessAdapter methods are unchanged.
```

`tests/test_permissions.py` exercises this contract through the real CLI and
resume metadata writer. For an actual harness, map the canonical operations
to a verified native enforcement mechanism and add execution-level tests.
Use `UnsupportedPermissions()` until that mapping exists. A new canonical
operation also requires a parser extension and handler acceptance tests.

Pi is deferred: its `tool_call` blocking hook is a candidate for a future real
adapter, but stock Pi has no native web-tool mapping to configure here.

## Native acceptance fixtures

These are explicit integration checks, separate from the fast pytest suite.
They run the installed native code with stub tool bodies or a local model API;
no provider credentials are required.

```bash
docker run --rm --network none --entrypoint node \
  -v "$PWD/tests/native:/tests:ro" agent-harness-lab:deepseek \
  /tests/deepseek-permissions.mjs
```

For Claude, prepare a run using `ClaudeAdapter.seed()` and its permission
handler, mount the returned paths (policy mount read-only), mount
`tests/native` at `/tests:ro`, and run `python3 /tests/claude-permissions.py`
in `agent-harness-lab:claude` with `--network none`. Set `FIXTURE_AUTH=account`
or `FIXTURE_AUTH=gateway`. Run with both denials, just `webfetch`, then an empty
policy. The fixture installs workspace allows, checks the actual model tool
catalog, and forces denied tool calls through a local API to verify rejection.
The account case uses a synthetic OAuth token; it does not test interactive login.
