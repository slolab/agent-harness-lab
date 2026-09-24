from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
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


def after_turn(key: str, before: dict[str, Any] | None) -> AfterTurn:
    last = previous = None
    settled = failed = interrupted = False
    deadline = time.monotonic() + WAIT_SECONDS
    try:
        while True:
            reading = read(key)
            if reading is None:
                failed = True
                break
            previous, last = last, reading
            risen = before is not None and last["usd"] > before["usd"]
            if before is None or (risen and previous and previous["usd"] == last["usd"]):
                settled = risen
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        interrupted = True
    risen = before is not None and last is not None and last["usd"] > before["usd"]
    delta = round(last["usd"] - before["usd"], 10) if risen and not failed else None
    warning = None
    if failed or before is None:
        warning = "usage_read_failed"
    elif not risen:
        warning = "usage_not_updated"
    after = {**last, "settled": settled} if last else None
    return AfterTurn({"before": before, "after": after, "delta_usd": delta}, warning, interrupted)
