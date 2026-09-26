"""Antigravity (agy) adapter — env and native skill mounts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ahl.permissions import UnsupportedPermissions
from ahl.capabilities import Capability
from ahl.config import RunConfig
from ahl.harnesses.base import Volumes, google_env, native_skill_mount, warn_unsupported_mcp

CONTAINER_AGY_SKILLS = "/root/.gemini/antigravity-cli/skills"


class AgyAdapter:
    permission_handler = UnsupportedPermissions()
    driver = None

    def build_env(self, config: RunConfig) -> dict[str, str]:
        return google_env(config)

    def seed(self, run_dir: Path, config: RunConfig) -> Volumes:
        return []

    def wire_capabilities(self, run_dir: Path, config: RunConfig, capabilities: list[Capability]) -> Volumes:
        warn_unsupported_mcp("agy", capabilities)
        return [
            (skill.path, native_skill_mount(skill, CONTAINER_AGY_SKILLS))
            for skill in capabilities
            if skill.kind == "skill" and skill.install == "mount" and skill.path is not None
        ]

    def parse_trace(self, run_dir: Path) -> dict[str, Any] | None:
        return None

    def start_command(self, config: RunConfig) -> str:
        return "agy"  # model comes from GEMINI_MODEL env

    def start_hints(self, run_dir: Path, config: RunConfig) -> list[str]:
        return []

    def docker_args(self, config: RunConfig) -> list[str]:
        return []
