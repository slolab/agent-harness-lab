from __future__ import annotations

import hashlib
import json
import subprocess
import urllib.error
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from ahl import cli
from ahl import runs as prepare
from conftest import Turn, interrupt

STATUS = Path(__file__).parent / "fixtures/a2/status"
CLAUDE = {"harness": "claude", "provider": "openrouter", "model": "anthropic/claude-sonnet-5"}
OPENCODE = {"harness": "opencode", "provider": "openrouter", "model": "deepseek/deepseek-v4.1-flash"}
PIN = {"only": ["deepinfra"], "quantizations": ["fp8"], "allow_fallbacks": False}
OPENCODE_PERMISSION_KEYS = (
    "read", "edit", "glob", "grep", "list", "bash", "task", "external_directory",
    "todowrite", "question", "webfetch", "websearch", "lsp", "doom_loop", "skill",
)
CLAUDE_MODEL_VARIABLES = (
    "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL",
)


def ahl(*args: Any):
    return CliRunner().invoke(cli.app, [str(a) for a in args])


def recorded(harness: str, name: str) -> str:
    return (STATUS / harness / f"{name}.jsonl").read_text()


def session_id(stdout: str) -> str:
    events = [json.loads(line) for line in stdout.splitlines()]
    [sid] = {e.get("session_id") or e.get("sessionID") for e in events} - {None}
    return sid


def write_config(path: Path, raw: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(raw))
    return path


def prompts(tmp_path: Path, count: int) -> list[str]:
    files = []
    for index in range(1, count + 1):
        path = tmp_path / f"t{index}.md"
        path.write_text(f"prompt {index}")
        files += ["--turn", str(path)]
    return files


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def interrupting(function):
    def interrupted(*args, **kwargs):
        interrupt()
        return function(*args, **kwargs)

    return interrupted


def container_env(args: list[str]) -> dict[str, str]:
    pairs = [args[i + 1] for i, arg in enumerate(args) if arg == "-e"]
    return dict(pair.split("=", 1) for pair in pairs)


def turn_commands(docker, container: str) -> list[list[str]]:
    return [call[call.index(container) + 1:] for call in docker.turn_execs()]


def test_a2_ac1_flags_default_and_failures_map_to_exit_codes(tmp_path, monkeypatch, docker):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    conf = tmp_path / "conf"
    conf.mkdir()
    write_config(conf / "config.yaml", OPENCODE)
    work = tmp_path / "work"
    (work / "prompts").mkdir(parents=True)
    prompt = "  Line one\r\n\tno trailing newline ✓".encode()
    (work / "prompts/t1.md").write_bytes(prompt)
    monkeypatch.chdir(work)
    docker.turns = [Turn(stdout=recorded("opencode", "success"))]

    result = ahl("run", "-c", "../conf/config.yaml", "--turn", "prompts/t1.md")

    assert result.exit_code == 0, result.output
    [run] = (conf / "runs").iterdir()
    assert docker.prompts == [prompt] and (run / "turns/1/prompt.md").read_bytes() == prompt
    assert docker.timeouts == [3600] and len(docker.builds()) == 1
    assert load(run / "result.json")["status"] == "completed"

    (tmp_path / "keys.env").write_text("OPENROUTER_API_KEY=test-key\n")
    common = [
        "run", "-c", "../conf/config.yaml", "--turn", "prompts/t1.md", "--turn", "prompts/t1.md",
        "--no-build", "--env-file", "../keys.env", "--runs-dir", "out", "--timeout", "90",
    ]
    infra, unmigrated_db = ("error", "infra"), work / "out/timeout/opencode/data/opencode.db"
    for name, turn, running, code, (status, reason) in [
        ("exec", Turn(exit_code=126, stderr="OCI runtime exec failed: permission denied"), True, 3, infra),
        ("daemon", Turn(exit_code=1, stderr="Error response from daemon: container is paused"), True, 3, infra),
        ("vanished", Turn(exit_code=137), False, 3, infra),
        ("timeout", Turn(raises=subprocess.TimeoutExpired("opencode", 90), effect=unmigrated_db.touch), True, 124,
         ("timeout", "timeout")),
    ]:
        docker.turns, docker.running = [turn], running
        failed = ahl(*common, "--name", name)

        assert failed.exit_code == code, failed.output
        outcome = load(work / "out" / name / "result.json")
        assert (outcome["status"], outcome["reason"]["code"]) == (status, reason)
        assert [t["status"] for t in outcome["turns"]] == [status, "skipped"]
    assert docker.timeouts[1:] == [90, 90, 90, 90] and len(docker.builds()) == 1
    assert [w["code"] for w in outcome["warnings"]] == ["trace_unreadable"]
    assert {outcome["totals"][k] for k in ("input_tokens", "output_tokens", "cache_read_tokens")} == {None}
    assert (work / "out/timeout/trace.jsonl").read_text() == ""

    before, launches = (work / "out/exec/result.json").read_bytes(), len(docker.launches())
    taken = ahl(*common, "--name", "exec")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    write_config(conf / "gemini.yaml", {"harness": "gemini", "provider": "gemini", "model": "gemini-3.5-flash"})
    no_driver = ahl("run", "-c", "../conf/gemini.yaml", "--turn", "prompts/t1.md", "--no-build", "--name", "g")

    assert (taken.exit_code, no_driver.exit_code) == (2, 2), taken.output + no_driver.output
    assert (work / "out/exec/result.json").read_bytes() == before
    assert not (conf / "runs/g").exists() and len(docker.launches()) == launches


