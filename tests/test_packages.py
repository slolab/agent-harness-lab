from __future__ import annotations

from pathlib import Path

import pytest

from ahl.config import ConfigError
from ahl.packages import CONTAINER_PACKAGES_DIR, parse_packages, wire_packages


def test_parse_packages_valid(tmp_path: Path, package_dir: Path):
    pkgs = parse_packages([{"name": "my-package", "path": str(package_dir)}], tmp_path)
    assert pkgs[0].name == "my-package"
    assert pkgs[0].install == "mount"  # default
    assert pkgs[0].path == package_dir


def test_parse_packages_missing_path(tmp_path: Path):
    with pytest.raises(ConfigError, match="missing required 'path'"):
        parse_packages([{"name": "x"}], tmp_path)


def test_wire_packages_mount_binds_host_path_directly(tmp_path: Path, package_dir: Path):
    packages = parse_packages([{"name": "my-package", "install": "mount", "path": str(package_dir)}], tmp_path)
    volumes, setup_commands = wire_packages(tmp_path / "run", packages)

    assert volumes == [(package_dir, f"{CONTAINER_PACKAGES_DIR}/my-package")]
    assert setup_commands == [f"uv tool install --quiet --editable {CONTAINER_PACKAGES_DIR}/my-package"]


def test_wire_packages_copy_snapshots_into_run_dir(tmp_path: Path, package_dir: Path):
    packages = parse_packages([{"name": "my-package", "install": "copy", "path": str(package_dir)}], tmp_path)
    run_dir = tmp_path / "run"
    volumes, _ = wire_packages(run_dir, packages)

    copied = run_dir / "packages" / "my-package"
    assert volumes == [(copied, f"{CONTAINER_PACKAGES_DIR}/my-package")]
    assert (copied / "pyproject.toml").is_file()
