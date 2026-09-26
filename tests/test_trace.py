from __future__ import annotations

import json
import re
import shutil
import sqlite3
from contextlib import closing
from importlib import resources
from pathlib import Path

import jsonschema
import pytest

from ahl.harnesses import get_adapter
from ahl.trace import write_trace

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures/a2"
USAGE_FIELDS = (
    "model", "response_id", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "cost_usd",
)
CACHED_RESPONSE = {
    "claude": {
        "model": "anthropic/claude-sonnet-5", "response_id": "gen-1790279177-XrfBgy7UKV4MNIbVAn5b",
        "input_tokens": 2, "output_tokens": 4, "cache_read_tokens": 38437, "cache_write_tokens": 158,
        "reasoning_tokens": 0, "cost_usd": None,
    },
    "opencode": {
        "model": "deepseek/deepseek-v4.1-flash", "response_id": None,
        "input_tokens": 5594, "output_tokens": 60 + 37, "cache_read_tokens": 1792, "cache_write_tokens": 0,
        "reasoning_tokens": 37, "cost_usd": 0.000902676,
    },
    "codex": {
        "model": "openai/gpt-6-sol", "response_id": "gen-1790411646-NeYVj0IYSw6PdCBFpeXN",
        "input_tokens": 11838 - 11620 - 150, "output_tokens": 5, "cache_read_tokens": 11620, "cache_write_tokens": 150,
        "reasoning_tokens": 0, "cost_usd": None,
    },
}


DETAIL = {
    "message": ("role", "text", "reasoning"),
    "tool_call": ("tool", "output", "is_error"),
    "usage": ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens"),
    "error": ("message",),
}
FULL_OUTPUT = "\n".join(map(str, range(3000)))


def schema() -> dict:
    return json.loads((resources.files("ahl") / "schemas/trace_event.schema.json").read_text())


def validated(run: Path) -> list[dict]:
    events = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
    validator = jsonschema.Draft202012Validator(schema(), format_checker=jsonschema.FormatChecker())
    for trace_event in events:
        validator.validate(trace_event)
    assert [e["seq"] for e in events] == list(range(len(events)))
    return events


def at(second: int) -> str:
    return f"2026-09-24T10:00:{second:02d}Z"


def claude_response(uuid: str, second: int, message_id: str, content: list, output: int, **extra) -> dict:
    usage = {
        "input_tokens": 3, "output_tokens": output, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 2,
        "output_tokens_details": {"thinking_tokens": 1},
    }
    message = {"id": message_id, "model": "M", "role": "assistant", "content": content, "usage": usage}
    return {"type": "assistant", "uuid": uuid, "timestamp": at(second), "message": message, **extra}


