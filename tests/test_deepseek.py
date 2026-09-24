"""DSH v3 persistence fixtures based on the published native event contract."""

from dataclasses import replace
import json

import pytest
import yaml

from ahl.config import ConfigError, Named
from ahl.harnesses import get_adapter
from ahl.docker import docker_run_args
from ahl.skills import wire_delegated_skills


def event(seq, kind, **data):
    return {"seq": seq, "type": kind, "time": 1000 + seq * 100, "data": data}


def usage(n=1):
    return {
        "inputTokens": n,
        "outputTokens": 2 * n,
        "cacheWriteTokens": 3 * n,
        "cacheReadTokens": 4 * n,
        "totalTokens": 10 * n + 1,
        "reasoningTokens": n,
    }


def chunk(kind, **data):
    return {"type": "chunk", "time": 1100, "chunk": {"type": kind, **data}}


def message(text, role="assistant", source=None):
    return {
        "id": text,
        "role": role,
        "source": source
        or {"kind": "model", "provider": "openrouter", "model": "vendor/model"},
        "content": [
            {"type": "text", "text": text},
            {"type": "reasoning", "text": "thinking"},
        ],
    }


def write_session(root, name, events, **header):
    path = root / "deepseek/home/sessions/project" / name / "session.v3.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "type": "session",
                    "version": 3,
                    "id": name,
                    "createdAt": 1000,
                    "cwd": "/workspace",
                    "isSeeded": False,
                    "delegationDepth": 0,
                    "agentPreset": "standard",
                    **header,
                },
                *events,
            ]
        )
        + "\n"
    )
    return path


def test_seed_reconciles_resume_settings_and_preserves_native_state(
    make_config, tmp_path
):
    adapter = get_adapter("deepseek")
    config = make_config(
        harness="deepseek", provider="openrouter", model="vendor/first"
    )
    root = tmp_path / "run"
    volumes = adapter.seed(root, config)
    assert volumes == [
        (root / "deepseek/home", "/root/.dsh"),
        (root / "deepseek/agents", "/root/.agents"),
    ]
    assert all(host.is_dir() for host, _ in volumes)
    home = root / "deepseek/home"
    patch, settings = home / "cordis.patch.yml", home / "settings.yaml"
    rows = yaml.safe_load(patch.read_text())
    assert next(
        row["config"] for row in rows if row["id"] == "agent-default-model"
    ) == {"provider": "openrouter", "model": "vendor/first"}
    rows.append({"id": "custom", "config": {"keep": True}})
    patch.write_text(yaml.safe_dump(rows))
    settings.write_text(
        yaml.safe_dump(
            {
                "theme": {"mode": "dark"},
                "llm-pi-ai": {
                    "providers": {
                        "another": {"models": [{"id": "keep"}]},
                        "openrouter": {"baseURL": "old"},
                    }
                },
                "agent-default-model": {"provider": "old", "model": "old"},
            }
        )
    )
    log = write_session(root, "saved", [])
    before = log.read_bytes()
    config = replace(config, model=Named("openrouter/auto", {}))
    adapter.seed(root, config)
    seeded = patch.read_bytes(), settings.read_bytes()
    adapter.seed(root, config)
    assert (patch.read_bytes(), settings.read_bytes()) == seeded
    rows = {row["id"]: row["config"] for row in yaml.safe_load(patch.read_text())}
    route = {
        "apiKeyEnv": "OPENROUTER_API_KEY",
        "api": "openai-completions",
        "baseURL": "https://openrouter.ai/api/v1",
        "models": [{"id": "openrouter/auto"}],
    }
    assert rows["llm-pi-ai"]["providers"]["openrouter"] == route
    assert rows["agent-default-model"] == {
        "provider": "openrouter",
        "model": "openrouter/auto",
    }
    assert rows["session-persistence-jsonl"] == {
        "root": "/root/.dsh/sessions",
        "compression": "none",
    }
    assert rows["custom"] == {"keep": True}
    saved = yaml.safe_load(settings.read_text())
    assert saved["llm-pi-ai"]["providers"]["openrouter"] == route
    assert saved["llm-pi-ai"]["providers"]["another"]["models"] == [{"id": "keep"}]
    assert saved["agent-default-model"] == {
        "provider": "openrouter",
        "model": "openrouter/auto",
    }
    assert saved["theme"] == {"mode": "dark"}
    assert log.read_bytes() == before
    # Neither malformed document may be silently replaced during resume.
    for target, invalid in [
        (patch, "invalid: mapping\n"),
        (settings, "- invalid-list\n"),
    ]:
        original = target.read_bytes()
        target.write_text(invalid)
        broken = patch.read_bytes(), settings.read_bytes()
        with pytest.raises(ConfigError, match=target.name):
            adapter.seed(root, config)
        assert (patch.read_bytes(), settings.read_bytes()) == broken
        target.write_bytes(original)


