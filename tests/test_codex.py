from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from importlib import resources
from itertools import takewhile
from pathlib import Path

import pytest
import yaml

from ahl.config import ConfigError, load_config
from ahl.harnesses import get_adapter
from ahl.trace import write_trace
from conftest import NPM_RELEASES, Turn
from test_headless import PIN, ahl, container_env, load, prompts, turn_commands, write_config
from test_trace import USAGE_FIELDS, validated

FIXTURES = Path(__file__).parent / "fixtures/a3"
EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.yaml"
IMAGES = resources.files("ahl") / "images"
MODEL = "openai/gpt-6-sol"
CODEX = {"harness": "codex", "provider": "openrouter", "model": MODEL}
CACHED_RESPONSE = {
    "model": MODEL, "response_id": "gen-1790409856-mGFt8XhmIDLp1Rcl6pnQ",
    "input_tokens": 11841 - 11618 - 155, "output_tokens": 5, "cache_read_tokens": 11618, "cache_write_tokens": 155,
    "reasoning_tokens": 0, "cost_usd": None,
}


def recorded(name: str) -> str:
    return (FIXTURES / "status" / f"{name}.jsonl").read_text()


def thread_id(stdout: str) -> str:
    return next(json.loads(line)["thread_id"] for line in stdout.splitlines() if "thread.started" in line)


def test_a3_ac1_documented_codex_config_loads_and_other_routes_fail(tmp_path):
    lines = EXAMPLE.read_text().splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("# Codex"))
    documented = "\n".join(line[4:] for line in takewhile(lambda line: line.startswith("#   "), lines[start + 1:]))
    raw = yaml.safe_load(documented)
    keys = tmp_path / "keys.env"
    keys.write_text("OPENROUTER_API_KEY=test-key\nANTHROPIC_API_KEY=test-key\nOPENAI_API_KEY=test-key\n")

    config = load_config(write_config(tmp_path / "config.yaml", raw), keys)

    assert (config.harness.name, config.provider.name, config.model.name) == ("codex", "openrouter", raw["model"])
    for change, problem in [
        ({"provider": "anthropic"}, "codex.*provider: openrouter"),
        ({"provider": "openai"}, "codex.*provider: openrouter"),
        ({"model": None}, "explicit model"),
    ]:
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
    ok = recorded("success")
    docker.turns = [Turn(stdout=ok), Turn(stdout=ok)]

    result = ahl("run", "-c", config, *prompts(tmp_path, 2), "--name", "codex")

    assert result.exit_code == 0, result.output
    assert docker.builds() == [[
        "docker", "build", "-t", "agent-harness-lab:codex", "-f", str(IMAGES / "codex.Dockerfile"),
        "--build-arg", "HARNESS_VERSION=0.156.1", str(IMAGES),
    ]]
    run = tmp_path / "runs/codex"
    session, outcome = load(run / "session.json"), load(run / "result.json")
    assert session["image"]["harness_version"] == "0.156.1"
    assert session["permissions"]["applied"] == ["webfetch", "websearch"]
    assert session["model_parameters"] == {"unsupported": ["provider"]} and "model.parameters.provider" in result.output

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
    assert not [p for p in run.rglob("*") if p.is_file() and "test-key" in p.read_text(errors="replace")]
    assert f"{skill_dir}:/root/.agents/skills/my-skill:ro" in launch

    first, second = turn_commands(docker, session["container"])
    for command in (first, second):
        assert command[:2] == ["codex", "exec"] and "--json" in command and command[-1] == "-"
        assert command[command.index("-m") + 1] == MODEL
    assert "resume" not in first and second[2:4] == ["resume", thread_id(ok)]
    assert [(t["status"], t["session_id"]) for t in outcome["turns"]] == [("completed", thread_id(ok))] * 2

    shell = ahl("up", "-c", config, "--no-build", "--name", "shell")

    assert shell.exit_code == 0, shell.output
    assert tomllib.loads((tmp_path / "runs/shell/codex/config.toml").read_text())["web_search"] == "disabled"
    assert load(tmp_path / "runs/shell/session.json")["permissions"]["applied"] == ["webfetch", "websearch"]

    write_config(config, CODEX)
    unpinned = ahl("build", "-c", config)

    assert unpinned.exit_code == 0, unpinned.output
    assert docker.registry_requests == ["https://registry.npmjs.org/@openai/codex/latest"]
    assert docker.builds()[-1][6:8] == ["--build-arg", f"HARNESS_VERSION={NPM_RELEASES['@openai/codex']}"]


