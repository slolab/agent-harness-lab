"""OpenCode adapter — env, config seeding, skill wiring, session trace parsing."""

from __future__ import annotations

import json
import shlex
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

from ahl.capabilities import Capability
from ahl.config import ConfigError, RunConfig
from ahl.harnesses.base import (
    TurnOutcome,
    Volumes,
    json_lines,
    native_skill_mount,
    provider_key,
    warn_unsupported_mcp,
)
from ahl.permissions import PermissionPolicy, PermissionSetup
from ahl.trace import event, iso_from_ms

CONTAINER_CONFIG_DIR = "/root/.config/opencode"
CONTAINER_DATA_DIR = "/root/.local/share/opencode"

# AHL provider name -> OpenCode provider_id for model IDs (provider/model).
MODEL_PROVIDER = {
    "anthropic": "anthropic",
    "openai": "openai",
    "openrouter": "openrouter",
    "gemini": "google",
    "vertex": "google-vertex",
}
WEB_PERMISSIONS = frozenset({"websearch", "webfetch"})
PERMISSION_KEYS = (
    "read", "edit", "glob", "grep", "list", "bash", "task", "external_directory",
    "todowrite", "question", "webfetch", "websearch", "lsp", "doom_loop", "skill",
)


class OpenCodePermissions:
    def prepare(
        self, run_dir: Path, config: RunConfig, policy: PermissionPolicy
    ) -> PermissionSetup:
        applied = policy.deny & WEB_PERMISSIONS
        path = _config_dir(run_dir) / "opencode.json"
        settings = json.loads(path.read_text())
        settings.pop("permission", None)
        if applied:
            settings["permission"] = dict.fromkeys(sorted(applied), "deny")
        path.write_text(json.dumps(settings, indent=2) + "\n")
        unsupported = {op: "no OpenCode native permission" for op in sorted(policy.deny - applied)}
        return PermissionSetup([], applied, unsupported)


class OpenCodeDriver:
    def applied_model_parameters(self, config: RunConfig) -> frozenset[str]:
        return frozenset({"provider"}) if config.provider.name == "openrouter" else frozenset()

    def env(self, config: RunConfig) -> dict[str, str]:
        # `opencode run` rejects every `ask` rule without waiting, and the defaults differ between versions.
        permission = dict.fromkeys(PERMISSION_KEYS, "allow") | {"question": "deny"}
        permission |= dict.fromkeys(sorted(config.permissions.deny & WEB_PERMISSIONS), "deny")
        return {"OPENCODE_PERMISSION": json.dumps(permission)}

    def command(self, config: RunConfig, session_id: str | None) -> list[str]:
        command = ["opencode", "run", "--format", "json", "-m", model_id(config)]
        return [*command, "--session", session_id] if session_id else command

    def session_id(self, stdout: str) -> str | None:
        return next((e["sessionID"] for e in json_lines(stdout) if isinstance(e.get("sessionID"), str)), None)

    def outcome(self, exit_code: int, stdout: str) -> TurnOutcome:
        events = json_lines(stdout)
        errors = [e.get("error") for e in events if e.get("type") == "error"]
        detail = "; ".join(_error_message(error) for error in errors)
        provider = any(isinstance(error, dict) and error.get("name") == "APIError" for error in errors)
        if exit_code:
            message = f"opencode exited with code {exit_code}" + (f": {detail}" if detail else "")
            return TurnOutcome("failed", "provider_error" if provider else "harness_exit", message)
        if errors:
            return TurnOutcome("failed", "provider_error" if provider else "harness_reported_error", detail)
        if not any(e.get("type") == "text" and (e.get("part") or {}).get("text", "").strip() for e in events):
            return TurnOutcome("failed", "no_assistant_output", "opencode produced no assistant message")
        return TurnOutcome("completed")

    def trace(self, run_dir: Path) -> list[dict[str, Any]]:
        return _trace_events(_data_dir(run_dir) / "opencode.db")


