"""Portable policy, native artifacts, and real CLI orchestration contracts."""

from dataclasses import replace
import json

import pytest
from typer.testing import CliRunner
import yaml

from ahl import cli
from ahl.config import ConfigError
from ahl.harnesses import get_adapter
from ahl.permissions import PermissionPolicy, PermissionSetup


def test_policy_config_normalizes_and_rejects_invalid_input(make_config):
    assert make_config().permissions == PermissionPolicy()
    for raw, expected in [
        ({}, []),
        ({"deny": []}, []),
        ({"deny": ["websearch", "webfetch", "websearch"]}, ["webfetch", "websearch"]),
    ]:
        assert make_config(extra={"permissions": raw}).permissions.deny == frozenset(
            expected
        )
    for raw in [
        None,
        [],
        "webfetch",
        {"allow": []},
        {"deny": None},
        {"deny": "webfetch"},
        {"deny": ["WebFetch"]},
        {"deny": [True]},
        {"deny": [{}]},
    ]:
        with pytest.raises(ConfigError, match="permissions"):
            make_config(extra={"permissions": raw})


@pytest.mark.parametrize("provider", ["anthropic", "openrouter"])
def test_claude_policy_replaces_only_managed_artifact(make_config, tmp_path, provider):
    config = make_config(provider=provider)
    adapter = get_adapter("claude")
    adapter.seed(tmp_path, config)
    user = tmp_path / "claude/settings.json"
    user.write_text('{"permissions":{"allow":["WebFetch"]},"theme":"dark"}')
    before = user.read_bytes()
    for denied, native in [
        (["websearch", "webfetch"], ["WebFetch", "WebSearch"]),
        (["webfetch"], ["WebFetch"]),
        ([], []),
    ]:
        policy = PermissionPolicy(frozenset(denied))
        result = adapter.permission_handler.prepare(tmp_path, config, policy)
        assert result.applied == policy.deny and result.unsupported == {}
        assert len(result.readonly_volumes) == 1
        host, target = result.readonly_volumes[0]
        assert host.is_relative_to(tmp_path / "permissions/claude")
        assert target == "/etc/claude-code/managed-settings.json"
        assert json.loads(host.read_text()) == {"permissions": {"deny": native}}
        assert user.read_bytes() == before


def test_deepseek_policy_reconciles_root_plugin_and_native_overrides(
    make_config, tmp_path
):
    config = make_config(harness="deepseek", provider="openrouter")
    adapter = get_adapter("deepseek")
    adapter.seed(tmp_path, config)
    patch = tmp_path / "deepseek/home/cordis.patch.yml"
    settings = tmp_path / "deepseek/home/settings.yaml"
    rows = yaml.safe_load(patch.read_text())
    rows.append({"insert": [{"id": "unrelated", "name": "keep"}]})
    patch.write_text(yaml.safe_dump(rows))
    settings.write_text(
        yaml.safe_dump({"theme": "dark", "ahl-native-permissions": {"deny": []}})
    )
    for denied in [["websearch", "webfetch"], ["websearch"], []]:
        policy = PermissionPolicy(frozenset(denied))
        cfg = replace(config, permissions=policy)
        adapter.seed(tmp_path, cfg)
        result = adapter.permission_handler.prepare(tmp_path, cfg, policy)
        assert result == PermissionSetup([], policy.deny, {})
        expected = {
            "deny": sorted(
                {"websearch": "web_search", "webfetch": "web_fetch"}[x] for x in denied
            )
        }
        current = yaml.safe_load(patch.read_text())
        owned = [
            r
            for r in current
            if any(p.get("id") == "ahl-native-permissions" for p in r.get("insert", []))
        ]
        assert owned == [
            {
                "insert": [
                    {
                        "id": "ahl-native-permissions",
                        "name": "/opt/ahl/permissions/deepseek.mjs",
                        "config": expected,
                    }
                ]
            }
        ]
        assert [r for r in current if r not in owned] == rows
        saved = yaml.safe_load(settings.read_text())
        assert saved == {"theme": "dark", "ahl-native-permissions": expected}
        before = patch.read_bytes(), settings.read_bytes()
        adapter.permission_handler.prepare(tmp_path, cfg, policy)
        assert (patch.read_bytes(), settings.read_bytes()) == before
        hints = " ".join(adapter.start_hints(tmp_path, cfg))
        assert ("disabled by AHL permissions" in hints) == ("websearch" in denied)
        assert ("requires DEEPSEEK_API_KEY" in hints) == ("websearch" not in denied)


