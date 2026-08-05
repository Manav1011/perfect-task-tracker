"""Repository test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.filesystem import LocalFilesystem, WorkspaceRoot
from backend.repositories import LocalWorkspaceRepository


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceRoot:
    """A fresh, valid workspace root under tmp_path."""
    return WorkspaceRoot.open(tmp_path, create=True)


@pytest.fixture
def repo(workspace: WorkspaceRoot) -> LocalWorkspaceRepository:
    """A LocalWorkspaceRepository bound to a fresh temp workspace."""
    return LocalWorkspaceRepository(LocalFilesystem(workspace))