"""Capability bundle config model — see docs/capability-format.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ahl.config import ConfigError

KINDS = {"skill", "mcp"}
INSTALL_MODES = {"mount", "copy", "pip"}

# install: mount  -> bind-mount the bundle straight from its host path (hot reload —
#                     edits on the host show up in the container immediately).
# install: copy   -> snapshot the bundle into the run dir once, at `ahl up` time.
# install: pip    -> mcp only; pip-install the package into the harness image.
# Marketplace/git/url installs are deferred — not implemented, not validated here.
INSTALL_MODES_BY_KIND = {
    "skill": {"mount", "copy"},
    "mcp": {"mount", "pip"},
}


@dataclass(frozen=True)
class Capability:
    kind: str
    name: str
    install: str
    path: Path | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    version: str | None = None


def parse_capabilities(raw: Any, root: Path) -> list[Capability]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError("'capabilities' must be a list")
    return [_parse_capability(item, root) for item in raw]


def _parse_capability(item: Any, root: Path) -> Capability:
    if not isinstance(item, dict):
        raise ConfigError("each 'capabilities' entry must be a mapping")

    name = item.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError("capability entry missing required 'name'")

    kind = item.get("kind")
    if kind not in KINDS:
        raise ConfigError(f"capability '{name}': 'kind' must be one of {sorted(KINDS)}")

    install = item.get("install")
    if install not in INSTALL_MODES:
        raise ConfigError(f"capability '{name}': 'install' must be one of {sorted(INSTALL_MODES)}")

    allowed_installs = INSTALL_MODES_BY_KIND[kind]
    if install not in allowed_installs:
        raise ConfigError(
            f"capability '{name}': kind '{kind}' only supports install: {sorted(allowed_installs)}"
        )

    path: Path | None = None
    if install in ("mount", "copy"):
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ConfigError(f"capability '{name}': install: {install} requires 'path'")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        if not path.is_dir():
            raise ConfigError(f"capability '{name}': path does not exist or is not a directory: {path}")

    command = item.get("command")
    if command is not None and not isinstance(command, str):
        raise ConfigError(f"capability '{name}': 'command' must be a string")

    args = item.get("args") or []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ConfigError(f"capability '{name}': 'args' must be a list of strings")

    env = item.get("env") or {}
    if not isinstance(env, dict):
        raise ConfigError(f"capability '{name}': 'env' must be a mapping")

    version = item.get("version")
    if version is not None and not isinstance(version, str):
        raise ConfigError(f"capability '{name}': 'version' must be a string")

    return Capability(
        kind=kind,
        name=name,
        install=install,
        path=path,
        command=command,
        args=args,
        env=env,
        version=version,
    )


def render_skill_fallback(skill_dir: Path) -> str:
    """Render a skill's SKILL.md for folding into a harness's context file.

    Used by harnesses with no native skill concept (gemini, opencode, agy).
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise ConfigError(f"skill capability path has no SKILL.md: {skill_dir}")
    return skill_md.read_text()
