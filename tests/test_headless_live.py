from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

pytestmark = pytest.mark.live

ENV_FILE = Path(os.environ.get("AHL_LIVE_ENV_FILE", Path(__file__).resolve().parents[1] / ".env"))
PIN = {"only": ["deepinfra"], "quantizations": ["fp8"], "allow_fallbacks": False}
CONFIGS = {
    "claude": {"harness": "claude", "provider": "openrouter", "model": "anthropic/claude-sonnet-5"},
    "opencode": {
        "harness": "opencode",
        "provider": "openrouter",
        "model": {"name": "deepseek/deepseek-v4.1-flash", "parameters": {"provider": PIN}},
    },
    "codex": {"harness": "codex", "provider": "openrouter", "model": "openai/gpt-6-sol"},
}
ASK_USER_TOOL = {"claude": "AskUserQuestion", "opencode": "question"}
DENIAL = re.compile(r"denied|not allowed|prevents you|no such tool|unavailable tool|not available|disallowed", re.I)


def ahl_run(tmp_path: Path, harness: str, *turns: str, **extra: Any) -> tuple[subprocess.CompletedProcess, Path]:
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(CONFIGS[harness] | extra))
    flags = []
    for index, text in enumerate(turns, start=1):
        (tmp_path / f"t{index}.md").write_text(text)
        flags += ["--turn", f"t{index}.md"]
    process = subprocess.run(
        [sys.executable, "-m", "ahl.cli", "run", "-c", "config.yaml", *flags,
         "--env-file", str(ENV_FILE), "--name", "smoke", "--timeout", "900"],
        cwd=tmp_path, capture_output=True, text=True, timeout=3600,
    )
    return process, tmp_path / "runs/smoke"


def smoke(tmp_path: Path, harness: str, extra: str = "") -> tuple[str, subprocess.CompletedProcess, Path]:
    nonce = secrets.token_hex(6)
    process, run = ahl_run(
        tmp_path, harness,
        f"Remember this nonce for later: {nonce}. Do not write the nonce to any file.\n{extra}"
        "Use a tool to create the file /workspace/hello.txt containing the word hello. Then reply with 'ready'.\n",
        "What is the nonce I gave you earlier? Reply with the nonce only.\n",
    )
    return nonce, process, run


def events(run: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]


def assistant_text(trace: list[dict[str, Any]], turn: int) -> str:
    return "\n".join(e["text"] for e in trace if e["type"] == "message" and e["role"] == "assistant" and e["turn"] == turn)


@pytest.mark.parametrize("harness", ["claude", "opencode"])
def test_a2_ac9_two_turn_smoke_recalls_the_nonce_in_one_session(tmp_path, harness):
    nonce, process, run = smoke(tmp_path, harness)

    assert process.returncode == 0, process.stdout[-3000:] + process.stderr[-3000:]
    outcome, trace = json.loads((run / "result.json").read_text()), events(run)
    assert [t["status"] for t in outcome["turns"]] == ["completed", "completed"]
    [session] = {t["session_id"] for t in outcome["turns"]}
    assert session and nonce in assistant_text(trace, 2)
    assert (run / "workspace/hello.txt").is_file()
    assert not [p for p in (run / "workspace").rglob("*") if p.is_file() and nonce in p.read_text(errors="replace")]
    assert outcome["totals"]["cost_usd_key_delta"] > 0
    usage = [e for e in trace if e["type"] == "usage"]
    if harness == "claude":
        assert len({e["model"] for e in usage}) == 1
        assert not [e for e in trace if e["type"] == "tool_call" and e["is_error"]]
        results = [json.loads(line) for n in (1, 2) for line in (run / f"turns/{n}/stdout.jsonl").read_text().splitlines()]
        assert [r["permission_denials"] for r in results if r["type"] == "result"] == [[], []]
    else:
        with sqlite3.connect(run / "opencode/data/opencode.db") as db:
            rows = [json.loads(data) for (data,) in db.execute("SELECT data FROM message")]
        tokens = [r["tokens"] for r in rows if r["role"] == "assistant"]
        expected = {
            "input_tokens": sum(t["input"] for t in tokens),
            "output_tokens": sum(t["output"] + t["reasoning"] for t in tokens),
            "cache_read_tokens": sum(t["cache"]["read"] for t in tokens),
            "cache_write_tokens": sum(t["cache"]["write"] for t in tokens),
        }
        assert {key: outcome["totals"][key] for key in expected} == expected
        assert expected["input_tokens"] > 0 and expected["output_tokens"] > 0