@pytest.mark.parametrize(
    "recording,exit_code,reason",
    [
        ("success", 0, None),
        ("bad_model", 1, "harness_exit"),
        ("bad_model", 0, "harness_reported_error"),
        ("no_text", 0, "no_assistant_output"),
        ("http_429", 1, "provider_error"),
    ],
)
def test_a3_ac4_turn_status_follows_recorded_codex_output(tmp_path, monkeypatch, docker, recording, exit_code, reason):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = write_config(tmp_path / "config.yaml", CODEX)
    stdout = recorded(recording)
    docker.turns = [Turn(exit_code=exit_code, stdout=stdout)]

    result = ahl("run", "-c", config, *prompts(tmp_path, 1), "--no-build", "--name", "r")

    assert result.exit_code == (1 if reason else 0), result.output
    [turn] = load(tmp_path / "runs/r/result.json")["turns"]
    assert (turn["status"], turn["exit_code"], turn["session_id"]) == (
        "failed" if reason else "completed", exit_code, thread_id(stdout),
    )
    assert (turn["reason"] or {}).get("code") == reason


def test_a3_live_view_shows_recorded_codex_turns_in_order_unless_quiet(tmp_path, monkeypatch, docker):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = write_config(tmp_path / "config.yaml", CODEX)
    view = re.compile(r"  \[(\S+)\] (\w+): (.*)")
    child = "01a0dcca-693d-7860-9edb-eb028dd13955"
    expected = [
        ("main", "text", "I’ll have one subagent run `ls /`"),
        ("main", "subagent", f"{child} Run `ls /` using your shell tool"),
        ("main", "tool", f'wait ["{child}"]'),
        ("main", "text", "20"),
        ("main", "error", "Model metadata for `openai/gpt-0-nonexistent` not found"),
        ("main", "error", '{"error":{"message":"openai/gpt-0-nonexistent is not a valid model ID"'),
        ("main", "error", '{"error":{"message":"openai/gpt-0-nonexistent is not a valid model ID"'),
    ]
    outputs = {}
    for name in ("loud", "quiet"):
        docker.turns = [Turn(stdout=recorded("subagent")), Turn(exit_code=1, stdout=recorded("bad_model"))]
        outputs[name] = ahl("run", "-c", config, *prompts(tmp_path, 2), "--no-build", "--name", name,
                            *(["--quiet"] if name == "quiet" else []))
        assert outputs[name].exit_code == 1, outputs[name].output

    shown = [match.groups() for line in outputs["loud"].stderr.splitlines() if (match := view.fullmatch(line))]
    assert [(agent, kind) for agent, kind, _ in shown] == [(agent, kind) for agent, kind, _ in expected]
    assert all(detail.startswith(fragment) for (_, _, detail), (_, _, fragment) in zip(shown, expected))
    assert not [line for line in outputs["quiet"].output.splitlines() if view.fullmatch(line)]


def test_a3_ac6_recorded_run_gives_a_valid_trace_with_prompts_and_one_usage_per_response(tmp_path):
    run = tmp_path / "run"
    shutil.copytree(FIXTURES / "trace", run)
    turns = json.loads((run / "turns.json").read_text())

    write_trace(run, get_adapter("codex").driver.trace(run), turns)

    events = validated(run)
    for turn in turns:
        user = [e for e in events if e["type"] == "message" and e["role"] == "user" and e["turn"] == turn["index"]]
        assert (run / turn["prompt_file"]).read_text().strip() in user[0]["text"]
    native = [json.loads(line) for path in (run / "codex/sessions").rglob("rollout-*.jsonl") for line in path.open()]
    responses = {r["payload"]["response_id"] for r in native if r["type"] == "token_usage_record"}
    repeats = [r for r in native if r["type"] == "event_msg" and r["payload"]["type"] == "token_count"]
    usage = [e for e in events if e["type"] == "usage"]
    assert len(repeats) >= len(responses) > 1
    assert sorted(e["response_id"] for e in usage) == sorted(responses)
    assert CACHED_RESPONSE in [{k: e[k] for k in USAGE_FIELDS} for e in usage]
