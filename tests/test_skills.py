from __future__ import annotations

from pathlib import Path

import pytest

from ahl.capabilities import parse_capabilities
from ahl.config import ConfigError
from ahl.skills import wire_delegated_skills


@pytest.mark.parametrize(
    ("harness", "agent"),
    [
        ("claude", "claude-code"),
        ("gemini", "gemini-cli"),
        ("opencode", "opencode"),
        ("agy", "antigravity-cli"),
        ("deepseek", "universal"),
    ],
)
def test_remote_skill_targets_active_harness(harness: str, agent: str, tmp_path: Path):
    skills = parse_capabilities(
        [
            {
                "kind": "skill",
                "name": "web-design-guidelines",
                "install": "npx",
                "source": "vercel-labs/agent-skills",
            }
        ],
        tmp_path,
    )

    volumes, commands = wire_delegated_skills(harness, skills)

    assert volumes == []
    assert commands == [
        "npx --yes skills add vercel-labs/agent-skills "
        f"--skill web-design-guidelines --agent {agent} --global --yes"
    ]


def test_local_copy_mounts_source_for_npx_install(tmp_path: Path, skill_dir: Path):
    skills = parse_capabilities(
        [
            {"kind": "skill", "name": "live", "install": "mount", "path": str(skill_dir)},
            {
                "kind": "skill",
                "name": "my-skill",
                "install": "copy",
                "path": str(skill_dir),
            }
        ],
        tmp_path,
    )

    volumes, commands = wire_delegated_skills("claude", skills)

    assert volumes == [(skill_dir, "/opt/ahl-skill-sources/0")]
    assert commands == [
        "npx --yes skills add /opt/ahl-skill-sources/0 "
        "--skill my-skill --agent claude-code --global --yes"
    ]


def test_claude_science_keeps_local_copy_out_of_delegated_flow(
    tmp_path: Path,
    skill_dir: Path,
):
    skills = parse_capabilities(
        [
            {
                "kind": "skill",
                "name": "my-skill",
                "install": "copy",
                "path": str(skill_dir),
            }
        ],
        tmp_path,
    )
    assert wire_delegated_skills("claude-science", skills) == ([], [])


def test_claude_science_rejects_remote_npx_skill(tmp_path: Path):
    skills = parse_capabilities(
        [
            {
                "kind": "skill",
                "name": "web-design-guidelines",
                "install": "npx",
                "source": "vercel-labs/agent-skills",
            }
        ],
        tmp_path,
    )
    with pytest.raises(ConfigError, match="not supported.*claude-science"):
        wire_delegated_skills("claude-science", skills)


def test_remote_plugin_expands_into_per_skill_npx_commands(tmp_path: Path):
    skills = parse_capabilities(
        [
            {
                "kind": "plugin",
                "name": "biotope",
                "install": "npx",
                "source": "https://github.com/biocypher/biotope",
                "skills": ["biotope-croissant", "biocypher"],
            }
        ],
        tmp_path,
    )

    assert [cap.name for cap in skills] == ["biotope-croissant", "biocypher"]
    assert all(cap.kind == "skill" and cap.install == "npx" and cap.path is None for cap in skills)
    volumes, commands = wire_delegated_skills("claude", skills)

    assert volumes == []
    assert commands == [
        "npx --yes skills add https://github.com/biocypher/biotope "
        "--skill biotope-croissant --agent claude-code --global --yes",
        "npx --yes skills add https://github.com/biocypher/biotope "
        "--skill biocypher --agent claude-code --global --yes",
    ]


def test_unknown_harness_rejects_delegated_skill(tmp_path: Path):
    skills = parse_capabilities(
        [
            {
                "kind": "skill",
                "name": "remote-skill",
                "install": "npx",
                "source": "owner/repo",
            }
        ],
        tmp_path,
    )
    with pytest.raises(ConfigError, match="not supported for harness 'unknown'"):
        wire_delegated_skills("unknown", skills)
