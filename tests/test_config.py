from __future__ import annotations

import pytest

from ahl.config import ConfigError


def test_load_config_happy_path(make_config):
    config = make_config(harness="claude", provider="anthropic", model="claude-sonnet-4-6")
    assert config.harness.name == "claude"
    assert config.provider.name == "anthropic"
    assert config.model.name == "claude-sonnet-4-6"
    assert config.capabilities == []
    assert config.packages == []


def test_load_config_missing_api_key(make_config):
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        make_config(harness="claude", provider="anthropic", api_key="")


def test_load_config_unsupported_provider(make_config):
    with pytest.raises(ConfigError, match="Unsupported provider"):
        make_config(provider="not-a-real-provider")


def test_load_config_unsupported_harness(make_config):
    with pytest.raises(ConfigError, match="Unsupported harness"):
        make_config(harness="not-a-real-harness")


def test_load_config_vertex_requires_project_and_location(make_config):
    with pytest.raises(ConfigError, match="parameters.project"):
        make_config(provider="vertex", extra={"provider": {"name": "vertex", "parameters": {}}})


def test_load_config_vertex_with_params(make_config):
    config = make_config(
        provider="vertex",
        extra={
            "provider": {
                "name": "vertex",
                "parameters": {"project": "proj-1", "location": "global"},
            }
        },
    )
    assert config.provider.name == "vertex"
    assert config.provider.parameters == {"project": "proj-1", "location": "global"}


def test_load_config_parses_capabilities(make_config, skill_dir):
    config = make_config(
        capabilities=[{"kind": "skill", "name": "my-skill", "install": "mount", "path": str(skill_dir)}]
    )
    assert len(config.capabilities) == 1
    cap = config.capabilities[0]
    assert cap.kind == "skill"
    assert cap.name == "my-skill"
    assert cap.path == skill_dir
