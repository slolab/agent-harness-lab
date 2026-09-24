from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ahl.harnesses.base import HeadlessDriver

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def event(kind: str, session: str | None, agent: str, ts: str | None, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "session": session, "agent": agent, "ts": ts, **fields}


def iso_from_ms(milliseconds: Any) -> str | None:
    if not isinstance(milliseconds, (int, float)):
        return None
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat()


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def write_trace(run_dir: Path, driver: HeadlessDriver, turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    starts = [(turn["index"], parse_ts(turn["started_at"])) for turn in turns if turn["started_at"]]
    events, turn = [], None
    for seq, native in enumerate(driver.trace(run_dir)):
        ts = parse_ts(native["ts"])
        if ts is not None:
            turn = next((index for index, start in reversed(starts) if start <= ts), None)
        events.append({"seq": seq, "type": native["type"], "session": native["session"],
                       "agent": native["agent"], "turn": turn, **native})
    (run_dir / "trace.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return events


def token_totals(events: list[dict[str, Any]]) -> dict[str, int | None]:
    usage = [e for e in events if e["type"] == "usage"]
    return {
        name: None if any(e[name] is None for e in usage) else sum(e[name] for e in usage)
        for name in TOKEN_FIELDS
    }
