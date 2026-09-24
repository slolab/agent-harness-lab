from __future__ import annotations

import json
import os
import pty
import re
import select
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path

import pytest
import yaml

from ahl.config import SUPPORTED_HARNESSES

IMAGES = resources.files("ahl") / "images"
EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+")
PINNED = {
    "claude": ("CLAUDE_CODE_VERSION", "@anthropic-ai/claude-code"),
    "opencode": ("OPENCODE_VERSION", "opencode-ai"),
}


def resolved_dockerfile(harness: str) -> tuple[dict[str, str], str]:
    text = (IMAGES / f"{harness}.Dockerfile").read_text()
    defaults = dict(re.findall(r"^ARG (\w+)=(\S+)$", text, re.M))
    return defaults, re.sub(r"\$\{(\w+)\}", lambda m: defaults.get(m[1], m[0]), text)


def test_a1_ac4_dockerfiles_pin_versions_and_label_every_image():
    lock = json.loads((IMAGES / "deepseek/package-lock.json").read_text())
    dsh_version = lock["packages"]["node_modules/@deepseek-ai/dsh"]["version"]
    harnesses = sorted(p.name.removesuffix(".Dockerfile") for p in IMAGES.iterdir() if p.name.endswith(".Dockerfile"))
    assert harnesses == sorted(SUPPORTED_HARNESSES)

    for harness in harnesses:
        defaults, text = resolved_dockerfile(harness)
        uv_tags = re.findall(r"ghcr\.io/astral-sh/uv:(\S+)", text)
        assert uv_tags and all(EXACT_VERSION.fullmatch(tag) for tag in uv_tags), harness
        labels = dict(
            pair.split("=", 1)
            for line in re.findall(r"^LABEL (.+)$", text, re.M)
            for pair in line.split()
        )
        expected = {"ahl.harness": harness}
        if harness in PINNED:
            arg, package = PINNED[harness]
            version = defaults[arg]
            assert EXACT_VERSION.fullmatch(version), harness
            assert f"{package}@{version}" in text, harness
            expected["ahl.harness.version"] = version
        if harness == "deepseek":
            expected["ahl.harness.version"] = dsh_version
        assert labels == expected, harness


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True).stdout


def ahl_process(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "ahl.cli", *args], cwd=cwd, capture_output=True, text=True)


def ahl_up_in_pty(*args: str, cwd: Path, command: str, timeout: float = 900) -> tuple[int, str]:
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-m", "ahl.cli", "up", *args],
        cwd=cwd, stdin=slave, stdout=slave, stderr=slave, close_fds=True,
    )
    os.close(slave)
    output, sent = b"", False
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 1)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
                if not sent and b"/workspace# " in output:
                    os.write(master, f"{command}\nexit\n".encode())
                    sent = True
            elif proc.poll() is not None:
                break
        else:
            proc.kill()
    finally:
        os.close(master)
    return proc.wait(), output.decode(errors="replace")


@pytest.mark.docker
@pytest.mark.parametrize("harness", ["claude", "opencode"])
def test_a1_ac3_ac4_build_outside_checkout_labels_installed_version(tmp_path, harness):
    image = f"agent-harness-lab:{harness}"

    result = ahl_process("build", "--harness", harness, cwd=tmp_path)

    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    labels = json.loads(docker("image", "inspect", "--format", "{{json .Config.Labels}}", image))
    installed = docker("run", "--rm", image, harness, "--version")
    assert labels["ahl.harness"] == harness
    assert labels["ahl.harness.version"] == EXACT_VERSION.search(installed)[0]


@pytest.mark.docker
def test_a1_ac5_ac8_up_records_started_image_and_installs_extras(tmp_path):
    lib = tmp_path / "extras-lib"
    (lib / "src/extras_lib").mkdir(parents=True)
    (lib / "src/extras_lib/__init__.py").write_text("")
    (lib / "pyproject.toml").write_text(
        "[project]\nname = 'extras-lib'\nversion = '0.1.0'\n"
        "[project.optional-dependencies]\ngraph = ['iniconfig']\n"
        "[build-system]\nrequires = ['hatchling']\nbuild-backend = 'hatchling.build'\n"
    )
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "harness": "claude",
        "provider": "anthropic",
        "packages": [{"name": "extras-lib", "install": "mount", "path": "./extras-lib", "extras": ["graph"]}],
    }))
    probe = "python3 -c 'import iniconfig; print(\"imported-\" + iniconfig.__name__)'"

    for name, flags in [("built", []), ("prebuilt", ["--no-build"])]:
        code, output = ahl_up_in_pty("--name", name, *flags, cwd=tmp_path, command=probe)

        assert code == 0, output[-3000:]
        assert "imported-iniconfig" in output, output[-3000:]
        [inspected] = json.loads(docker("image", "inspect", "agent-harness-lab:claude"))
        session = json.loads((tmp_path / "runs" / name / "session.json").read_text())
        assert session["image"] == {
            "name": "agent-harness-lab:claude",
            "id": inspected["Id"],
            "harness_version": inspected["Config"]["Labels"]["ahl.harness.version"],
        }
