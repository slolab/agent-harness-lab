from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ahl.config import ConfigError, resolve_config_path
from ahl.docker import CONTAINER_WORKSPACE


@dataclass(frozen=True)
class Mount:
    path: Path
    target: str
    readonly: bool


def parse_mounts(raw: Any, root: Path) -> list[Mount]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError("'mounts' must be a list")
    mounts: list[Mount] = []
    for index, item in enumerate(raw):
        entry = f"mounts[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{entry} must be a mapping")
        path, target, readonly = item.get("path"), item.get("target"), item.get("readonly", True)
        if not isinstance(path, str) or not path:
            raise ConfigError(f"{entry}: 'path' must be a non-empty string")
        if not isinstance(target, str) or not posixpath.isabs(target):
            raise ConfigError(f"{entry}: 'target' must be an absolute container path, got {target!r}")
        if not isinstance(readonly, bool):
            raise ConfigError(f"{entry}: 'readonly' must be true or false")
        source = resolve_config_path(path, root)
        if not source.exists():
            raise ConfigError(f"{entry}: path does not exist: {source}")
        target = "/" + posixpath.normpath(target).lstrip("/")
        if target == CONTAINER_WORKSPACE:
            raise ConfigError(f"{entry}: target cannot be {CONTAINER_WORKSPACE} itself")
        if any(m.target == target for m in mounts):
            raise ConfigError(f"{entry}: duplicate target {target}")
        mounts.append(Mount(source, target, readonly))
    return mounts
