from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from ahl.harnesses import get_adapter


def test_get_adapter_unknown_name_raises():
    with pytest.raises(ValueError, match="no adapter registered"):
        get_adapter("not-a-harness")


class TestClaudeAdapter:
    def test_build_env_contains_no_anthropic_key(self, make_config):
        config = make_config(harness="claude", provider="anthropic", api_key="")
        assert get_adapter("claude").build_env(config) == {"DISABLE_AUTOUPDATER": "1"}

    def test_wire_capabilities_mount_install_binds_host_path_directly(
        self, make_config, tmp_path: Path, skill_dir: Path, capsys
    ):
        config = make_config(
            harness="claude",
            provider="anthropic",
            capabilities=[
                {"kind": "skill", "name": "my-skill", "install": "mount", "path": str(skill_dir.relative_to(tmp_path))},
                {"kind": "skill", "name": "copied", "install": "copy", "path": str(skill_dir)},
                {"kind": "mcp", "name": "my-mcp", "install": "pip", "command": "my-mcp-server"},
            ],
        )
        run_dir = tmp_path / "run"
        adapter = get_adapter("claude")
        volumes = adapter.wire_capabilities(run_dir, config, config.capabilities)

        # Bind-mounted straight from the host skill_dir — no copy, so editing
        # skill_dir later is reflected without rerunning ahl up (hot reload).
        assert volumes == [(skill_dir, "/workspace/.claude/skills/my-skill")]
        assert "not wired for harness 'claude'" in capsys.readouterr().err

    def test_parse_trace_reads_messages_and_tool_calls(self, make_config, tmp_path: Path):
        config = make_config(harness="claude", provider="anthropic")
        run_dir = tmp_path / "run"
        adapter = get_adapter("claude")
        assert adapter.seed(run_dir, config) == [(run_dir / "claude", "/root/.claude")]
        assert (run_dir / "claude/projects").is_dir()
        session_dir = run_dir / "claude" / "projects" / "-workspace"
        session_dir.mkdir()
        records = [
            {
                "type": "user",
                "sessionId": "session-1",
                "cwd": "/workspace",
                "version": "2.1.0",
                "timestamp": "2026-07-20T10:00:00Z",
                "message": {"role": "user", "content": "inspect data.csv"},
            },
            {
                "type": "assistant",
                "sessionId": "session-1",
                "timestamp": "2026-07-20T10:00:20Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-8",
                    "content": [
                        {"type": "text", "text": "I will inspect it."},
                        {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "data.csv"}},
                    ],
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 30,
                        "cache_creation_input_tokens": 200,
                        "cache_read_input_tokens": 50,
                    },
                },
            },
            {
                "type": "user",
                "sessionId": "session-1",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tool-1", "content": "a,b\\n1,2"}
                    ],
                },
            },
        ]
        (session_dir / "session-1.jsonl").write_text(
            "\n".join(json.dumps(record) for record in records) + "\n"
        )

        trace = adapter.parse_trace(run_dir)
        assert len(trace["sessions"]) == 1
        session = trace["sessions"][0]
        assert session["id"] == "session-1"
        assert session["summary"]["user_messages"][0] == "inspect data.csv"
        assert session["summary"]["assistant_messages"] == ["I will inspect it."]
        assert session["summary"]["tool_calls"] == [
            {
                "id": "tool-1",
                "name": "Read",
                "args": {"file_path": "data.csv"},
                "result": "a,b\\n1,2",
                "is_error": False,
            }
        ]
        # Cost/tokens/time capture (D2): observed response usage,
        # wall-clock from record timestamps, model as actually served.
        assert session["model"] == "claude-opus-4-8"
        assert session["usage"] == {
            "input_tokens": 100,
            "output_tokens": 30,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 50,
            "cache_creation": {
                "ephemeral_5m_input_tokens": None,
                "ephemeral_1h_input_tokens": None,
            },
        }
        assert session["started_at"] == "2026-07-20T10:00:00Z"
        assert session["ended_at"] == "2026-07-20T10:00:20Z"
        assert session["wall_clock_seconds"] == 20.0
        totals = trace["totals"]
        assert totals["sessions"] == 1
        assert totals["models"] == ["claude-opus-4-8"]
        assert totals["usage"]["output_tokens"] == 30
        assert totals["wall_clock_seconds"] == 20.0


