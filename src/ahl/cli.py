"""Agent Harness Lab CLI."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from ahl.config import ConfigError, RunConfig, load_config, parse_harness, read_config
from ahl.docker import CONTAINER_WORKSPACE, IMAGES_DIR, docker_run_args, dockerfile_path, image_name
from ahl.harnesses import get_adapter
from ahl.packages import PackageCopy, wire_packages
from ahl.permissions import PermissionSetup, validate_setup
from ahl.skills import wire_delegated_skills
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
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Env file with provider keys; wins over the shell. Default: .env next to the config"),
    ] = None,
    runs_dir: Annotated[
        Path | None,
        typer.Option("--runs-dir", help="Directory holding run directories. Default: runs/ next to the config"),
    ] = None,
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

    Builds the image, configures harness authentication, then drops you into
    an interactive shell in the sandbox. Drive the harness by hand; inspect
    the harness's own logs in the workspace afterwards.

    `--resume <name>` continues a previous run in its existing `<runs dir>/<name>/`
    directory instead of starting fresh: the workspace and harness home/config
    state from that run are reused as-is (skills are still re-wired so updates
    apply), so a conversation/session can keep going where it left off.
    """

    if resume and name:
        raise typer.BadParameter("--resume and --name are mutually exclusive")

    try:
        run_config = load_config(config, env_file)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc

    # Docker Desktop treats non-zero `docker run` exits (e.g. shell exit after
    # Ctrl-C → status 130) as "container errors" and prints Gordon tips.
    os.environ.setdefault("DOCKER_CLI_HINTS", "false")
    _ensure_docker()
    if run_config.network:
        _check_network(run_config.network)
    if build:
        _build_image(run_config.harness.name)
    image = _image_record(run_config.harness.name)

    runs_root = runs_dir.expanduser().resolve() if runs_dir else run_config.root / "runs"
    if resume:
        run_id = resume
        run_dir = runs_root / run_id
        if not run_dir.is_dir():
            raise typer.BadParameter(f"no run named '{run_id}' found at {run_dir}")
        _check_resumable(run_dir, run_config)
    else:
        run_id = name or _run_id(run_config.harness.name)
        run_dir = runs_root / run_id
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

    adapter = get_adapter(run_config.harness.name)
    container_name = f"ahl-{run_id}"
    env = adapter.build_env(run_config)

    try:
        extra_volumes = adapter.seed(run_dir, run_config)
        permissions = adapter.permission_handler.prepare(
            run_dir, run_config, run_config.permissions
        )
        validate_setup(run_config.permissions, permissions)
        if permissions.unsupported:
            detail = "; ".join(
                f"{op}: {reason}" for op, reason in sorted(permissions.unsupported.items())
            )
            typer.echo(f"[ahl] warning: permissions not applied: {detail}", err=True)
        readonly_volumes = adapter.wire_capabilities(run_dir, run_config, run_config.capabilities)
        skill_volumes, skill_commands = wire_delegated_skills(
            run_config.harness.name,
            run_config.capabilities,
        )
        package_volumes, package_copies, package_commands = wire_packages(
            run_dir,
            run_config.packages,
        )
        readonly_volumes += permissions.readonly_volumes
        readonly_volumes += skill_volumes
        readonly_volumes += package_volumes
        readonly_volumes += [(m.path, m.target) for m in run_config.mounts if m.readonly]
        extra_volumes += [(m.path, m.target) for m in run_config.mounts if not m.readonly]
        setup_commands = skill_commands + package_commands
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
        extra_args=adapter.docker_args(run_config),
        image=image["id"],
    )

    _write_session(
        run_dir, run_config, run_id, workspace_dir, permissions, image, resumed=bool(resume)
    )
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
    for hint in adapter.start_hints(run_dir, run_config):
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
    try:
        subprocess.run(run_args, check=True)
        try:
            for copy in package_copies:
                subprocess.run(
                    ["docker", "exec", container_name, "mkdir", "-p", copy.container_path],
                    check=True,
                )
                subprocess.run(
                    [
                        "docker",
                        "cp",
                        f"{copy.host_path}/.",
                        f"{container_name}:{copy.container_path}/",
                    ],
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
    except FileNotFoundError:
        _fail_docker_missing()
    except subprocess.CalledProcessError as exc:
        _fail_docker_command(exc)


@app.command()
def build(
    harness: Annotated[str | None, typer.Option("--harness", help="Harness image to build")] = None,
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Build the harness this config selects")
    ] = None,
) -> None:
    """Build a harness image from the image files shipped with AHL."""
    if (harness is None) == (config is None):
        raise typer.BadParameter("pass exactly one of --harness or --config")
    try:
        selected = parse_harness(harness if config is None else read_config(config).get("harness"))
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _ensure_docker()
    _build_image(selected.name)


