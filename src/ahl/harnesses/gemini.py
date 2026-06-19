"""Gemini CLI adapter — env, home seeding, skill wiring, session trace parsing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ahl.capabilities import Capability, render_skill_fallback
from ahl.config import ConfigError, RunConfig
from ahl.harnesses.base import Volumes, google_env, warn_unsupported_mcp

CONTAINER_GEMINI_HOME = "/root/.gemini"
CONTAINER_WORKSPACE = "/workspace"
WORKSPACE_TMP = "tmp/workspace"

AUTH_TYPE = {
    "vertex": "vertex-ai",
    "gemini": "gemini-api-key",
}


class GeminiAdapter:
    def build_env(self, config: RunConfig) -> dict[str, str]:
        return google_env(config)

    def _home(self, run_dir: Path) -> Path:
        return run_dir / "gemini"

    def seed(self, run_dir: Path, config: RunConfig) -> Volumes:
        home = self._home(run_dir)
        _seed_home(home, config)
        return [(home, CONTAINER_GEMINI_HOME)]

    def wire_capabilities(self, run_dir: Path, config: RunConfig, capabilities: list[Capability]) -> Volumes:
        warn_unsupported_mcp("gemini", capabilities)
        skills = [c for c in capabilities if c.kind == "skill"]
        if not skills:
            return []
        home = self._home(run_dir)
        # Gemini CLI always loads a global ~/.gemini/GEMINI.md context file,
        # regardless of project — no extra mount needed, home is already mounted.
        sections = [render_skill_fallback(skill.path) for skill in skills if skill.path]
        (home / "GEMINI.md").write_text("\n\n".join(sections) + "\n")
        return []

    def parse_trace(self, run_dir: Path) -> dict[str, Any] | None:
        return _parse_trace(self._home(run_dir))

    def start_command(self, config: RunConfig) -> str:
        base = "gemini --skip-trust"
        return f"{base} -m {config.model.name}" if config.model.name else base

    def start_hints(self, config: RunConfig) -> list[str]:
        auth = AUTH_TYPE.get(config.provider.name)
        if auth:
            return [f"Auth pre-configured as {auth} in ~/.gemini/settings.json"]
        return []


def _seed_home(home: Path, config: RunConfig) -> None:
    """Write Gemini CLI config under home (mounted at /root/.gemini in container)."""
    auth = AUTH_TYPE.get(config.provider.name)
    if auth is None:
        raise ConfigError(
            f"Gemini harness requires provider gemini or vertex, not '{config.provider.name}'"
        )

    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(
        json.dumps({"security": {"auth": {"selectedType": auth}}}, indent=2) + "\n"
    )
    (home / "projects.json").write_text(
        json.dumps({"projects": {CONTAINER_WORKSPACE: "workspace"}}, indent=2) + "\n"
    )
    (home / "trustedFolders.json").write_text(
        json.dumps({CONTAINER_WORKSPACE: "TRUST_FOLDER"}, indent=2) + "\n"
    )

    ws_tmp = home / WORKSPACE_TMP
    ws_tmp.mkdir(parents=True, exist_ok=True)
    (ws_tmp / ".project_root").write_text(f"{CONTAINER_WORKSPACE}\n")
    (ws_tmp / "chats").mkdir(exist_ok=True)
    (ws_tmp / "logs").mkdir(exist_ok=True)


def _parse_trace(home: Path) -> dict[str, Any]:
    """Parse Gemini CLI logs and chat JSONL from a session home directory."""
    ws_tmp = home / WORKSPACE_TMP
    logs = _parse_logs(ws_tmp / "logs.json")
    sessions = [_parse_session(path) for path in sorted((ws_tmp / "chats").glob("session-*.jsonl"))]
    sessions = [s for s in sessions if s is not None]

    return {
        "logs": logs,
        "sessions": sessions,
        "paths": {
            "home": str(home),
            "workspace_tmp": str(ws_tmp),
        },
    }


def _parse_logs(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _parse_session(path: Path) -> dict[str, Any] | None:
    records: list[dict[str, Any]] = []
    metadata: dict[str, Any] | None = None

    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            records.append(record)
            if record.get("type") == "session_metadata" and metadata is None:
                metadata = record
    except OSError:
        return None

    if not records:
        return None

    return {
        "file": path.name,
        "metadata": metadata,
        "record_count": len(records),
        "records": records,
        "summary": _summarize_session(records),
    }


def _summarize_session(records: list[dict[str, Any]]) -> dict[str, Any]:
    users: list[str] = []
    assistant_texts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for record in records:
        rtype = record.get("type")
        if rtype == "user":
            text = _extract_text(record)
            if text:
                users.append(text)
        elif rtype == "gemini":
            text = _extract_text(record)
            if text:
                assistant_texts.append(text)
            for call in _extract_tool_calls(record):
                tool_calls.append(call)
        elif rtype == "message_update":
            for call in _extract_tool_calls(record):
                tool_calls.append(call)

    return {
        "user_messages": users,
        "assistant_messages": assistant_texts,
        "tool_calls": tool_calls,
    }


def _extract_text(record: dict[str, Any]) -> str:
    if isinstance(record.get("message"), str):
        return record["message"]
    content = record.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            parts.append(part["text"])
    return "\n".join(parts)


def _extract_tool_calls(record: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    content = record.get("content")
    if not isinstance(content, list):
        return calls
    for part in content:
        if not isinstance(part, dict):
            continue
        fc = part.get("functionCall")
        if isinstance(fc, dict):
            calls.append({"name": fc.get("name"), "args": fc.get("args")})
    return calls
