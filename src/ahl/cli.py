"""Agent Harness Lab CLI."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from ahl.config import (
    EXACT_VERSION,
    HARNESS_NPM_PACKAGES,
    ConfigError,
    RunConfig,
    load_config,
    parse_harness,
    parse_harness_version,
    read_config,
)
from ahl.docker import CONTAINER_WORKSPACE, IMAGES_DIR, docker_daemon_unreachable, dockerfile_path, image_name
from ahl.harnesses import get_adapter
from ahl.headless import run_headless
from ahl.runs import PreparedRun, prepare_run, start_container

app = typer.Typer(no_args_is_help=True)
VERSIONED_HARNESSES = {*HARNESS_NPM_PACKAGES, "deepseek"}
ConfigOption = Annotated[Path, typer.Option("--config", "-c", help="Path to local config.yaml")]
EnvFileOption = Annotated[
    Path | None,
    typer.Option("--env-file", help="Env file with provider keys; wins over the shell. Default: .env next to the config"),
]
RunsDirOption = Annotated[
    Path | None,
    typer.Option("--runs-dir", help="Directory holding run directories. Default: runs/ next to the config"),
]
BuildOption = Annotated[bool, typer.Option("--build/--no-build", help="Build harness image before launching")]
NameOption = Annotated[str | None, typer.Option("--name", "-n", help="Name this run instead of the default timestamp id")]


@app.callback()
def main() -> None:
    """Agent Harness Lab — isolated harness shell for hands-on tool testing."""


@app.command()
def up(
    config: ConfigOption = Path("config.yaml"),
    env_file: EnvFileOption = None,
    runs_dir: RunsDirOption = None,
    build: BuildOption = True,
    name: NameOption = None,
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
    run_config = _load(config, env_file)
    run_dir = _run_dir(run_config, runs_dir, resume or name)
    if resume:
        if not run_dir.is_dir():
            raise typer.BadParameter(f"no run named '{run_dir.name}' found at {run_dir}")
        _check_resumable(run_dir, run_config)
    else:
        _check_new(run_dir, "use --resume to continue it")
    image = _docker_setup(run_config, build, config)
    if not resume:
        _claim(run_dir, "use --resume to continue it")
    run = _prepare(run_config, run_dir, image, resume=bool(resume), headless=False)

    typer.echo(f"AHL session: {run_dir.name}")
    typer.echo(f"Provider: {run_config.provider.name} | Model: {run_config.model.name or '(harness default)'}")
    if run_config.workspace.install == "copy":
        template = run_config.workspace.path
        typer.echo(
            f"Workspace: {run.workspace} -> {CONTAINER_WORKSPACE} "
            f"(copy{f' of {template}' if template else ' — no template, empty'}, "
            f"original untouched)"
        )
    else:
        typer.echo(f"Workspace: {run.workspace} -> {CONTAINER_WORKSPACE}")
    for host, container in run.extra_volumes:
        typer.echo(f"Mount: {host} -> {container}")
    for host, container in run.readonly_volumes:
        typer.echo(f"Mount: {host} -> {container} (ro)")
    for mount in run_config.mounts:
        typer.echo(f"Mount: {mount.path} -> {mount.target}{' (ro)' if mount.readonly else ''}")
    for copy in run.package_copies:
        typer.echo(f"Copy: {copy.host_path} -> {copy.container_path}")
    for cmd in run.setup_commands:
        typer.echo(f"Setup: {cmd}")
    typer.echo(f"Start the harness inside the shell with:\n  {run.adapter.start_command(run_config)}")
    for hint in run.adapter.start_hints(run_dir, run_config):
        typer.echo(f"  ({hint})")
    typer.echo("Exit the shell to stop.")

    try:
        if run.package_copies:
            _shell_with_copied_packages(run)
        else:
            subprocess.run(run.docker_args, check=False)
    except KeyboardInterrupt:
        pass
    finally:
        subprocess.run(["docker", "rm", "-f", run.container], capture_output=True, check=False)
        trace = run.adapter.parse_trace(run_dir)
        if trace is not None:
            trace_path = run_dir / "trace.json"
            trace_path.write_text(json.dumps(trace, indent=2, sort_keys=True))
            typer.echo(f"Trace: {trace_path}")


@app.command()
def run(
    turn: Annotated[
        list[Path],
        typer.Option("--turn", exists=True, dir_okay=False, resolve_path=True, help="Prompt file for the next turn"),
    ],
    config: ConfigOption = Path("config.yaml"),
    env_file: EnvFileOption = None,
    runs_dir: RunsDirOption = None,
    name: NameOption = None,
    timeout: Annotated[float, typer.Option("--timeout", min=1, help="Seconds allowed per turn")] = 3600,
    build: BuildOption = True,
) -> None:
    """Run each --turn file as one turn of a single headless harness session.

    Exit codes: 0 completed, 1 failed, 2 usage error, 3 infrastructure error,
    124 timeout, 130 interrupted. The run directory holds result.json,
    session.json, trace.jsonl and the raw output of every turn.
    """
    run_config = _load(config, env_file)
    if get_adapter(run_config.harness.name).driver is None:
        raise typer.BadParameter(f"harness '{run_config.harness.name}' has no headless driver; use claude or opencode")
    run_dir = _run_dir(run_config, runs_dir, name)
    _check_new(run_dir, "choose another --name")
    failure = image = None
    interrupted = False
    try:
        image = _docker_setup(run_config, build, config)
    except typer.Exit as exc:
        failure = f"Docker setup failed before turn 1 with exit code {exc.exit_code}; see stderr"
    except KeyboardInterrupt:
        interrupted = True
    _claim(run_dir, "choose another --name")
    prepared = _prepare(run_config, run_dir, image, resume=False, headless=True)
    raise typer.Exit(run_headless(prepared, turn, timeout, failure, interrupted))


def _load(config: Path, env_file: Path | None) -> RunConfig:
    try:
        return load_config(config, env_file)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _run_dir(config: RunConfig, runs_dir: Path | None, name: str | None) -> Path:
    runs_root = runs_dir.expanduser().resolve() if runs_dir else config.root / "runs"
    return runs_root / (name or _run_id(config.harness.name))


def _taken(run_dir: Path, hint: str) -> typer.BadParameter:
    return typer.BadParameter(f"run '{run_dir.name}' already exists at {run_dir} ({hint})")


def _check_new(run_dir: Path, hint: str) -> None:
    if run_dir.exists():
        raise _taken(run_dir, hint)


def _claim(run_dir: Path, hint: str) -> None:
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise _taken(run_dir, hint) from exc


def _docker_setup(config: RunConfig, build: bool, config_path: Path) -> dict[str, Any]:
    # Docker Desktop treats non-zero `docker run` exits (e.g. shell exit after
    # Ctrl-C → status 130) as "container errors" and prints Gordon tips.
    os.environ.setdefault("DOCKER_CLI_HINTS", "false")
    _ensure_docker()
    if config.network:
        _check_network(config.network)
    if build:
        _build_image(config.harness.name, config.harness_version)
    return _image_record(config.harness.name, config.harness_version, config_path)


def _prepare(
    config: RunConfig, run_dir: Path, image: dict[str, Any] | None, *, resume: bool, headless: bool
) -> PreparedRun:
    try:
        return prepare_run(config, run_dir, image, resume=resume, headless=headless)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _shell_with_copied_packages(run: PreparedRun) -> None:
    try:
        start_container(run)
        subprocess.run(["docker", "exec", "-it", run.container, "bash"], check=False)
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
        raw = {"harness": harness} if config is None else read_config(config)
        selected = parse_harness(raw.get("harness"))
        pinned = parse_harness_version(raw.get("harness_version"), selected.name)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _ensure_docker()
    _build_image(selected.name, pinned)


def _build_image(harness: str, pinned: str | None) -> None:
    image = image_name(harness)
    dockerfile = dockerfile_path(harness)
    version = pinned or _unpinned_version(harness)
    build_args = ["--build-arg", f"HARNESS_VERSION={version}"] if version else []
    typer.echo(f"Building {image} from {dockerfile}" + (f" with {harness} {version}" if version else ""))
    try:
        subprocess.run(
            ["docker", "build", "-t", image, "-f", str(dockerfile), *build_args, str(IMAGES_DIR)],
            check=True,
            # Default provenance attestations change the image ID on every build, even a cached one.
            # Unlike --provenance=false, the variable also works with the legacy builder.
            env={**os.environ, "BUILDX_NO_DEFAULT_ATTESTATIONS": "1"},
        )
    except FileNotFoundError:
        _fail_docker_missing()
    except subprocess.CalledProcessError as exc:
        _fail_docker_command(exc)


def _unpinned_version(harness: str) -> str | None:
    if harness == "deepseek":
        lock = json.loads((IMAGES_DIR / "deepseek/package-lock.json").read_text())
        return lock["packages"]["node_modules/@deepseek-ai/dsh"]["version"]
    if harness not in HARNESS_NPM_PACKAGES:
        return None
    url = f"https://registry.npmjs.org/{HARNESS_NPM_PACKAGES[harness]}/latest"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.load(response)["version"]
    except (OSError, ValueError, KeyError) as exc:
        typer.secho(
            f"Cannot resolve the current {harness} release from {url}: {exc}. "
            "Set harness_version, or pass --no-build.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1) from None


_DOCKER_MISSING = "Docker CLI not found on PATH. Install Docker and retry."
_DOCKER_UNREACHABLE = (
    "Cannot reach the Docker daemon. Is Docker Desktop (or the docker service) running?"
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
    if docker_daemon_unreachable(combined):
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


def _image_record(harness: str, pinned: str | None, config: Path) -> dict[str, Any]:
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
    installed = (info["Config"].get("Labels") or {}).get("ahl.harness.version")
    rebuild = f"Rebuild it with `ahl build -c {config}`."
    if harness in VERSIONED_HARNESSES and not EXACT_VERSION.fullmatch(installed or ""):
        raise typer.BadParameter(
            f"{image} records no exact {harness} version (ahl.harness.version={installed!r}). {rebuild}"
        )
    if pinned and installed != pinned:
        raise typer.BadParameter(f"harness_version: {image} has {harness} {installed}, not {pinned}. {rebuild}")
    return {"name": image, "id": info["Id"], "harness_version": installed}


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


def _run_id(harness: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{harness}"


if __name__ == "__main__":
    app()
