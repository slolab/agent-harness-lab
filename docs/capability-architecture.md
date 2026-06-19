# Capability architecture — implementation plan

Implementation plan for mounting external capabilities (MCP servers, Agent Skills — see `docs/capability-format.md` for what a bundle looks like) into AHL-driven harness runs, and for the `src/ahl` refactor this requires. No code has been written yet; this is the design future implementation should follow.

## Why a refactor, not just a config addition

Today, harness-specific behavior lives in a flat dispatch table (`harness.py`'s `builders = {...}`) plus an `if/elif` block in `cli.py`'s `up` command that branches on `run_config.harness.name` to decide whether to call `seed_gemini_home`/`seed_opencode_state` and which `parse_trace` to use. That works for two axes (harness × {seed, trace}) but won't scale once capabilities (skill/MCP × install mode × per-harness wiring) layer on top — every new combination would mean another branch in `cli.py`. This plan introduces a small adapter abstraction instead, so adding a harness or a capability kind is additive, not a growing conditional.

## Target architecture

### `HarnessAdapter` (Protocol)

```python
class HarnessAdapter(Protocol):
    def build_env(self, config: RunConfig) -> dict[str, str]: ...
    def seed(self, run_dir: Path, config: RunConfig) -> None: ...
    def wire_capabilities(self, run_dir: Path, config: RunConfig, capabilities: list[Capability]) -> None: ...
    def parse_trace(self, run_dir: Path) -> dict[str, Any]: ...
    def start_command(self, config: RunConfig) -> str: ...
    def start_hints(self, config: RunConfig) -> list[str]: ...
```

Four concrete adapters: `GeminiAdapter`, `OpenCodeAdapter`, `ClaudeAdapter`, `AgyAdapter`. `GeminiAdapter`/`OpenCodeAdapter` wrap today's `gemini.py`/`opencode.py` logic unchanged (`seed_home`→`seed`, existing `parse_trace`, etc.); `ClaudeAdapter`/`AgyAdapter` are new — today these harnesses have no seed/trace step at all, so this is also where that gap (already tracked in `TODO`) gets filled.

A registry/factory, `get_adapter(harness_name: str) -> HarnessAdapter`, is the single place a new harness gets added (Open/Closed Principle). `cli.py`'s `up` command depends only on the `HarnessAdapter` interface (Dependency Inversion) — it stops importing `ahl.gemini`/`ahl.opencode` directly; those modules become *implementations behind the interface*, not modules `cli.py` reaches into.

### `Capability` (value object)

```python
@dataclass(frozen=True)
class Capability:
    kind: Literal["mcp", "skill"]
    name: str
    install: Literal["mount", "copy", "pip"]
    # kind == "mcp": command, args, env
    # kind == "skill": path (required for install in {mount, copy})
    # install == "mount": bind-mount `path` straight from the host — hot reload
    # install == "copy": snapshot `path` into the run dir once, at `ahl up` time
    # install == "pip": mcp only; version (optional)
    # Marketplace/git/url installs are deferred — not modeled here yet.
```

Parsed from a new `capabilities:` list in `config.yaml` using the same bare-string-or-`{name, parameters}` pattern `config.py`'s `_parse_named` already implements for `harness`/`provider`/`model` — reuse that parser, don't write a second one.

### `CapabilityWirer` (composed, not inherited)

Each `HarnessAdapter` holds a `CapabilityWirer` rather than inheriting one, so behavior is shared by composition:

- A native-MCP-config writer — one implementation per harness, since the config file format differs (`.mcp.json` vs `settings.json`'s `mcpServers` vs `opencode.json`'s `mcp` block), but the input (`Capability` with `kind="mcp"`) is identical.
- A native-skill-dir writer — shared by Claude and OpenCode, since both natively discover `skills/<name>/SKILL.md` on disk (Claude: `.claude/skills/<name>/`; OpenCode: `~/.config/opencode/skills/<name>/` or `.opencode/skills/<name>/`/`.claude/skills/<name>/` project-local — see https://opencode.ai/docs/skills/, no config-file edit needed, it's pure filesystem discovery). Honors `install`: `mount` bind-mounts the skill straight from its host path (hot reload — host edits show up in the container immediately); `copy` snapshots it into the run dir once at `ahl up` time. Either way, for Claude this needs a volume mount over the workspace's `.claude/skills`; for OpenCode a `mount`-mode skill is bind-mounted over the already-mounted config dir, while `copy` mode just lands inside that same mount with no extra volume.
- A fallback "fold `SKILL.md` into the harness's context file" writer — one implementation, reused by `GeminiAdapter` and `AgyAdapter`'s wirers, since neither has a native skill concept (Gemini: global `~/.gemini/GEMINI.md`; agy: unverified, currently a no-op + warning instead of a guess).

A harness lacking support for a capability kind degrades to the fallback (or a no-op + a logged warning for MCP-incapable harnesses, if any turn out to exist) — never a hard error.

### `CapabilityInstaller` (orthogonal)

Separate from wiring: makes sure the capability's underlying package/checkout is actually present in the container before the wirer can point a harness at it.

- `install: pip` → a `RUN pip install <name>[==version]` line baked into the relevant `docker/<harness>.Dockerfile` at build time.
- `install: mount` → volume-mount the host checkout (same mechanism `cli.py` already uses for `extra_volumes`) and run an editable install at container start.

Kept separate from `CapabilityWirer` deliberately — "is it installed" and "is it wired into this harness's config" are independent concerns (Single Responsibility); a capability could in principle be installed but not wired (e.g. for manual experimentation), or wired against a path that's installed by some other means.

### `ahl-logtee` (debug log capture)

A new tiny module + console-script entry point: `ahl-logtee <logfile> -- <command...>`. It `exec`s the real command, teeing its stderr into `<logfile>`. The MCP `CapabilityWirer` wraps every MCP capability's launch command with this before writing it into the harness's native config. This is a local process wrapper around `exec`, not a network proxy — it doesn't reopen the no-proxy decision already recorded in `docs/specs.md`.

**Where `<logfile>` points, per harness** (reusing/extending each harness's already-mounted log directory rather than inventing a new mount):

| Harness | Existing mounted log dir | Capability log path |
|---|---|---|
| OpenCode | `data_dir / "log"` (already created in `seed_state`, already globbed by `parse_trace`'s `trace["log_files"]`) | same dir — **zero parser changes needed** |
| Gemini | `home / "tmp/workspace"` (walked by `parse_trace`, which globs `logs.json` + `chats/*.jsonl`) | `home / "tmp/workspace/logs/capabilities/<name>.log"` — needs one small glob addition to `gemini.py`'s log-reading code |
| Claude | TBD — no seeded home dir exists yet (Phase 0 adds one) | wherever that home dir lands |
| agy | TBD — same as Claude | wherever that home dir lands |

## Config model changes

`config.py`'s `RunConfig` gains `capabilities: list[Capability]`. Validation: an unsupported kind for the selected harness is a documented no-op + warning (logged to the operator), not a `ConfigError` — consistent with `docs/specs.md`'s "configuration over code" principle and with capabilities being optional, additive mounts rather than required ones.

## Testing approach

There is no test suite yet (`pyproject.toml` lists `ruff` as the only dev dependency). Adapters are pure functions/classes over paths and dicts — no Docker needed to unit-test `build_env`, `wire_capabilities`, `parse_trace`, etc. This refactor is a natural point to add the first tests, using `tmp_path` fixtures for seeded directories and asserting on the assembled `docker run` args / written config files, without spinning up containers.

One smoke-test path per harness should still assemble full `docker_run_args` end-to-end to catch integration mistakes the unit tests miss. Note: `runs/smoke-proxy*` in the repo today are stale rehearsals from the pre-no-proxy design — worth deleting once the new smoke tests exist, not in scope for this doc-only pass.

## Phased milestones

1. **Phase 0 — pure refactor.** Introduce `HarnessAdapter` + the four concrete adapters wrapping today's logic unchanged, including new (currently no-op beyond env/start-command) `ClaudeAdapter`/`AgyAdapter`. Switch `cli.py` to the registry. No behavior change — verify by re-running `ahl up` for gemini and opencode and diffing `trace.json`/`session.json` output against pre-refactor runs.
2. **Phase 1 — config only.** Add `capabilities:` parsing/validation to `config.py`. No wiring yet.
3. **Phase 2 — MCP wiring.** Implement the native-MCP-config writer for Claude, Gemini, OpenCode. agy deferred (support unverified).
4. **Phase 3 — skill wiring.** Native skill-dir writer shared by Claude/OpenCode; fallback fold-in writer for Gemini (shared instance, see above).
5. **Phase 4 — log capture.** Build `ahl-logtee`; wire it into the MCP writer; verify capability stderr lands in `runs/<id>/...` next to each harness's native logs.
6. **Phase 5 — real bundles.** Build the actual `biotope`/`biocypher` skill + MCP bundles in their own repos, per `docs/capability-format.md`; exercise both variants through AHL.
7. **Phase 6 — comparison tooling.** A minimal manual diff script across two runs' `trace.json`/`session.json` — a thin slice of `docs/specs.md`'s FR-H4, just enough to actually compare skill-vs-MCP outcomes for the same task.

## Open questions

- **agy's actual MCP/skill support is unverified.** No confirmed config surface found yet; Phase 2/3 may end up best-effort (PATH-only) for this harness.
- **Exact package names** for the biotope/biocypher MCP wrappers are TBD — decided in those repos during Phase 5, not here.
- **`variant: both`** (mounting skill and MCP for the same tool simultaneously, to measure which the agent prefers when given the choice) — whether this ships in v1 of capability wiring or is deferred past Phase 5 is undecided; the `Capability` list shape (one entry per kind, same `name`) already supports it without a model change either way.
