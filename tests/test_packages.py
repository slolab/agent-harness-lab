from __future__ import annotations

from pathlib import Path

import pytest

from ahl.config import ConfigError
from ahl.packages import (
    CONTAINER_PACKAGES_DIR,
    parse_packages,
    wire_packages,
)


def test_parse_packages_missing_path(tmp_path: Path):
    with pytest.raises(ConfigError, match="missing required 'path'"):
        parse_packages([{"name": "x"}], tmp_path)


def test_wire_packages_mount_binds_host_path_directly(tmp_path: Path, package_dir: Path):
    packages = parse_packages([{"name": "my-package", "path": str(package_dir)}], tmp_path)
    volumes, copies, setup_commands = wire_packages(tmp_path / "run", packages)

    assert volumes == [(package_dir, f"{CONTAINER_PACKAGES_DIR}/my-package")]
    assert copies == []
    assert setup_commands == [
        f"uv pip install --system --break-system-packages --quiet --editable {CONTAINER_PACKAGES_DIR}/my-package"
    ]


def test_wire_packages_cli_uses_uv_tool_install(tmp_path: Path):
    cli_pkg = tmp_path / "my-cli"
    cli_pkg.mkdir()
    (cli_pkg / "pyproject.toml").write_text(
        "[project]\nname = 'my-cli'\nversion = '0.1.0'\n\n"
        "[project.scripts]\nmy-cli = 'my_cli.main:main'\n"
    )
    packages = parse_packages([{"name": "my-cli", "path": str(cli_pkg)}], tmp_path)
    _, _, setup_commands = wire_packages(tmp_path / "run", packages)

    assert setup_commands == [f"uv tool install --quiet --editable {CONTAINER_PACKAGES_DIR}/my-cli"]


def test_wire_packages_copy_snapshots_without_mount(tmp_path: Path, package_dir: Path):
    packages = parse_packages([{"name": "my-package", "install": "copy", "path": str(package_dir)}], tmp_path)
    run_dir = tmp_path / "run"
    volumes, copies, _ = wire_packages(run_dir, packages)

    copied = run_dir / "packages" / "my-package"
    assert volumes == []
    assert len(copies) == 1
    assert copies[0].host_path == copied
    assert copies[0].container_path == f"{CONTAINER_PACKAGES_DIR}/my-package"
    assert (copied / "pyproject.toml").is_file()
