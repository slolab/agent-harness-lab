from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, NamedTuple

KEY_URL = "https://openrouter.ai/api/v1/key"
POLL_SECONDS = 10
WAIT_SECONDS = 120


class AfterTurn(NamedTuple):
    key_usage: dict[str, Any]
    warning: str | None
    interrupted: bool


def read(key: str) -> dict[str, Any] | None:
    request = urllib.request.Request(KEY_URL, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            usd = json.load(response)["data"]["usage"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(usd, (int, float)):
        return None
    return {"usd": float(usd), "at": datetime.now(timezone.utc).isoformat()}


def after_turn(
    key: str, before: dict[str, Any] | None, interruptible: Callable[[], AbstractContextManager[Any]]
) -> AfterTurn:
    readings = [read(key)]
    deadline = time.monotonic() + WAIT_SECONDS
    interrupted = False
    try:
        with interruptible():
            while before and readings[-1] and not _settled(before, readings) and time.monotonic() < deadline:
                time.sleep(POLL_SECONDS)
                readings.append(read(key))
    except KeyboardInterrupt:
        interrupted = True
    last = next((reading for reading in reversed(readings) if reading), None)
    failed = before is None or None in readings
    risen = before is not None and last is not None and last["usd"] > before["usd"]
    delta = round(last["usd"] - before["usd"], 10) if risen and not failed else None
    warning = None
    if failed:
        warning = "usage_read_failed"
    elif not risen:
        warning = "usage_not_updated"
    after = {**last, "settled": _settled(before, readings)} if last else None
    return AfterTurn({"before": before, "after": after, "delta_usd": delta}, warning, interrupted)


def _settled(before: dict[str, Any] | None, readings: list[dict[str, Any] | None]) -> bool:
    if before is None or len(readings) < 2 or None in readings[-2:]:
        return False
    return readings[-2]["usd"] == readings[-1]["usd"] > before["usd"]
