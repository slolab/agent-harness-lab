from __future__ import annotations

from pathlib import Path

import pytest

from ahl.capabilities import parse_capabilities, render_skill_fallback
from ahl.config import ConfigError


def test_parse_capabilities_none_returns_empty(tmp_path: Path):
    assert parse_capabilities(None, tmp_path) == []


def test_parse_capabilities_valid_skill(tmp_path: Path, skill_dir: Path):
    caps = parse_capabilities(
        [{"kind": "skill", "name": "my-skill", "install": "mount", "path": str(skill_dir)}],
        tmp_path,
    )
    assert len(caps) == 1
    assert caps[0].kind == "skill"
    assert caps[0].path == skill_dir


def test_parse_capabilities_relative_path_resolved_against_root(tmp_path: Path, skill_dir: Path):
    rel = skill_dir.relative_to(tmp_path)
    caps = parse_capabilities(
        [{"kind": "skill", "name": "my-skill", "install": "mount", "path": str(rel)}],
        tmp_path,
    )
    assert caps[0].path == skill_dir


def test_parse_capabilities_valid_mcp(tmp_path: Path):
    caps = parse_capabilities(
        [{"kind": "mcp", "name": "my-mcp", "install": "pip", "command": "my-mcp-server"}],
        tmp_path,
    )
    assert caps[0].kind == "mcp"
    assert caps[0].command == "my-mcp-server"


def test_parse_capabilities_not_a_list(tmp_path: Path):
    with pytest.raises(ConfigError, match="must be a list"):
        parse_capabilities({"kind": "skill"}, tmp_path)


def test_parse_capabilities_invalid_kind(tmp_path: Path, skill_dir: Path):
    with pytest.raises(ConfigError, match="'kind'"):
        parse_capabilities(
            [{"kind": "not-a-kind", "name": "x", "install": "mount", "path": str(skill_dir)}],
            tmp_path,
        )


def test_parse_capabilities_invalid_install(tmp_path: Path, skill_dir: Path):
    with pytest.raises(ConfigError, match="'install'"):
        parse_capabilities(
            [{"kind": "skill", "name": "x", "install": "not-an-install-mode", "path": str(skill_dir)}],
            tmp_path,
        )


def test_parse_capabilities_skill_rejects_pip_install(tmp_path: Path):
    with pytest.raises(ConfigError, match="only supports install"):
        parse_capabilities(
            [{"kind": "skill", "name": "x", "install": "pip"}],
            tmp_path,
        )


def test_parse_capabilities_mcp_rejects_copy_install(tmp_path: Path):
    with pytest.raises(ConfigError, match="only supports install"):
        parse_capabilities(
            [{"kind": "mcp", "name": "x", "install": "copy"}],
            tmp_path,
        )


def test_parse_capabilities_valid_skill_copy_install(tmp_path: Path, skill_dir: Path):
    caps = parse_capabilities(
        [{"kind": "skill", "name": "my-skill", "install": "copy", "path": str(skill_dir)}],
        tmp_path,
    )
    assert caps[0].install == "copy"
    assert caps[0].path == skill_dir


def test_parse_capabilities_mount_requires_path(tmp_path: Path):
    with pytest.raises(ConfigError, match="requires 'path'"):
        parse_capabilities(
            [{"kind": "skill", "name": "x", "install": "mount"}],
            tmp_path,
        )


def test_parse_capabilities_path_must_exist(tmp_path: Path):
    with pytest.raises(ConfigError, match="does not exist"):
        parse_capabilities(
            [{"kind": "skill", "name": "x", "install": "mount", "path": str(tmp_path / "nope")}],
            tmp_path,
        )


def test_render_skill_fallback_reads_skill_md(skill_dir: Path):
    text = render_skill_fallback(skill_dir)
    assert "my-skill" in text
    assert "Do the thing." in text


def test_render_skill_fallback_missing_skill_md(tmp_path: Path):
    empty = tmp_path / "empty-skill"
    empty.mkdir()
    with pytest.raises(ConfigError, match="no SKILL.md"):
        render_skill_fallback(empty)