def test_a2_ac2_ac3_ac5_every_terminal_state_leaves_a_complete_run_directory(tmp_path, monkeypatch, docker):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = write_config(tmp_path / "config.yaml", CLAUDE)
    turns = prompts(tmp_path, 3)
    ok, error = recorded("claude", "success"), recorded("claude", "error_result")
    scenarios = [
        ("completed", [Turn(stdout=ok)] * 3, {}, 0, ["completed"] * 3, None),
        ("failed", [Turn(stdout=ok), Turn(exit_code=1, stdout=error)], {}, 1,
         ["completed", "failed", "skipped"], ("failed", "harness_exit")),
        ("timeout", [Turn(raises=subprocess.TimeoutExpired("claude", 5))], {"sigints": ["kill -KILL -1"]}, 124,
         ["timeout", "skipped", "skipped"], ("timeout", "timeout")),
        ("interrupted", [Turn(effect=interrupt, interrupt_wait=True)], {}, 130,
         ["interrupted", "skipped", "skipped"], ("interrupted", "interrupted")),
        ("interrupted-wait", [Turn(stdout=ok), Turn(stdout=ok), Turn(stdout=ok, interrupt_wait=True)], {}, 130,
         ["completed", "completed", "interrupted"], ("interrupted", "interrupted")),
        ("infra", [], {"failing": {"build": 1}}, 3, ["skipped"] * 3, ("error", "infra")),
        ("interrupted-build", [], {"sigints": ["docker build"]}, 130,
         ["interrupted", "skipped", "skipped"], ("interrupted", "interrupted")),
        ("interrupted-prepare", [], {}, 130, ["interrupted", "skipped", "skipped"], ("interrupted", "interrupted")),
    ]
    for name, scripted, stub, code, statuses, run_reason in scenarios:
        docker.turns = list(scripted)
        docker.failing, docker.sigints = stub.get("failing", {}), stub.get("sigints", [])
        build = [] if name in ("infra", "interrupted-build") else ["--no-build"]

        with monkeypatch.context() as patch:
            if name == "interrupted-prepare":
                patch.setattr(prepare, "resolve_workspace", interrupting(prepare.resolve_workspace))
            result = ahl("run", "-c", config, *turns, *build, "--name", name)

        assert result.exit_code == code, (name, result.output)
        run = tmp_path / "runs" / name
        assert {"session.json", "result.json", "trace.jsonl", "trace.json", "claude", "workspace"} <= {
            p.name for p in run.iterdir()
        }
        session, outcome = load(run / "session.json"), load(run / "result.json")
        assert session["mode"] == "headless" and session["model_parameters"] == {"unsupported": []}
        assert session["container"] in docker.removals() or not scripted
        assert (outcome["status"], outcome["reason"] and outcome["reason"]["code"]) == (
            run_reason or ("completed", None)
        )
        assert [t["status"] for t in outcome["turns"]] == statuses
        for index, turn in enumerate(outcome["turns"], start=1):
            prompt = run / f"turns/{index}/prompt.md"
            started = index <= len(scripted)
            assert prompt.read_text() == f"prompt {index}" and turn["prompt_file"] == f"turns/{index}/prompt.md"
            assert turn["prompt_sha256"] == hashlib.sha256(prompt.read_bytes()).hexdigest()
            assert (run / f"turns/{index}/stdout.jsonl").is_file() == started
            assert (run / f"turns/{index}/stderr.log").is_file() == started
            assert (turn["reason"] is None) == (turn["status"] == "completed")
            if started:
                assert turn["started_at"] and turn["ended_at"] and turn["key_usage"]["after"]
            else:
                assert turn["reason"]["code"] == {"skipped": "skipped_after_failure"}.get(turn["status"], "interrupted")
                assert {k for k, v in turn.items() if v is not None} == {
                    "index", "prompt_file", "prompt_sha256", "status", "reason",
                }
        if name in ("timeout", "interrupted", "interrupted-wait"):
            after = outcome["turns"][len(scripted) - 1]["key_usage"]
            assert after["after"]["settled"] is False and after["delta_usd"] == pytest.approx(0.01)
        assert {outcome["totals"][k] for k in ("input_tokens", "output_tokens", "cache_read_tokens")} == {0}


