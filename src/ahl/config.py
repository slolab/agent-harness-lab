"""Configuration loading for Agent Harness Lab."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from ahl.capabilities import Capability
    from ahl.packages import Package
    from ahl.workspace import Workspace


SUPPORTED_HARNESSES = {"claude", "opencode", "agy", "gemini"}

# Provider name -> host env var holding the real API key (read from .env).
PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "vertex": "GOOGLE_API_KEY",
}


class ConfigError(ValueError):
    """Raised when config is missing or invalid."""


@dataclass(frozen=True)
class Named:
    """A config selection that is either a bare name or a name with parameters.

    Both forms are accepted in YAML:

        provider: gemini
        provider:
          name: vertex
          parameters: {project: ..., location: ...}
    """

    name: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class RunConfig:
    root: Path
    harness: Named
    provider: Named
    model: Named
    workspace: "Workspace"
    capabilities: list["Capability"]
    packages: list["Package"]

    @property
    def key_env(self) -> str:
        return PROVIDER_KEY_ENV[self.provider.name]


def load_config(config_path: Path) -> RunConfig:
    config_path = config_path.expanduser().resolve()
    if not config_path.exists():
        raise ConfigError(f"Missing config file: {config_path}")

    root = config_path.parent
    load_dotenv(root / ".env")
    raw = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")

    harness = _parse_named(raw.get("harness"), "harness")
    if harness.name not in SUPPORTED_HARNESSES:
        allowed = ", ".join(sorted(SUPPORTED_HARNESSES))
        raise ConfigError(f"Unsupported harness '{harness.name}'. Expected one of: {allowed}")

    provider = _parse_named(raw.get("provider"), "provider")
    if provider.name not in PROVIDER_KEY_ENV:
        allowed = ", ".join(sorted(PROVIDER_KEY_ENV))
        raise ConfigError(f"Unsupported provider '{provider.name}'. Expected one of: {allowed}")
    if provider.name == "vertex":
        for param in ("project", "location"):
            if not provider.parameters.get(param):
                raise ConfigError(f"provider 'vertex' requires parameters.{param}")

    model_raw = raw.get("model")
    model = _parse_named(model_raw, "model") if model_raw is not None else Named("", {})

    key_env = PROVIDER_KEY_ENV[provider.name]
    if not os.getenv(key_env):
        raise ConfigError(
            f"Missing API key env {key_env}. Add it to {root / '.env'} or your shell."
        )

    from ahl.capabilities import parse_capabilities
    from ahl.packages import parse_packages
    from ahl.workspace import parse_workspace

    workspace = parse_workspace(raw.get("workspace"), root)
    capabilities = parse_capabilities(raw.get("capabilities"), root)
    packages = parse_packages(raw.get("packages"), root)

    return RunConfig(
        root=root,
        harness=harness,
        provider=provider,
        model=model,
        workspace=workspace,
        capabilities=capabilities,
        packages=packages,
    )


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _parse_named(value: Any, key: str) -> Named:
    if isinstance(value, str) and value:
        return Named(value, {})
    if isinstance(value, dict):
        name = value.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError(f"Missing or invalid '{key}.name'")
        params = value.get("parameters") or {}
        if not isinstance(params, dict):
            raise ConfigError(f"'{key}.parameters' must be a mapping")
        return Named(name, params)
    raise ConfigError(f"'{key}' must be a string or a mapping with 'name'")