def write_claude(run: Path) -> list[tuple]:
    saved = "projects/-workspace/S/tool-results/t2.txt"
    (run / "claude" / saved).parent.mkdir(parents=True)
    (run / "claude" / saved).write_text(FULL_OUTPUT)
    prompt = {"type": "user", "uuid": "u1", "timestamp": at(0), "message": {"role": "user", "content": "do it"}}
    main = [
        prompt,
        {"type": "user", "uuid": "u2", "timestamp": at(1), "isMeta": True,
         "message": {"role": "user", "content": [{"type": "text", "text": "reminder"}]}},
        claude_response("u3", 2, "m1", [{"type": "thinking", "thinking": "plan"}], 1),
        claude_response("u4", 3, "m1", [
            {"type": "text", "text": "running"}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {}},
        ], 9),
        {"type": "user", "uuid": "u5", "timestamp": at(5), "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "a.txt"}, {"type": "text", "text": "note"},
        ]}},
        {"type": "user", "uuid": "u10", "timestamp": at(5), "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t2", "content": "<persisted-output>preview</persisted-output>"},
        ]}, "toolUseResult": {"stdout": "capped", "persistedOutputPath": f"/root/.claude/{saved}"}},
        {"type": "system", "uuid": "u6", "content": "compacted"},
        {**claude_response("u7", 7, "m2", [{"type": "text", "text": "API Error: 529"}], 0), "isApiErrorMessage": True},
        claude_response("u8", 8, "m3", [{"type": "text", "text": "No response requested."}], 0),
    ]
    main[-1]["message"]["model"] = main[-2]["message"]["model"] = "<synthetic>"
    subagent = [prompt, claude_response("u9", 4, "m4", [{"type": "text", "text": "sub done"}], 2,
                                        isSidechain=True, agentId="a1")]
    for name, records in (("S.jsonl", main), ("S/subagents/agent-a1.jsonl", subagent)):
        path = run / "claude/projects/-workspace" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return [
        ("message", 1, "main", "user", "do it", None),
        ("message", 1, "main", "system", "reminder", None),
        ("message", 1, "main", "assistant", "running", "plan"),
        ("tool_call", 1, "main", "Bash", "a.txt", False),
        ("tool_call", 1, "main", "Bash", FULL_OUTPUT, False),
        ("usage", 1, "main", 3, 9, 10, 2, 1),
        ("message", 1, "a1", "assistant", "sub done", None),
        ("usage", 1, "a1", 3, 2, 10, 2, 1),
        ("message", 1, "main", "system", "note", None),
        ("message", 1, "main", "system", "compacted", None),
        ("error", 2, "main", "API Error: 529"),
        ("message", 2, "main", "assistant", "No response requested.", None),
    ]


def write_opencode(run: Path) -> list[tuple]:
    data = run / "opencode/data"
    (data / "tool-output").mkdir(parents=True)
    (data / "tool-output/tool_1").write_text(FULL_OUTPUT)
    (run / "opencode/config").mkdir()
    (run / "opencode/config/opencode.json").write_text("{}")
    tokens = {"input": 5, "output": 7, "reasoning": 3, "cache": {"read": 11, "write": 0}}
    zero = {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}

    def tool(tool_id: str, status: str, **state) -> dict:
        return {"type": "tool", "callID": tool_id, "tool": tool_id, "state": {"status": status, "input": {}, **state}}

    messages = [
        ("ses_main", {"role": "user"}, [
            {"type": "text", "text": "do it"}, {"type": "text", "text": "file contents", "synthetic": True},
        ]),
        ("ses_main", {"role": "assistant", "modelID": "M", "tokens": tokens}, [
            {"type": "reasoning", "text": "plan"}, {"type": "text", "text": "running"},
            tool("saved", "completed", output="preview", metadata={
                "outputPath": "/root/.local/share/opencode/tool-output/tool_1",
            }),
            tool("outside", "completed", output="kept", metadata={
                "outputPath": "/root/.local/share/opencode/../config/opencode.json",
            }),
            tool("failed", "error", error="no such file"),
        ]),
        ("ses_child", {"role": "assistant", "modelID": "M", "tokens": {**zero, "input": 1, "output": 1}}, [
            {"type": "text", "text": "sub done"},
        ]),
        ("ses_main", {"role": "assistant", "error": {"name": "APIError", "data": {"message": "rate limited"}},
                      "tokens": zero}, []),
    ]
    with closing(sqlite3.connect(data / "opencode.db")) as db, db:
        db.executescript(
            "CREATE TABLE session (id TEXT, parent_id TEXT);"
            "CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT);"
            "CREATE TABLE part (id TEXT, message_id TEXT, data TEXT);"
            "INSERT INTO session VALUES ('ses_main', NULL), ('ses_child', 'ses_main');"
        )
        for n, (session, info, parts) in enumerate(messages):
            db.execute("INSERT INTO message VALUES (?, ?, ?, ?)", (f"m{n}", session, 1790244000000 + n, json.dumps(info)))
            db.executemany("INSERT INTO part VALUES (?, ?, ?)", [
                (f"p{n}{i}", f"m{n}", json.dumps(part)) for i, part in enumerate(parts)
            ])
    return [
        ("message", 1, "main", "user", "do it", None),
        ("message", 1, "main", "system", "file contents", None),
        ("message", 1, "main", "assistant", "running", "plan"),
        ("tool_call", 1, "main", "saved", FULL_OUTPUT, False),
        ("tool_call", 1, "main", "outside", "kept", False),
        ("tool_call", 1, "main", "failed", "no such file", True),
        ("usage", 1, "main", 5, 10, 11, 0, 3),
        ("message", 1, "ses_child", "assistant", "sub done", None),
        ("usage", 1, "ses_child", 1, 1, 0, 0, 0),
        ("error", 1, "main", "rate limited"),
    ]


