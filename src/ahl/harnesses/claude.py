"""Claude Code adapter — account/OpenRouter auth, isolated home, skills, and traces."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ahl.capabilities import Capability
from ahl.config import RunConfig
from ahl.permissions import PermissionPolicy, PermissionSetup
from ahl.harnesses.base import (
    TurnOutcome,
    Volumes,
    json_lines,
    native_skill_mount,
    provider_key,
    warn_unsupported_mcp,
)
from ahl.trace import event

CONTAINER_CLAUDE_HOME = "/root/.claude"
CONTAINER_WORKSPACE_SKILLS = "/workspace/.claude/skills"
ASK_USER_TOOL = "AskUserQuestion"
MODEL_ALIASES = (
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)

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


class ClaudePermissions:
    def prepare(
        self, run_dir: Path, config: RunConfig, policy: PermissionPolicy
    ) -> PermissionSetup:
        mapping = {"websearch": "WebSearch", "webfetch": "WebFetch"}
        applied = policy.deny.intersection(mapping)
        unsupported = {
            op: "no Claude native tool mapping" for op in sorted(policy.deny - applied)
        }
        path = run_dir / "permissions/claude/managed-settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        settings = {"permissions": {"deny": sorted(mapping[op] for op in applied)}}
        path.write_text(json.dumps(settings, indent=2) + "\n")
        return PermissionSetup(
            [(path, "/etc/claude-code/managed-settings.json")], applied, unsupported
        )


class ClaudeDriver:
    def applied_model_parameters(self, config: RunConfig) -> frozenset[str]:
        return frozenset()

    def env(self, config: RunConfig) -> dict[str, str]:
        # As root, Claude Code refuses --dangerously-skip-permissions unless told it runs in a sandbox.
        env = {"IS_SANDBOX": "1"}
        if config.provider.name == "openrouter":
            env |= dict.fromkeys(MODEL_ALIASES, config.model.name)
        return env

    def command(self, config: RunConfig, session_id: str | None) -> list[str]:
        command = [
            "claude", "-p", "--output-format", "stream-json", "--verbose",
            "--dangerously-skip-permissions", "--disallowedTools", ASK_USER_TOOL,
        ]
        if config.model.name:
            command += ["--model", config.model.name]
        return [*command, "--resume", session_id] if session_id else command

    def session_id(self, stdout: str) -> str | None:
        ids = [e["session_id"] for e in json_lines(stdout) if isinstance(e.get("session_id"), str)]
        return ids[-1] if ids else None

    def outcome(self, exit_code: int, stdout: str) -> TurnOutcome:
        events = json_lines(stdout)
        result = next((e for e in reversed(events) if e.get("type") == "result"), {})
        provider = result.get("terminal_reason") == "api_error" or result.get("api_error_status") is not None
        detail = result.get("result") or "; ".join(map(str, result.get("errors") or []))
        if exit_code:
            message = f"claude exited with code {exit_code}" + (f": {detail}" if detail else "")
            return TurnOutcome("failed", "provider_error" if provider else "harness_exit", message)
        if result.get("is_error"):
            reason = "provider_error" if provider else "harness_reported_error"
            return TurnOutcome("failed", reason, detail or f"claude reported {result.get('subtype')}")
        replies = [e["message"] for e in events if e.get("type") == "assistant" and isinstance(e.get("message"), dict)]
        if not any(_extract_message_text(reply.get("content")).strip() for reply in replies):
            return TurnOutcome("failed", "no_assistant_output", "claude produced no assistant message")
        return TurnOutcome("completed")

    def trace(self, run_dir: Path) -> list[dict[str, Any]]:
        return _trace_events(run_dir / "claude")


class ClaudeAdapter:
    permission_handler = ClaudePermissions()
    driver = ClaudeDriver()

    def build_env(self, config: RunConfig) -> dict[str, str]:
        if config.provider.name == "openrouter":
            return {
                "DISABLE_AUTOUPDATER": "1",
                "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
                "ANTHROPIC_AUTH_TOKEN": provider_key(config),
                "ANTHROPIC_API_KEY": "",
                "ANTHROPIC_MODEL": config.model.name,
            }
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
        return shlex.join(["claude", "--model", config.model.name]) if config.model.name else "claude"

    def start_hints(self, run_dir: Path, config: RunConfig) -> list[str]:
        if config.provider.name == "openrouter":
            return [
                "OpenRouter credential and model configured through environment variables",
                "If resuming an account-login run, use /logout and restart Claude to select OpenRouter",
                "OpenRouter guarantees Claude compatibility only with Anthropic models through Anthropic's first-party provider",
            ]
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

    identity = _response_identity(record, source, line_number)
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


def _response_identity(record: dict[str, Any], source: str, line_number: int) -> _ResponseId:
    message_id, uuid = record["message"].get("id"), record.get("uuid")
    if isinstance(message_id, str) and message_id:
        return ("message", message_id)
    if isinstance(uuid, str) and uuid:
        return ("uuid", uuid)
    return ("source", source, line_number)


def _trace_events(home: Path) -> list[dict[str, Any]]:
    records: list[tuple[dict[str, Any], str, int]] = []
    seen: set[str] = set()
    for path in sorted((home / "projects").glob("**/*.jsonl")):
        source = str(path.relative_to(home))
        for line_number, record in enumerate(json_lines(path.read_text(errors="replace")), start=1):
            uuid = record.get("uuid")
            if isinstance(uuid, str) and uuid in seen:
                continue
            seen.add(uuid)
            records.append((record, source, line_number))

    ledger: _ResponseLedger = {}
    results: dict[str, tuple[str, bool]] = {}
    for record, source, line_number in records:
        _record_usage(ledger, record, source, line_number)
        for block in _blocks(record):
            if block.get("type") == "tool_result" and block.get("tool_use_id"):
                results[str(block["tool_use_id"])] = (_extract_text(block.get("content")), bool(block.get("is_error")))

    items: list[dict[str, Any]] = []
    responses: dict[_ResponseId, dict[str, Any]] = {}
    for record, source, line_number in records:
        kind, message = record.get("type"), record.get("message")
        header = (
            record.get("sessionId"),
            record.get("agentId") if record.get("isSidechain") else "main",
            record.get("timestamp"),
        )
        if kind == "system" and isinstance(record.get("content"), str) and record["content"]:
            items.append(event("message", *header, role="system", text=record["content"], reasoning=None))
        if not isinstance(message, dict):
            continue
        if kind == "user" and (text := _extract_message_text(message.get("content"))):
            injected = record.get("isMeta") or any(b.get("type") == "tool_result" for b in _blocks(record))
            items.append(event("message", *header, role="system" if injected else "user", text=text, reasoning=None))
        elif kind == "assistant" and record.get("isApiErrorMessage"):
            text = _extract_message_text(message.get("content")) or "API error"
            items.append(event("error", *header, message=text))
        elif kind == "assistant":
            identity = _response_identity(record, source, line_number)
            if identity not in responses:
                responses[identity] = {"identity": identity, "header": header, "message": message, "blocks": []}
                items.append(responses[identity])
            blocks = responses[identity]["blocks"]
            blocks += [block for block in _blocks(record) if block not in blocks]

    events: list[dict[str, Any]] = []
    for item in items:
        events += _response_events(item, ledger, results) if "blocks" in item else [item]
    return sorted(events, key=lambda e: e["ts"] or "")


def _blocks(record: dict[str, Any]) -> list[dict[str, Any]]:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def _response_events(
    response: dict[str, Any], ledger: _ResponseLedger, results: dict[str, tuple[str, bool]]
) -> list[dict[str, Any]]:
    header, message, blocks = response["header"], response["message"], response["blocks"]

    def joined(kind: str, key: str) -> str:
        return "\n".join(b[key] for b in blocks if b.get("type") == kind and isinstance(b.get(key), str) and b[key])

    text, reasoning = joined("text", "text"), joined("thinking", "thinking")
    events = [event("message", *header, role="assistant", text=text, reasoning=reasoning or None)] if text or reasoning else []
    for block in blocks:
        if block.get("type") == "tool_use":
            output, is_error = results.get(str(block.get("id")), (None, None))
            tool_input = block.get("input")
            events.append(event(
                "tool_call", *header, id=block.get("id"), tool=str(block.get("name")),
                input=tool_input if isinstance(tool_input, (dict, str)) else {}, output=output, is_error=is_error,
            ))
    usage = ledger.get(response["identity"])
    if usage and message.get("model") != "<synthetic>":
        counters = usage.counters
        events.append(event(
            "usage", *header, model=message.get("model"), response_id=message.get("id"),
            input_tokens=counters.get("input_tokens"), output_tokens=counters.get("output_tokens"),
            cache_read_tokens=counters.get("cache_read_input_tokens"),
            cache_write_tokens=counters.get("cache_creation_input_tokens"),
            reasoning_tokens=None, cost_usd=None,
        ))
    return events


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
