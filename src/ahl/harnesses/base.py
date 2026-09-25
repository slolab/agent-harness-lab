"""HarnessAdapter Protocol — see docs/capability-architecture.md.

Each adapter owns its per-run state directory layout and is the single place
that knows how to build a harness's container env, seed its config, wire
capability bundles (skills/MCP) into it, and parse its native logs back out.
`cli.py` depends only on this Protocol, never on a specific harness module.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ahl.capabilities import Capability
from ahl.config import PROVIDER_KEY_ENV, RunConfig
from ahl.docker import Volumes
from ahl.permissions import PermissionHandler


@dataclass(frozen=True)
class TurnReport:
    session_id: str | None
    error: str | None
    provider_error: bool
    replied: bool


class HeadlessDriver(Protocol):
    def applied_model_parameters(self, config: RunConfig) -> frozenset[str]: ...
    def env(self, config: RunConfig) -> dict[str, str]: ...
    def command(self, config: RunConfig, session_id: str | None) -> list[str]: ...
    def report(self, stdout: str) -> TurnReport: ...
    def view(self, record: dict[str, Any]) -> list[tuple[str, str, str]]: ...
    def trace(self, run_dir: Path) -> list[dict[str, Any]]: ...


class HarnessAdapter(Protocol):
    permission_handler: PermissionHandler
    driver: HeadlessDriver | None

    def build_env(self, config: RunConfig) -> dict[str, str]: ...
    def seed(self, run_dir: Path, config: RunConfig) -> Volumes: ...
    def wire_capabilities(self, run_dir: Path, config: RunConfig, capabilities: list[Capability]) -> Volumes: ...
    def parse_trace(self, run_dir: Path) -> dict[str, Any] | None: ...
    def start_command(self, config: RunConfig) -> str: ...
    def start_hints(self, run_dir: Path, config: RunConfig) -> list[str]: ...
    def docker_args(self, config: RunConfig) -> list[str]: ...


def json_lines(text: str) -> list[dict[str, Any]]:
    records = []
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def read_saved_output(host_dir: Path, container_dir: str, container_path: Any) -> str | None:
    # Harnesses save a long tool output to a file and record its path where the agent can rewrite it,
    # so only files inside the run-owned directory are read.
    if not isinstance(container_path, str) or not container_path.startswith(f"{container_dir}/"):
        return None
    path = (host_dir / container_path.removeprefix(f"{container_dir}/")).resolve()
    if not path.is_relative_to(host_dir.resolve()) or not path.is_file():
        return None
    return path.read_text(errors="replace")


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
