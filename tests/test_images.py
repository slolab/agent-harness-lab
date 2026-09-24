from __future__ import annotations

import json
import os
import pty
import re
import select
import subprocess
import sys
import time
import urllib.request
from importlib import resources
from pathlib import Path

import pytest
import yaml

from ahl.config import SUPPORTED_HARNESSES

IMAGES = resources.files("ahl") / "images"
EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+")
PACKAGES = {"claude": "@anthropic-ai/claude-code", "opencode": "opencode-ai"}
PINNED = {"claude": "2.1.273", "opencode": "1.18.31"}


def resolved_dockerfile(harness: str) -> tuple[dict[str, str], str]:
    text = (IMAGES / f"{harness}.Dockerfile").read_text()
    defaults = dict(re.findall(r"^ARG (\w+)=(\S+)$", text, re.M))
    return defaults, re.sub(r"\$\{(\w+)\}", lambda m: defaults.get(m[1], m[0]), text)


def test_a1_ac4_dockerfiles_hard_code_no_versions_and_label_every_image():
    harnesses = sorted(p.name.removesuffix(".Dockerfile") for p in IMAGES.iterdir() if p.name.endswith(".Dockerfile"))
    assert harnesses == sorted(SUPPORTED_HARNESSES)

    for harness in harnesses:
        defaults, text = resolved_dockerfile(harness)
        assert not [arg for arg, value in defaults.items() if arg != "NODE_VERSION" and EXACT_VERSION.search(value)]
        assert set(re.findall(r"ghcr\.io/astral-sh/uv:(\S+)", text)) == {"latest"}, harness
        raw = (IMAGES / f"{harness}.Dockerfile").read_text()
        labels = dict(
            pair.split("=", 1)
            for line in re.findall(r"^LABEL (.+)$", raw, re.M)
            for pair in line.split()
        )
        expected = {"ahl.harness": harness}
        if harness in {*PACKAGES, "deepseek"}:
            expected["ahl.harness.version"] = "${HARNESS_VERSION:?required}"
        assert labels == expected, harness


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True).stdout


def installed_version(image: str, harness: str) -> str:
    return EXACT_VERSION.search(docker("run", "--rm", image, harness, "--version"))[0]


def current_release(harness: str) -> str:
    with urllib.request.urlopen(f"https://registry.npmjs.org/{PACKAGES[harness]}/latest", timeout=30) as response:
        return json.load(response)["version"]


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
def test_a1_ac3_ac4_build_outside_checkout_installs_pinned_or_current_version(tmp_path, harness):
    image = f"agent-harness-lab:{harness}"
    (tmp_path / "pinned.yaml").write_text(yaml.safe_dump({"harness": harness, "harness_version": PINNED[harness]}))
    direct = subprocess.run(
        ["docker", "build", "-f", str(IMAGES / f"{harness}.Dockerfile"), str(IMAGES)], capture_output=True, text=True
    )
    assert direct.returncode != 0 and "HARNESS_VERSION" in direct.stderr, direct.stderr[-3000:]

    def build(*flags: str) -> tuple[str, str]:
        result = ahl_process("build", *flags, cwd=tmp_path)
        assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
        labels = json.loads(docker("image", "inspect", "--format", "{{json .Config.Labels}}", image))
        assert labels == {"ahl.harness": harness, "ahl.harness.version": installed_version(image, harness)}
        return docker("image", "inspect", "--format", "{{.Id}}", image), labels["ahl.harness.version"]

    pinned = build("-c", "pinned.yaml")
    assert build("-c", "pinned.yaml") == pinned and pinned[1] == PINNED[harness]
    before = current_release(harness)
    _, current = build("--harness", harness)
    assert current in {before, current_release(harness)}


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
    package = {"name": "extras-lib", "install": "mount", "path": "./extras-lib", "extras": ["graph"]}
    probe = "python3 -c 'import iniconfig; print(\"imported-\" + iniconfig.__name__)'"

    for pin, harness_version in [("pinned", {"harness_version": PINNED["claude"]}), ("current", {})]:
        config = {"harness": "claude", "provider": "anthropic", "packages": [package], **harness_version}
        (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
        before = current_release("claude")
        recorded = set()
        for name, flags in [(f"{pin}-built", []), (f"{pin}-prebuilt", ["--no-build"])]:
            code, output = ahl_up_in_pty("--name", name, *flags, cwd=tmp_path, command=probe)

            assert code == 0, output[-3000:]
            assert "imported-iniconfig" in output, output[-3000:]
            image_id = docker("image", "inspect", "--format", "{{.Id}}", "agent-harness-lab:claude").strip()
            session = json.loads((tmp_path / "runs" / name / "session.json").read_text())
            installed = installed_version(image_id, "claude")
            assert session["image"] == {"name": "agent-harness-lab:claude", "id": image_id, "harness_version": installed}
            recorded.add(installed)

        expected = {PINNED["claude"]} if harness_version else {before, current_release("claude")}
        assert len(recorded) == 1 and recorded <= expected
