from __future__ import annotations

import json
import os
import re
import subprocess
from importlib import metadata, resources
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

import ahl
from ahl import cli
from ahl.config import ConfigError, load_config

IMAGES = resources.files("ahl") / "images"
PACKAGE_DIR = Path(ahl.__file__).resolve().parent
EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.yaml"


def ahl_cli(*args: Any):
    return CliRunner().invoke(cli.app, [str(a) for a in args])


def write_config(path: Path, **raw: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw))
    return path


def container_env(args: list[str], name: str) -> str | None:
    values = [a.split("=", 1)[1] for a in args if a.startswith(f"{name}=")]
    return values[-1] if values else None


OPENROUTER = {"harness": "opencode", "provider": "openrouter", "model": "qwen/qwen3.7-flash"}


def test_a1_ac1_paths_resolve_against_config_dir_and_working_dir(tmp_path, monkeypatch, docker):
    tmp_path = tmp_path.resolve()
    project = tmp_path / "project"
    template = project / "templates/ws"
    template.mkdir(parents=True)
    (template / "seed.txt").write_text("seed")
    skill = project / "skills/my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: my-skill\ndescription: test\n---\n")
    lib = project / "pkgs/lib"
    lib.mkdir(parents=True)
    (lib / "pyproject.toml").write_text("[project]\nname = 'lib'\nversion = '0.1.0'\n")
    (project / "data").mkdir()
    write_config(
        project / "conf/config.yaml",
        **OPENROUTER,
        workspace="../templates/ws",
        capabilities=[{"kind": "skill", "name": "my-skill", "install": "mount", "path": "../skills/my-skill"}],
        packages=[{"name": "lib", "path": "../pkgs/lib"}],
        mounts=[{"path": "../data", "target": "/data"}],
    )
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets/keys.env").write_text("OPENROUTER_API_KEY=file-key\n")
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    common = [
        "up", "-c", "../project/conf/config.yaml", "--env-file", "../secrets/keys.env", "--runs-dir", "out/runs",
    ]

    result = ahl_cli(*common, "--no-build", "--name", "r1")

    assert result.exit_code == 0, result.output
    run = cwd / "out/runs/r1"
    args = docker.launches()[-1]
    assert {
        f"{run / 'workspace'}:/workspace",
        f"{skill}:/root/.config/opencode/skills/my-skill:ro",
        f"{lib}:/opt/ahl-packages/lib:ro",
        f"{project / 'data'}:/data:ro",
    } <= set(args)
    assert container_env(args, "OPENROUTER_API_KEY") == "file-key"
    assert (run / "workspace/seed.txt").read_text() == "seed"
    (run / "workspace/progress.txt").write_text("kept")

    assert [ahl_cli(*common, *flags).exit_code for flags in [["--name", "r1"], ["--resume", "r2"]]] == [2, 2]
    assert docker.builds() == [] and docker.registry_requests == []
    resumed = ahl_cli(*common, "--no-build", "--resume", "r1")

    assert resumed.exit_code == 0, resumed.output
    assert (run / "workspace/progress.txt").read_text() == "kept"
    assert len(json.loads((run / "session.json").read_text())["resumed_at"]) == 1
    assert not (project / "conf/runs").exists()


def test_a1_ac2_env_file_precedence_and_missing_key_errors(tmp_path, monkeypatch, docker):
    tmp_path = tmp_path.resolve()
    config = write_config(tmp_path / "conf/config.yaml", **OPENROUTER)
    explicit = tmp_path / "explicit.env"
    explicit.write_text("OPENROUTER_API_KEY=file-key\n")
    monkeypatch.chdir(tmp_path)

    def key_in_container(*flags: str) -> str | None:
        result = ahl_cli("up", "-c", "conf/config.yaml", "--no-build", "--name", f"r{len(docker.calls)}", *flags)
        assert result.exit_code == 0, result.output
        return container_env(docker.launches()[-1], "OPENROUTER_API_KEY")

    monkeypatch.setenv("OPENROUTER_API_KEY", "shell-key")
    assert key_in_container("--env-file", "explicit.env") == "file-key"

    monkeypatch.setenv("OPENROUTER_API_KEY", "shell-key")
    (tmp_path / "conf/.env").write_text("OPENROUTER_API_KEY=dotenv-key\n")
    assert key_in_container() == "shell-key"

    with pytest.raises(ConfigError, match=re.escape(str(tmp_path / "missing.env"))):
        load_config(config, Path("missing.env"))

    monkeypatch.delenv("OPENROUTER_API_KEY")
    empty = tmp_path / "empty.env"
    empty.write_text("# no provider key\n")
    with pytest.raises(ConfigError, match=re.escape(str(empty))):
        load_config(config, empty)
    (tmp_path / "conf/.env").write_text("# no provider key\n")
    with pytest.raises(ConfigError, match=f"neither in {re.escape(str(tmp_path / 'conf/.env'))}"):
        load_config(config)
    (tmp_path / "conf/.env").unlink()
    with pytest.raises(ConfigError, match="no env file was read"):
        load_config(config)