@pytest.mark.parametrize("harness", ["claude", "opencode"])
def test_a2_ac10_an_ask_user_instruction_never_waits_for_input(tmp_path, harness):
    _, process, run = smoke(
        tmp_path, harness, "Before you do anything else, ask me a clarifying question with your tool for asking the user.\n"
    )

    assert process.returncode in (0, 1), process.stdout[-3000:] + process.stderr[-3000:]
    outcome, trace = json.loads((run / "result.json").read_text()), events(run)
    assert assistant_text(trace, 1)
    if outcome["status"] != "completed":
        assert outcome["status"] == "failed"
        assert outcome["reason"]["code"] in {"harness_exit", "harness_reported_error", "no_assistant_output"}
        [failing] = [t for t in outcome["turns"] if t["status"] == "failed"]
        tool = ASK_USER_TOOL[harness]
        denied_calls = [
            e for e in trace
            if e["type"] == "tool_call" and e["turn"] == failing["index"] and e["is_error"]
            and tool in f"{e['tool']} {e['output']}" and DENIAL.search(e["output"] or "")
        ]
        native = [
            json.loads(line) for line in (run / f"turns/{failing['index']}/stdout.jsonl").read_text().splitlines()
        ]
        denied_natively = [
            d for e in native if e.get("type") == "result" for d in e.get("permission_denials") or []
            if d.get("tool_name") == tool
        ]
        assert denied_calls or denied_natively


def native_responses(run: Path, harness: str) -> list[str]:
    if harness == "claude":
        records = [json.loads(line) for path in (run / "claude/projects").rglob("*.jsonl") for line in path.open()]
        return sorted({
            r["message"]["id"] for r in records
            if r.get("type") == "assistant" and not r.get("isApiErrorMessage") and r["message"].get("model") != "<synthetic>"
        })
    with sqlite3.connect(run / "opencode/data/opencode.db") as db:
        rows = [(session, json.loads(data)) for session, data in db.execute("SELECT session_id, data FROM message")]
    return sorted(
        session for session, r in rows
        if r["role"] == "assistant" and any((r["tokens"]["input"], r["tokens"]["output"], r["tokens"]["reasoning"],
                                             r["tokens"]["cache"]["read"], r["tokens"]["cache"]["write"]))
    )


@pytest.mark.parametrize("harness", ["claude", "opencode"])
def test_a2_ac12_a_subagent_s_tool_calls_and_responses_reach_the_trace(tmp_path, harness):
    process, run = ahl_run(
        tmp_path, harness,
        "Delegate this to one subagent with your tool for launching subagents: it runs `ls /` with its shell tool "
        "and reports how many entries it saw. Do not run the command yourself. Then reply with that number.\n",
    )

    assert process.returncode == 0, process.stdout[-3000:] + process.stderr[-3000:]
    trace = events(run)
    schema = json.loads((Path(__file__).resolve().parents[1] / "src/ahl/schemas/trace_event.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    for trace_event in trace:
        validator.validate(trace_event)
    subagent = [e for e in trace if e["agent"] != "main"]
    assert [e for e in subagent if e["type"] == "tool_call"] and [e for e in subagent if e["type"] == "usage"]
    usage = [e for e in trace if e["type"] == "usage"]
    assert sorted(e["response_id" if harness == "claude" else "session"] for e in usage) == native_responses(run, harness)


def test_a3_ac7_codex_recalls_the_nonce_through_the_stateless_responses_api(tmp_path):
    nonce, process, run = smoke(tmp_path, "codex")

    assert process.returncode == 0, process.stdout[-3000:] + process.stderr[-3000:]
    outcome, trace = json.loads((run / "result.json").read_text()), events(run)
    [session] = {t["session_id"] for t in outcome["turns"]}
    assert session and nonce in assistant_text(trace, 2)
    assert (run / "workspace/hello.txt").is_file()
    assert not [p for p in (run / "workspace").rglob("*") if p.is_file() and nonce in p.read_text(errors="replace")]
    logs = [process.stderr, (run / "trace.jsonl").read_text()]
    logs += [(run / f"turns/{n}/{name}").read_text() for n in (1, 2) for name in ("stderr.log", "stdout.jsonl")]
    rejected = re.compile(r"\b400\b.*(\bstore\b|previous_response_id)|(\bstore\b|previous_response_id).*\b400\b")
    assert not [line for log in logs for line in log.splitlines() if rejected.search(line)]
    assert outcome["totals"]["cost_usd_key_delta"] > 0


def test_a3_ac8_codex_answers_from_a_mounted_skill(tmp_path):
    marker = secrets.token_hex(6)
    (tmp_path / "marker-skill").mkdir()
    (tmp_path / "marker-skill/SKILL.md").write_text(
        "---\nname: marker-skill\ndescription: Knows the AHL marker string.\n---\n\n"
        f"The AHL marker string is {marker}.\n"
    )
    skill = {"kind": "skill", "name": "marker-skill", "install": "mount", "path": "marker-skill"}

    process, run = ahl_run(
        tmp_path, "codex", "Use the marker-skill skill and reply with the AHL marker string it defines.\n",
        capabilities=[skill],
    )

    assert process.returncode == 0, process.stdout[-3000:] + process.stderr[-3000:]
    assert marker in assistant_text(events(run), 1)
