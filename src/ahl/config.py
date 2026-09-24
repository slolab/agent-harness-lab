"""Configuration loading for Agent Harness Lab."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ahl.permissions import PermissionPolicy, parse_permissions

if TYPE_CHECKING:
    from ahl.capabilities import Capability
    from ahl.mounts import Mount
    from ahl.packages import Package
    from ahl.workspace import Workspace


SUPPORTED_HARNESSES = {"claude", "claude-science", "opencode", "agy", "gemini", "deepseek"}


def uses_account_login(harness: str, provider: str) -> bool:
    """Claude's Anthropic route uses account login; gateways use credentials."""
    return harness in {"claude", "claude-science"} and provider == "anthropic"


# Provider name -> host env var holding the real API key (read from .env).
PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
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
    mounts: list["Mount"]
    network: str | None
    env: dict[str, str]
    permissions: PermissionPolicy = PermissionPolicy()

    @property
    def key_env(self) -> str:
        return PROVIDER_KEY_ENV[self.provider.name]


def resolve_config_path(value: str, root: Path) -> Path:
    return (root / Path(value).expanduser()).resolve()


def read_config(config_path: Path) -> dict[str, Any]:
    config_path = config_path.expanduser().resolve()
    if not config_path.exists():
        raise ConfigError(f"Missing config file: {config_path}")
    raw = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")
    return raw


def parse_harness(value: Any) -> Named:
    harness = _parse_named(value, "harness")
    if harness.name not in SUPPORTED_HARNESSES:
        allowed = ", ".join(sorted(SUPPORTED_HARNESSES))
        raise ConfigError(f"Unsupported harness '{harness.name}'. Expected one of: {allowed}")
    return harness


def load_config(config_path: Path, env_file: Path | None = None) -> RunConfig:
    config_path = config_path.expanduser().resolve()
    raw = read_config(config_path)
    root = config_path.parent
    env_source = _load_env(root, env_file)

    harness = parse_harness(raw.get("harness"))
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

    if harness.name == "claude-science" and provider.name != "anthropic":
        raise ConfigError(
            f"Harness '{harness.name}' authenticates with a Claude account and requires "
            "provider: anthropic"
        )

    if harness.name == "claude" and provider.name not in {"anthropic", "openrouter"}:
        raise ConfigError("Harness 'claude' requires provider: anthropic or openrouter")
    if provider.name == "openrouter":
        if harness.name not in {"claude", "opencode", "deepseek"}:
            raise ConfigError(f"Harness '{harness.name}' does not support provider: openrouter")
        if not model.name.strip():
            raise ConfigError("provider: openrouter requires an explicit model")
    if harness.name == "deepseek":
        if provider.name != "openrouter":
            raise ConfigError("Harness 'deepseek' requires provider: openrouter")
        port = harness.parameters.get("port", 3080)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ConfigError("DeepSeek port must be an integer from 1 to 65535")

    key_env = PROVIDER_KEY_ENV[provider.name]
    if not uses_account_login(harness.name, provider.name) and not os.getenv(key_env):
        if env_source:
            where = f"it is set neither in {env_source} nor in your shell"
        else:
            where = f"no env file was read (none at {root / '.env'}) and your shell does not set it"
        raise ConfigError(f"Missing API key env {key_env}: {where}.")

    network = raw.get("network")
    if network is not None and (not isinstance(network, str) or not network):
        raise ConfigError("'network' must be the name of an existing Docker network")

    from ahl.capabilities import parse_capabilities
    from ahl.mounts import parse_mounts
    from ahl.packages import parse_packages
    from ahl.workspace import parse_workspace

    config = RunConfig(
        root=root,
        harness=harness,
        provider=provider,
        model=model,
        workspace=parse_workspace(raw.get("workspace"), root),
        capabilities=parse_capabilities(raw.get("capabilities"), root),
        packages=parse_packages(raw.get("packages"), root),
        mounts=parse_mounts(raw.get("mounts"), root),
        network=network,
        env=_parse_env(raw.get("env")),
        permissions=parse_permissions(raw.get("permissions", {})),
    )
    _reject_managed_env(config)
    return config


def _load_env(root: Path, env_file: Path | None) -> Path | None:
    if env_file is not None:
        path = env_file.expanduser().resolve()
        if not path.is_file():
            raise ConfigError(f"Env file not found: {path}")
        load_dotenv(path, override=True)
        return path
    default = root / ".env"
    if default.is_file():
        load_dotenv(default)
        return default
    return None


def load_dotenv(path: Path, *, override: bool = False) -> None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def _parse_env(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("'env' must be a mapping of variable names to string values")
    for name, value in raw.items():
        if not isinstance(value, str):
            raise ConfigError(f"env.{name}: value must be a string, got {type(value).__name__}")
    return dict(raw)


def _reject_managed_env(config: RunConfig) -> None:
    from ahl.harnesses import get_adapter

    if not config.env:
        return
    managed = set(PROVIDER_KEY_ENV.values()) | set(get_adapter(config.harness.name).build_env(config))
    for name in config.env:
        if name in managed:
            raise ConfigError(
                f"env.{name}: AHL sets this variable for harness '{config.harness.name}'"
                f" with provider '{config.provider.name}'; remove it from 'env'"
            )


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
