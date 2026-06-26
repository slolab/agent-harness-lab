"""Local package preinstall — `packages:` in config.yaml.

Distinct from `capabilities.py`'s `Capability` (agent-facing skill/MCP
wiring, per-harness): a `Package` is harness-agnostic plumbing — install
a local checkout that's still under active development and isn't published
anywhere yet. CLIs (`[project.scripts]`) are installed with `uv tool
install`; libraries (no console entrypoints) with `uv pip install
--system`. Wiring is identical regardless of which harness's container it
runs in, so it lives outside `ahl.harnesses` and is applied once in
`cli.py`.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ahl.config import ConfigError
from ahl.docker import Volumes

CONTAINER_PACKAGES_DIR = "/opt/ahl-packages"
INSTALL_MODES = {"mount", "copy"}


@dataclass(frozen=True)
class Package:
    name: str
    install: str
    path: Path


@dataclass(frozen=True)
class PackageCopy:
    """Host directory to `docker cp` into the container (install: copy)."""

    host_path: Path
    container_path: str


def parse_packages(raw: Any, root: Path) -> list[Package]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError("'packages' must be a list")
    return [_parse_package(item, root) for item in raw]


def _parse_package(item: Any, root: Path) -> Package:
    if not isinstance(item, dict):
        raise ConfigError("each 'packages' entry must be a mapping")

    name = item.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError("package entry missing required 'name'")

    install = item.get("install", "mount")
    if install not in INSTALL_MODES:
        raise ConfigError(f"package '{name}': 'install' must be one of {sorted(INSTALL_MODES)}")

    raw_path = item.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ConfigError(f"package '{name}': missing required 'path'")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_dir():
        raise ConfigError(f"package '{name}': path does not exist or is not a directory: {path}")

    return Package(name=name, install=install, path=path)


def wire_packages(
    run_dir: Path, packages: list[Package]
) -> tuple[Volumes, list[PackageCopy], list[str]]:
    """Resolve packages to mounts, container copies, and setup commands.

    `install: mount` bind-mounts the host checkout read-only (hot reload on
    host; editable install must not write into the source — hatchling OK,
  setuptools needs `install: copy`).

    `install: copy` snapshots into `run_dir/packages/<name>/` on the host,
    then `cli.py` `docker cp`s that tree into the container filesystem (no
    mount — writable, so setuptools editable install works).
    """
    volumes: Volumes = []
    copies: list[PackageCopy] = []
    setup_commands: list[str] = []
    for pkg in packages:
        container_path = f"{CONTAINER_PACKAGES_DIR}/{pkg.name}"
        if pkg.install == "mount":
            volumes.append((pkg.path, container_path))
        else:  # copy
            copied = _copy_package(pkg, run_dir / "packages")
            copies.append(PackageCopy(host_path=copied, container_path=container_path))
        setup_commands.append(_package_setup_command(pkg.path, container_path))
    return volumes, copies, setup_commands


def _has_cli_entrypoints(path: Path) -> bool:
    pyproject = path / "pyproject.toml"
    if not pyproject.is_file():
        return False
    data = tomllib.loads(pyproject.read_text())
    project = data.get("project", {})
    if project.get("scripts") or project.get("gui-scripts"):
        return True
    entry_points = project.get("entry-points", {})
    return bool(entry_points.get("console_scripts") or entry_points.get("gui_scripts"))


def _package_setup_command(pkg_path: Path, container_path: str) -> str:
    if _has_cli_entrypoints(pkg_path):
        return f"uv tool install --quiet --editable {container_path}"
    return f"uv pip install --system --break-system-packages --quiet --editable {container_path}"


def _copy_package(pkg: Package, dest_dir: Path) -> Path:
    dest = dest_dir / pkg.name
    if dest.exists():
        shutil.rmtree(dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=pkg.path,
            capture_output=True,
            text=True,
            check=True,
        )
        rel_paths = [p for p in result.stdout.splitlines() if p]
        dest.mkdir(parents=True, exist_ok=True)
        for rel in rel_paths:
            src = pkg.path / rel
            dst = dest / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_file() or src.is_symlink():
                shutil.copy2(src, dst, follow_symlinks=False)
    except subprocess.CalledProcessError:
        # Not a git repo — fall back to plain copytree
        shutil.copytree(pkg.path, dest)
    return dest
