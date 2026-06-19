from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from ahl.config import RunConfig, load_config


@pytest.fixture
def make_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Write a config.yaml + .env under tmp_path and load it as a RunConfig."""

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
