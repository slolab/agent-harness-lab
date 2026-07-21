# Spec: Delegated Skill Installation

**Intent:** AHL preserves live local skill development while delegating every non-mounted skill installation to `npx skills`, so local and remote skills work across CLI harnesses without AHL owning source-specific or harness-specific installation behavior.

## Problem

AHL supports local skills through custom mount and copy behavior but does not support remote skill sources. Replacing all skill handling with `npx skills` would remove local hot reload, an important development capability. Engineers need one delegated installation path for local snapshots and remote skills while preserving direct mounting for live local development.

## Success Criteria

| # | Criterion | Verified by |
|---|---|---|
| 1 | WHEN a local skill uses `install: mount`, AHL SHALL expose the original host directory read-only, and host edits SHALL become visible in the running harness without reinstalling or restarting it. | E2E test: edit the mounted `SKILL.md` and observe the updated content in the same session. |
| 2 | WHEN a local skill uses `install: copy`, AHL SHALL install it through `npx skills` into the configured harness's global skill location without requiring an agent flag from the user. | Matrix test across Claude Code, Gemini CLI, OpenCode, and Antigravity CLI using a local skill folder. |
| 3 | WHEN a remote source and skill selector are declared, AHL SHALL resolve and install the selected skill through `npx skills` into the configured harness's global skill location. | Matrix test using `vercel-labs/agent-skills` and `web-design-guidelines`; each harness discovers exactly the selected skill. |
| 4 | WHEN the configured harness changes, the same skill declaration SHALL target the corresponding installer agent automatically. | Configuration-only test across `claude`, `gemini`, `opencode`, and `agy`; no capability declaration changes or user-supplied `--agent` value. |
| 5 | Skill installation SHALL run unattended, and AHL SHALL stop before launching the harness if the source cannot be resolved, the selected skill is absent, or installation fails. | Error-path tests for unreachable source, unknown skill, unsupported harness, and installer failure. |
| 6 | Existing Claude Science local-skill ZIP preparation SHALL remain unchanged; delegated and remote installation for Claude Science SHALL be rejected as unsupported. | Existing Claude Science regression test plus a remote-source rejection test. |

## Non-Goals

- Replacing direct local mounts with `npx skills`
- Hot reload for copied or remote skills
- Read-only installation for copied or remote skills
- Pinning or reproducing remote content
- Offline restoration or dependency caching
- Exposing skill update, removal, search, or discovery commands through AHL
- Remote skill installation for Claude Science
- MCP capability installation

## Constraints

- Non-mounted skill installation uses `vercel-labs/skills`; AHL does not duplicate its repository or manifest discovery.
- Installation is global and non-interactive.
- AHL derives the installer target from the configured harness:

| AHL harness | Installer agent |
|---|---|
| `claude` | `claude-code` |
| `gemini` | `gemini-cli` |
| `opencode` | `opencode` |
| `agy` | `antigravity-cli` |

- Existing local `install: mount` behavior remains compatible.
- Copied and remote skills may be writable inside the ephemeral container.
- Remote sources use the installer's default resolution behavior.

## Trade-off Priorities

Local hot reload > correct harness targeting and fail-fast behavior > delegated installation breadth > remote immutability and reproducibility.

## Known Unknowns

- Whether every supported harness version discovers the installer's global location exactly as documented — escalate if any harness fails the matrix test.
- Whether Antigravity CLI behaves identically to the installer's `antigravity-cli` target in AHL's container — escalate if the installed skill is not discovered.
