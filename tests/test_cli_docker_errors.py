from ahl.docker import docker_daemon_unreachable


def test_docker_daemon_unreachable_matches_common_messages():
    assert docker_daemon_unreachable(
        "ERROR: failed to connect to the docker API at unix:///Users/vlad/.docker/run/docker.sock; "
        "check if the path is correct and if the daemon is running: "
        "dial unix /Users/vlad/.docker/run/docker.sock: connect: no such file or directory"
    )
    assert docker_daemon_unreachable(
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
        "Is the docker daemon running?"
    )
    assert not docker_daemon_unreachable("ERROR: failed to solve: process /bin/sh returned non-zero")


def test_failed_docker_launch_does_not_print_provider_credentials(capsys):
    import subprocess

    import pytest
    import typer

    from ahl.cli import _fail_docker_command

    error = subprocess.CalledProcessError(125, ['docker', 'run', '-e',
        'OPENROUTER_API_KEY=test-provider-secret', '--env', 'ANTHROPIC_AUTH_TOKEN=other-secret',
        'agent-harness-lab:deepseek'])
    with pytest.raises(typer.Exit):
        _fail_docker_command(error)
    output = capsys.readouterr().err
    assert 'test-provider-secret' not in output and 'other-secret' not in output
    assert 'exit 125' in output and 'agent-harness-lab:deepseek' in output
