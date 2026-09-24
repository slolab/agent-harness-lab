"""Official DeepSeek browser harness: run-owned configuration and native v3 logs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ahl.capabilities import Capability
from ahl.config import ConfigError, RunConfig
from ahl.permissions import PermissionPolicy, PermissionSetup
from ahl.harnesses.base import (
    Volumes,
    native_skill_mount,
    provider_key,
    warn_unsupported_mcp,
)

HOME = "/root/.dsh"
USAGE_FIELDS = {
    "input_tokens": "inputTokens",
    "output_tokens": "outputTokens",
    "cache_creation_input_tokens": "cacheWriteTokens",
    "cache_read_input_tokens": "cacheReadTokens",
    "total_tokens": "totalTokens",
}


class DeepSeekPermissions:
    def prepare(
        self, run_dir: Path, config: RunConfig, policy: PermissionPolicy
    ) -> PermissionSetup:
        home = run_dir / "deepseek/home"
        patch_path, settings_path = home / "cordis.patch.yml", home / "settings.yaml"
        rows = _read_yaml(patch_path, list)
        settings = _read_yaml(settings_path, dict)
        plugin_id = "ahl-native-permissions"
        mapping = {"websearch": "web_search", "webfetch": "web_fetch"}
        applied = policy.deny.intersection(mapping)
        unsupported = {
            op: "no DeepSeek native tool mapping"
            for op in sorted(policy.deny - applied)
        }
        native = {"deny": sorted(mapping[op] for op in applied)}
        # Remove only our insertion, preserving siblings and unrelated patch rows.
        reconciled = []
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("insert"), list):
                remaining = [
                    p
                    for p in row["insert"]
                    if not (isinstance(p, dict) and p.get("id") == plugin_id)
                ]
                if len(remaining) != len(row["insert"]):
                    row = {**row, "insert": remaining}
                    if not remaining and set(row) == {"insert"}:
                        continue
            reconciled.append(row)
        reconciled.append(
            {
                "insert": [
                    {
                        "id": plugin_id,
                        "name": "/opt/ahl/permissions/deepseek.mjs",
                        "config": native,
                    }
                ]
            }
        )
        # Native settings override patch config. Reapply owned values on resume.
        if plugin_id in settings:
            settings[plugin_id] = _merge_owned(settings[plugin_id], native)
        home.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(yaml.safe_dump(reconciled, sort_keys=False))
        if settings_path.exists():
            settings_path.write_text(yaml.safe_dump(settings, sort_keys=False))
        return PermissionSetup([], applied, unsupported)


class DeepSeekAdapter:
    permission_handler = DeepSeekPermissions()

    def build_env(self, config: RunConfig) -> dict[str, str]:
        return {"DSH_HOME": HOME, "OPENROUTER_API_KEY": provider_key(config)}

    def seed(self, run_dir: Path, config: RunConfig) -> Volumes:
        home = run_dir / "deepseek/home"
        agents = run_dir / "deepseek/agents"
        route = {
            "apiKeyEnv": "OPENROUTER_API_KEY",
            "api": "openai-completions",
            "baseURL": "https://openrouter.ai/api/v1",
            "models": [{"id": config.model.name}],
        }
        owned = {
            "llm-pi-ai": {"providers": {"openrouter": route}},
            "agent-default-model": {
                "provider": "openrouter",
                "model": config.model.name,
            },
            "session-persistence-jsonl": {
                "root": f"{HOME}/sessions",
                "compression": "none",
            },
        }
        patch_path = home / "cordis.patch.yml"
        settings_path = home / "settings.yaml"
        rows = _read_yaml(patch_path, list)
        settings = _read_yaml(settings_path, dict)
        # Read/validate both documents before changing either. Patch config replaces
        # a native row's config; preserve other rows and unrelated config fields.
        for name, values in owned.items():
            matches = [
                row for row in rows if isinstance(row, dict) and row.get("id") == name
            ]
            if len(matches) > 1:
                raise ConfigError(f"{patch_path}: duplicate AHL-owned row {name}")
            if matches:
                matches[0]["config"] = _merge_owned(matches[0].get("config"), values)
            else:
                rows.append({"id": name, "config": values})
            if name in settings:
                settings[name] = _merge_owned(settings[name], values)
        home.mkdir(parents=True, exist_ok=True)
        agents.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(yaml.safe_dump(rows, sort_keys=False))
        if settings_path.exists():
            settings_path.write_text(yaml.safe_dump(settings, sort_keys=False))
        return [(home, HOME), (agents, "/root/.agents")]

    def wire_capabilities(
        self, run_dir: Path, config: RunConfig, capabilities: list[Capability]
    ) -> Volumes:
        warn_unsupported_mcp("deepseek", capabilities)
        return [
            (cap.path, native_skill_mount(cap, f"{HOME}/skills"))
            for cap in capabilities
            if cap.kind == "skill" and cap.install == "mount" and cap.path is not None
        ]

    def start_command(self, config: RunConfig) -> str:
        return f"ahl-deepseek --port {config.harness.parameters.get('port', 3080)}"

    def start_hints(self, run_dir: Path, config: RunConfig) -> list[str]:
        return [
            "Open DSH's printed authenticated URL in your browser; host access is loopback-only",
            (
                "Native DeepSeek search is disabled by AHL permissions"
                if "websearch" in config.permissions.deny
                else "Native DeepSeek search requires DEEPSEEK_API_KEY, which AHL does not supply; search will fail without it"
            ),
            "On resume, select your saved session in the browser; state and skills are retained",
        ]

    def docker_args(self, config: RunConfig) -> list[str]:
        port = config.harness.parameters.get("port", 3080)
        relay = 3082 if port == 3081 else 3081
        return ["-p", f"127.0.0.1:{port}:{relay}"]

    def parse_trace(self, run_dir: Path) -> dict[str, Any]:
        return _parse_trace(run_dir / "deepseek/home")


def _read_yaml(path: Path, expected: type) -> Any:
    try:
        value = yaml.safe_load(path.read_text()) if path.exists() else None
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    if value is None:
        return expected()
    if not isinstance(value, expected):
        raise ConfigError(f"{path}: expected {expected.__name__}")
    return value


def _merge_owned(previous: Any, owned: dict) -> dict:
    result = dict(previous) if isinstance(previous, dict) else {}
    for key, value in owned.items():
        result[key] = (
            _merge_owned(result.get(key), value) if isinstance(value, dict) else value
        )
    return result


def _parse_trace(home: Path) -> dict[str, Any]:
    warnings: list[str] = []
    sessions = []
    all_responses: dict[tuple[str, int], tuple[dict, bool]] = {}
    seen: set[tuple[str, int]] = set()
    root = home / "sessions"
    for directory in sorted(p for p in root.glob("*/*") if p.is_dir()):
        path = directory / "session.v3.jsonl"
        if not path.exists():
            if any(directory.glob("session*.jsonl*")):
                warnings.append(
                    f"{directory}: unsupported session generation or compression; expected session.v3.jsonl"
                )
            continue
        parsed = _read_session(path, warnings)
        if parsed is None:
            continue
        header, events = parsed
        session, responses = _normalize_session(header, events, seen, warnings, path)
        sessions.append(session)
        all_responses.update(responses)
    return {
        "paths": {"home": str(home), "sessions": str(root)},
        "sessions": sessions,
        "totals": _usage_summary(list(all_responses.values())),
        "warnings": warnings,
    }


def _read_session(path: Path, warnings: list[str]) -> tuple[dict, list[dict]] | None:
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeError) as exc:
        warnings.append(f"{path}: unreadable log ({exc})")
        return None
    header = None
    events = {}
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            row = None
        if not isinstance(row, dict):
            warnings.append(f"{path}:{line_number}: malformed record skipped")
            continue
        if row.get("type") == "session" and header is None:
            if (
                row.get("version") != 3
                or not isinstance(row.get("id"), str)
                or not row["id"]
            ):
                warnings.append(
                    f"{path}:{line_number}: unsupported or invalid session header"
                )
                return None
            header = row
        elif (
            type(row.get("seq")) is int
            and row["seq"] >= 0
            and isinstance(row.get("data"), dict)
            and isinstance(row.get("type"), str)
        ):
            seq = row["seq"]
            if seq in events and events[seq] != row:
                warnings.append(
                    f"{path}:{line_number}: conflicting event seq {seq}; keeping first"
                )
            events.setdefault(seq, row)
        else:
            warnings.append(f"{path}:{line_number}: malformed event skipped")
    if header is None:
        warnings.append(f"{path}: missing v3 session header")
        return None
    return header, [events[seq] for seq in sorted(events)]


def _normalize_session(
    header: dict, events: list[dict], seen: set, warnings: list[str], path: Path
) -> tuple[dict, dict]:
    session = {
        key: header.get(key)
        for key in (
            "id",
            "createdAt",
            "cwd",
            "isSeeded",
            "parentSession",
            "origin",
            "agentPreset",
        )
    }
    session.update(
        {
            "path": str(path),
            "messages": [],
            "context_messages": [],
            "tool_calls": [],
            "models": [],
            "children": [],
        }
    )
    boundary = -1
    if header.get("isSeeded") is True:
        boundaries = [
            e["seq"]
            for e in events
            if e["type"] == "session/end-seed" and e["data"].get("inherited") is True
        ]
        if boundaries:
            boundary = max(boundaries)
        else:
            warnings.append(
                f"{path}: seeded session has no inherited boundary; own history cannot be determined"
            )
            events = []
    responses = {}
    tools = {}
    times = []
    for event in events:
        identity = (header["id"], event["seq"])
        if event["seq"] <= boundary or identity in seen:
            continue
        seen.add(identity)
        data, kind = event["data"], event["type"]
        if type(event.get("time")) in (int, float):
            times.append(event["time"])
        if kind in {"assistant/message", "assistant/attempt"} or (
            kind == "compaction/summary" and isinstance(data.get("usage"), dict)
        ):
            usage = data.get("usage")
            unsuccessful = (
                kind == "assistant/attempt" or data.get("interrupted") is True
            )
            if kind == "assistant/attempt":
                usage = None
                for record in _list(data.get("stream")):
                    chunk = _dict(record.get("chunk"))
                    if record.get("type") == "chunk" and chunk.get("type") == "usage":
                        usage = chunk.get("usage")
            responses[identity] = (_dict(usage), unsuccessful)
        if kind in {"assistant/message", "user/message"}:
            msg = _dict(data.get("message")) if kind == "assistant/message" else data
            normalized = {
                "seq": event["seq"],
                "role": "assistant" if kind == "assistant/message" else "user",
                "text": _text(msg.get("content"), "text"),
                "reasoning": _text(msg.get("content"), "reasoning"),
                "source": msg.get("source"),
            }
            target = (
                "messages"
                if kind == "assistant/message"
                or _dict(msg.get("source")).get("kind") == "user"
                else "context_messages"
            )
            session[target].append(normalized)
            source = _dict(msg.get("source"))
            if kind == "assistant/message" and source.get("model"):
                model = {key: source.get(key) for key in ("provider", "model")}
                if model not in session["models"]:
                    session["models"].append(model)
        if kind in {"model/selection", "compaction/summary", "request/header"}:
            selection = (
                _dict(_dict(data.get("header")).get("config"))
                if kind == "request/header"
                else data
            )
            if selection.get("model"):
                model = {key: selection.get(key) for key in ("provider", "model")}
                if model not in session["models"]:
                    session["models"].append(model)
        if kind == "agent-preset/selected":
            session["agentPreset"] = data.get("agentPreset")
        if kind == "subagent/catalog":
            session["children"].append(data)
        if kind == "tool/call" and isinstance(data.get("callId"), str):
            call = tools.setdefault(
                data["callId"], {"id": data["callId"], "result": None}
            )
            args = data.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    pass
            call.update({"name": data.get("name"), "args": args})
        if kind == "tool/result":
            for block in _list(_dict(data.get("message")).get("content")):
                call_id = block.get("toolCallId")
                if block.get("type") == "tool-result" and isinstance(call_id, str):
                    call = tools.setdefault(
                        call_id, {"id": call_id, "name": None, "args": None}
                    )
                    call.update(
                        {
                            "result": block.get("content"),
                            "is_error": block.get("isError", False),
                        }
                    )
    session["tool_calls"] = list(tools.values())
    session["timing"] = _timing(times)
    session.update(_usage_summary(list(responses.values())))
    return session, responses


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[dict]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _text(content: Any, kind: str) -> str:
    return "\n".join(
        block["text"]
        for block in _list(content)
        if block.get("type") == kind and isinstance(block.get("text"), str)
    )


def _usage_summary(responses: list[tuple[dict, bool]]) -> dict:
    usage = {}
    for output, native in USAGE_FIELDS.items():
        values = [record.get(native) for record, _ in responses]
        usage[output] = (
            sum(values) if all(type(v) is int and v >= 0 for v in values) else None
        )
    usage["cache_creation"] = {
        "ephemeral_5m_input_tokens": None if responses else 0,
        "ephemeral_1h_input_tokens": None if responses else 0,
    }
    incomplete = sum(
        failed
        or any(
            type(record.get(key)) is not int or record[key] < 0
            for key in list(USAGE_FIELDS.values())[:4]
        )
        for record, failed in responses
    )
    return {
        "usage": usage,
        "api_responses": len(responses),
        "incomplete_api_responses": incomplete,
    }


def _timing(times: list[int | float]) -> dict:
    if not times:
        return {"started_at": None, "ended_at": None, "duration_seconds": None}
    start, end = min(times), max(times)
    try:
        return {
            "started_at": datetime.fromtimestamp(
                start / 1000, timezone.utc
            ).isoformat(),
            "ended_at": datetime.fromtimestamp(end / 1000, timezone.utc).isoformat(),
            "duration_seconds": (end - start) / 1000,
        }
    except (ValueError, OverflowError, OSError):
        return {"started_at": None, "ended_at": None, "duration_seconds": None}
