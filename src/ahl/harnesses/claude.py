"""Claude Code adapter — account login, isolated home, skills, and traces."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ahl.capabilities import Capability
from ahl.config import RunConfig
from ahl.harnesses.base import (
    Volumes,
    native_skill_mount,
    warn_unsupported_mcp,
)

CONTAINER_CLAUDE_HOME = "/root/.claude"
CONTAINER_WORKSPACE_SKILLS = "/workspace/.claude/skills"

# Token fields Claude Code records under `message.usage` on assistant records.
# Keep base counters separate from the cache lifetime breakdown, which must
# never be added to the cache_creation_input_tokens total a second time.
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
CACHE_USAGE_FIELDS = ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")


@dataclass
class _ResponseUsage:
    # Absent keys are unknown; a recorded zero is valid evidence.
    counters: dict[str, int] = field(default_factory=dict)
    terminal: bool = False

    def merge(self, other: _ResponseUsage) -> None:
        for name, value in other.counters.items():
            self.counters[name] = max(self.counters.get(name, 0), value)
        self.terminal |= other.terminal


_ResponseId = tuple[str, str] | tuple[str, str, int]
_ResponseLedger = dict[_ResponseId, _ResponseUsage]


class ClaudeAdapter:
    def build_env(self, config: RunConfig) -> dict[str, str]:
        # Deliberately do not inject ANTHROPIC_API_KEY. With no provider
        # credential in the environment Claude Code offers its Claude-account
        # OAuth flow (Pro/Max and eligible Team/Enterprise plans).
        return {"DISABLE_AUTOUPDATER": "1"}

    def _home(self, run_dir: Path) -> Path:
        return run_dir / "claude"

    def seed(self, run_dir: Path, config: RunConfig) -> Volumes:
        home = self._home(run_dir)
        home.mkdir(parents=True, exist_ok=True)
        # Claude writes project session JSONL under ~/.claude/projects. Mounting
        # this per-run directory makes traces recoverable and keeps consecutive
        # runs from seeing one another's history.
        (home / "projects").mkdir(exist_ok=True)
        return [(home, CONTAINER_CLAUDE_HOME)]

    def wire_capabilities(
        self,
        run_dir: Path,
        config: RunConfig,
        capabilities: list[Capability],
    ) -> Volumes:
        warn_unsupported_mcp("claude", capabilities)
        volumes: Volumes = []
        for skill in capabilities:
            if skill.kind != "skill" or skill.install != "mount" or skill.path is None:
                continue
            container_path = native_skill_mount(skill, CONTAINER_WORKSPACE_SKILLS)
            volumes.append((skill.path, container_path))
        return volumes

    def parse_trace(self, run_dir: Path) -> dict[str, Any]:
        return _parse_trace(self._home(run_dir))

    def start_command(self, config: RunConfig) -> str:
        return f"claude --model {config.model.name}" if config.model.name else "claude"

    def start_hints(self, run_dir: Path, config: RunConfig) -> list[str]:
        return [
            "No ANTHROPIC_API_KEY is injected; choose Claude App account login when prompted",
            "OAuth credentials and session history are isolated to this run",
        ]

    def docker_args(self, config: RunConfig) -> list[str]:
        return []


def _parse_trace(home: Path) -> dict[str, Any]:
    session_files = sorted((home / "projects").glob("**/*.jsonl"))
    sessions = []
    responses: _ResponseLedger = {}
    for path in session_files:
        parsed = _parse_session(path, str(path.relative_to(home)))
        if parsed is None:
            continue
        session, session_responses = parsed
        sessions.append(session)
        for identity, usage in session_responses.items():
            responses.setdefault(identity, _ResponseUsage()).merge(usage)
    return {
        "paths": {"home": str(home), "projects": str(home / "projects")},
        "sessions": sessions,
        "totals": _trace_totals(sessions, responses),
    }


def _trace_totals(
    sessions: list[dict[str, Any]], responses: _ResponseLedger,
) -> dict[str, Any]:
    """Summarize the response union: session histories can overlap after forks."""
    starts = sorted(s["started_at"] for s in sessions if s.get("started_at"))
    ends = sorted(s["ended_at"] for s in sessions if s.get("ended_at"))
    started_at = starts[0] if starts else None
    ended_at = ends[-1] if ends else None
    return {
        "sessions": len(sessions),
        "models": sorted({s["model"] for s in sessions if s.get("model")}),
        **_usage_summary(responses),
        "started_at": started_at,
        "ended_at": ended_at,
        "wall_clock_seconds": _wall_clock_seconds(started_at, ended_at),
    }


def _parse_session(
    path: Path, source: str,
) -> tuple[dict[str, Any], _ResponseLedger] | None:
    records: list[dict[str, Any]] = []
    responses: _ResponseLedger = {}
    try:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
                _record_usage(responses, record, source, line_number)
    except OSError:
        return None

    if not records:
        return None

    messages: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: dict[str, dict[str, Any]] = {}

    for record in records:
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        text = _extract_message_text(content)
        if role in {"user", "assistant"} and text:
            messages.append(
                {
                    "role": role,
                    "text": text,
                    "timestamp": record.get("timestamp"),
                    "uuid": record.get("uuid"),
                }
            )
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "args": block.get("input"),
                        }
                    )
                elif block.get("type") == "tool_result" and block.get("tool_use_id"):
                    tool_results[str(block["tool_use_id"])] = {
                        "content": _extract_text(block.get("content")),
                        "is_error": bool(block.get("is_error", False)),
                    }

    for call in tool_calls:
        result = tool_results.get(str(call.get("id")))
        if result is not None:
            call["result"] = result["content"]
            call["is_error"] = result["is_error"]

    timestamps = sorted(
        ts for ts in (r.get("timestamp") for r in records) if isinstance(ts, str)
    )
    started_at = timestamps[0] if timestamps else None
    ended_at = timestamps[-1] if timestamps else None
    return {
        "file": path.name,
        "id": next((r.get("sessionId") for r in records if r.get("sessionId")), None),
        "cwd": next((r.get("cwd") for r in records if r.get("cwd")), None),
        "version": next((r.get("version") for r in records if r.get("version")), None),
        "model": _session_model(records),
        "started_at": started_at,
        "ended_at": ended_at,
        "wall_clock_seconds": _wall_clock_seconds(started_at, ended_at),
        "record_count": len(records),
        **_usage_summary(responses),
        "messages": messages,
        "summary": {
            "user_messages": [m["text"] for m in messages if m["role"] == "user" and m["text"]],
            "assistant_messages": [
                m["text"] for m in messages if m["role"] == "assistant" and m["text"]
            ],
            "tool_calls": tool_calls,
        },
    }, responses


def _session_model(records: list[dict[str, Any]]) -> str | None:
    """The model that actually served the session (ground truth, per assistant record)."""
    for record in records:
        message = record.get("message")
        if isinstance(message, dict) and isinstance(message.get("model"), str):
            return message["model"]
    return None


def _record_usage(
    responses: _ResponseLedger, record: dict[str, Any], source: str, line_number: int,
) -> None:
    """Reconcile cumulative snapshots, including late updates and early replays."""
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return
    if record.get("isApiErrorMessage") is True:
        return
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return

    message_id, uuid = message.get("id"), record.get("uuid")
    identity: _ResponseId
    if isinstance(message_id, str) and message_id:
        identity = ("message", message_id)
    elif isinstance(uuid, str) and uuid:
        identity = ("uuid", uuid)
    else:
        identity = ("source", source, line_number)

    cache = usage.get("cache_creation")
    if not isinstance(cache, dict):
        cache = {}
    counters = {}
    for fields, values in ((USAGE_FIELDS, usage), (CACHE_USAGE_FIELDS, cache)):
        for name in fields:
            value = values.get(name)
            if type(value) is int and value >= 0:
                counters[name] = value
    stop_reason = message.get("stop_reason")
    snapshot = _ResponseUsage(counters, isinstance(stop_reason, str) and bool(stop_reason))
    responses.setdefault(identity, _ResponseUsage()).merge(snapshot)


def _usage_summary(responses: _ResponseLedger) -> dict[str, Any]:
    """Report known base usage; retain unknown cache lifetime buckets as null."""
    usage: dict[str, Any] = {
        name: sum(response.counters.get(name, 0) for response in responses.values())
        for name in USAGE_FIELDS
    }
    usage["cache_creation"] = {
        name: (
            sum(response.counters[name] for response in responses.values())
            if all(name in response.counters for response in responses.values()) else None
        )
        for name in CACHE_USAGE_FIELDS
    }
    return {
        "usage": usage,
        "api_responses": len(responses),
        "incomplete_api_responses": sum(
            not response.terminal or any(name not in response.counters for name in USAGE_FIELDS)
            for response in responses.values()
        ),
    }


def _wall_clock_seconds(started_at: str | None, ended_at: str | None) -> float | None:
    start, end = _parse_ts(started_at), _parse_ts(ended_at)
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def _parse_ts(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text = content.get("text")
        return text if isinstance(text, str) else ""
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
            elif block.get("type") == "tool_result":
                result_text = _extract_text(block.get("content"))
                if result_text:
                    parts.append(result_text)
    return "\n".join(parts)


def _extract_message_text(content: Any) -> str:
    """Extract conversational text without treating tool results as user prompts."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )
