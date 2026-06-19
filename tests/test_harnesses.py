from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahl.harnesses import get_adapter
from ahl.harnesses.agy import AgyAdapter
from ahl.harnesses.claude import ClaudeAdapter
from ahl.harnesses.gemini import GeminiAdapter
from ahl.harnesses.opencode import OpenCodeAdapter


def test_get_adapter_returns_expected_class():
    assert isinstance(get_adapter("claude"), ClaudeAdapter)
    assert isinstance(get_adapter("gemini"), GeminiAdapter)
    assert isinstance(get_adapter("opencode"), OpenCodeAdapter)
    assert isinstance(get_adapter("agy"), AgyAdapter)


def test_get_adapter_unknown_name_raises():
    with pytest.raises(ValueError, match="no adapter registered"):
        get_adapter("not-a-harness")


def _mcp_capability(tmp_path: Path):
    from ahl.capabilities import parse_capabilities

    return parse_capabilities(
        [{"kind": "mcp", "name": "my-mcp", "install": "pip", "command": "my-mcp-server"}],
        tmp_path,
    )


class TestClaudeAdapter:
    def test_seed_returns_no_volumes(self, make_config, tmp_path: Path):
        config = make_config(harness="claude", provider="anthropic")
        adapter = get_adapter("claude")
        assert adapter.seed(tmp_path / "run", config) == []

    def test_wire_capabilities_no_skills_is_noop(self, make_config, tmp_path: Path):
        config = make_config(harness="claude", provider="anthropic")
        adapter = get_adapter("claude")
        assert adapter.wire_capabilities(tmp_path / "run", config, []) == []

    def test_wire_capabilities_mount_install_binds_host_path_directly(
        self, make_config, tmp_path: Path, skill_dir: Path
    ):
        config = make_config(
            harness="claude",
            provider="anthropic",
            capabilities=[{"kind": "skill", "name": "my-skill", "install": "mount", "path": str(skill_dir)}],
        )
        run_dir = tmp_path / "run"
        adapter = get_adapter("claude")
        volumes = adapter.wire_capabilities(run_dir, config, config.capabilities)

        # Bind-mounted straight from the host skill_dir — no copy, so editing
        # skill_dir later is reflected without rerunning ahl up (hot reload).
        assert volumes == [(skill_dir, "/workspace/.claude/skills/my-skill")]

    def test_wire_capabilities_copy_install_snapshots_into_run_dir(
        self, make_config, tmp_path: Path, skill_dir: Path
    ):
        config = make_config(
            harness="claude",
            provider="anthropic",
            capabilities=[{"kind": "skill", "name": "my-skill", "install": "copy", "path": str(skill_dir)}],
        )
        run_dir = tmp_path / "run"
        adapter = get_adapter("claude")
        volumes = adapter.wire_capabilities(run_dir, config, config.capabilities)

        copied = run_dir / "claude" / "skills" / "my-skill"
        assert volumes == [(copied, "/workspace/.claude/skills/my-skill")]
        assert (copied / "SKILL.md").read_text() == (skill_dir / "SKILL.md").read_text()

    def test_wire_capabilities_warns_on_mcp(self, make_config, tmp_path: Path, capsys):
        config = make_config(harness="claude", provider="anthropic")
        adapter = get_adapter("claude")
        adapter.wire_capabilities(tmp_path / "run", config, _mcp_capability(tmp_path))
        assert "not wired for harness 'claude'" in capsys.readouterr().err

    def test_parse_trace_returns_none(self, tmp_path: Path):
        assert get_adapter("claude").parse_trace(tmp_path / "run") is None


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

    def test_wire_capabilities_folds_skill_into_global_gemini_md(self, make_config, tmp_path: Path, skill_dir: Path):
        config = make_config(
            harness="gemini",
            provider="gemini",
            capabilities=[{"kind": "skill", "name": "my-skill", "install": "mount", "path": str(skill_dir)}],
        )
        run_dir = tmp_path / "run"
        adapter = get_adapter("gemini")
        adapter.seed(run_dir, config)
        volumes = adapter.wire_capabilities(run_dir, config, config.capabilities)

        assert volumes == []
        context = (run_dir / "gemini" / "GEMINI.md").read_text()
        assert "Do the thing." in context

    def test_parse_trace_on_freshly_seeded_home_has_no_sessions(self, make_config, tmp_path: Path):
        config = make_config(harness="gemini", provider="gemini")
        run_dir = tmp_path / "run"
        adapter = get_adapter("gemini")
        adapter.seed(run_dir, config)
        trace = adapter.parse_trace(run_dir)
        assert trace["sessions"] == []
        assert trace["logs"] == []


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
            capabilities=[{"kind": "skill", "name": "my-skill", "install": "mount", "path": str(skill_dir)}],
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

    def test_wire_capabilities_copy_install_snapshots_inside_already_mounted_config_dir(
        self, make_config, tmp_path: Path, skill_dir: Path
    ):
        # install: copy lands inside config_dir, which seed() already mounts
        # as a whole — so no extra volume is needed for the copy to be visible.
        config = make_config(
            harness="opencode",
            provider="anthropic",
            capabilities=[{"kind": "skill", "name": "my-skill", "install": "copy", "path": str(skill_dir)}],
        )
        run_dir = tmp_path / "run"
        adapter = get_adapter("opencode")
        adapter.seed(run_dir, config)
        volumes = adapter.wire_capabilities(run_dir, config, config.capabilities)

        assert volumes == []
        config_dir = run_dir / "opencode" / "config"
        copied = config_dir / "skills" / "my-skill" / "SKILL.md"
        assert copied.read_text() == (skill_dir / "SKILL.md").read_text()

    def test_parse_trace_reports_log_files(self, make_config, tmp_path: Path):
        config = make_config(harness="opencode", provider="anthropic")
        run_dir = tmp_path / "run"
        adapter = get_adapter("opencode")
        adapter.seed(run_dir, config)
        (run_dir / "opencode" / "data" / "log" / "session.log").write_text("hi")

        trace = adapter.parse_trace(run_dir)
        assert trace["log_files"] == ["session.log"]

    def test_parse_trace_reads_sessions_from_sqlite_db(self, make_config, tmp_path: Path):
        # Recent OpenCode versions store session/message/part data in
        # opencode.db (sqlite), not the file-based layout the public docs
        # describe — this is the actual on-disk schema, reproduced minimally.
        config = make_config(harness="opencode", provider="anthropic")
        run_dir = tmp_path / "run"
        adapter = get_adapter("opencode")
        adapter.seed(run_dir, config)
        _write_fake_opencode_db(run_dir / "opencode" / "data" / "opencode.db")

        trace = adapter.parse_trace(run_dir)

        assert len(trace["sessions"]) == 1
        session = trace["sessions"][0]
        assert session["id"] == "ses_1"
        assert session["title"] == "test session"
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
        (json.dumps({"role": "assistant"}),),
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
    def test_wire_capabilities_warns_and_noops(self, make_config, tmp_path: Path, skill_dir: Path, capsys):
        config = make_config(
            harness="agy",
            provider="gemini",
            capabilities=[{"kind": "skill", "name": "my-skill", "install": "mount", "path": str(skill_dir)}],
        )
        adapter = get_adapter("agy")
        volumes = adapter.wire_capabilities(tmp_path / "run", config, config.capabilities)

        assert volumes == []
        assert "not supported on harness 'agy'" in capsys.readouterr().err