def test_a2_ac3_container_names_are_unique_recorded_and_removed(tmp_path, monkeypatch, docker):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = write_config(tmp_path / "config.yaml", OPENCODE)
    turn = prompts(tmp_path, 1)
    for runs in ("a", "b"):
        docker.turns = [Turn(stdout=recorded("opencode", "success"))]
        headless = ahl("run", "-c", config, *turn, "--no-build", "--runs-dir", tmp_path / runs, "--name", "same")
        shell = ahl("up", "-c", config, "--no-build", "--runs-dir", tmp_path / f"up-{runs}", "--name", "same")
        assert (headless.exit_code, shell.exit_code) == (0, 0), headless.output + shell.output

    names = [load(tmp_path / d / "same/session.json")["container"] for d in ("a", "up-a", "b", "up-b")]
    assert len(set(names)) == 4
    assert [args[args.index("--name") + 1] for args in docker.launches()] == names
    assert set(names) <= set(docker.removals())


def test_a2_ac5_key_usage_waits_for_a_settled_rise_and_warns_otherwise(tmp_path, monkeypatch, docker):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = write_config(tmp_path / "config.yaml", OPENCODE)
    ok = recorded("opencode", "success")
    docker.turns = [Turn(stdout=ok), Turn(stdout=ok)]
    docker.usage = [10.0, 10.0, 10.02, 10.05, 10.05, 10.05]

    settled = ahl("run", "-c", config, *prompts(tmp_path, 2), "--no-build", "--name", "settled")

    assert settled.exit_code == 0, settled.output
    outcome = load(tmp_path / "runs/settled/result.json")
    first, second = [t["key_usage"] for t in outcome["turns"]]
    assert first["before"]["usd"] == 10.0 and first["after"]["usd"] == 10.05
    assert first["after"]["settled"] is True and first["delta_usd"] == pytest.approx(0.05)
    assert docker.reads_at_turn == [1, 6]
    assert second["delta_usd"] is None and second["after"]["settled"] is False
    assert outcome["totals"]["cost_usd_key_delta"] is None
    assert [(w["code"], w["turn"]) for w in outcome["warnings"]] == [("usage_not_updated", 2)]

    docker.turns, docker.usage = [Turn(stdout=ok)], [urllib.error.URLError("unreachable")]
    unreadable = ahl("run", "-c", config, *prompts(tmp_path, 1), "--no-build", "--name", "unreadable")

    assert unreadable.exit_code == 0, unreadable.output
    outcome = load(tmp_path / "runs/unreadable/result.json")
    assert outcome["status"] == "completed" and outcome["turns"][0]["key_usage"]["delta_usd"] is None
    assert outcome["totals"]["cost_usd_key_delta"] is None
    assert [(w["code"], w["turn"]) for w in outcome["warnings"]] == [("usage_read_failed", 1)]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    write_config(config, {**OPENCODE, "provider": "anthropic", "model": "claude-sonnet-4-6"})
    docker.turns, reads = [Turn(stdout=ok)], len(docker.usage_reads)
    direct = ahl("run", "-c", config, *prompts(tmp_path, 1), "--no-build", "--name", "direct")

    assert direct.exit_code == 0, direct.output
    outcome = load(tmp_path / "runs/direct/result.json")
    assert outcome["turns"][0]["key_usage"] is None and outcome["totals"]["cost_usd_key_delta"] is None
    assert len(docker.usage_reads) == reads


