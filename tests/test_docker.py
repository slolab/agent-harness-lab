from __future__ import annotations

from pathlib import Path

from ahl.docker import GIT_IDENTITY_ENV, docker_run_args, dockerfile_path, image_name


def test_image_name():
    assert image_name("claude") == "agent-harness-lab:claude"


def test_dockerfile_path():
    root = Path("/some/root")
    assert dockerfile_path(root, "gemini") == root / "docker" / "gemini.Dockerfile"


def test_docker_run_args_assembles_mounts_and_env(make_config, tmp_path: Path):
    config = make_config(harness="claude", provider="anthropic")
    workspace_dir = tmp_path / "ws"
    extra = [(tmp_path / "skills", "/workspace/.claude/skills")]
    args = docker_run_args(
        config,
        {"ANTHROPIC_API_KEY": "test-key"},
        workspace_dir,
        name="ahl-test",
        extra_volumes=extra,
    )

    assert args[:4] == ["docker", "run", "--rm", "-it"]
    assert f"{workspace_dir}:/workspace" in args
    assert "--name" in args and args[args.index("--name") + 1] == "ahl-test"
    assert f"{tmp_path / 'skills'}:/workspace/.claude/skills" in args
    assert "ANTHROPIC_API_KEY=test-key" in args
    for key, value in GIT_IDENTITY_ENV.items():
        assert f"{key}={value}" in args
    assert args[-3:] == ["agent-harness-lab:claude", "/usr/local/bin/init-firewall.sh", "bash"]


def test_docker_run_args_no_extra_volumes_or_name(make_config, tmp_path: Path):
    config = make_config(harness="gemini", provider="gemini")
    args = docker_run_args(config, {"GEMINI_API_KEY": "test-key"}, tmp_path / "ws")
    assert "--name" not in args
    assert args.count("-v") == 1  # only the workspace mount


def test_docker_run_args_readonly_volumes_get_ro_suffix(make_config, tmp_path: Path):
    config = make_config(harness="claude", provider="anthropic")
    args = docker_run_args(
        config,
        {"ANTHROPIC_API_KEY": "test-key"},
        tmp_path / "ws",
        readonly_volumes=[(tmp_path / "skill", "/workspace/.claude/skills/my-skill")],
    )
    assert f"{tmp_path / 'skill'}:/workspace/.claude/skills/my-skill:ro" in args


def test_docker_run_args_setup_commands_chain_into_exec_bash(make_config, tmp_path: Path):
    config = make_config(harness="claude", provider="anthropic")
    args = docker_run_args(
        config,
        {"ANTHROPIC_API_KEY": "test-key"},
        tmp_path / "ws",
        setup_commands=["uv tool install --quiet --editable /opt/ahl-packages/foo"],
    )

    assert args[-1] == "uv tool install --quiet --editable /opt/ahl-packages/foo && exec bash"
    assert args[-5:-1] == ["agent-harness-lab:claude", "/usr/local/bin/init-firewall.sh", "sh", "-c"]
