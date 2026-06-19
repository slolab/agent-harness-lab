"""Workspace resolution — `workspace:` in config.yaml.

Mirrors the `mount`/`copy` vocabulary already used by `capabilities.py`/
`packages.py`. AHL's purpose is isolated, repeatable, debuggable testing for
people building skills/MCP servers — not driving a persistent real project —
so `copy` (a fresh per-run snapshot of a template, or an empty dir if no
template is given at all) is the default, not `mount`.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ahl.config import ConfigError

INSTALL_MODES = {"mount", "copy"}


@dataclass(frozen=True)
class Workspace:
    path: Path | None
    install: str


def parse_workspace(value: Any, root: Path) -> Workspace:
    if value is None:
        return Workspace(path=None, install="copy")
    if isinstance(value, str):
        if not value:
            raise ConfigError("'workspace' must be a non-empty string, a mapping, or omitted")
        return Workspace(path=_resolve_path(value, root), install="copy")
    if isinstance(value, dict):
        install = value.get("install", "copy")
        if install not in INSTALL_MODES:
            raise ConfigError(f"'workspace.install' must be one of {sorted(INSTALL_MODES)}")
        raw_path = value.get("path")
        if raw_path is None:
            if install == "mount":
                raise ConfigError("'workspace.install: mount' requires 'path'")
            return Workspace(path=None, install=install)
        if not isinstance(raw_path, str) or not raw_path:
            raise ConfigError("'workspace.path' must be a non-empty string")
        return Workspace(path=_resolve_path(raw_path, root), install=install)
    raise ConfigError("'workspace' must be a string, a mapping, or omitted")


def _resolve_path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def resolve_workspace(run_dir: Path, workspace: Workspace) -> Path:
    """Return the host directory to mount at /workspace, materializing it if needed.

    `mount`: the template directory itself, live and persistent (created if
    missing). `copy`: a fresh snapshot of the template under
    `run_dir/workspace/`, or an empty directory there if no template was given
    — the template itself is never written to.
    """
    if workspace.install == "mount":
        assert workspace.path is not None  # enforced at parse time
        workspace.path.mkdir(parents=True, exist_ok=True)
        return workspace.path

    dest = run_dir / "workspace"
    if workspace.path is not None:
        if not workspace.path.is_dir():
            raise ConfigError(f"workspace template does not exist or is not a directory: {workspace.path}")
        shutil.copytree(workspace.path, dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)
    return dest
