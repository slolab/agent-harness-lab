"""Claude Science adapter — local web app with per-run state and workspace."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

from ahl.permissions import UnsupportedPermissions
from ahl.capabilities import Capability
from ahl.config import ConfigError, RunConfig
from ahl.harnesses.base import Volumes, warn_unsupported_mcp

CONTAINER_HOME = "/root/.claude-science"
DEFAULT_PORT = 8000


class ClaudeScienceAdapter:
    permission_handler = UnsupportedPermissions()
    driver = None

    def build_env(self, config: RunConfig) -> dict[str, str]:
        # Claude Science authenticates in its web UI with a Claude account.
        return {"DO_NOT_TRACK": "1"}

    def _state_dir(self, run_dir: Path) -> Path:
        return run_dir / "claude-science"

    def _home(self, run_dir: Path) -> Path:
        return self._state_dir(run_dir) / "home"

    def _skill_uploads(self, run_dir: Path) -> Path:
        return self._state_dir(run_dir) / "skill-uploads"

    def seed(self, run_dir: Path, config: RunConfig) -> Volumes:
        _port(config)  # validate harness parameters before Docker is invoked
        _dangerously_no_sandbox(config)
        home = self._home(run_dir)
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.toml").write_text(
            "disable_telemetry = true\n\n[update]\nauto_update = false\n"
        )
        return [(home, CONTAINER_HOME)]

    def wire_capabilities(
        self,
        run_dir: Path,
        config: RunConfig,
        capabilities: list[Capability],
    ) -> Volumes:
        warn_unsupported_mcp("claude-science", capabilities)
        uploads = self._skill_uploads(run_dir)
        for skill in capabilities:
            if skill.kind != "skill" or skill.path is None:
                continue
            uploads.mkdir(parents=True, exist_ok=True)
            _zip_skill(skill.path, uploads / f"{skill.name}.zip", skill.name)
        # Claude Science exposes custom skill installation only through its UI.
        # These ZIPs remain host-side so the host browser's file picker can
        # upload them without granting the app access to arbitrary host paths.
        return []

    def parse_trace(self, run_dir: Path) -> dict[str, Any] | None:
        # Explicit non-goal for the first Claude Science harness.
        return None

    def start_command(self, config: RunConfig) -> str:
        port = _port(config)
        command = (
            f"claude-science serve --no-browser --no-auto-update "
            f"--host 0.0.0.0 --port {port}"
        )
        if _dangerously_no_sandbox(config):
            command += " --dangerously-no-sandbox"
        return command

    def start_hints(self, run_dir: Path, config: RunConfig) -> list[str]:
        port = _port(config)
        hints = [
            f"The command prints a single-use sign-in URL reachable at localhost:{port}",
            "If that URL names 0.0.0.0, replace only its hostname with localhost",
            "Sign in with a Claude account; no API key is present in the container",
            "Grant /workspace folder access when prompted; outputs then persist in this run",
        ]
        uploads = self._skill_uploads(run_dir)
        if uploads.is_dir() and any(uploads.glob("*.zip")):
            hints.append(
                "One manual beta fallback is required for custom skills: in Settings > Skills, "
                f"upload each ZIP from {uploads}"
            )
        if config.model.name:
            hints.append(
                f"model '{config.model.name}' is not a Claude Science CLI setting; the app uses "
                "the models available to the signed-in plan"
            )
        if _dangerously_no_sandbox(config):
            hints.append(
                "WARNING: Claude Science's inner sandbox is disabled; the Docker container is "
                "the remaining isolation boundary"
            )
        else:
            hints.append(
                "Docker seccomp/AppArmor restrictions are relaxed so the inner bubblewrap "
                "sandbox can create nested namespaces"
            )
        return hints

    def docker_args(self, config: RunConfig) -> list[str]:
        port = _port(config)
        return [
            "--platform",
            "linux/amd64",
            "-p",
            f"127.0.0.1:{port}:{port}",
            "-p",
            f"127.0.0.1:{port + 1}:{port + 1}",
            "--security-opt",
            "seccomp=unconfined",
            "--security-opt",
            "apparmor=unconfined",
        ]


def _port(config: RunConfig) -> int:
    value = config.harness.parameters.get("port", DEFAULT_PORT)
    if isinstance(value, bool):
        raise ConfigError("harness 'claude-science' parameters.port must be an integer")
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError("harness 'claude-science' parameters.port must be an integer") from exc
    if not 1 <= port <= 65534:
        raise ConfigError("harness 'claude-science' parameters.port must be between 1 and 65534")
    return port


def _dangerously_no_sandbox(config: RunConfig) -> bool:
    value = config.harness.parameters.get("dangerously_no_sandbox", False)
    if not isinstance(value, bool):
        raise ConfigError(
            "harness 'claude-science' parameters.dangerously_no_sandbox must be true or false"
        )
    return value


def _zip_skill(source: Path, destination: Path, name: str) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, Path(name) / path.relative_to(source))