@pytest.mark.parametrize(
    "harness,provider",
    [
        ("gemini", "gemini"),
        ("agy", "gemini"),
        ("claude-science", "anthropic"),
    ],
)
def test_unsupported_policy_warns_launches_and_records_gap(
    make_config, tmp_path, docker, harness, provider
):
    (tmp_path / "workspace").mkdir()
    make_config(
        harness=harness,
        provider=provider,
        extra={"permissions": {"deny": ["websearch", "webfetch"]}},
    )
    runner = CliRunner()
    args = [
        "up",
        "-c",
        str(tmp_path / "config.yaml"),
        "--no-build",
        "--name",
        "unsupported",
    ]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert len(docker.launches()) == 1
    warnings = [
        line for line in result.output.splitlines() if "permissions not applied" in line
    ]
    assert len(warnings) == 1 and all(
        op in warnings[0] for op in ["webfetch", "websearch"]
    )
    run = tmp_path / "runs/unsupported"
    meta = json.loads((run / "session.json").read_text())["permissions"]
    assert meta["deny"] == ["webfetch", "websearch"] and meta["applied"] == []
    assert set(meta["unsupported"]) == {"webfetch", "websearch"}
    assert all(meta["unsupported"].values())
    assert not (run / "permissions").exists()
    native_before = {
        p: p.read_bytes()
        for p in run.rglob("*")
        if p.is_file() and p.name not in {"session.json", "trace.json"}
    }
    make_config(harness=harness, provider=provider)
    resumed = runner.invoke(cli.app, args[:-2] + ["--resume", "unsupported"])
    assert resumed.exit_code == 0, resumed.output
    assert "permissions not applied" not in resumed.output
    assert all(p.read_bytes() == content for p, content in native_before.items())
    assert json.loads((run / "session.json").read_text())["permissions"] == {
        "deny": [],
        "applied": [],
        "unsupported": {},
    }


def test_cli_handler_composition_and_resume_metadata(
    make_config, tmp_path, monkeypatch, docker
):
    (tmp_path / "workspace").mkdir()
    adapter = get_adapter("claude")

    class FakeHandler:
        def prepare(self, run_dir, config, policy):
            assert (run_dir / "claude/projects").is_dir()  # after seeding
            artifact = run_dir / "fake-policy.json"
            artifact.write_text(json.dumps(sorted(policy.deny)))
            return PermissionSetup([(artifact, "/fake-policy.json")], policy.deny, {})

    monkeypatch.setattr(adapter, "permission_handler", FakeHandler())
    runner = CliRunner()
    started = None
    for i, denied in enumerate([["websearch", "webfetch"], ["webfetch"], []]):
        make_config(extra={"permissions": {"deny": denied}})
        result = runner.invoke(
            cli.app,
            [
                "up",
                "-c",
                str(tmp_path / "config.yaml"),
                "--no-build",
                "--resume" if i else "--name",
                "policy",
            ],
        )
        assert result.exit_code == 0, result.output
        root = tmp_path / "runs/policy"
        meta = json.loads((root / "session.json").read_text())
        assert meta["permissions"] == {
            "deny": sorted(denied),
            "applied": sorted(denied),
            "unsupported": {},
        }
        assert f"{root}/fake-policy.json:/fake-policy.json:ro" in docker.launches()[-1]
        assert json.loads((root / "fake-policy.json").read_text()) == sorted(denied)
        if i == 0:
            started = meta["started_at"]
            (root / "workspace/history.txt").write_text("keep")
        else:
            assert meta["started_at"] == started and len(meta["resumed_at"]) == i
            assert (root / "workspace/history.txt").read_text() == "keep"


def test_invalid_handler_results_and_io_errors_never_launch(
    make_config, tmp_path, monkeypatch, docker
):
    (tmp_path / "workspace").mkdir()
    make_config(extra={"permissions": {"deny": ["webfetch"]}})

    class BadHandler:
        result = None
        calls = 0

        def prepare(self, *args):
            self.calls += 1
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    handler = BadHandler()
    monkeypatch.setattr(get_adapter("claude"), "permission_handler", handler)
    invalid = [
        PermissionSetup([], frozenset(), {}),
        PermissionSetup([], frozenset({"webfetch"}), {"webfetch": "duplicate"}),
        PermissionSetup([], frozenset({"webfetch", "websearch"}), {}),
        PermissionSetup([], frozenset(), {"webfetch": ""}),
        OSError("cannot write policy"),
    ]
    for i, bad in enumerate(invalid):
        handler.result = bad
        result = CliRunner().invoke(
            cli.app,
            [
                "up",
                "-c",
                str(tmp_path / "config.yaml"),
                "--no-build",
                "--name",
                f"bad-{i}",
            ],
        )
        assert result.exit_code != 0
        assert not (tmp_path / f"runs/bad-{i}/session.json").exists()
    assert handler.calls == len(invalid)
    assert docker.launches() == []


@pytest.mark.parametrize(
    "harness,provider", [("claude", "anthropic"), ("deepseek", "openrouter"), ("opencode", "anthropic")]
)
def test_native_handlers_report_unmapped_extensions(
    make_config, tmp_path, harness, provider
):
    # Future canonical operations must degrade to an explicit gap in older mappings.
    config = make_config(harness=harness, provider=provider)
    adapter = get_adapter(harness)
    adapter.seed(tmp_path, config)
    policy = PermissionPolicy(frozenset({"webfetch", "future_operation"}))
    result = adapter.permission_handler.prepare(tmp_path, config, policy)
    assert result.applied == frozenset({"webfetch"})
    assert set(result.unsupported) == {"future_operation"}
    assert result.unsupported["future_operation"]
