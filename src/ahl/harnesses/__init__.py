"""Per-harness adapter registry. See `ahl.harnesses.base.HarnessAdapter`."""

from __future__ import annotations

from ahl.harnesses.agy import AgyAdapter
from ahl.harnesses.base import HarnessAdapter
from ahl.harnesses.claude import ClaudeAdapter
from ahl.harnesses.claude_science import ClaudeScienceAdapter
from ahl.harnesses.deepseek import DeepSeekAdapter
from ahl.harnesses.gemini import GeminiAdapter
from ahl.harnesses.opencode import OpenCodeAdapter

_ADAPTERS: dict[str, HarnessAdapter] = {
    "gemini": GeminiAdapter(),
    "deepseek": DeepSeekAdapter(),
    "opencode": OpenCodeAdapter(),
    "claude": ClaudeAdapter(),
    "claude-science": ClaudeScienceAdapter(),
    "agy": AgyAdapter(),
}


def get_adapter(harness_name: str) -> HarnessAdapter:
    try:
        return _ADAPTERS[harness_name]
    except KeyError:
        raise ValueError(f"no adapter registered for harness '{harness_name}'") from None


__all__ = ["HarnessAdapter", "get_adapter"]