class OpenCodeAdapter:
    permission_handler = OpenCodePermissions()
    driver = OpenCodeDriver()

    def build_env(self, config: RunConfig) -> dict[str, str]:
        provider = config.provider.name
        key = provider_key(config)
        if provider == "anthropic":
            return {"ANTHROPIC_API_KEY": key}
        if provider == "openrouter":
            return {"OPENROUTER_API_KEY": key}
        if provider == "openai":
            return {"OPENAI_API_KEY": key}
        if provider == "gemini":
            return {"GEMINI_API_KEY": key}
        if provider == "vertex":
            params = config.provider.parameters
            location = str(params["location"])
            project = str(params["project"])
            return {
                "GOOGLE_API_KEY": key,
                "GOOGLE_CLOUD_PROJECT": project,
                "GOOGLE_CLOUD_LOCATION": location,
                "VERTEX_LOCATION": location,
            }
        raise ValueError(f"unsupported provider: {provider}")

    def seed(self, run_dir: Path, config: RunConfig) -> Volumes:
        config_dir = _config_dir(run_dir)
        data_dir = _data_dir(run_dir)
        _seed_state(config_dir, data_dir, config)
        return [
            (config_dir, CONTAINER_CONFIG_DIR),
            (data_dir, CONTAINER_DATA_DIR),
        ]

    def wire_capabilities(self, run_dir: Path, config: RunConfig, capabilities: list[Capability]) -> Volumes:
        warn_unsupported_mcp("opencode", capabilities)
        # OpenCode natively discovers skills under ~/.config/opencode/skills/<name>/SKILL.md
        # (also .opencode/skills, .claude/skills — see https://opencode.ai/docs/skills/).
        container_skills_dir = f"{CONTAINER_CONFIG_DIR}/skills"
        volumes: Volumes = []
        for skill in capabilities:
            if skill.kind != "skill" or skill.install != "mount" or skill.path is None:
                continue
            # Bind-mounted straight from the host path (overlaying the
            # already-mounted config_dir): hot reload, no copy step.
            volumes.append((skill.path, native_skill_mount(skill, container_skills_dir)))
        return volumes

    def parse_trace(self, run_dir: Path) -> dict[str, Any] | None:
        return _parse_trace(_data_dir(run_dir))

    def start_command(self, config: RunConfig) -> str:
        return shlex.join(["opencode", "-m", model_id(config)])

    def start_hints(self, run_dir: Path, config: RunConfig) -> list[str]:
        if config.provider.name == "vertex":
            return [
                "Vertex express API key pre-configured (google-vertex options.apiKey)",
                "No ADC / gcloud auth needed",
            ]
        return ["API key injected via env (no /connect needed)"]

    def docker_args(self, config: RunConfig) -> list[str]:
        return []


def _config_dir(run_dir: Path) -> Path:
    return run_dir / "opencode" / "config"


def _data_dir(run_dir: Path) -> Path:
    return run_dir / "opencode" / "data"


def model_id(config: RunConfig) -> str:
    """Build provider/model ID for OpenCode from AHL config."""
    model = config.model.name
    if not model:
        raise ConfigError("OpenCode harness requires model in config.yaml")
    if config.provider.name == "openrouter":
        return f"openrouter/{model}"
    if "/" in model:
        return model
    provider_id = MODEL_PROVIDER[config.provider.name]
    return f"{provider_id}/{model}"


def _seed_state(config_dir: Path, data_dir: Path, config: RunConfig) -> None:
    """Write OpenCode global config; API keys come from container env."""
    provider = config.provider.name
    if provider not in MODEL_PROVIDER:
        raise ConfigError(
            f"OpenCode harness does not support provider '{provider}'. "
            f"Use one of: {', '.join(sorted(MODEL_PROVIDER))}"
        )

    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "log").mkdir(exist_ok=True)

    model = model_id(config)
    opencode_config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "model": model,
        "small_model": model,
    }
    if provider == "openrouter" and "provider" in config.model.parameters:
        routing = {"options": {"provider": config.model.parameters["provider"]}}
        opencode_config["provider"] = {"openrouter": {"models": {config.model.name: routing}}}
    if provider == "vertex":
        params = config.provider.parameters
        opencode_config["provider"] = {
            "google-vertex": {
                "options": {
                    "project": str(params["project"]),
                    "location": str(params["location"]),
                    # Express-mode API key (x-goog-api-key). Without this OpenCode
                    # falls back to ADC and fails in the container.
                    "apiKey": "{env:GOOGLE_API_KEY}",
                }
            }
        }

    (config_dir / "opencode.json").write_text(json.dumps(opencode_config, indent=2) + "\n")


def _parse_trace(data_dir: Path) -> dict[str, Any]:
    """Summarize OpenCode session data under ~/.local/share/opencode.

    Recent OpenCode versions store session/message/part data in a SQLite db
    (`opencode.db`), not the file-based `project/<slug>/storage/` layout the
    public docs (https://opencode.ai/docs/troubleshooting/) still describe —
    and auth lives in the `account`/`control_account` tables, not `auth.json`.
    """
    trace: dict[str, Any] = {
        "paths": {"data": str(data_dir)},
        "log_files": [],
        "has_auth": False,
        "sessions": [],
    }

    log_dir = data_dir / "log"
    if log_dir.is_dir():
        trace["log_files"] = sorted(p.name for p in log_dir.iterdir() if p.is_file())

    db_path = data_dir / "opencode.db"
    if db_path.is_file():
        trace["has_auth"] = _has_auth(db_path)
        trace["sessions"] = _parse_sessions(db_path)

    return trace


def _has_auth(db_path: Path) -> bool:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT (SELECT count(*) FROM account) + (SELECT count(*) FROM control_account)"
        ).fetchone()
    return bool(row and row[0])


