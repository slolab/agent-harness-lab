"""Antigravity (agy) adapter — env only; no confirmed skill/MCP config surface yet."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ahl.capabilities import Capability
from ahl.config import RunConfig
from ahl.harnesses.base import Volumes, google_env


class AgyAdapter:
    def build_env(self, config: RunConfig) -> dict[str, str]:
        return google_env(config)

    def seed(self, run_dir: Path, config: RunConfig) -> Volumes:
        return []

    def wire_capabilities(self, run_dir: Path, config: RunConfig, capabilities: list[Capability]) -> Volumes:
        for cap in capabilities:
            print(
                f"[ahl] warning: capability '{cap.name}' ({cap.kind}) is not supported on "
                "harness 'agy' yet (no confirmed skill/MCP config surface) — skipping",
                file=sys.stderr,
            )
        return []

    def parse_trace(self, run_dir: Path) -> dict[str, Any] | None:
        return None

    def start_command(self, config: RunConfig) -> str:
        return "agy"  # model comes from GEMINI_MODEL env

    def start_hints(self, config: RunConfig) -> list[str]:
        return []