def test_launch_composes_native_skills_and_loopback_ports(
    make_config, tmp_path, skill_dir, capsys
):
    config = make_config(
        harness="deepseek",
        provider="openrouter",
        capabilities=[
            {
                "kind": "skill",
                "name": "my-skill",
                "install": "mount",
                "path": str(skill_dir),
            },
            {
                "kind": "skill",
                "name": "copied",
                "install": "copy",
                "path": str(skill_dir),
            },
            {
                "kind": "mcp",
                "name": "unsupported",
                "install": "pip",
                "command": "example",
            },
        ],
    )
    adapter = get_adapter("deepseek")
    mounts = adapter.wire_capabilities(tmp_path, config, config.capabilities)
    assert mounts == [(skill_dir, "/root/.dsh/skills/my-skill")]
    assert "not wired for harness 'deepseek'" in capsys.readouterr().err
    sources, commands = wire_delegated_skills("deepseek", config.capabilities)
    assert sources == [(skill_dir, "/opt/ahl-skill-sources/0")]
    assert commands == [
        "npx --yes skills add /opt/ahl-skill-sources/0 "
        "--skill copied --agent universal --global --yes"
    ]
    assert "DEEPSEEK_API_KEY" in " ".join(adapter.start_hints(tmp_path, config))
    for params, published, relay in [
        ({}, 3080, 3081),
        ({"port": 3081}, 3081, 3082),
        ({"port": 4321}, 4321, 3081),
    ]:
        cfg = replace(config, harness=Named("deepseek", params))
        assert adapter.start_command(cfg) == f"ahl-deepseek --port {published}"
        args = docker_run_args(
            cfg,
            adapter.build_env(cfg),
            tmp_path,
            image="agent-harness-lab:deepseek",
            readonly_volumes=mounts,
            extra_args=adapter.docker_args(cfg),
        )
        assert args[args.index("-p") + 1] == f"127.0.0.1:{published}:{relay}"
        assert args.index("-p") < args.index("agent-harness-lab:deepseek")
        assert f"{skill_dir}:/root/.dsh/skills/my-skill:ro" in args


