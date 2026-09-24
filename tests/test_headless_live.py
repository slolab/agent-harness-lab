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
}
ASK_USER_TOOL = {"claude": "AskUserQuestion", "opencode": "question"}
DENIAL = re.compile(r"denied|not allowed|prevents you|no such tool|unavailable tool|not available|disallowed", re.I)


def smoke(tmp_path: Path, harness: str, extra: str = "") -> tuple[str, subprocess.CompletedProcess, Path]:
    nonce = secrets.token_hex(6)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(CONFIGS[harness]))
    (tmp_path / "t1.md").write_text(
        f"Remember this nonce for later: {nonce}. Do not write the nonce to any file.\n{extra}"
        "Use a tool to create the file /workspace/hello.txt containing the word hello. Then reply with 'ready'.\n"
    )
    (tmp_path / "t2.md").write_text("What is the nonce I gave you earlier? Reply with the nonce only.\n")
    process = subprocess.run(
        [sys.executable, "-m", "ahl.cli", "run", "-c", "config.yaml", "--turn", "t1.md", "--turn", "t2.md",
         "--env-file", str(ENV_FILE), "--name", "smoke", "--timeout", "900"],
        cwd=tmp_path, capture_output=True, text=True, timeout=3600,
    )
    return nonce, process, tmp_path / "runs/smoke"


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
