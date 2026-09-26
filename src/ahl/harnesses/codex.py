from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ahl.capabilities import Capability
from ahl.config import RunConfig
from ahl.harnesses.base import (
    TurnReport,
    Volumes,
    json_lines,
    native_skill_mount,
    provider_key,
    token_count,
    warn_unsupported_mcp,
)
from ahl.permissions import PermissionPolicy, PermissionSetup
from ahl.trace import event

CONTAINER_HOME = "/root/.codex"
CONTAINER_SKILLS = "/root/.agents/skills"
WEB_PERMISSIONS = frozenset({"websearch", "webfetch"})
PROVIDER_ERROR = re.compile(r"\bstatus:? (429|5\d\d)\b")
TOOL_INPUTS = {
    "command_execution": "command",
    "file_change": "changes",
    "web_search": "query",
    "collab_tool_call": "receiver_thread_ids",
}
CALLS = {"function_call", "custom_tool_call"}
OUTPUTS = {"function_call_output", "custom_tool_call_output"}
FAILED = {"completed": False, "failed": True, "declined": True}
# Command auth, unlike env_key, makes Codex load OpenRouter's model catalogue instead of fallback metadata.
# plugins = false stops Codex cloning its plugin marketplace, about 100 MB, into every run's home.
# A shell snapshot holds the environment, key included, and a killed turn leaves it in the run directory.
CONFIG = """\
model_provider = "openrouter"
model = {model}
approval_policy = "never"
sandbox_mode = "danger-full-access"

[features]
plugins = false
shell_snapshot = false

[model_providers.openrouter]
name = "OpenRouter"
base_url = "https://openrouter.ai/api/v1"
wire_api = "responses"

[model_providers.openrouter.auth]
command = "sh"
args = ["-c", 'printf %s "$OPENROUTER_API_KEY"']
"""


class CodexPermissions:
    def prepare(self, run_dir: Path, config: RunConfig, policy: PermissionPolicy) -> PermissionSetup:
        applied = policy.deny & WEB_PERMISSIONS
        if applied:
            path = _home(run_dir) / "config.toml"
            # Codex has no fetch tool, so this one switch covers webfetch. A top-level key precedes the tables.
            path.write_text('web_search = "disabled"\n' + path.read_text())
        unsupported = {op: "no Codex native permission" for op in sorted(policy.deny - applied)}
        return PermissionSetup([], applied, unsupported)


class CodexDriver:
    def applied_model_parameters(self, config: RunConfig) -> frozenset[str]:
        return frozenset()

    def env(self, config: RunConfig) -> dict[str, str]:
        return {}

    def command(self, config: RunConfig, session_id: str | None) -> list[str]:
        resume = ["resume", session_id] if session_id else []
        return ["codex", "exec", *resume, "--json", "--skip-git-repo-check", "-m", config.model.name, "-"]

    def report(self, stdout: str) -> TurnReport:
        events = json_lines(stdout)
        completed = max((i for i, e in enumerate(events) if e.get("type") == "turn.completed"), default=-1)
        errors = [_error(e) for e in events[completed + 1:] if e.get("type") in ("error", "turn.failed")]
        items = [_item(e) for e in events]
        return TurnReport(
            session_id=next((e["thread_id"] for e in events if isinstance(e.get("thread_id"), str)), None),
            error=errors[-1] if errors else None,
            provider_error=any(PROVIDER_ERROR.search(error) for error in errors),
            replied=any(i.get("type") == "agent_message" and str(i.get("text") or "").strip() for i in items),
        )

    def view(self, record: dict[str, Any]) -> list[tuple[str, str, str]]:
        if record.get("type") in ("error", "turn.failed"):
            return [("main", "error", _error(record))]
        item = _item(record) if record.get("type") == "item.completed" else {}
        kind = item.get("type")
        if kind == "agent_message":
            return [("main", "text", str(item.get("text")))]
        if kind == "error":
            return [("main", "error", str(item.get("message")))]
        if kind == "collab_tool_call" and item.get("tool") == "spawn_agent":
            threads = " ".join(item.get("receiver_thread_ids") or [])
            return [("main", "subagent", f"{threads} {item.get('prompt') or ''}")]
        if kind in TOOL_INPUTS:
            detail = item.get(TOOL_INPUTS[kind])
            detail = detail if isinstance(detail, str) else json.dumps(detail)
            return [("main", "tool", f"{item.get('tool') or kind} {detail}")]
        return []

    def trace(self, run_dir: Path) -> list[dict[str, Any]]:
        events = []
        for path in sorted((_home(run_dir) / "sessions").glob("**/rollout-*.jsonl")):
            events += _rollout_events(json_lines(path.read_text(errors="replace")))
        return sorted(events, key=lambda trace_event: trace_event["ts"] or "")


