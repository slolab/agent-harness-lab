"""HarnessAdapter Protocol — see docs/capability-architecture.md.

Each adapter owns its per-run state directory layout and is the single place
that knows how to build a harness's container env, seed its config, wire
capability bundles (skills/MCP) into it, and parse its native logs back out.
`cli.py` depends only on this Protocol, never on a specific harness module.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Protocol

from ahl.capabilities import Capability
from ahl.config import PROVIDER_KEY_ENV, RunConfig
from ahl.docker import Volumes
from ahl.permissions import PermissionHandler


class HarnessAdapter(Protocol):
    permission_handler: PermissionHandler

    def build_env(self, config: RunConfig) -> dict[str, str]: ...
    def seed(self, run_dir: Path, config: RunConfig) -> Volumes: ...
    def wire_capabilities(self, run_dir: Path, config: RunConfig, capabilities: list[Capability]) -> Volumes: ...
    def parse_trace(self, run_dir: Path) -> dict[str, Any] | None: ...
    def start_command(self, config: RunConfig) -> str: ...
    def start_hints(self, run_dir: Path, config: RunConfig) -> list[str]: ...
    def docker_args(self, config: RunConfig) -> list[str]: ...


def provider_key(config: RunConfig) -> str:
    return os.environ[PROVIDER_KEY_ENV[config.provider.name]]


def google_env(config: RunConfig) -> dict[str, str]:
    """Shared env builder for gemini and agy — both run on @google/genai."""
    if config.provider.name == "vertex":
        params = config.provider.parameters
        env = {
            "GOOGLE_GENAI_USE_VERTEXAI": "true",
            "GOOGLE_CLOUD_PROJECT": str(params["project"]),
            "GOOGLE_CLOUD_LOCATION": str(params["location"]),
            "GOOGLE_API_KEY": provider_key(config),
        }
    else:
        env = {"GEMINI_API_KEY": provider_key(config)}
    if config.model.name:
        env["GEMINI_MODEL"] = config.model.name
    return env
def native_skill_mount(skill: Capability, container_skills_dir: str) -> str:
    """Container path a skill should be visible at, under a harness's native skills dir."""
    return f"{container_skills_dir}/{skill.name}"


def warn_unsupported_mcp(harness_name: str, capabilities: list[Capability]) -> None:
    for cap in capabilities:
        if cap.kind == "mcp":
            print(
                f"[ahl] warning: MCP capability '{cap.name}' is not wired for harness "
                f"'{harness_name}' yet (Phase 2, unimplemented) — skipping",
                file=sys.stderr,
            )