def _build_image(harness: str) -> None:
    image = image_name(harness)
    dockerfile = dockerfile_path(harness)
    typer.echo(f"Building {image} from {dockerfile}")
    try:
        subprocess.run(
            ["docker", "build", "-t", image, "-f", str(dockerfile), str(IMAGES_DIR)],
            check=True,
            # Default provenance attestations change the image ID on every build, even a cached one.
            # Unlike --provenance=false, the variable also works with the legacy builder.
            env={**os.environ, "BUILDX_NO_DEFAULT_ATTESTATIONS": "1"},
        )
    except FileNotFoundError:
        _fail_docker_missing()
    except subprocess.CalledProcessError as exc:
        _fail_docker_command(exc)


_DOCKER_MISSING = "Docker CLI not found on PATH. Install Docker and retry."
_DOCKER_UNREACHABLE = (
    "Cannot reach the Docker daemon. Is Docker Desktop (or the docker service) running?"
)


def _docker_daemon_unreachable(text: str) -> bool:
    lower = text.lower()
    return any(
        needle in lower
        for needle in (
            "cannot connect to the docker daemon",
            "failed to connect to the docker api",
            "is the docker daemon running",
        )
    )


def _ensure_docker() -> None:
    """Fail fast with a clear message when Docker isn't usable."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        _fail_docker_missing()
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            typer.echo(detail, err=True)
        typer.secho(_DOCKER_UNREACHABLE, fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


def _fail_docker_missing() -> NoReturn:
    typer.secho(_DOCKER_MISSING, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _fail_docker_command(exc: subprocess.CalledProcessError) -> NoReturn:
    """Exit without a Python traceback after a docker subprocess failure.

    Docker already printed its own error to stderr; add a short hint when the
    failure looks like a stopped daemon, otherwise a one-line summary.
    """
    combined = "\n".join(
        part.decode(errors="replace") if isinstance(part, bytes) else part
        for part in (exc.stderr, exc.stdout)
        if part
    )
    if _docker_daemon_unreachable(combined):
        typer.secho(_DOCKER_UNREACHABLE, fg=typer.colors.RED, err=True)
    else:
        # Docker run argv contains provider credentials. Keep diagnostics useful
        # without copying environment values into terminal output.
        if isinstance(exc.cmd, (list, tuple)):
            safe_args = []
            environment_value = False
            for arg in map(str, exc.cmd):
                if environment_value:
                    arg = arg.split("=", 1)[0] + "=<redacted>"
                safe_args.append(arg)
                environment_value = arg in {"-e", "--env"}
            cmd = " ".join(safe_args)
        else:
            cmd = str(exc.cmd)
        typer.secho(
            f"Docker command failed (exit {exc.returncode}): {cmd}",
            fg=typer.colors.RED,
            err=True,
        )
    raise typer.Exit(exc.returncode) from None


def _check_network(network: str) -> None:
    result = subprocess.run(
        ["docker", "network", "inspect", network], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise typer.BadParameter(f"network: Docker network '{network}' does not exist")


def _image_record(harness: str) -> dict[str, Any]:
    image = image_name(harness)
    result = subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        typer.echo(result.stderr.strip(), err=True)
        typer.secho(
            f"Image {image} is not available. Build it with `ahl build --harness {harness}`.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    [info] = json.loads(result.stdout)
    labels = info["Config"].get("Labels") or {}
    return {"name": image, "id": info["Id"], "harness_version": labels.get("ahl.harness.version")}


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


def _write_session(
    run_dir: Path,
    config: RunConfig,
    run_id: str,
    workspace_dir: Path,
    permissions: PermissionSetup,
    image: dict[str, Any],
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
        "image": image,
        "permissions": {
            "deny": sorted(config.permissions.deny),
            "applied": sorted(permissions.applied),
            "unsupported": dict(sorted(permissions.unsupported.items())),
        },
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
