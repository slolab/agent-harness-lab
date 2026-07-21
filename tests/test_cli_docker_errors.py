from ahl.cli import _docker_daemon_unreachable


def test_docker_daemon_unreachable_matches_common_messages():
    assert _docker_daemon_unreachable(
        "ERROR: failed to connect to the docker API at unix:///Users/vlad/.docker/run/docker.sock; "
        "check if the path is correct and if the daemon is running: "
        "dial unix /Users/vlad/.docker/run/docker.sock: connect: no such file or directory"
    )
    assert _docker_daemon_unreachable(
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
        "Is the docker daemon running?"
    )
    assert not _docker_daemon_unreachable("ERROR: failed to solve: process /bin/sh returned non-zero")