def test_a2_ac6_claude_runs_unattended_resumes_and_maps_every_model_alias(tmp_path, monkeypatch, docker):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    model = CLAUDE["model"]
    config = write_config(
        tmp_path / "config.yaml", {**CLAUDE, "model": {"name": model, "parameters": {"provider": PIN}}}
    )
    ok = recorded("claude", "success")
    docker.turns = [Turn(stdout=ok), Turn(stdout=ok)]

    result = ahl("run", "-c", config, *prompts(tmp_path, 2), "--no-build", "--name", "claude")

    assert result.exit_code == 0, result.output
    assert "model.parameters.provider" in result.output
    run = tmp_path / "runs/claude"
    session = load(run / "session.json")
    assert session["model_parameters"] == {"unsupported": ["provider"]}
    env = container_env(docker.launches()[-1])
    assert env["IS_SANDBOX"] == "1"
    assert {var: env.get(var) for var in CLAUDE_MODEL_VARIABLES} == dict.fromkeys(CLAUDE_MODEL_VARIABLES, model)
    first, second = turn_commands(docker, session["container"])
    for command in (first, second):
        assert command[:2] == ["claude", "-p"] and "--dangerously-skip-permissions" in command
        assert command[command.index("--disallowedTools") + 1] == "AskUserQuestion"
        assert command[command.index("--model") + 1] == model
        assert not {"--bare", "--disable-slash-commands"} & set(command)
    assert "--resume" not in first and second[second.index("--resume") + 1] == session_id(ok)
    assert [t["session_id"] for t in load(run / "result.json")["turns"]] == [session_id(ok)] * 2


