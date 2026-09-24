from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

STUB = Path(__file__).parent / "stub_driver.py"
pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def opencode_image() -> None:
    subprocess.run([sys.executable, "-m", "ahl.cli", "build", "--harness", "opencode"], check=True)


@pytest.fixture
def project(tmp_path: Path, opencode_image) -> Path:
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"harness": "opencode", "provider": "anthropic", "model": "claude-sonnet-4-6"})
    )
    (tmp_path / "keys.env").write_text("ANTHROPIC_API_KEY=unused\n")
    (tmp_path / "prompt.md").write_text("stub turn")
    return tmp_path


@pytest.fixture
def leftovers():
    processes: list[subprocess.Popen] = []
    containers: list[str] = []
    yield processes, containers
    for process in processes:
        if process.poll() is None:
            process.kill()
            process.wait()
    for name in containers:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def stub_run(processes: list[subprocess.Popen], project: Path, turn: str, *flags: str) -> subprocess.Popen:
    process = subprocess.Popen(
        [sys.executable, str(STUB), "run", "-c", "config.yaml", "--turn", "prompt.md",
         "--env-file", "keys.env", "--no-build", *flags],
        cwd=project, env={**os.environ, "AHL_STUB_TURN": turn}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )
    processes.append(process)
    return process


def finished(process: subprocess.Popen) -> tuple[int, str]:
    output, _ = process.communicate(timeout=300)
    return process.returncode, output


def container_state(name: str) -> str | None:
    result = subprocess.run(
        ["docker", "container", "inspect", "--format", "{{.State.Status}}", name], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def test_a2_ac3_timeout_and_killed_runs_leave_no_blocking_container(project, leftovers):
    processes, containers = leftovers
    slow = stub_run(processes, project, "sleep 60", "--name", "slow", "--timeout", "5")
    printed = next((line for line in slow.stdout if line.startswith("Turn 1:")), "")
    run = project / "runs/slow"
    container, deadline = json.loads((run / "session.json").read_text())["container"], time.monotonic() + 60
    while container_state(container) is not None and time.monotonic() < deadline:
        os.killpg(slow.pid, signal.SIGINT)
    code, output = finished(slow)

    assert code == 124, printed + output
    outcome = json.loads((run / "result.json").read_text())
    assert (outcome["status"], outcome["reason"]["code"]) == ("timeout", "timeout")
    assert [(t["status"], t["reason"]["code"]) for t in outcome["turns"]] == [("timeout", "timeout")]
    assert container_state(container) is None and "is gone" not in output

    killed = stub_run(processes, project, "sleep 300", "--runs-dir", "first", "--name", "r")
    session, deadline = project / "first/r/session.json", time.monotonic() + 180
    while not (session.is_file() and (project / "first/r/turns/1/stdout.jsonl").is_file()):
        assert killed.poll() is None and time.monotonic() < deadline, killed.stdout.read()
        time.sleep(0.5)
    leftover = json.loads(session.read_text())["container"]
    containers.append(leftover)
    killed.send_signal(signal.SIGKILL)
    killed.wait()

    assert container_state(leftover) == "running"
    code, output = finished(stub_run(processes, project, "true", "--runs-dir", "second", "--name", "r"))
    assert code == 0, output
    assert json.loads((project / "second/r/session.json").read_text())["container"] != leftover
    subprocess.run(["docker", "rm", "-f", leftover], check=True, capture_output=True)
    assert container_state(leftover) is None


def test_a2_ac4_files_written_in_the_container_belong_to_the_caller(project, leftovers):
    writes = (
        "mkdir -p /workspace/out/deep /root/.local/share/opencode/cache"
        " && echo x > /workspace/out/deep/file && ln -s /workspace/out /workspace/link"
        " && echo y > /root/.config/opencode/state.json && touch /root/.local/share/opencode/cache/entry"
        " && chmod 700 /workspace/out /root/.local/share/opencode/cache && chmod 600 /workspace/out/deep/file"
    )
    code, output = finished(stub_run(leftovers[0], project, writes, "--name", "owned"))

    assert code == 0, output
    run = project / "runs/owned"
    assert (run / "workspace/out/deep/file").read_text() == "x\n"
    assert (run / "opencode/data/cache/entry").is_file() and (run / "workspace/link").is_symlink()
    owners = {(p.lstat().st_uid, p.lstat().st_gid) for p in [run, *run.rglob("*")]}
    assert owners == {(os.getuid(), os.getgid())}
    shutil.rmtree(run)
    assert not run.exists()