def test_settlements_retry_compaction_and_tools_are_counted_once(tmp_path):
    adapter = get_adapter("deepseek")
    committed = event(
        3,
        "assistant/message",
        turn=1,
        step=1,
        message=message("hello"),
        usage=usage(2),
        stream=[chunk("usage", usage=usage(99))],
    )
    committed["data"]["message"]["content"].append(
        {
            "type": "tool-call",
            "id": "a",
            "name": "bash",
            "arguments": '{"command":"pwd"}',
        }
    )
    path = write_session(
        tmp_path,
        "parent",
        [
            event(0, "user/message", **message("prompt", "user", {"kind": "user"})),
            event(
                1,
                "user/message",
                **message("injected", "user", {"kind": "plugin", "plugin": "skill"}),
            ),
            event(2, "model/selection", provider="openrouter", model="vendor/model"),
            committed,
            committed,
            event(
                4,
                "assistant/attempt",
                turn=1,
                step=1,
                stream=[
                    chunk("usage", usage=usage(90)),
                    chunk("usage", usage=usage(3)),
                    chunk("error", error={"message": "retry"}),
                ],
            ),
            event(
                5,
                "compaction/summary",
                usage=usage(4),
                summary=[],
                provider="openrouter",
                model="vendor/model",
            ),
            event(
                6, "tool/call", callId="a", name="bash", arguments='{"command":"pwd"}'
            ),
            event(
                7,
                "tool/result",
                message={
                    "role": "user",
                    "source": {"kind": "tool", "callId": "a"},
                    "content": [
                        {
                            "type": "tool-result",
                            "toolCallId": "a",
                            "content": [{"type": "text", "text": "/workspace"}],
                        }
                    ],
                },
            ),
            event(8, "tool/call", callId="b", name="edit", arguments="{broken"),
            event(
                9,
                "subagent/catalog",
                childId="child",
                mode="one-shot",
                version=1,
                childCreatedAt=2000,
            ),
        ],
    )
    path.with_name("session.v2.jsonl").write_text(path.read_text())
    trace = adapter.parse_trace(tmp_path)
    session = trace["sessions"][0]
    assert trace["totals"]["api_responses"] == 3
    assert trace["totals"]["incomplete_api_responses"] == 1
    assert trace["totals"]["usage"] == {
        "input_tokens": 9,
        "output_tokens": 18,
        "cache_creation_input_tokens": 27,
        "cache_read_input_tokens": 36,
        "total_tokens": 93,
        "cache_creation": {
            "ephemeral_5m_input_tokens": None,
            "ephemeral_1h_input_tokens": None,
        },
    }
    assert [(m["role"], m["text"]) for m in session["messages"]] == [
        ("user", "prompt"),
        ("assistant", "hello"),
    ]
    assert session["messages"][1]["reasoning"] == "thinking"
    assert session["context_messages"][0]["text"] == "injected"
    assert session["models"] == [{"provider": "openrouter", "model": "vendor/model"}]
    assert session["children"][0]["childId"] == "child"
    assert session["tool_calls"] == [
        {
            "id": "a",
            "name": "bash",
            "args": {"command": "pwd"},
            "result": [{"type": "text", "text": "/workspace"}],
            "is_error": False,
        },
        {"id": "b", "name": "edit", "args": "{broken", "result": None},
    ]
    assert session["timing"]["duration_seconds"] == 0.9


def test_forks_exclude_inheritance_but_resume_keeps_own_events(tmp_path):
    adapter = get_adapter("deepseek")
    parent = event(0, "assistant/message", message=message("parent"), usage=usage())
    inherited_call = event(
        1, "tool/call", callId="same", name="bash", arguments='{"command":"parent"}'
    )
    inherited_only = event(
        2, "tool/call", callId="inherited-only", name="read", arguments="{}"
    )
    write_session(tmp_path, "parent", [parent, inherited_call, inherited_only])
    own_usage = usage(2)
    del own_usage["totalTokens"]  # optional total does not make base usage incomplete
    events = [
        parent,
        inherited_call,
        inherited_only,
        event(3, "session/end-seed", inherited=True),
        event(
            4, "assistant/message", message=message("older inherited"), usage=usage(100)
        ),
        event(5, "session/end-seed", inherited=True),
        event(6, "assistant/message", message=message("own"), usage=own_usage),
        event(7, "tool/call", callId="same", name="edit", arguments='{"file":"child"}'),
        event(8, "session/end-seed"),
        event(
            9,
            "assistant/message",
            message=message("resumed"),
            usage=usage(3),
            interrupted=True,
        ),
    ]
    path = write_session(
        tmp_path,
        "child",
        events,
        isSeeded=True,
        parentSession="parent",
        origin="subagent",
    )
    trace = adapter.parse_trace(tmp_path)
    assert trace["totals"]["api_responses"] == 3
    assert trace["totals"]["usage"]["input_tokens"] == 6
    assert trace["totals"]["incomplete_api_responses"] == 1
    child = next(s for s in trace["sessions"] if s["id"] == "child")
    assert [m["text"] for m in child["messages"]] == ["own", "resumed"]
    assert child["models"] == [{"provider": "openrouter", "model": "vendor/model"}]
    assert child["tool_calls"] == [
        {"id": "same", "name": "edit", "args": {"file": "child"}, "result": None}
    ]
    parent_session = next(s for s in trace["sessions"] if s["id"] == "parent")
    assert parent_session["tool_calls"] == [
        {"id": "same", "name": "bash", "args": {"command": "parent"}, "result": None},
        {"id": "inherited-only", "name": "read", "args": {}, "result": None},
    ]
    assert child["parentSession"] == "parent" and child["origin"] == "subagent"
    with path.open("a") as f:
        f.write(
            json.dumps(
                event(10, "assistant/message", message=message("new"), usage=usage(4))
            )
            + "\n"
        )
    assert adapter.parse_trace(tmp_path)["totals"]["usage"]["input_tokens"] == 10