def write_codex(run: Path) -> list[tuple]:
    def record(second: int, kind: str, **payload) -> dict:
        return {"timestamp": at(second), "type": kind, "payload": payload}

    def text(role: str, value: str) -> dict:
        kind = "output_text" if role == "assistant" else "input_text"
        return {"type": "message", "role": role, "content": [{"type": kind, "text": value}]}

    def said(value: str) -> dict:
        return {"type": "item_completed", "item": {"type": "UserMessage", "content": [{"type": "text", "text": value}]}}

    def usage(response_id: str, total: int, **counts) -> dict:
        tokens = {"input_tokens": total, "cached_input_tokens": 10, "output_tokens": 9, "reasoning_output_tokens": 1}
        return {"response_id": response_id, "usage": {**tokens, **counts}}

    main = [
        record(0, "session_meta", id="S", source="exec"),
        record(0, "turn_context", model="M"),
        record(0, "response_item", **text("developer", "skills list")),
        record(0, "response_item", **text("user", "<environment_context>")),
        record(0, "response_item", **text("user", "do it")),
        record(0, "event_msg", **said("do it")),
        record(1, "response_item", type="reasoning", summary=[{"type": "summary_text", "text": "plan"}], content=None),
        record(1, "response_item", **text("assistant", "running")),
        record(1, "response_item", type="function_call", name="exec_command", arguments='{"cmd": "seq 3000"}',
               call_id="c1"),
        record(2, "event_msg", type="item_completed", item={
            "type": "CommandExecution", "id": "c1", "status": "completed", "aggregated_output": FULL_OUTPUT,
        }),
        record(2, "response_item", type="function_call_output", call_id="c1", output="Warning: truncated output"),
        record(2, "token_usage_record", **usage("r1", 20, cache_write_input_tokens=4)),
        record(4, "response_item", type="reasoning", summary=[{"type": "summary_text", "text": "look it up"}]),
        record(4, "response_item", type="web_search_call", id="ws1", status="completed",
               action={"type": "search", "query": "q"}),
        record(4, "response_item", type="custom_tool_call", name="apply_patch", input="*** Begin Patch", call_id="c2"),
        record(4, "event_msg", type="item_completed", item={"type": "FileChange", "id": "c2", "status": "failed"}),
        record(4, "response_item", type="custom_tool_call_output", call_id="c2", output="patch rejected"),
        record(5, "token_usage_record", **usage("r2", 30)),
        record(7, "event_msg", type="task_complete", error={"message": "rate limited"}),
    ]
    subagent = [
        record(3, "session_meta", id="A", source={"subagent": {"thread_spawn": {"parent_thread_id": "S"}}}),
        record(3, "turn_context", model="M"),
        record(3, "response_item", **text("user", "sub task")),
        record(3, "event_msg", **said("sub task")),
        record(3, "response_item", **text("assistant", "sub done")),
        record(3, "token_usage_record", **usage("r3", 15, cache_write_input_tokens=0)),
    ]
    for name, records in (("rollout-S.jsonl", main), ("rollout-A.jsonl", subagent)):
        path = run / "codex/sessions/2026/09/24" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return [
        ("message", 1, "main", "system", "skills list", None),
        ("message", 1, "main", "system", "<environment_context>", None),
        ("message", 1, "main", "user", "do it", None),
        ("message", 1, "main", "assistant", "running", "plan"),
        ("tool_call", 1, "main", "exec_command", FULL_OUTPUT, False),
        ("usage", 1, "main", 6, 9, 10, 4, 1),
        ("message", 1, "A", "user", "sub task", None),
        ("message", 1, "A", "assistant", "sub done", None),
        ("usage", 1, "A", 5, 9, 10, 0, 1),
        ("message", 1, "main", "assistant", "", "look it up"),
        ("tool_call", 1, "main", "web_search", None, False),
        ("tool_call", 1, "main", "apply_patch", "patch rejected", True),
        ("usage", 1, "main", 20, 9, 10, None, 1),
        ("error", 2, "main", "rate limited"),
    ]