def test_a2_ac6_opencode_models_routing_permissions_and_resume(tmp_path, monkeypatch, docker):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    model_id = "openrouter/" + OPENCODE["model"]
    raw = {
        **OPENCODE,
        "model": {"name": OPENCODE["model"], "parameters": {"provider": PIN}},
        "permissions": {"deny": ["websearch", "webfetch"]},
    }
    config = write_config(tmp_path / "config.yaml", raw)
    ok = recorded("opencode", "success")
    docker.turns = [Turn(stdout=ok), Turn(stdout=ok)]

    result = ahl("run", "-c", config, *prompts(tmp_path, 2), "--no-build", "--name", "routed")

    assert result.exit_code == 0, result.output
    assert "model.parameters" not in result.output
    run = tmp_path / "runs/routed"
    seeded, session = load(run / "opencode/config/opencode.json"), load(run / "session.json")
    assert seeded["model"] == seeded["small_model"] == model_id
    assert seeded["provider"]["openrouter"]["models"][OPENCODE["model"]]["options"]["provider"] == PIN
    assert session["model_parameters"] == {"unsupported": []}
    assert session["permissions"]["applied"] == ["webfetch", "websearch"]
    headless = json.loads(container_env(docker.launches()[-1])["OPENCODE_PERMISSION"])
    assert {**seeded["permission"], **headless} == dict.fromkeys(OPENCODE_PERMISSION_KEYS, "allow") | {
        "question": "deny", "webfetch": "deny", "websearch": "deny",
    }
    first, second = turn_commands(docker, session["container"])
    for command in (first, second):
        assert command[:2] == ["opencode", "run"] and command[command.index("-m") + 1] == model_id
        assert command[command.index("--format") + 1] == "json"
    assert "--session" not in first and second[second.index("--session") + 1] == session_id(ok)

    shell = ahl("up", "-c", config, "--no-build", "--name", "shell")

    assert shell.exit_code == 0 and "permissions not applied" not in shell.output, shell.output
    assert load(tmp_path / "runs/shell/opencode/config/opencode.json")["permission"] == {
        "webfetch": "deny", "websearch": "deny",
    }
    assert load(tmp_path / "runs/shell/session.json")["permissions"]["applied"] == ["webfetch", "websearch"]
    assert "OPENCODE_PERMISSION" not in container_env(docker.launches()[-1])

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    write_config(config, {**raw, "provider": "anthropic", "model": {"name": "claude-sonnet-4-6", "parameters": {"provider": PIN}}})
    docker.turns = [Turn(stdout=ok)]
    direct = ahl("run", "-c", config, *prompts(tmp_path, 1), "--no-build", "--name", "direct")

    assert direct.exit_code == 0 and "model.parameters.provider" in direct.output, direct.output
    seeded = load(tmp_path / "runs/direct/opencode/config/opencode.json")
    assert "provider" not in seeded and seeded["model"] == seeded["small_model"] == "anthropic/claude-sonnet-4-6"
    assert load(tmp_path / "runs/direct/session.json")["model_parameters"] == {"unsupported": ["provider"]}


@pytest.mark.parametrize(
    "harness,recording,exit_code,status,reason",
    [
        ("claude", "error_result", 0, "failed", "harness_reported_error"),
        ("claude", "no_text", 0, "failed", "no_assistant_output"),
        ("claude", "http_429", 1, "failed", "provider_error"),
        ("opencode", "error", 1, "failed", "harness_exit"),
        ("opencode", "error", 0, "failed", "harness_reported_error"),
        ("opencode", "no_text", 0, "failed", "no_assistant_output"),
        ("opencode", "http_429", 1, "failed", "provider_error"),
    ],
)
def test_a2_ac1_ac7_turn_status_follows_recorded_harness_output(
    tmp_path, monkeypatch, docker, harness, recording, exit_code, status, reason
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = write_config(tmp_path / "config.yaml", {"claude": CLAUDE, "opencode": OPENCODE}[harness])
    stdout = recorded(harness, recording)
    docker.turns = [Turn(exit_code=exit_code, stdout=stdout)]

    result = ahl("run", "-c", config, *prompts(tmp_path, 1), "--no-build", "--name", "r")

    assert result.exit_code == 1, result.output
    outcome = load(tmp_path / "runs/r/result.json")
    [turn] = outcome["turns"]
    assert (turn["status"], turn["exit_code"], turn["session_id"]) == (status, exit_code, session_id(stdout))
    assert (turn["reason"] or {}).get("code") == reason
    assert (outcome["status"], outcome["reason"]) == (turn["status"], turn["reason"])
    assert (tmp_path / "runs/r/turns/1/stdout.jsonl").read_text() == stdout
