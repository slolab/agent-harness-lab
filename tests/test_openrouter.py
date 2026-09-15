"""Provider routing through public config and adapter contracts."""

import json
import shlex

import pytest
import yaml

from ahl.config import ConfigError, load_config
from ahl.harnesses import get_adapter


@pytest.mark.parametrize("harness", ["claude", "opencode", "deepseek"])
def test_openrouter_route_keeps_native_model_and_secret_in_env(
    make_config, tmp_path, harness
):
    for model in ["vendor/model", "openrouter/auto", "vendor/model; echo 'unsafe'"]:
        config = make_config(
            harness=harness, provider="openrouter", model=model, api_key="route-secret"
        )
        adapter = get_adapter(harness)
        env = adapter.build_env(config)
        if harness == "claude":
            assert env == {
                "DISABLE_AUTOUPDATER": "1",
                "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
                "ANTHROPIC_AUTH_TOKEN": "route-secret",
                "ANTHROPIC_API_KEY": "",
                "ANTHROPIC_MODEL": model,
            }
            assert shlex.split(adapter.start_command(config)) == [
                "claude",
                "--model",
                model,
            ]
            assert "/logout" in " ".join(adapter.start_hints(tmp_path, config))
        else:
            expected = {"OPENROUTER_API_KEY": "route-secret"}
            if harness == "deepseek":
                expected["DSH_HOME"] = "/root/.dsh"
            assert env == expected
        run = tmp_path / "run"
        adapter.seed(run, config)
        if harness == "deepseek":
            rows = {
                r["id"]: r["config"]
                for r in yaml.safe_load(
                    (run / "deepseek/home/cordis.patch.yml").read_text()
                )
            }
            assert rows["agent-default-model"]["model"] == model
            assert rows["llm-pi-ai"]["providers"]["openrouter"]["models"] == [
                {"id": model}
            ]
        if harness == "opencode":
            assert shlex.split(adapter.start_command(config)) == [
                "opencode",
                "-m",
                "openrouter/" + model,
            ]
            assert (
                json.loads((run / "opencode/config/opencode.json").read_text())["model"]
                == "openrouter/" + model
            )
        assert "route-secret" not in " ".join(adapter.start_hints(run, config))
        assert all(
            "route-secret" not in p.read_text() for p in run.rglob("*") if p.is_file()
        )


def test_openrouter_requires_key_and_explicit_model_and_loads_dotenv(
    make_config, monkeypatch, tmp_path
):
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        make_config(harness="opencode", provider="openrouter", api_key="")
    for model in [None, "", "   "]:
        with pytest.raises(ConfigError, match="model"):
            make_config(harness="claude", provider="openrouter", extra={"model": model})
    make_config(harness="opencode", provider="openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY")
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=dotenv-secret\n")
    config = load_config(tmp_path / "config.yaml")
    assert get_adapter("opencode").build_env(config) == {
        "OPENROUTER_API_KEY": "dotenv-secret"
    }


@pytest.mark.parametrize(
    "harness,provider",
    [
        ("gemini", "openrouter"),
        ("agy", "openrouter"),
        ("claude-science", "openrouter"),
        ("deepseek", "anthropic"),
        ("claude", "openai"),
    ],
)
def test_unsupported_routes_fail_during_config_loading(make_config, harness, provider):
    with pytest.raises(ConfigError, match="provider|OpenRouter"):
        make_config(harness=harness, provider=provider)


@pytest.mark.parametrize("port", [True, 0, 65536, "3080", 3080.5])
def test_deepseek_rejects_invalid_port_before_launch(make_config, port):
    with pytest.raises(ConfigError, match="port must"):
        make_config(
            harness="deepseek",
            provider="openrouter",
            extra={"harness": {"name": "deepseek", "parameters": {"port": port}}},
        )