class TestClaudeScienceAdapter:
    def test_seed_mounts_isolated_home_and_writes_config(self, make_config, tmp_path: Path):
        config = make_config(harness="claude-science", provider="anthropic", api_key="")
        run_dir = tmp_path / "run"
        adapter = get_adapter("claude-science")
        volumes = adapter.seed(run_dir, config)

        home = run_dir / "claude-science" / "home"
        assert volumes == [(home, "/root/.claude-science")]
        assert "disable_telemetry = true" in (home / "config.toml").read_text()
        assert "ANTHROPIC_API_KEY" not in adapter.build_env(config)

    def test_wire_capabilities_packages_uploadable_skill_zip(
        self, make_config, tmp_path: Path, skill_dir: Path
    ):
        config = make_config(
            harness="claude-science",
            provider="anthropic",
            api_key="",
            capabilities=[{"kind": "skill", "name": "my-skill", "install": "mount", "path": str(skill_dir)}],
        )
        run_dir = tmp_path / "run"
        adapter = get_adapter("claude-science")
        assert adapter.wire_capabilities(run_dir, config, config.capabilities) == []

        archive_path = run_dir / "claude-science" / "skill-uploads" / "my-skill.zip"
        with zipfile.ZipFile(archive_path) as archive:
            assert "my-skill/SKILL.md" in archive.namelist()

    def test_start_command_and_docker_args_publish_both_loopback_ports(
        self, make_config
    ):
        config = make_config(
            harness="claude-science",
            provider="anthropic",
            api_key="",
            extra={"harness": {"name": "claude-science", "parameters": {"port": 8765}}},
        )
        adapter = get_adapter("claude-science")
        assert "--port 8765" in adapter.start_command(config)
        assert "--dangerously-no-sandbox" not in adapter.start_command(config)
        args = adapter.docker_args(config)
        assert "127.0.0.1:8765:8765" in args
        assert "127.0.0.1:8766:8766" in args
        assert "seccomp=unconfined" in args
        assert "apparmor=unconfined" in args

    def test_dangerously_no_sandbox_is_explicit_opt_in(self, make_config):
        config = make_config(
            harness="claude-science",
            provider="anthropic",
            api_key="",
            extra={
                "harness": {
                    "name": "claude-science",
                    "parameters": {"dangerously_no_sandbox": True},
                }
            },
        )
        assert "--dangerously-no-sandbox" in get_adapter("claude-science").start_command(config)


class TestGeminiAdapter:
    def test_seed_writes_settings_and_returns_home_volume(self, make_config, tmp_path: Path):
        config = make_config(harness="gemini", provider="gemini")
        run_dir = tmp_path / "run"
        adapter = get_adapter("gemini")
        volumes = adapter.seed(run_dir, config)

        home = run_dir / "gemini"
        assert volumes == [(home, "/root/.gemini")]
        settings = json.loads((home / "settings.json").read_text())
        assert settings["security"]["auth"]["selectedType"] == "gemini-api-key"
        trace = adapter.parse_trace(run_dir)
        assert trace["sessions"] == [] and trace["logs"] == []

    def test_wire_capabilities_mounts_native_global_skill(
        self, make_config, tmp_path: Path, skill_dir: Path
    ):
        config = make_config(
            harness="gemini",
            provider="gemini",
            capabilities=[{"kind": "skill", "name": "my-skill", "install": "mount", "path": str(skill_dir)}],
        )
        run_dir = tmp_path / "run"
        adapter = get_adapter("gemini")
        adapter.seed(run_dir, config)
        volumes = adapter.wire_capabilities(run_dir, config, config.capabilities)

        assert volumes == [(skill_dir, "/root/.gemini/skills/my-skill")]


