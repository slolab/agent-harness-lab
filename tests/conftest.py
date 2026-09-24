from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from ahl.config import PROVIDER_KEY_ENV, RunConfig, load_config

_real_run = subprocess.run


@pytest.fixture(autouse=True)
def provider_keys_restored(monkeypatch: pytest.MonkeyPatch):
    for key in PROVIDER_KEY_ENV.values():
        # setenv registers an unset key, so teardown also removes values load_dotenv writes into os.environ.
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)


@dataclass
class DockerStub:
    calls: list[list[str]] = field(default_factory=list)
    build_envs: list[dict[str, str] | None] = field(default_factory=list)
    networks: set[str] = field(default_factory=set)
    image_id: str | None = "sha256:" + "ab" * 32
    labels: dict[str, str] = field(default_factory=dict)
    ahl_tracked: bool = True

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if args[0] == "git":
            if self.ahl_tracked or "ls-files" not in args:
                return _real_run(args, **kwargs)
            if kwargs.get("check"):
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 1, "", "error: pathspec did not match any file(s) known to git")
        self.calls.append(args)
        if args[:2] == ["docker", "build"]:
            self.build_envs.append(kwargs.get("env"))
        if args[:3] == ["docker", "image", "inspect"]:
            if self.image_id is None:
                return subprocess.CompletedProcess(args, 1, "", f"Error response from daemon: No such image: {args[3]}")
            info = [{"Id": self.image_id, "Config": {"Labels": self.labels}}]
            return subprocess.CompletedProcess(args, 0, json.dumps(info), "")
        if args[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(args, 0 if args[3] in self.networks else 1, "[]", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def launches(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["docker", "run"]]

    def builds(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["docker", "build"]]


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> DockerStub:
    stub = DockerStub()
    monkeypatch.setattr(subprocess, "run", stub)
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
