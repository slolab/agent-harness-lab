from __future__ import annotations

import pytest

from ahl.config import ConfigError


def test_load_config_account_defaults(make_config):
    config = make_config(harness="claude", provider="anthropic", extra={"model": None}, api_key="")
    assert config.harness.name == "claude"
    assert config.provider.name == "anthropic"
    assert config.model.name == ""
    assert config.capabilities == []
    assert config.packages == []


def test_load_config_missing_api_key_for_key_based_harness(make_config):
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        make_config(harness="opencode", provider="anthropic", api_key="")


def test_load_config_account_login_harness_requires_anthropic_provider(make_config):
    with pytest.raises(ConfigError, match="requires provider: anthropic"):
        make_config(harness="claude-science", provider="gemini")


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
        harness="gemini",
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
