"""Claude Code adapter — env and native skill-dir capability wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ahl.capabilities import Capability
from ahl.config import RunConfig
from ahl.harnesses.base import Volumes, copy_skill_bundle, native_skill_mount, provider_key, warn_unsupported_mcp

CONTAINER_WORKSPACE_SKILLS = "/workspace/.claude/skills"


class ClaudeAdapter:
    def build_env(self, config: RunConfig) -> dict[str, str]:
        env = {
            "ANTHROPIC_API_KEY": provider_key(config),
            "DISABLE_AUTOUPDATER": "1",
        }
        if config.model.name:
            env["ANTHROPIC_MODEL"] = config.model.name
        return env

    def _skills_dir(self, run_dir: Path) -> Path:
        return run_dir / "claude" / "skills"

    def seed(self, run_dir: Path, config: RunConfig) -> Volumes:
        # No pre-auth config needed (Claude reads the key straight from env);
        # no native log/session directory has been identified yet either.
        return []

    def wire_capabilities(self, run_dir: Path, config: RunConfig, capabilities: list[Capability]) -> Volumes:
        warn_unsupported_mcp("claude", capabilities)
        volumes: Volumes = []
        for skill in capabilities:
            if skill.kind != "skill" or skill.path is None:
                continue
            container_path = native_skill_mount(skill, CONTAINER_WORKSPACE_SKILLS)
            if skill.install == "mount":
                # Bind-mounted straight from the host path: hot reload, edits
                # show up in the container immediately.
                volumes.append((skill.path, container_path))
            else:  # copy
                copied = copy_skill_bundle(skill, self._skills_dir(run_dir))
                volumes.append((copied, container_path))
        return volumes

    def parse_trace(self, run_dir: Path) -> dict[str, Any] | None:
        return None

    def start_command(self, config: RunConfig) -> str:
        return f"claude --model {config.model.name}" if config.model.name else "claude"

    def start_hints(self, config: RunConfig) -> list[str]:
        return []