def test_partial_usage_and_damaged_or_unsupported_logs_are_visible(
    tmp_path, monkeypatch
):
    adapter = get_adapter("deepseek")
    assert adapter.parse_trace(tmp_path)["totals"]["api_responses"] == 0
    write_session(
        tmp_path,
        "good",
        [
            event(
                0,
                "assistant/message",
                message=message("partial"),
                usage={
                    "inputTokens": 0,
                    "outputTokens": True,
                    "cacheReadTokens": -1,
                    "cacheWriteTokens": "2",
                },
            ),
            event(2, "compaction/summary", summary=[]),
        ],
    )
    write_session(
        tmp_path,
        "missing",
        [
            event(
                0,
                "request/header",
                header={
                    "config": {"provider": "openrouter", "model": "vendor/failed"},
                    "tools": [],
                },
                reason="initial",
            ),
            event(1, "assistant/attempt", stream=[]),
        ],
    )
    damaged = write_session(tmp_path, "damaged", [])
    with damaged.open("a") as f:
        f.write('not json\n[]\n{"seq":false,"type":"assistant/message","data":{}}\n')
    unsupported = write_session(tmp_path, "old-only", [])
    unsupported.rename(unsupported.with_name("session.v2.jsonl"))
    unreadable = write_session(tmp_path, "unreadable", [])
    original = type(unreadable).read_text

    def read(path, *args, **kwargs):
        if path == unreadable:
            raise PermissionError("unreadable fixture")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(unreadable), "read_text", read)
    trace = adapter.parse_trace(tmp_path)
    assert {s["id"] for s in trace["sessions"]} == {"good", "missing", "damaged"}
    failed = next(s for s in trace["sessions"] if s["id"] == "missing")
    assert failed["models"] == [{"provider": "openrouter", "model": "vendor/failed"}]
    partial = next(s for s in trace["sessions"] if s["id"] == "good")
    assert partial["usage"]["input_tokens"] == 0
    assert all(
        partial["usage"][key] is None
        for key in (
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "total_tokens",
        )
    )
    assert trace["totals"]["api_responses"] == 2
    assert trace["totals"]["incomplete_api_responses"] == 2
    assert all(
        trace["totals"]["usage"][field] is None
        for field in [
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "total_tokens",
        ]
    )
    warnings = trace["warnings"]
    assert any(
        w.startswith(str(damaged) + ":") and "malformed record" in w for w in warnings
    )
    assert any(
        w.startswith(str(unreadable) + ":") and "unreadable log" in w for w in warnings
    )
    assert any(
        w.startswith(str(unsupported.parent) + ":")
        and "unsupported session generation" in w
        for w in warnings
    )