def test_a1_ac3_ac4_build_reads_harness_and_version_and_passes_the_version(tmp_path, monkeypatch, docker):
    conf = tmp_path / "conf"
    write_config(
        conf / "config.yaml",
        harness={"name": "deepseek", "parameters": {"port": 4321}},
        provider="openrouter",
        model="qwen/qwen3.7-flash",
        packages=[{"name": "gone", "path": "./missing"}],
    )
    write_config(conf / "pinned.yaml", harness="claude", harness_version="2.1.273")
    write_config(conf / "gemini.yaml", harness="gemini", harness_version="1.0.0")
    lock = json.loads((IMAGES / "deepseek/package-lock.json").read_text())
    dsh = lock["packages"]["node_modules/@deepseek-ai/dsh"]["version"]
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    result = ahl_cli("build", "-c", "../conf/config.yaml")

    assert result.exit_code == 0, result.output
    assert docker.builds() == [[
        "docker", "build", "-t", "agent-harness-lab:deepseek", "-f", str(IMAGES / "deepseek.Dockerfile"),
        "--build-arg", f"HARNESS_VERSION={dsh}", str(IMAGES),
    ]]
    assert docker.build_envs == [{**os.environ, "BUILDX_NO_DEFAULT_ATTESTATIONS": "1"}]

    (conf / ".env").write_text("OPENROUTER_API_KEY=must-not-load\n")
    assert ahl_cli("build", "--harness", "claude").exit_code == 0
    assert ahl_cli("build", "-c", "../conf/pinned.yaml").exit_code == 0
    assert ahl_cli("build", "--harness", "gemini").exit_code == 0
    assert "OPENROUTER_API_KEY" not in os.environ
    assert [(b[3], b[6:-1]) for b in docker.builds()[1:]] == [
        ("agent-harness-lab:claude", ["--build-arg", "HARNESS_VERSION=2.1.281"]),
        ("agent-harness-lab:claude", ["--build-arg", "HARNESS_VERSION=2.1.273"]),
        ("agent-harness-lab:gemini", []),
    ]
    assert docker.registry_requests == ["https://registry.npmjs.org/@anthropic-ai/claude-code/latest"]
    rejected = ahl_cli("build", "-c", "../conf/gemini.yaml")
    assert rejected.exit_code == 2 and "harness_version" in rejected.output
    keys = tmp_path / "keys.env"
    keys.write_text("GEMINI_API_KEY=test-key\n")
    for harness, version in [("gemini", "1.0.0"), ("opencode", "latest")]:
        bad = write_config(conf / "bad.yaml", harness=harness, provider="gemini", harness_version=version)
        with pytest.raises(ConfigError, match="harness_version"):
            load_config(bad, keys)
    assert ahl_cli("build").exit_code == 2
    assert ahl_cli("build", "--harness", "claude", "-c", "../conf/config.yaml").exit_code == 2
    assert docker.launches() == []


