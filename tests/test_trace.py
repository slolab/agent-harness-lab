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
}


def schema() -> dict:
    return json.loads((resources.files("ahl") / "schemas/trace_event.schema.json").read_text())


@pytest.mark.parametrize("harness", ["claude", "opencode"])
def test_a2_ac8_recorded_runs_give_valid_traces_with_prompts_outputs_and_tokens(tmp_path, harness):
    run = tmp_path / "run"
    shutil.copytree(FIXTURES / "trace" / harness, run)
    if harness == "opencode":
        (run / "opencode/data").mkdir(parents=True)
        with closing(sqlite3.connect(run / "opencode/data/opencode.db")) as db:
            db.executescript((run / "opencode.sql").read_text())
    turns = json.loads((run / "turns.json").read_text())

    write_trace(run, get_adapter(harness).driver, turns)

    events = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
    validator = jsonschema.Draft202012Validator(schema(), format_checker=jsonschema.FormatChecker())
    for trace_event in events:
        validator.validate(trace_event)
    assert [e["seq"] for e in events] == list(range(len(events)))
    for turn in turns:
        user = [e for e in events if e["type"] == "message" and e["role"] == "user" and e["turn"] == turn["index"]]
        assert (run / turn["prompt_file"]).read_text().strip() in user[0]["text"]
    calls = [e for e in events if e["type"] == "tool_call"]
    assert calls and all(isinstance(e["output"], str) and e["is_error"] is False for e in calls)
    assert CACHED_RESPONSE[harness] in [{k: e[k] for k in USAGE_FIELDS} for e in events if e["type"] == "usage"]
    assert not [p for p in FIXTURES.rglob("*") if p.is_file() and b"sk-or-" in p.read_bytes()]


def test_a2_ac8_schema_and_documentation_list_the_same_events_and_fields():
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
