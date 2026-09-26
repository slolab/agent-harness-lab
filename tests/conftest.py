from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from ahl.config import HARNESS_NPM_PACKAGES, PROVIDER_KEY_ENV, RunConfig, load_config

_real_run = subprocess.run
NPM_RELEASES = {"@anthropic-ai/claude-code": "2.1.281", "opencode-ai": "1.18.32"}
KEY_URL = "https://openrouter.ai/api/v1/key"


@pytest.fixture(autouse=True)
def provider_keys_restored(monkeypatch: pytest.MonkeyPatch):
    for key in PROVIDER_KEY_ENV.values():
        # setenv registers an unset key, so teardown also removes values load_dotenv writes into os.environ.
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)


def interrupt() -> None:
    os.kill(os.getpid(), signal.SIGINT)


def unpinned_build_labels(harness: str) -> dict[str, str]:
    package = HARNESS_NPM_PACKAGES.get(harness)
    return {"ahl.harness": harness} | ({"ahl.harness.version": NPM_RELEASES[package]} if package else {})


@dataclass
class Turn:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    raises: BaseException | None = None
    effect: Callable[[], None] | None = None
    interrupt_wait: bool = False


@dataclass
class DockerStub:
    calls: list[list[str]] = field(default_factory=list)
    build_envs: list[dict[str, str] | None] = field(default_factory=list)
    networks: set[str] = field(default_factory=set)
    image_id: str | None = "sha256:" + "ab" * 32
    labels: dict[str, str] | None = None
    ahl_tracked: bool = True
    registry_requests: list[str] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    prompts: list[bytes] = field(default_factory=list)
    timeouts: list[float | None] = field(default_factory=list)
    failing: dict[str, int] = field(default_factory=dict)
    sigints: list[str] = field(default_factory=list)
    running: bool = True
    usage: list[float | Exception] = field(default_factory=list)
    usage_reads: list[float | None] = field(default_factory=list)
    reads_at_turn: list[int] = field(default_factory=list)
    interrupted_sleeps: int = 0
    clock: float = 0.0

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if args[0] == "git":
            if self.ahl_tracked or "ls-files" not in args:
                return _real_run(args, **kwargs)
            if kwargs.get("check"):
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 1, "", "error: pathspec did not match any file(s) known to git")
        self.calls.append(args)
        if any(pattern in " ".join(args) for pattern in self.sigints):
            interrupt()
        if args[1] in self.failing:
            code = self.failing[args[1]]
            if kwargs.get("check"):
                raise subprocess.CalledProcessError(code, args, "", "Error response from daemon: stubbed failure")
            return subprocess.CompletedProcess(args, code, "", "Error response from daemon: stubbed failure")
        if args[:3] == ["docker", "exec", "-i"]:
            return self._turn(args, kwargs)
        if args[:3] == ["docker", "container", "inspect"]:
            return subprocess.CompletedProcess(args, 0, "true\n" if self.running else "false\n", "")
        if args[:2] == ["docker", "build"]:
            self.build_envs.append(kwargs.get("env"))
        if args[:3] == ["docker", "image", "inspect"]:
            if self.image_id is None:
                return subprocess.CompletedProcess(args, 1, "", f"Error response from daemon: No such image: {args[3]}")
            harness = args[3].removeprefix("agent-harness-lab:")
            labels = self.labels if self.labels is not None else unpinned_build_labels(harness)
            info = [{"Id": self.image_id, "Config": {"Labels": labels}}]
            return subprocess.CompletedProcess(args, 0, json.dumps(info), "")
        if args[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(args, 0 if args[3] in self.networks else 1, "[]", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def _turn(self, args: list[str], kwargs: dict[str, Any]) -> subprocess.CompletedProcess:
        turn = self.turns.pop(0)
        self.reads_at_turn.append(len(self.usage_reads))
        self.prompts.append(kwargs["stdin"].read())
        self.timeouts.append(kwargs.get("timeout"))
        self.interrupted_sleeps += turn.interrupt_wait
        if turn.effect:
            turn.effect()
        kwargs["stdout"].write(turn.stdout.encode())
        kwargs["stderr"].write(turn.stderr.encode())
        if turn.raises:
            raise turn.raises
        return subprocess.CompletedProcess(args, turn.exit_code)

    def urlopen(self, request: Any, **kwargs: Any) -> io.BytesIO:
        url = getattr(request, "full_url", request)
        if url == KEY_URL:
            return self._key_usage(request)
        self.registry_requests.append(url)
        package = url.removeprefix("https://registry.npmjs.org/").removesuffix("/latest")
        return io.BytesIO(json.dumps({"version": NPM_RELEASES[package]}).encode())

    def _key_usage(self, request: Any) -> io.BytesIO:
        assert request.get_header("Authorization") == "Bearer test-key"
        if self.usage:
            value = self.usage.pop(0) if len(self.usage) > 1 else self.usage[0]
        else:
            value = 1.0 + 0.01 * len(self.prompts)
        if isinstance(value, Exception):
            self.usage_reads.append(None)
            raise value
        self.usage_reads.append(value)
        return io.BytesIO(json.dumps({"data": {"usage": value}}).encode())

    def sleep(self, seconds: float) -> None:
        if self.interrupted_sleeps:
            self.interrupted_sleeps -= 1
            interrupt()
        self.clock += seconds

    def monotonic(self) -> float:
        return self.clock

    def launches(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["docker", "run"]]

    def builds(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["docker", "build"]]

    def turn_execs(self) -> list[list[str]]:
        return [c for c in self.calls if c[:3] == ["docker", "exec", "-i"]]

    def removals(self) -> list[str]:
        return [c[3] for c in self.calls if c[:3] == ["docker", "rm", "-f"]]


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> DockerStub:
    stub = DockerStub()
    monkeypatch.setattr(subprocess, "run", stub)
    monkeypatch.setattr(urllib.request, "urlopen", stub.urlopen)
    monkeypatch.setattr(time, "sleep", stub.sleep)
    monkeypatch.setattr(time, "monotonic", stub.monotonic)
    return stub


@pytest.fixture
def make_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Load a temporary config with synthetic credentials, isolated from the host."""

    def _make(
        *,
        harness: str = "claude",
        provider: str = "anthropic",
        model: str = "claude-sonnet-4-6",
        capabilities: list[dict[str, Any]] | None = None,
        packages: list[dict[str, Any]] | None = None,
        api_key: str = "test-key",
        extra: dict[str, Any] | None = None,
    ) -> RunConfig:
        root = tmp_path
        env_var = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "vertex": "GOOGLE_API_KEY",
        }.get(provider)
        if env_var:
            monkeypatch.setenv(env_var, api_key)

        raw: dict[str, Any] = {
            "harness": harness,
            "provider": provider,
            "model": model,
            "workspace": "./workspace",
        }
        if capabilities is not None:
            raw["capabilities"] = capabilities
        if packages is not None:
            raw["packages"] = packages
        if extra:
            raw.update(extra)

        config_path = root / "config.yaml"
        config_path.write_text(yaml.safe_dump(raw))
        return load_config(config_path)

    return _make


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    """A minimal valid skill bundle directory (just SKILL.md)."""
    skill = tmp_path / "my-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: a test skill\n---\n\n# my-skill\n\nDo the thing.\n"
    )
    return skill


@pytest.fixture
def package_dir(tmp_path: Path) -> Path:
    """A minimal local package checkout (just a marker file — wiring doesn't need it to be installable)."""
    pkg = tmp_path / "my-package"
    pkg.mkdir()
    (pkg / "pyproject.toml").write_text("[project]\nname = 'my-package'\nversion = '0.1.0'\n")
    return pkg
