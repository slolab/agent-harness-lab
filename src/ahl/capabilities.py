"""Capability bundle config model — see docs/capability-format.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ahl.config import ConfigError

KINDS = {"skill", "mcp", "plugin"}
INSTALL_MODES = {"mount", "copy", "npx", "pip"}

# install: mount  -> bind-mount the bundle straight from its host path (hot reload —
#                     edits on the host show up in the container immediately).
# install: copy   -> install a local bundle through `npx skills` at container start.
# install: npx    -> resolve and install a remote skill through `npx skills`.
# install: pip    -> mcp only; pip-install the package into the harness image.
INSTALL_MODES_BY_KIND = {
    "skill": {"mount", "copy", "npx"},
    "mcp": {"mount", "pip"},
    "plugin": {"mount", "copy"},
}


@dataclass(frozen=True)
class Capability:
    kind: str
    name: str
    install: str
    path: Path | None = None
    source: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    version: str | None = None


def parse_capabilities(raw: Any, root: Path) -> list[Capability]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError("'capabilities' must be a list")
    caps: list[Capability] = []
    for item in raw:
        if isinstance(item, dict) and item.get("kind") == "plugin":
            caps.extend(_expand_plugin(item, root))
        else:
            caps.append(_parse_capability(item, root))
    return caps


def _expand_plugin(item: dict, root: Path) -> list[Capability]:
    """Expand a Claude Code plugin into one skill Capability per bundled skill.

    A plugin (`.claude-plugin/plugin.json` + `skills/<name>/SKILL.md`) ships a
    whole skill set, so referencing it once is the correct unit — cherry-picking
    individual skills out of it is not. Expansion happens here at parse time, so
    every harness's existing skill wiring handles the result unchanged.
    """
    name = item.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError("plugin entry missing required 'name'")
    install = item.get("install")
    if install not in INSTALL_MODES_BY_KIND["plugin"]:
        raise ConfigError(
            f"plugin '{name}': 'install' must be one of "
            f"{sorted(INSTALL_MODES_BY_KIND['plugin'])}"
        )
    raw_path = item.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ConfigError(f"plugin '{name}': install: {install} requires 'path'")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not (path / ".claude-plugin" / "plugin.json").is_file():
        raise ConfigError(f"plugin '{name}': no .claude-plugin/plugin.json at {path}")
    skills_dir = path / "skills"
    skills = sorted(d for d in skills_dir.iterdir() if (d / "SKILL.md").is_file()) \
        if skills_dir.is_dir() else []
    if not skills:
        raise ConfigError(f"plugin '{name}': no skills/<name>/SKILL.md found under {path}")
    return [
        Capability(kind="skill", name=skill.name, install=install, path=skill)
        for skill in skills
    ]


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

    source = item.get("source")
    if source is not None and (not isinstance(source, str) or not source):
        raise ConfigError(f"capability '{name}': 'source' must be a non-empty string")
    if install == "npx" and source is None:
        raise ConfigError(f"capability '{name}': install: npx requires 'source'")

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
        source=source,
        command=command,
        args=args,
        env=env,
        version=version,
    )