def test_a1_ac5_session_records_ahl_and_image_provenance(tmp_path, monkeypatch, docker):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = write_config(tmp_path / "config.yaml", **OPENROUTER, harness_version="1.18.31")
    (tmp_path / "lib").mkdir()
    head = subprocess.run(
        ["git", "-C", str(PACKAGE_DIR), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(PACKAGE_DIR), "diff", "--quiet", "HEAD"]).returncode == 1
    docker.labels = {"ahl.harness": "opencode", "ahl.harness.version": "1.18.31"}

    built = ahl_cli("up", "-c", config, "--name", "built")

    assert built.exit_code == 0, built.output
    assert "HARNESS_VERSION=1.18.31" in docker.builds()[-1]
    order = [c[:3] for c in docker.calls if c[1] in {"build", "image", "run"}]
    assert order == [["docker", "build", "-t"], ["docker", "image", "inspect"], ["docker", "run", "--rm"]]
    session = json.loads((tmp_path / "runs/built/session.json").read_text())
    assert session["ahl"] == {
        "version": metadata.version("agent-harness-lab"),
        "git_sha": head,
        "git_dirty": dirty,
    }
    assert session["image"] == {
        "name": "agent-harness-lab:opencode",
        "id": docker.image_id,
        "harness_version": "1.18.31",
    }
    assert docker.image_id in docker.launches()[-1] and session["image"]["name"] not in docker.launches()[-1]
    assert session["harness"] == "opencode" and session["run_id"] == "built"

    unpinned = write_config(tmp_path / "unpinned.yaml", **OPENROUTER)
    for path, label in [
        (config, {"ahl.harness.version": "1.18.32"}),
        (unpinned, {}),
        (unpinned, {"ahl.harness.version": "latest"}),
    ]:
        docker.labels = {"ahl.harness": "opencode", **label}
        stale = ahl_cli("up", "-c", path, "--no-build", "--name", "stale")

        assert stale.exit_code == 2 and "Rebuild" in stale.output, stale.output
        assert not (tmp_path / "runs/stale").exists()
    assert len(docker.launches()) == 1

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    write_config(config, harness="gemini", provider="gemini", packages=[{"name": "lib", "install": "copy", "path": "./lib"}])
    docker.image_id = None
    missing = ahl_cli("up", "-c", config, "--no-build", "--name", "untracked")

    assert missing.exit_code == 1 and "ahl build --harness gemini" in missing.output
    assert not (tmp_path / "runs/untracked").exists()

    docker.image_id = "sha256:" + "cd" * 32
    docker.labels = {"ahl.harness": "gemini"}
    docker.ahl_tracked = False
    untracked = ahl_cli("up", "-c", config, "--no-build", "--name", "untracked")

    assert untracked.exit_code == 0, untracked.output
    assert docker.launches()[-1][:4] == ["docker", "run", "--rm", "-d"] and docker.image_id in docker.launches()[-1]
    session = json.loads((tmp_path / "runs/untracked/session.json").read_text())
    assert session["image"] == {"name": "agent-harness-lab:gemini", "id": docker.image_id, "harness_version": None}
    assert session["ahl"]["git_sha"] is None and session["ahl"]["git_dirty"] is None


def test_a1_ac6_mounts_bind_read_only_by_default_and_reject_invalid_entries(make_config, tmp_path, docker):
    (tmp_path / "workspace").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "scratch").mkdir()
    make_config(extra={"mounts": [
        {"path": "./data", "target": "/workspace/data"},
        {"path": "./scratch", "target": "/scratch", "readonly": False},
    ]})

    result = ahl_cli("up", "-c", tmp_path / "config.yaml", "--no-build", "--name", "mounts")

    assert result.exit_code == 0, result.output
    args = docker.launches()[-1]
    assert f"{tmp_path / 'data'}:/workspace/data:ro" in args
    assert f"{tmp_path / 'scratch'}:/scratch" in args
    for mounts, error in [
        ([{"path": "./missing", "target": "/x"}], r"mounts\[0\].*does not exist"),
        ([{"path": "./data", "target": "data"}], r"mounts\[0\].*absolute"),
        ([{"path": "./data", "target": "/workspace/"}], r"mounts\[0\].*/workspace"),
        ([{"path": "./data", "target": "//workspace"}], r"mounts\[0\].*/workspace"),
        ([{"path": "./data", "target": "/d"}, {"path": "./scratch", "target": "/d"}], r"mounts\[1\].*duplicate"),
        ([{"path": "./data", "target": "/d"}, {"path": "./scratch", "target": "//d/"}], r"mounts\[1\].*duplicate"),
        ([{"path": "./data", "target": "/d", "readonly": "no"}], r"mounts\[0\].*readonly"),
    ]:
        with pytest.raises(ConfigError, match=error):
            make_config(extra={"mounts": mounts})


def test_a1_ac7_network_and_env_reach_container_and_reject_invalid(make_config, tmp_path, docker):
    (tmp_path / "workspace").mkdir()
    docker.networks = {"lab_default"}
    make_config(
        harness="opencode",
        provider="openrouter",
        model="qwen/qwen3.7-flash",
        extra={"network": "lab_default", "env": {"NEO4J_URI": "bolt://neo4j:7687"}},
    )

    result = ahl_cli("up", "-c", tmp_path / "config.yaml", "--no-build", "--name", "net")

    assert result.exit_code == 0, result.output
    args = docker.launches()[-1]
    assert args[args.index("--network") + 1] == "lab_default"
    assert container_env(args, "NEO4J_URI") == "bolt://neo4j:7687"

    make_config(harness="opencode", provider="openrouter", model="qwen/qwen3.7-flash",
                extra={"network": "missing_net"})
    missing = ahl_cli("up", "-c", tmp_path / "config.yaml", "--no-build", "--name", "nonet")

    assert missing.exit_code == 2 and "missing_net" in missing.output
    assert len(docker.launches()) == 1 and not (tmp_path / "runs/nonet").exists()
    for harness, provider, env in [
        ("opencode", "openrouter", {"NEO4J_PORT": 7687}),
        ("claude", "anthropic", {"OPENROUTER_API_KEY": "sk-or-other"}),
        ("claude", "openrouter", {"ANTHROPIC_BASE_URL": "https://example.test"}),
    ]:
        [name] = env
        with pytest.raises(ConfigError, match=name):
            make_config(harness=harness, provider=provider, model="qwen/qwen3.7-flash", extra={"env": env})


