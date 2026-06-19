# Capability bundle format

This is the reference for whoever bundles a tool (e.g. `biotope`, `biocypher`) as an AHL-mountable capability. **AHL defines no format of its own here.** Bundle against the existing open standards below; AHL only consumes what they already produce.

## Skills

Use the standard Agent Skill format: a directory with a `SKILL.md` (YAML frontmatter + instructions) at its root, optionally alongside `scripts/`/`references/`/other resources. Anthropic's Agent Skills documentation is the authoritative spec for frontmatter fields and directory layout — verify against it at bundling time rather than against this doc:

- https://docs.claude.com/en/docs/claude-code/skills
- https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills

**AHL's only added expectation:** the skill directory is self-contained and addressable by a host path — `config.yaml`'s `capabilities` list will reference it by path, the same way `workspace` is referenced today.

## MCP servers

Build a standard MCP server per the Model Context Protocol spec. The protocol spec and SDKs are the authoritative reference — don't restate protocol details here:

- https://modelcontextprotocol.io/
- Python SDK / `fastmcp` for a quick stdio server: https://github.com/modelcontextprotocol/python-sdk

**AHL's added expectations** (AHL-specific constraints, not protocol requirements):

1. **Launchable via stdio with a single shell command** — `command [args...]`, optional `env`. This is the same `{command, args, env}` shape every harness's native MCP config (Claude's `.mcp.json`, Gemini's `settings.json` `mcpServers`, OpenCode's `opencode.json` `mcp` block) already expects, so AHL just forwards it — no transport negotiation needed.
2. **Diagnostics go to stderr only, never stdout.** This is already standard MCP-server hygiene (stdout is the JSON-RPC channel), but it's called out explicitly because AHL's log-capture mechanism (a small `ahl-logtee` wrapper around the launch command) depends on it: stderr gets redirected into a file under the same mounted log directory the harness's own native trace parser already reads.
3. **Resolvable to one command name.** Package it as a pip console-script entry point, an npm `bin` entry, or a plain executable — so both of AHL's install modes ("bake" the package into the harness's Docker image, or "mount" a host checkout and install it editable at container start) work without special-casing the launch command.

## Non-goals

- No AHL manifest file (no `capability.yaml` or equivalent).
- No AHL-specific metadata format layered on top of `SKILL.md` or the MCP server's own descriptor.
- No required dependency on AHL itself anywhere in the bundle's code — a bundle built this way should work with any MCP-capable / Skill-capable client, not just AHL.

See `docs/capability-architecture.md` for how AHL wires a bundle built this way into a given harness, and how its logs get captured.
