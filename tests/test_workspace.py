from __future__ import annotations

from pathlib import Path

import pytest

from ahl.config import ConfigError
from ahl.workspace import parse_workspace, resolve_workspace


def test_parse_workspace_omitted_is_ephemeral_copy(tmp_path: Path):
    ws = parse_workspace(None, tmp_path)
    assert ws.path is None
    assert ws.install == "copy"


def test_parse_workspace_bare_string_defaults_to_copy(tmp_path: Path):
    ws = parse_workspace("./template", tmp_path)
    assert ws.path == tmp_path / "template"
    assert ws.install == "copy"


def test_parse_workspace_mount_requires_path(tmp_path: Path):
    with pytest.raises(ConfigError, match="requires 'path'"):
        parse_workspace({"install": "mount"}, tmp_path)


def test_resolve_workspace_copy_snapshots_template(tmp_path: Path):
    template = tmp_path / "template"
    template.mkdir()
    (template / "data.txt").write_text("seed data")
    run_dir = tmp_path / "run"

    ws = parse_workspace(str(template), tmp_path)
    resolved = resolve_workspace(run_dir, ws)

    assert resolved == run_dir / "workspace"
    assert (resolved / "data.txt").read_text() == "seed data"
    assert (template / "data.txt").read_text() == "seed data"  # template untouched


def test_resolve_workspace_no_template_is_empty(tmp_path: Path):
    ws = parse_workspace(None, tmp_path)
    resolved = resolve_workspace(tmp_path / "run", ws)
    assert resolved.is_dir()
    assert list(resolved.iterdir()) == []


def test_resolve_workspace_mount_uses_template_directly(tmp_path: Path):
    template = tmp_path / "live-project"
    ws = parse_workspace({"install": "mount", "path": str(template)}, tmp_path)
    resolved = resolve_workspace(tmp_path / "run", ws)
    assert resolved == template
    assert template.is_dir()  # created if missing


def test_resolve_workspace_resume_reuses_existing_copy_without_resnapshotting(tmp_path: Path):
    template = tmp_path / "template"
    template.mkdir()
    (template / "data.txt").write_text("seed data")
    run_dir = tmp_path / "run"

    ws = parse_workspace(str(template), tmp_path)
    resolved = resolve_workspace(run_dir, ws)
    (resolved / "progress.txt").write_text("work done in a prior run")

    resumed = resolve_workspace(run_dir, ws, resume=True)

    assert resumed == resolved
    assert (resumed / "progress.txt").read_text() == "work done in a prior run"
    assert (resumed / "data.txt").read_text() == "seed data"


def test_resolve_workspace_resume_without_prior_copy_errors(tmp_path: Path):
    template = tmp_path / "template"
    template.mkdir()
    ws = parse_workspace(str(template), tmp_path)

    with pytest.raises(ConfigError, match="cannot resume"):
        resolve_workspace(tmp_path / "run", ws, resume=True)
