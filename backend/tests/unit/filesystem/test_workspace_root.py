"""WorkspaceRoot tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.filesystem import WorkspaceRoot, WorkspaceRootError


def test_open_existing(tmp_path: Path) -> None:
    (tmp_path / ".ptt").mkdir()
    root = WorkspaceRoot.open(tmp_path)
    assert root.path == tmp_path.resolve()
    assert root.config_dir == tmp_path.resolve() / ".ptt"


def test_open_missing_root(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceRootError):
        WorkspaceRoot.open(tmp_path / "does-not-exist")


def test_open_creates_marker_when_asked(tmp_path: Path) -> None:
    root = WorkspaceRoot.open(tmp_path, create=True)
    assert root.config_dir.exists()


def test_open_rejects_missing_marker(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceRootError):
        WorkspaceRoot.open(tmp_path)


def test_child_path_under_root(workspace: WorkspaceRoot) -> None:
    p = workspace.child("a", "b")
    assert str(p).startswith(str(workspace.path))


def test_child_rejects_escape(workspace: WorkspaceRoot) -> None:
    with pytest.raises(WorkspaceRootError):
        workspace.child("..", "..", "etc")