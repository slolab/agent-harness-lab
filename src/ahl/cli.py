"""Agent Harness Lab CLI."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer

from ahl.config import ConfigError, RunConfig, load_config
from ahl.docker import CONTAINER_WORKSPACE, docker_run_args, dockerfile_path, image_name
from ahl.harnesses import get_adapter
from ahl.packages import wire_packages
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
) -> None:
    """Launch the configured harness in a container shell.

    Builds the image, injects the real provider key, then drops you into an
    interactive shell in the sandbox. Drive the harness by hand; inspect the
    harness's own logs in the workspace afterwards.
    """

    try:
        run_config = load_config(config)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc

    run_id = _run_id(run_config.harness.name)
    run_dir = run_config.root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    try:
        workspace_dir = resolve_workspace(run_dir, run_config.workspace)
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
        package_volumes, setup_commands = wire_packages(run_dir, run_config.packages)
        readonly_volumes += package_volumes
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc

    args = docker_run_args(
        run_config,
        env,
        workspace_dir,
        name=container_name,
        extra_volumes=extra_volumes,
        readonly_volumes=readonly_volumes,
        setup_commands=setup_commands,
    )

    _write_session(run_dir, run_config, run_id, workspace_dir)
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
    for cmd in setup_commands:
        typer.echo(f"Setup: {cmd}")
    typer.echo(f"Start the harness inside the shell with:\n  {adapter.start_command(run_config)}")
    for hint in adapter.start_hints(run_config):
        typer.echo(f"  ({hint})")
    typer.echo("Exit the shell to stop.")

    try:
        subprocess.run(args, check=False)
    except KeyboardInterrupt:
        subprocess.run(["docker", "rm", "-f", container_name], check=False)
    finally:
        trace = adapter.parse_trace(run_dir)
        if trace is not None:
            trace_path = run_dir / "trace.json"
            trace_path.write_text(json.dumps(trace, indent=2, sort_keys=True))
            typer.echo(f"Trace: {trace_path}")


def _build_image(config: RunConfig) -> None:
    image = image_name(config.harness.name)
    dockerfile = dockerfile_path(config.root, config.harness.name)
    typer.echo(f"Building {image} from {dockerfile}")
    subprocess.run(
        ["docker", "build", "-t", image, "-f", str(dockerfile), str(config.root / "docker")],
        check=True,
    )


def _write_session(run_dir: Path, config: RunConfig, run_id: str, workspace_dir: Path) -> None:
    record = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "harness": config.harness.name,
        "provider": config.provider.name,
        "model": config.model.name or None,
        "workspace": str(workspace_dir),
        "workspace_install": config.workspace.install,
        "workspace_template": str(config.workspace.path) if config.workspace.path else None,
    }
    (run_dir / "session.json").write_text(json.dumps(record, indent=2, sort_keys=True))


def _run_id(harness: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{harness}"


if __name__ == "__main__":
    app()