class TestOpenCodeAdapter:
    def test_seed_writes_config_and_returns_two_volumes(self, make_config, tmp_path: Path):
        config = make_config(harness="opencode", provider="anthropic")
        run_dir = tmp_path / "run"
        adapter = get_adapter("opencode")
        volumes = adapter.seed(run_dir, config)

        assert volumes == [
            (run_dir / "opencode" / "config", "/root/.config/opencode"),
            (run_dir / "opencode" / "data", "/root/.local/share/opencode"),
        ]
        oc_config = json.loads((run_dir / "opencode" / "config" / "opencode.json").read_text())
        assert oc_config["model"] == "anthropic/claude-sonnet-4-6"

    def test_wire_capabilities_mount_install_binds_host_path_directly(
        self, make_config, tmp_path: Path, skill_dir: Path
    ):
        # OpenCode natively discovers ~/.config/opencode/skills/<name>/SKILL.md
        # (https://opencode.ai/docs/skills/). install: mount bind-mounts the
        # host skill_dir straight there — hot reload, no copy step.
        config = make_config(
            harness="opencode",
            provider="anthropic",
            capabilities=[
                {"kind": "skill", "name": "my-skill", "install": "mount", "path": str(skill_dir)},
                {"kind": "skill", "name": "copy", "install": "copy", "path": str(skill_dir)},
            ],
        )
        run_dir = tmp_path / "run"
        adapter = get_adapter("opencode")
        adapter.seed(run_dir, config)
        volumes = adapter.wire_capabilities(run_dir, config, config.capabilities)

        assert volumes == [(skill_dir, "/root/.config/opencode/skills/my-skill")]
        # seed()'s own opencode.json must be left untouched.
        config_dir = run_dir / "opencode" / "config"
        oc_config = json.loads((config_dir / "opencode.json").read_text())
        assert "instructions" not in oc_config

    def test_parse_trace_reads_sessions_from_sqlite_db(self, make_config, tmp_path: Path):
        # Recent OpenCode versions store session/message/part data in
        # opencode.db (sqlite), not the file-based layout the public docs
        # describe — this is the actual on-disk schema, reproduced minimally.
        config = make_config(harness="opencode", provider="anthropic")
        run_dir = tmp_path / "run"
        adapter = get_adapter("opencode")
        adapter.seed(run_dir, config)
        (run_dir / "opencode/data/log/session.log").write_text("native log")
        _write_fake_opencode_db(run_dir / "opencode" / "data" / "opencode.db")

        trace = adapter.parse_trace(run_dir)

        assert trace["log_files"] == ["session.log"]
        assert len(trace["sessions"]) == 1
        session = trace["sessions"][0]
        assert session["id"] == "ses_1"
        assert session["title"] == "test session"
        # The assistant has text and tool parts: joined rows must not multiply usage.
        assert session["tokens"] == {"input": 10, "output": 5}
        assert session["cost"] == 0.01
        summary = session["summary"]
        assert summary["user_messages"] == ["hello"]
        assert summary["assistant_messages"] == ["hi there"]
        assert summary["tool_calls"] == [{"name": "glob", "args": {"pattern": "*"}}]

def _write_fake_opencode_db(db_path: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE session (
            id text PRIMARY KEY, title text, slug text,
            time_created integer, time_updated integer,
            cost real, tokens_input integer, tokens_output integer
        );
        CREATE TABLE message (id text PRIMARY KEY, session_id text, time_created integer, data text);
        CREATE TABLE part (id text PRIMARY KEY, message_id text, data text);
        CREATE TABLE account (id text PRIMARY KEY);
        CREATE TABLE control_account (email text);
        """
    )
    conn.execute(
        "INSERT INTO session VALUES ('ses_1', 'test session', 'slug-1', 1, 2, 0.01, 10, 5)"
    )
    conn.execute(
        "INSERT INTO message VALUES ('msg_1', 'ses_1', 1, ?)",
        (json.dumps({"role": "user"}),),
    )
    conn.execute(
        "INSERT INTO part VALUES ('prt_1', 'msg_1', ?)",
        (json.dumps({"type": "text", "text": "hello"}),),
    )
    conn.execute(
        "INSERT INTO message VALUES ('msg_2', 'ses_1', 2, ?)",
        (json.dumps({"role": "assistant", "cost": 0.01,
                     "tokens": {"input": 10, "output": 5}}),),
    )
    conn.execute(
        "INSERT INTO part VALUES ('prt_2', 'msg_2', ?)",
        (json.dumps({"type": "text", "text": "hi there"}),),
    )
    conn.execute(
        "INSERT INTO part VALUES ('prt_3', 'msg_2', ?)",
        (json.dumps({"type": "tool", "tool": "glob", "state": {"input": {"pattern": "*"}}}),),
    )
    conn.commit()
    conn.close()


class TestAgyAdapter:
    def test_wire_capabilities_mounts_native_global_skill(
        self, make_config, tmp_path: Path, skill_dir: Path
    ):
        config = make_config(
            harness="agy",
            provider="gemini",
            capabilities=[{"kind": "skill", "name": "my-skill", "install": "mount", "path": str(skill_dir)}],
        )
        adapter = get_adapter("agy")
        volumes = adapter.wire_capabilities(tmp_path / "run", config, config.capabilities)

        assert volumes == [
            (skill_dir, "/root/.gemini/antigravity-cli/skills/my-skill")
        ]
