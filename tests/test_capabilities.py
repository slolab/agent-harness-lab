from __future__ import annotations

from pathlib import Path

import pytest

from ahl.capabilities import parse_capabilities
from ahl.config import ConfigError


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


def test_parse_capabilities_remote_npx_requires_source(tmp_path: Path):
    with pytest.raises(ConfigError, match="requires 'source'"):
        parse_capabilities(
            [{"kind": "skill", "name": "remote-skill", "install": "npx"}],
            tmp_path,
        )


def test_parse_capabilities_remote_source_must_be_nonempty(tmp_path: Path):
    with pytest.raises(ConfigError, match="non-empty string"):
        parse_capabilities(
            [
                {
                    "kind": "skill",
                    "name": "remote-skill",
                    "install": "npx",
                    "source": "",
                }
            ],
            tmp_path,
        )


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


def _make_plugin(root: Path, skill_names: list[str]) -> Path:
    plugin = root / "my-plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "my-plugin"}')
    for name in skill_names:
        skill = plugin / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n\n# {name}\n")
    return plugin


def test_parse_capabilities_plugin_expands_to_all_skills(tmp_path: Path):
    plugin = _make_plugin(tmp_path, ["alpha", "beta", "gamma"])
    caps = parse_capabilities(
        [{"kind": "plugin", "name": "my-plugin", "install": "mount", "path": str(plugin)}],
        tmp_path,
    )
    assert [c.name for c in caps] == ["alpha", "beta", "gamma"]
    assert all(c.kind == "skill" and c.install == "mount" for c in caps)
    assert caps[0].path == plugin / "skills" / "alpha"


def test_parse_capabilities_plugin_without_manifest_rejected(tmp_path: Path):
    bare = tmp_path / "bare"
    (bare / "skills" / "s").mkdir(parents=True)
    (bare / "skills" / "s" / "SKILL.md").write_text("---\nname: s\n---\n")
    with pytest.raises(ConfigError, match="plugin.json"):
        parse_capabilities(
            [{"kind": "plugin", "name": "bare", "install": "mount", "path": str(bare)}],
            tmp_path,
        )


def test_parse_capabilities_plugin_with_no_skills_rejected(tmp_path: Path):
    plugin = _make_plugin(tmp_path, [])
    with pytest.raises(ConfigError, match="no skills"):
        parse_capabilities(
            [{"kind": "plugin", "name": "my-plugin", "install": "mount", "path": str(plugin)}],
            tmp_path,
        )


def test_parse_capabilities_local_plugin_skills_subset(tmp_path: Path):
    plugin = _make_plugin(tmp_path, ["alpha", "beta", "gamma"])
    caps = parse_capabilities(
        [{
            "kind": "plugin", "name": "my-plugin", "install": "mount",
            "path": str(plugin), "skills": ["gamma", "alpha"],
        }],
        tmp_path,
    )
    # Order follows the requested `skills:` list, not directory order.
    assert [c.name for c in caps] == ["gamma", "alpha"]
    assert caps[0].path == plugin / "skills" / "gamma"


def test_parse_capabilities_local_plugin_unknown_skill_rejected(tmp_path: Path):
    plugin = _make_plugin(tmp_path, ["alpha", "beta"])
    with pytest.raises(ConfigError, match="skills not found"):
        parse_capabilities(
            [{
                "kind": "plugin", "name": "my-plugin", "install": "mount",
                "path": str(plugin), "skills": ["alpha", "missing"],
            }],
            tmp_path,
        )


def test_parse_capabilities_remote_plugin_without_source_rejected(tmp_path: Path):
    with pytest.raises(ConfigError, match="requires 'source'"):
        parse_capabilities(
            [{"kind": "plugin", "name": "biotope", "install": "npx", "skills": ["a"]}],
            tmp_path,
        )


def test_parse_capabilities_remote_plugin_without_skills_rejected(tmp_path: Path):
    with pytest.raises(ConfigError, match="requires a 'skills' list"):
        parse_capabilities(
            [{
                "kind": "plugin", "name": "biotope", "install": "npx",
                "source": "https://github.com/biocypher/biotope",
            }],
            tmp_path,
        )


def test_parse_capabilities_remote_plugin_empty_skills_rejected(tmp_path: Path):
    with pytest.raises(ConfigError, match="non-empty list"):
        parse_capabilities(
            [{
                "kind": "plugin", "name": "biotope", "install": "npx",
                "source": "https://github.com/biocypher/biotope", "skills": [],
            }],
            tmp_path,
        )
