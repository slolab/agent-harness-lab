"""OpenCode adapter — env, config seeding, skill wiring, session trace parsing."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ahl.capabilities import Capability
from ahl.config import ConfigError, RunConfig
from ahl.harnesses.base import Volumes, copy_skill_bundle, native_skill_mount, provider_key, warn_unsupported_mcp

CONTAINER_CONFIG_DIR = "/root/.config/opencode"
CONTAINER_DATA_DIR = "/root/.local/share/opencode"

# AHL provider name -> OpenCode provider_id for model IDs (provider/model).
MODEL_PROVIDER = {
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "google",
    "vertex": "google-vertex",
}


class OpenCodeAdapter:
    def build_env(self, config: RunConfig) -> dict[str, str]:
        provider = config.provider.name
        key = provider_key(config)
        if provider == "anthropic":
            return {"ANTHROPIC_API_KEY": key}
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

    def _config_dir(self, run_dir: Path) -> Path:
        return run_dir / "opencode" / "config"

    def _data_dir(self, run_dir: Path) -> Path:
        return run_dir / "opencode" / "data"

    def seed(self, run_dir: Path, config: RunConfig) -> Volumes:
        config_dir = self._config_dir(run_dir)
        data_dir = self._data_dir(run_dir)
        _seed_state(config_dir, data_dir, config)
        return [
            (config_dir, CONTAINER_CONFIG_DIR),
            (data_dir, CONTAINER_DATA_DIR),
        ]

    def wire_capabilities(self, run_dir: Path, config: RunConfig, capabilities: list[Capability]) -> Volumes:
        warn_unsupported_mcp("opencode", capabilities)
        # OpenCode natively discovers skills under ~/.config/opencode/skills/<name>/SKILL.md
        # (also .opencode/skills, .claude/skills — see https://opencode.ai/docs/skills/).
        config_dir = self._config_dir(run_dir)
        container_skills_dir = f"{CONTAINER_CONFIG_DIR}/skills"
        volumes: Volumes = []
        for skill in capabilities:
            if skill.kind != "skill" or skill.path is None:
                continue
            if skill.install == "mount":
                # Bind-mounted straight from the host path (overlaying the
                # already-mounted config_dir): hot reload, no copy step.
                volumes.append((skill.path, native_skill_mount(skill, container_skills_dir)))
            else:  # copy — lands inside config_dir, which is already mounted as a whole
                copy_skill_bundle(skill, config_dir / "skills")
        return volumes

    def parse_trace(self, run_dir: Path) -> dict[str, Any] | None:
        return _parse_trace(self._data_dir(run_dir))

    def start_command(self, config: RunConfig) -> str:
        return f"opencode -m {model_id(config)}"

    def start_hints(self, config: RunConfig) -> list[str]:
        if config.provider.name == "vertex":
            return [
                "Vertex express API key pre-configured (google-vertex options.apiKey)",
                "No ADC / gcloud auth needed",
            ]
        return ["API key injected via env (no /connect needed)"]


def model_id(config: RunConfig) -> str:
    """Build provider/model ID for OpenCode from AHL config."""
    model = config.model.name
    if not model:
        raise ConfigError("OpenCode harness requires model in config.yaml")
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

    opencode_config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "model": model_id(config),
    }
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