class CodexAdapter:
    permission_handler = CodexPermissions()
    driver = CodexDriver()

    def build_env(self, config: RunConfig) -> dict[str, str]:
        return {"OPENROUTER_API_KEY": provider_key(config)}

    def seed(self, run_dir: Path, config: RunConfig) -> Volumes:
        home = _home(run_dir)
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.toml").write_text(CONFIG.format(model=json.dumps(config.model.name)))
        return [(home, CONTAINER_HOME)]

    def wire_capabilities(self, run_dir: Path, config: RunConfig, capabilities: list[Capability]) -> Volumes:
        warn_unsupported_mcp("codex", capabilities)
        return [
            (skill.path, native_skill_mount(skill, CONTAINER_SKILLS))
            for skill in capabilities
            if skill.kind == "skill" and skill.install == "mount" and skill.path is not None
        ]

    def parse_trace(self, run_dir: Path) -> dict[str, Any]:
        return {"paths": {"home": str(_home(run_dir))}, "events": self.driver.trace(run_dir)}

    def start_command(self, config: RunConfig) -> str:
        return "codex"

    def start_hints(self, run_dir: Path, config: RunConfig) -> list[str]:
        return ["OpenRouter key read from OPENROUTER_API_KEY; provider, model and approvals in ~/.codex/config.toml"]

    def docker_args(self, config: RunConfig) -> list[str]:
        return []


def _home(run_dir: Path) -> Path:
    return run_dir / "codex"


def _item(record: dict[str, Any]) -> dict[str, Any]:
    item = record.get("item")
    return item if isinstance(item, dict) else {}


def _error(record: dict[str, Any]) -> str:
    detail = record.get("error") if record.get("type") == "turn.failed" else record
    return str((detail if isinstance(detail, dict) else {}).get("message"))


def _joined(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    return "\n".join(part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str))


def _rollout_events(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = [(r.get("type"), r["payload"], r.get("timestamp")) for r in records if isinstance(r.get("payload"), dict)]
    completed = [_item(payload) for kind, payload, _ in entries if kind == "event_msg"]
    items = {item["id"]: item for item in completed if isinstance(item.get("id"), str)}
    prompts = {_joined(item.get("content")) for item in completed if item.get("type") == "UserMessage"}
    outputs = {payload.get("call_id"): payload.get("output") for _, payload, _ in entries
               if payload.get("type") in OUTPUTS}
    events: list[dict[str, Any]] = []
    reasoning: list[tuple[str | None, str]] = []
    session, agent, model = None, "main", None

    def take_reasoning() -> str | None:
        joined = "\n".join(text for _, text in reasoning) or None
        reasoning.clear()
        return joined

    def flush() -> None:
        if reasoning:
            ts = reasoning[0][0]
            events.append(event("message", session, agent, ts, role="assistant", text="", reasoning=take_reasoning()))

    def emit(kind: str, ts: str | None, **fields: Any) -> None:
        flush()
        events.append(event(kind, session, agent, ts, **fields))

    for kind, payload, ts in entries:
        part, role = payload.get("type"), payload.get("role")
        if kind == "session_meta":
            session = payload.get("id")
            agent = "main" if isinstance(payload.get("source"), str) else session
        elif kind == "turn_context":
            model = payload.get("model")
        elif kind == "response_item" and part == "reasoning":
            if text := "\n".join(filter(None, (_joined(payload.get("summary")), _joined(payload.get("content"))))):
                reasoning.append((ts, text))
        elif kind == "response_item" and part == "message" and role == "assistant":
            text = _joined(payload.get("content"))
            events.append(event("message", session, agent, ts, role="assistant", text=text, reasoning=take_reasoning()))
        elif kind == "response_item" and part == "message":
            text = _joined(payload.get("content"))
            prompt = role == "user" and text in prompts
            emit("message", ts, role="user" if prompt else "system", text=text, reasoning=None)
        elif kind == "response_item" and part in CALLS:
            emit("tool_call", ts, **_tool_call(payload, items, outputs))
        elif kind == "response_item" and part == "web_search_call":
            action = payload.get("action")
            emit("tool_call", ts, id=payload.get("id"), tool="web_search",
                 input=action if isinstance(action, dict) else {}, output=None,
                 is_error=FAILED.get(payload.get("status")))
        elif kind == "token_usage_record":
            emit("usage", ts, model=model, **_usage(payload))
        elif kind == "event_msg" and part == "task_complete" and isinstance(payload.get("error"), dict):
            emit("error", ts, message=str(payload["error"].get("message")))
    flush()
    return events


def _tool_call(payload: dict[str, Any], items: dict[str, dict[str, Any]], outputs: dict[Any, Any]) -> dict[str, Any]:
    call_id = payload.get("call_id")
    item = items.get(call_id) or {}
    # Codex truncates the output it returns to the model; the completed command item keeps all of it.
    output = item.get("aggregated_output")
    if not isinstance(output, str):
        output = outputs.get(call_id)
    return {
        "id": call_id,
        "tool": str(payload.get("name")),
        "input": _input(payload.get("arguments", payload.get("input"))),
        "output": output if isinstance(output, str) else None,
        "is_error": FAILED.get(item.get("status")),
    }


def _input(raw: Any) -> dict[str, Any] | str:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        return parsed if isinstance(parsed, dict) else raw
    return raw if isinstance(raw, dict) else {}


def _usage(payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    total, cached = token_count(usage.get("input_tokens")), token_count(usage.get("cached_input_tokens"))
    written = token_count(usage.get("cache_write_input_tokens"))
    response_id = payload.get("response_id")
    return {
        "response_id": response_id if isinstance(response_id, str) else None,
        "input_tokens": None if total is None or cached is None else total - cached - (written or 0),
        "output_tokens": token_count(usage.get("output_tokens")),
        "cache_read_tokens": cached,
        "cache_write_tokens": written,
        "reasoning_tokens": token_count(usage.get("reasoning_output_tokens")),
        "cost_usd": None,
    }
