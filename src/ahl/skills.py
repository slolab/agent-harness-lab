"""Delegated skill installation through the `skills` CLI."""

from __future__ import annotations

import shlex

from ahl.capabilities import Capability
from ahl.config import ConfigError
from ahl.docker import Volumes

CONTAINER_SKILL_SOURCES = "/opt/ahl-skill-sources"

# AHL harness name -> vercel-labs/skills agent identifier.
INSTALLER_AGENTS = {
    "claude": "claude-code",
    "gemini": "gemini-cli",
    "opencode": "opencode",
    "agy": "antigravity-cli",
}


def wire_delegated_skills(
    harness_name: str,
    capabilities: list[Capability],
) -> tuple[Volumes, list[str]]:
    """Return local-source mounts and non-interactive `npx skills` commands.

    Local `mount` skills remain adapter-owned so they retain live hot reload.
    Local `copy` and remote `npx` skills are installed globally for the active
    harness before the interactive shell starts.
    """
    delegated = [
        cap
        for cap in capabilities
        if cap.kind == "skill" and cap.install in {"copy", "npx"}
    ]
    if not delegated:
        return [], []

    if harness_name == "claude-science":
        remote = next((cap for cap in delegated if cap.install == "npx"), None)
        if remote is not None:
            raise ConfigError(
                f"skill '{remote.name}': remote npx installation is not supported "
                "for harness 'claude-science'"
            )
        # Local mount/copy skills keep Claude Science's uploadable-ZIP flow.
        return [], []

    agent = INSTALLER_AGENTS.get(harness_name)
    if agent is None:
        raise ConfigError(
            f"delegated skill installation is not supported for harness '{harness_name}'"
        )

    volumes: Volumes = []
    commands: list[str] = []
    for index, skill in enumerate(delegated):
        if skill.install == "copy":
            if skill.path is None:  # guarded by config parsing; keeps this API total
                raise ConfigError(
                    f"skill '{skill.name}': install: copy requires 'path'"
                )
            source = f"{CONTAINER_SKILL_SOURCES}/{index}"
            volumes.append((skill.path, source))
        else:
            if skill.source is None:  # guarded by config parsing
                raise ConfigError(
                    f"skill '{skill.name}': install: npx requires 'source'"
                )
            source = skill.source

        commands.append(
            shlex.join(
                [
                    "npx",
                    "--yes",
                    "skills",
                    "add",
                    source,
                    "--skill",
                    skill.name,
                    "--agent",
                    agent,
                    "--global",
                    "--yes",
                ]
            )
        )

    return volumes, commands
