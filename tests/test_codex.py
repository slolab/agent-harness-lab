from __future__ import annotations

import shutil
import subprocess
import tomllib
from importlib import resources
from itertools import takewhile
from pathlib import Path

import pytest
import yaml

from ahl.config import ConfigError, load_config
from conftest import NPM_RELEASES, Turn
from test_headless import (
    CODEX, PIN, ahl, container_env, load, prompts, recorded, session_id, turn_commands, write_config,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.yaml"
ROLLOUTS = Path(__file__).parent / "fixtures/a2/trace/codex/codex/sessions"
IMAGES = resources.files("ahl") / "images"
MODEL = CODEX["model"]


def test_a3_ac1_documented_codex_config_loads_and_other_routes_fail(tmp_path):
    lines = EXAMPLE.read_text().splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("# Codex"))
    documented = "\n".join(line[4:] for line in takewhile(lambda line: line.startswith("#   "), lines[start + 1:]))
    raw = yaml.safe_load(documented)
    keys = tmp_path / "keys.env"
    keys.write_text("OPENROUTER_API_KEY=test-key\nANTHROPIC_API_KEY=test-key\n")

    config = load_config(write_config(tmp_path / "config.yaml", raw), keys)

    assert (config.harness.name, config.provider.name, config.model.name) == ("codex", "openrouter", raw["model"])
    for change, problem in [({"provider": "anthropic"}, "codex.*provider: openrouter"), ({"model": None}, "explicit model")]:
        with pytest.raises(ConfigError, match=problem):
            load_config(write_config(tmp_path / "config.yaml", {**raw, **change}), keys)


def test_a3_ac2_ac3_ac4_ac5_codex_runs_seeded_pinned_unattended_and_resumes(tmp_path, monkeypatch, docker, skill_dir):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    raw = {
        **CODEX,
        "model": {"name": MODEL, "parameters": {"provider": PIN}},
        "harness_version": "0.156.1",
        "permissions": {"deny": ["websearch", "webfetch"]},
        "capabilities": [{"kind": "skill", "name": "my-skill", "install": "mount", "path": str(skill_dir)}],
    }
    config = write_config(tmp_path / "config.yaml", raw)
    docker.labels = {"ahl.harness": "codex", "ahl.harness.version": "0.156.1"}
    run = tmp_path / "runs/codex"
    ok = recorded("codex", "success")
    docker.turns = [Turn(stdout=ok, effect=lambda: shutil.copytree(ROLLOUTS, run / "codex/sessions")), Turn(stdout=ok)]

    result = ahl("run", "-c", config, *prompts(tmp_path, 2), "--name", "codex")

    assert result.exit_code == 0, result.output
    assert docker.builds() == [[
        "docker", "build", "-t", "agent-harness-lab:codex", "-f", str(IMAGES / "codex.Dockerfile"),
        "--build-arg", "HARNESS_VERSION=0.156.1", str(IMAGES),
    ]]
    assert {"session.json", "result.json", "trace.jsonl", "trace.json", "codex", "turns"} <= {p.name for p in run.iterdir()}
    session, outcome = load(run / "session.json"), load(run / "result.json")
    assert session["image"]["harness_version"] == "0.156.1"
    assert session["permissions"]["applied"] == ["webfetch", "websearch"]
    assert session["model_parameters"] == {"unsupported": ["provider"]} and "model.parameters.provider" in result.output
    assert {"message", "tool_call", "usage"} <= {e["type"] for e in load(run / "trace.json")["events"]}

    seeded = tomllib.loads((run / "codex/config.toml").read_text())
    provider = seeded["model_providers"][seeded["model_provider"]]
    assert (provider["base_url"], provider["wire_api"]) == ("https://openrouter.ai/api/v1", "responses")
    assert (seeded["approval_policy"], seeded["sandbox_mode"], seeded["web_search"]) == (
        "never", "danger-full-access", "disabled",
    )
    token = subprocess.Popen(
        [provider["auth"]["command"], *provider["auth"]["args"]], env={"OPENROUTER_API_KEY": "from-env"},
        stdout=subprocess.PIPE, text=True,
    ).communicate()[0]
    assert token.strip() == "from-env"
    launch = docker.launches()[-1]
    assert container_env(launch)["OPENROUTER_API_KEY"] == "test-key"
    assert seeded["features"]["shell_snapshot"] is False
    assert not [p for p in run.rglob("*") if p.is_file() and "test-key" in p.read_text(errors="replace")]
    assert f"{skill_dir}:/root/.agents/skills/my-skill:ro" in launch

    first, second = turn_commands(docker, session["container"])
    for command in (first, second):
        assert command[:2] == ["codex", "exec"] and "--json" in command and command[-1] == "-"
        assert command[command.index("-m") + 1] == MODEL
    assert "resume" not in first and second[2:4] == ["resume", session_id(ok)]
    assert [(t["status"], t["session_id"]) for t in outcome["turns"]] == [("completed", session_id(ok))] * 2

    shell = ahl("up", "-c", config, "--no-build", "--name", "shell")

    assert shell.exit_code == 0, shell.output
    assert tomllib.loads((tmp_path / "runs/shell/codex/config.toml").read_text())["web_search"] == "disabled"
    assert load(tmp_path / "runs/shell/session.json")["permissions"]["applied"] == ["webfetch", "websearch"]

    write_config(config, CODEX)
    unpinned = ahl("build", "-c", config)

    assert unpinned.exit_code == 0, unpinned.output
    assert docker.registry_requests == ["https://registry.npmjs.org/@openai/codex/latest"]
    assert docker.builds()[-1][6:8] == ["--build-arg", f"HARNESS_VERSION={NPM_RELEASES['@openai/codex']}"]