@pytest.mark.parametrize(
    "harness,write_native", [("claude", write_claude), ("opencode", write_opencode), ("codex", write_codex)]
)
def test_a2_ac8_converters_follow_the_documented_rules_on_edge_records(tmp_path, harness, write_native):
    expected = write_native(tmp_path)
    turns = [{"index": 1, "started_at": "2026-09-24T09:00:00+00:00"}, {"index": 2, "started_at": at(6)}]

    write_trace(tmp_path, get_adapter(harness).driver.trace(tmp_path), turns)

    events = validated(tmp_path)
    assert [(e["type"], e["turn"], e["agent"], *(e[k] for k in DETAIL[e["type"]])) for e in events] == expected


@pytest.mark.parametrize("harness", ["claude", "opencode", "codex"])
def test_a2_ac8_a3_ac6_recorded_runs_give_valid_traces_with_prompts_outputs_and_tokens(tmp_path, harness):
    run = tmp_path / "run"
    shutil.copytree(FIXTURES / "trace" / harness, run)
    if harness == "opencode":
        (run / "opencode/data").mkdir(parents=True)
        with closing(sqlite3.connect(run / "opencode/data/opencode.db")) as db:
            db.executescript((run / "opencode.sql").read_text())
    turns = json.loads((run / "turns.json").read_text())

    write_trace(run, get_adapter(harness).driver.trace(run), turns)

    events = validated(run)
    for turn in turns:
        user = [e for e in events if e["type"] == "message" and e["role"] == "user" and e["turn"] == turn["index"]]
        assert (run / turn["prompt_file"]).read_text().strip() in user[0]["text"]
    calls = [e for e in events if e["type"] == "tool_call"]
    assert calls and all(isinstance(e["output"], str) and e["is_error"] is False for e in calls)
    usage = [e for e in events if e["type"] == "usage"]
    assert CACHED_RESPONSE[harness] in [{k: e[k] for k in USAGE_FIELDS} for e in usage]
    if harness == "codex":
        native = [json.loads(line) for path in (run / "codex/sessions").rglob("*.jsonl") for line in path.open()]
        assert sorted(e["response_id"] for e in usage) == sorted(
            {r["payload"]["response_id"] for r in native if r["type"] == "token_usage_record"}
        )
        assert calls[0]["input"]["cmd"].startswith("apply_patch")


def test_a2_ac8_schema_matches_the_documentation_and_no_fixture_holds_a_key():
    documented: dict[str, set[str]] = {}
    section = None
    for line in (ROOT / "docs/trace-schema.md").read_text().splitlines():
        if line.startswith("#"):
            heading = re.fullmatch(r"#{2,3} (?:`(\w+)`|Common fields)", line)
            section = (heading[1] or "common") if heading else None
            if section:
                documented[section] = set()
        elif section and (field := re.match(r"\| `(\w+)` \|", line)):
            documented[section].add(field[1])

    trace = schema()
    declared = {"common": set(trace["properties"])} | {
        branch["properties"]["type"]["const"]: set(branch["properties"]) - {"type"} for branch in trace["oneOf"]
    }
    assert set(declared) == {"common", "message", "tool_call", "usage", "error"}
    assert documented == declared
    assert not [p for p in FIXTURES.parent.rglob("*") if p.is_file() and b"sk-or-" in p.read_bytes()]
