from __future__ import annotations

from pathlib import Path

from ahl.docker import docker_run_args


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
        readonly_volumes=[(tmp_path / "skill", "/workspace/.claude/skills/my-skill")],
    )

    assert args[:4] == ["docker", "run", "--rm", "-it"]
    assert f"{workspace_dir}:/workspace" in args
    assert "--name" in args and args[args.index("--name") + 1] == "ahl-test"
    assert f"{tmp_path / 'skills'}:/workspace/.claude/skills" in args
    assert f"{tmp_path / 'skill'}:/workspace/.claude/skills/my-skill:ro" in args
    assert "ANTHROPIC_API_KEY=test-key" in args
    assert {
        "GIT_AUTHOR_NAME=Agent Harness Lab",
        "GIT_AUTHOR_EMAIL=ahl@localhost",
        "GIT_COMMITTER_NAME=Agent Harness Lab",
        "GIT_COMMITTER_EMAIL=ahl@localhost",
    } <= set(args)
    assert args[-3:] == ["agent-harness-lab:claude", "/usr/local/bin/init-firewall.sh", "bash"]


def test_docker_run_args_no_extra_volumes_or_name(make_config, tmp_path: Path):
    config = make_config(harness="gemini", provider="gemini")
    args = docker_run_args(config, {"GEMINI_API_KEY": "test-key"}, tmp_path / "ws")
    assert "--name" not in args
    assert args.count("-v") == 1  # only the workspace mount


def test_docker_run_args_includes_adapter_specific_args(make_config, tmp_path: Path):
    config = make_config(harness="claude-science", provider="anthropic", api_key="")
    extra_args = ["--platform", "linux/amd64", "-p", "127.0.0.1:8000:8000"]
    args = docker_run_args(config, {}, tmp_path / "ws", extra_args=extra_args)
    assert args[4:8] == extra_args


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


def test_docker_run_args_detached_hold_uses_sleep_infinity(make_config, tmp_path: Path):
    config = make_config(harness="claude", provider="anthropic")
    args = docker_run_args(
        config,
        {"ANTHROPIC_API_KEY": "test-key"},
        tmp_path / "ws",
        name="ahl-test",
        detached=True,
        hold=True,
    )

    assert "-d" in args
    assert "-it" not in args
    assert args[-3:] == ["/usr/local/bin/init-firewall.sh", "sleep", "infinity"]