def _parse_sessions(db_path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        sessions = conn.execute(
            "SELECT id, title, slug, time_created, time_updated, cost, tokens_input, tokens_output "
            "FROM session ORDER BY time_created"
        ).fetchall()
        return [_parse_session(conn, *row) for row in sessions]


def _parse_session(
    conn: sqlite3.Connection,
    session_id: str,
    title: str,
    slug: str,
    time_created: int,
    time_updated: int,
    cost: float,
    tokens_input: int,
    tokens_output: int,
) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT message.id, message.data, part.data FROM message "
        "JOIN part ON part.message_id = message.id "
        "WHERE message.session_id = ? "
        "ORDER BY message.time_created, message.id, part.id",
        (session_id,),
    ).fetchall()

    return {
        "id": session_id,
        "title": title,
        "slug": slug,
        "time_created": time_created,
        "time_updated": time_updated,
        "cost": cost,
        "tokens": {"input": tokens_input, "output": tokens_output},
        "summary": _summarize_session(rows),
    }


def _summarize_session(rows: list[tuple[str, str, str]]) -> dict[str, Any]:
    users: list[str] = []
    assistant_texts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    roles: dict[str, str] = {}
    for message_id, message_raw, part_raw in rows:
        if message_id not in roles:
            roles[message_id] = json.loads(message_raw).get("role", "")
        role = roles[message_id]

        part = json.loads(part_raw)
        ptype = part.get("type")
        if ptype == "text":
            text = part.get("text") or ""
            if not text:
                continue
            if role == "user":
                users.append(text)
            elif role == "assistant":
                assistant_texts.append(text)
        elif ptype == "tool" and role == "assistant":
            state = part.get("state") or {}
            tool_calls.append({"name": part.get("tool"), "args": state.get("input")})

    return {
        "user_messages": users,
        "assistant_messages": assistant_texts,
        "tool_calls": tool_calls,
    }


def _trace_events(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
        parents = dict(conn.execute("SELECT id, parent_id FROM session"))
        messages = conn.execute(
            "SELECT id, session_id, time_created, data FROM message ORDER BY time_created, id"
        ).fetchall()
        parts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for message_id, data in conn.execute("SELECT message_id, data FROM part ORDER BY message_id, id"):
            parts[message_id].append(json.loads(data))

    events: list[dict[str, Any]] = []
    for message_id, session, created, raw in messages:
        info = json.loads(raw)
        header = (session, session if parents.get(session) else "main", iso_from_ms(created))
        message_parts = parts[message_id]
        if info.get("role") == "user":
            for role, synthetic in (("user", False), ("system", True)):
                if text := _joined(message_parts, "text", synthetic):
                    events.append(event("message", *header, role=role, text=text, reasoning=None))
            continue
        text, reasoning = _joined(message_parts, "text"), _joined(message_parts, "reasoning")
        if text or reasoning:
            events.append(event("message", *header, role="assistant", text=text, reasoning=reasoning or None))
        events += [_tool_call(header, part) for part in message_parts if part.get("type") == "tool"]
        if usage := _usage(info):
            events.append(event("usage", *header, **usage))
        if info.get("error"):
            events.append(event("error", *header, message=_error_message(info["error"])))
    return events


def _joined(parts: list[dict[str, Any]], kind: str, synthetic: bool = False) -> str:
    return "\n".join(
        part["text"]
        for part in parts
        if part.get("type") == kind and bool(part.get("synthetic")) == synthetic and isinstance(part.get("text"), str)
        and part["text"]
    )


def _tool_call(header: tuple[str, str, str | None], part: dict[str, Any]) -> dict[str, Any]:
    state = part.get("state") or {}
    status = state.get("status")
    output = {"completed": state.get("output"), "error": state.get("error")}.get(status)
    tool_input = state.get("input")
    return event(
        "tool_call",
        *header,
        id=part.get("callID"),
        tool=str(part.get("tool")),
        input=tool_input if isinstance(tool_input, (dict, str)) else {},
        output=output if isinstance(output, str) else None,
        is_error={"completed": False, "error": True}.get(status),
    )


def _usage(info: dict[str, Any]) -> dict[str, Any] | None:
    tokens = info.get("tokens")
    if info.get("role") != "assistant" or not isinstance(tokens, dict):
        return None
    cache = tokens.get("cache") or {}
    output, reasoning = _count(tokens.get("output")), _count(tokens.get("reasoning"))
    counts = {
        "input_tokens": _count(tokens.get("input")),
        "output_tokens": None if output is None or reasoning is None else output + reasoning,
        "cache_read_tokens": _count(cache.get("read")),
        "cache_write_tokens": _count(cache.get("write")),
    }
    if not any(counts.values()):
        return None
    cost = info.get("cost")
    return {
        "model": info.get("modelID"),
        "response_id": None,
        **counts,
        "reasoning_tokens": reasoning,
        "cost_usd": cost if isinstance(cost, (int, float)) else None,
    }


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _error_message(error: Any) -> str:
    if not isinstance(error, dict):
        return str(error)
    data = error.get("data")
    message = data.get("message") if isinstance(data, dict) else None
    return str(message or error.get("name") or "unknown error")
