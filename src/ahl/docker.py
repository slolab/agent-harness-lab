"""Generic Docker invocation — harness-agnostic.

Nothing here knows about a specific harness; per-harness env/seed/capability
logic lives in `ahl.harnesses`.
"""

from __future__ import annotations

from pathlib import Path

from ahl.config import RunConfig

IMAGES_DIR = Path(__file__).parent / "images"
IMAGE_REPOSITORY = "agent-harness-lab"
INIT_SCRIPT = "/usr/local/bin/init-firewall.sh"
CONTAINER_WORKSPACE = "/workspace"
GIT_IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": "Agent Harness Lab",
    "GIT_AUTHOR_EMAIL": "ahl@localhost",
    "GIT_COMMITTER_NAME": "Agent Harness Lab",
    "GIT_COMMITTER_EMAIL": "ahl@localhost",
}

Volumes = list[tuple[Path, str]]


def image_name(harness: str) -> str:
    return f"{IMAGE_REPOSITORY}:{harness}"


def dockerfile_path(harness: str) -> Path:
    return IMAGES_DIR / f"{harness}.Dockerfile"


def docker_run_args(
    config: RunConfig,
    env: dict[str, str],
    workspace_dir: Path,
    *,
    image: str,
    name: str | None = None,
    extra_volumes: Volumes | None = None,
    readonly_volumes: Volumes | None = None,
    setup_commands: list[str] | None = None,
    detached: bool = False,
    hold: bool = False,
    extra_args: list[str] | None = None,
) -> list[str]:
    """`readonly_volumes` keep host checkouts under development safe from the agent; the workspace is always writable."""
    args = [
        "docker",
        "run",
        "--rm",
    ]
    if detached:
        args.append("-d")
    else:
        args.append("-it")
    args.extend(extra_args or [])
    args.extend(
        [
            "-v",
            f"{workspace_dir}:{CONTAINER_WORKSPACE}",
            "-w",
            CONTAINER_WORKSPACE,
        ]
    )
    if name:
        args.extend(["--name", name])
    if config.network:
        args.extend(["--network", config.network])
    for mount in config.mounts:
        args.extend(["-v", f"{mount.path}:{mount.target}{':ro' if mount.readonly else ''}"])
    for host, container in extra_volumes or []:
        args.extend(["-v", f"{host}:{container}"])
    for host, container in readonly_volumes or []:
        args.extend(["-v", f"{host}:{container}:ro"])
    for key, value in {**GIT_IDENTITY_ENV, **config.env, **env}.items():
        args.extend(["-e", f"{key}={value}"])
    args.append(image)
    if hold or detached:
        args.extend([INIT_SCRIPT, "sleep", "infinity"])
    elif setup_commands:
        shell_cmd = " && ".join([*setup_commands, "exec bash"])
        args.extend([INIT_SCRIPT, "sh", "-c", shell_cmd])
    else:
        args.extend([INIT_SCRIPT, "bash"])
    return args