def test_a1_ac8_extras_reach_installs_for_tools_and_libraries_in_both_modes(make_config, tmp_path, docker):
    (tmp_path / "workspace").mkdir()
    packages = []
    for kind, scripts in [("tool", "\n[project.scripts]\ntool = 'tool:main'\n"), ("lib", "")]:
        for mode in ("mount", "copy"):
            path = tmp_path / f"{kind}-{mode}"
            path.mkdir()
            (path / "pyproject.toml").write_text(f"[project]\nname = '{kind}-{mode}'\nversion = '0.1.0'\n{scripts}")
            packages.append({"name": f"{kind}-{mode}", "install": mode, "path": str(path), "extras": ["graph", "db"]})
    make_config(packages=packages)

    result = ahl_cli("up", "-c", tmp_path / "config.yaml", "--no-build", "--name", "extras")

    assert result.exit_code == 0, result.output
    [setup] = [c[-1] for c in docker.calls if c[:2] == ["docker", "exec"] and c[-3:-1] == ["sh", "-c"]]
    assert setup.split(" && ") == [
        "uv tool install --quiet --editable '/opt/ahl-packages/tool-mount[graph,db]'",
        "uv tool install --quiet --editable '/opt/ahl-packages/tool-copy[graph,db]'",
        "uv pip install --system --break-system-packages --quiet --editable '/opt/ahl-packages/lib-mount[graph,db]'",
        "uv pip install --system --break-system-packages --quiet --editable '/opt/ahl-packages/lib-copy[graph,db]'",
    ]


def test_a1_ac9_defaults_read_local_env_write_local_runs_and_build(tmp_path, monkeypatch, docker):
    (tmp_path / "workspace").mkdir()
    write_config(tmp_path / "config.yaml", **OPENROUTER, workspace="./workspace")
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=dotenv-key\n")
    monkeypatch.chdir(tmp_path)

    result = ahl_cli("up")

    assert result.exit_code == 0, result.output
    [run] = (tmp_path / "runs").iterdir()
    assert json.loads((run / "session.json").read_text())["run_id"] == run.name
    assert container_env(docker.launches()[-1], "OPENROUTER_API_KEY") == "dotenv-key"
    assert docker.builds() == [[
        "docker", "build", "-t", "agent-harness-lab:opencode", "-f", str(IMAGES / "opencode.Dockerfile"),
        "--build-arg", "HARNESS_VERSION=1.18.32", str(IMAGES),
    ]]


def test_a1_ac10_example_config_demonstrates_new_keys_and_loads(tmp_path):
    keys = tmp_path / "keys.env"
    keys.write_text("GEMINI_API_KEY=test-key\n")
    example = load_config(EXAMPLE, keys)
    assert example.root == EXAMPLE.parent
    assert example.workspace.path == EXAMPLE.parent / "projects/demo"

    lines, active = [], False
    for line in EXAMPLE.read_text().splitlines():
        if re.match(r"# (harness_version|mounts|network|env|packages):", line):
            active = True
        elif not line.startswith("#   "):
            active = False
        lines.append(line[2:] if active else line)
    raw = yaml.safe_load("\n".join(lines)) | {"harness": "opencode"}
    checkout = tmp_path / "checkout"
    for entry in raw["mounts"] + raw["packages"]:
        (checkout / entry["path"]).mkdir(parents=True, exist_ok=True)
    demo = checkout / "config.yaml"
    demo.write_text(yaml.safe_dump(raw))

    config = load_config(demo, keys)

    assert config.harness_version == raw["harness_version"]
    assert config.mounts
    assert [m.path for m in config.mounts] == [(checkout / e["path"]).resolve() for e in raw["mounts"]]
    assert config.network == raw["network"]
    assert config.env == raw["env"] and config.env
    assert [p.path for p in config.packages] == [(checkout / e["path"]).resolve() for e in raw["packages"]]
    assert any(p.extras for p in config.packages)
