"""Agent Harness Lab CLI."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import typer

from ahl.config import ConfigError, RunConfig, load_config
from ahl.docker import CONTAINER_WORKSPACE, docker_run_args, dockerfile_path, image_name
from ahl.harnesses import get_adapter
from ahl.packages import PackageCopy, wire_packages
from ahl.workspace import resolve_workspace

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Agent Harness Lab — isolated harness shell for hands-on tool testing."""


@app.command()
def up(
    config: Annotated[
        Path, typer.Option("--config", "-c", help="Path to local config.yaml")
    ] = Path("config.yaml"),
    build: Annotated[
        bool, typer.Option("--build/--no-build", help="Build harness image before launching")
    ] = True,
    name: Annotated[
        str | None, typer.Option("--name", "-n", help="Name this run instead of the default timestamp id")
    ] = None,
    resume: Annotated[
        str | None,
        typer.Option("--resume", help="Continue a previous run by name, reusing its workspace and harness state"),
    ] = None,
) -> None:
    """Launch the configured harness in a container shell.

    Builds the image, injects the real provider key, then drops you into an
    interactive shell in the sandbox. Drive the harness by hand; inspect the
    harness's own logs in the workspace afterwards.

    `--resume <name>` continues a previous run in its existing `runs/<name>/`
    directory instead of starting fresh: the workspace and harness home/config
    state from that run are reused as-is (skills are still re-wired so updates
    apply), so a conversation/session can keep going where it left off.
    """

    if resume and name:
        raise typer.BadParameter("--resume and --name are mutually exclusive")

    try:
        run_config = load_config(config)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if resume:
        run_id = resume
        run_dir = run_config.root / "runs" / run_id
        if not run_dir.is_dir():
            raise typer.BadParameter(f"no run named '{run_id}' found at {run_dir}")
        _check_resumable(run_dir, run_config)
    else:
        run_id = name or _run_id(run_config.harness.name)
        run_dir = run_config.root / "runs" / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise typer.BadParameter(
                f"run '{run_id}' already exists at {run_dir} (use --resume to continue it)"
            ) from exc

    try:
        workspace_dir = resolve_workspace(run_dir, run_config.workspace, resume=bool(resume))
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if build:
        _build_image(run_config)

    adapter = get_adapter(run_config.harness.name)
    container_name = f"ahl-{run_id}"
    env = adapter.build_env(run_config)

    try:
        extra_volumes = adapter.seed(run_dir, run_config)
        readonly_volumes = adapter.wire_capabilities(run_dir, run_config, run_config.capabilities)
        package_volumes, package_copies, setup_commands = wire_packages(run_dir, run_config.packages)
        readonly_volumes += package_volumes
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc

    use_copy_flow = bool(package_copies)
    args = docker_run_args(
        run_config,
        env,
        workspace_dir,
        name=container_name,
        extra_volumes=extra_volumes,
        readonly_volumes=readonly_volumes,
        setup_commands=None if use_copy_flow else setup_commands,
        detached=use_copy_flow,
        hold=use_copy_flow,
    )

    _write_session(run_dir, run_config, run_id, workspace_dir, resumed=bool(resume))
    typer.echo(f"AHL session: {run_id}")
    typer.echo(f"Provider: {run_config.provider.name} | Model: {run_config.model.name or '(harness default)'}")
    if run_config.workspace.install == "copy":
        template = run_config.workspace.path
        typer.echo(
            f"Workspace: {workspace_dir} -> {CONTAINER_WORKSPACE} "
            f"(copy{f' of {template}' if template else ' — no template, empty'}, "
            f"original untouched)"
        )
    else:
        typer.echo(f"Workspace: {workspace_dir} -> {CONTAINER_WORKSPACE}")
    for host, container in extra_volumes:
        typer.echo(f"Mount: {host} -> {container}")
    for host, container in readonly_volumes:
        typer.echo(f"Mount: {host} -> {container} (ro)")
    for copy in package_copies:
        typer.echo(f"Copy: {copy.host_path} -> {copy.container_path}")
    for cmd in setup_commands:
        typer.echo(f"Setup: {cmd}")
    typer.echo(f"Start the harness inside the shell with:\n  {adapter.start_command(run_config)}")
    for hint in adapter.start_hints(run_config):
        typer.echo(f"  ({hint})")
    typer.echo("Exit the shell to stop.")

    try:
        if use_copy_flow:
            _run_with_copied_packages(container_name, args, package_copies, setup_commands)
        else:
            subprocess.run(args, check=False)
    except KeyboardInterrupt:
        subprocess.run(["docker", "rm", "-f", container_name], check=False)
    finally:
        trace = adapter.parse_trace(run_dir)
        if trace is not None:
            trace_path = run_dir / "trace.json"
            trace_path.write_text(json.dumps(trace, indent=2, sort_keys=True))
            typer.echo(f"Trace: {trace_path}")


def _run_with_copied_packages(
    container_name: str,
    run_args: list[str],
    package_copies: list[PackageCopy],
    setup_commands: list[str],
) -> None:
    """Start detached, docker cp packages in, setup, then interactive bash."""
    subprocess.run(run_args, check=True)
    try:
        for copy in package_copies:
            subprocess.run(
                ["docker", "exec", container_name, "mkdir", "-p", copy.container_path],
                check=True,
            )
            subprocess.run(
                ["docker", "cp", f"{copy.host_path}/.", f"{container_name}:{copy.container_path}/"],
                check=True,
            )
        if setup_commands:
            subprocess.run(
                ["docker", "exec", container_name, "sh", "-c", " && ".join(setup_commands)],
                check=True,
            )
        subprocess.run(["docker", "exec", "-it", container_name, "bash"], check=False)
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], check=False)


def _build_image(config: RunConfig) -> None:
    image = image_name(config.harness.name)
    dockerfile = dockerfile_path(config.root, config.harness.name)
    typer.echo(f"Building {image} from {dockerfile}")
    subprocess.run(
        ["docker", "build", "-t", image, "-f", str(dockerfile), str(config.root / "docker")],
        check=True,
    )


def _check_resumable(run_dir: Path, config: RunConfig) -> None:
    """Guard against resuming a run directory laid out for a different harness.

    `run_dir/<harness>/` and the volume mounts derived from it are
    harness-specific (see each adapter's `seed`); resuming under a different
    harness would mount the wrong adapter's state into the wrong places.
    """
    session_path = run_dir / "session.json"
    if not session_path.is_file():
        return
    try:
        previous = json.loads(session_path.read_text())
    except json.JSONDecodeError:
        return
    previous_harness = previous.get("harness")
    if previous_harness and previous_harness != config.harness.name:
        raise typer.BadParameter(
            f"run '{run_dir.name}' was started with harness '{previous_harness}', "
            f"but config.yaml now selects '{config.harness.name}' — can't resume across harnesses"
        )


def _write_session(run_dir: Path, config: RunConfig, run_id: str, workspace_dir: Path, *, resumed: bool) -> None:
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
        "run_id": run_id,
        "started_at": started_at,
        "harness": config.harness.name,
        "provider": config.provider.name,
        "model": config.model.name or None,
        "workspace": str(workspace_dir),
        "workspace_install": config.workspace.install,
        "workspace_template": str(config.workspace.path) if config.workspace.path else None,
    }
    if resumed_at:
        record["resumed_at"] = resumed_at
    session_path.write_text(json.dumps(record, indent=2, sort_keys=True))


def _run_id(harness: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{harness}"


if __name__ == "__main__":
    app()
