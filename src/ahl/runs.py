from __future__ import annotations

import json
import posixpath
import secrets
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

import typer

from ahl.config import RunConfig
from ahl.docker import CONTAINER_WORKSPACE, Volumes, docker_run_args, image_name
from ahl.harnesses import HarnessAdapter, get_adapter
from ahl.packages import PackageCopy, wire_packages
from ahl.permissions import validate_setup
from ahl.skills import wire_delegated_skills
from ahl.workspace import resolve_workspace


@dataclass(frozen=True)
class PreparedRun:
    dir: Path
    config: RunConfig
    adapter: HarnessAdapter
    container: str
    docker_args: list[str]
    workspace: Path
    extra_volumes: Volumes
    readonly_volumes: Volumes
    package_copies: list[PackageCopy]
    setup_commands: list[str]

    @property
    def writable_mounts(self) -> Volumes:
        return [(self.workspace, CONTAINER_WORKSPACE), *self.extra_volumes]

    @property
    def external_mounts(self) -> Volumes:
        return [*self.readonly_volumes, *((mount.path, mount.target) for mount in self.config.mounts)]


def prepare_run(
    config: RunConfig, run_dir: Path, image: dict[str, Any] | None, *, resume: bool, headless: bool
) -> PreparedRun:
    workspace = resolve_workspace(run_dir, config.workspace, resume=resume)
    adapter = get_adapter(config.harness.name)
    env = adapter.build_env(config) | (adapter.driver.env(config) if headless else {})
    extra_volumes = adapter.seed(run_dir, config)
    permissions = adapter.permission_handler.prepare(run_dir, config, config.permissions)
    validate_setup(config.permissions, permissions)
    if permissions.unsupported:
        detail = "; ".join(f"{op}: {reason}" for op, reason in sorted(permissions.unsupported.items()))
        typer.echo(f"[ahl] warning: permissions not applied: {detail}", err=True)
    applied = adapter.driver.applied_model_parameters(config) if adapter.driver else frozenset()
    unsupported = sorted(set(config.model.parameters) - applied)
    for key in unsupported:
        typer.echo(f"[ahl] warning: model.parameters.{key} is not applied by harness '{config.harness.name}'", err=True)
    readonly_volumes = adapter.wire_capabilities(run_dir, config, config.capabilities)
    skill_volumes, skill_commands = wire_delegated_skills(config.harness.name, config.capabilities)
    package_volumes, package_copies, package_commands = wire_packages(run_dir, config.packages)
    readonly_volumes += permissions.readonly_volumes + skill_volumes + package_volumes
    setup_commands = skill_commands + package_commands

    container = f"ahl-{run_dir.name}-{secrets.token_hex(4)}"
    detached = headless or bool(package_copies)
    args = docker_run_args(
        config,
        env,
        workspace,
        name=container,
        extra_volumes=extra_volumes,
        readonly_volumes=readonly_volumes,
        setup_commands=None if detached else setup_commands,
        detached=detached,
        hold=detached,
        extra_args=adapter.docker_args(config),
        image=image["id"] if image else image_name(config.harness.name),
    )
    record: dict[str, Any] = {
        "container": container,
        "image": image,
        "model_parameters": {"unsupported": unsupported},
        "permissions": {
            "deny": sorted(config.permissions.deny),
            "applied": sorted(permissions.applied),
            "unsupported": dict(sorted(permissions.unsupported.items())),
        },
    }
    if headless:
        record["mode"] = "headless"
    _write_session(run_dir, config, workspace, record, resumed=resume)
    run = PreparedRun(
        run_dir, config, adapter, container, args, workspace,
        extra_volumes, readonly_volumes, package_copies, setup_commands,
    )
    _create_mountpoints(run)
    return run


def _create_mountpoints(run: PreparedRun) -> None:
    # Docker creates a missing mountpoint as root, which would leave root-owned paths in a writable mount.
    for source, target in run.external_mounts:
        parents = [(host, path) for host, path in run.writable_mounts if target.startswith(path + "/")]
        if not parents:
            continue
        host, path = max(parents, key=lambda mount: len(mount[1]))
        mountpoint = host / posixpath.relpath(target, path)
        if source.is_dir():
            mountpoint.mkdir(parents=True, exist_ok=True)
        else:
            mountpoint.parent.mkdir(parents=True, exist_ok=True)
            mountpoint.touch(exist_ok=True)


def write_native_trace(run: PreparedRun) -> Path | None:
    trace = run.adapter.parse_trace(run.dir)
    if trace is None:
        return None
    path = run.dir / "trace.json"
    path.write_text(json.dumps(trace, indent=2, sort_keys=True))
    return path


def start_container(run: PreparedRun) -> None:
    subprocess.run(run.docker_args, check=True, stdout=subprocess.DEVNULL)
    for copy in run.package_copies:
        subprocess.run(["docker", "exec", run.container, "mkdir", "-p", copy.container_path], check=True)
        subprocess.run(
            ["docker", "cp", f"{copy.host_path}/.", f"{run.container}:{copy.container_path}/"], check=True
        )
    if run.setup_commands:
        subprocess.run(["docker", "exec", run.container, "sh", "-c", " && ".join(run.setup_commands)], check=True)


def _ahl_record() -> dict[str, Any]:
    source = Path(__file__).resolve()

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(source.parent), *args], capture_output=True, text=True, check=False
        )

    sha = dirty = None
    try:
        # A wheel installed into a virtualenv inside another repository's work tree
        # must not report that repository's commit, so require AHL's own file to be tracked.
        if git("ls-files", "--error-unmatch", source.name).returncode == 0:
            sha = git("rev-parse", "HEAD").stdout.strip()
            dirty = bool(git("status", "--porcelain", "--untracked-files=no").stdout.strip())
    except FileNotFoundError:
        pass
    return {"version": version("agent-harness-lab"), "git_sha": sha, "git_dirty": dirty}


def _write_session(
    run_dir: Path,
    config: RunConfig,
    workspace: Path,
    fields: dict[str, Any],
    *,
    resumed: bool,
) -> None:
    session_path = run_dir / "session.json"
    now = datetime.now(timezone.utc).isoformat()

    started_at = now
    resumed_at: list[str] = []
    if resumed and session_path.is_file():
        try:
            previous = json.loads(session_path.read_text())
        except json.JSONDecodeError:
            previous = {}
        started_at = previous.get("started_at", now)
        resumed_at = list(previous.get("resumed_at", []))
        resumed_at.append(now)

    record: dict[str, Any] = {
        "ahl": _ahl_record(),
        **fields,
        "run_id": run_dir.name,
        "started_at": started_at,
        "harness": config.harness.name,
        "provider": config.provider.name,
        "model": config.model.name or None,
        "workspace": str(workspace),
        "workspace_install": config.workspace.install,
        "workspace_template": str(config.workspace.path) if config.workspace.path else None,
    }
    if resumed_at:
        record["resumed_at"] = resumed_at
    session_path.write_text(json.dumps(record, indent=2, sort_keys=True))
