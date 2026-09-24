from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def schema() -> dict:
    return json.loads((resources.files("ahl") / "schemas/trace_event.schema.json").read_text())


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
