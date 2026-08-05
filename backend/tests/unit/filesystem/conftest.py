"""Filesystem test fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.filesystem import LocalFilesystem, WorkspaceRoot


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[WorkspaceRoot]:
    """A fresh, valid workspace root under tmp_path."""
    root = WorkspaceRoot.open(tmp_path, create=True)
    yield root


@pytest.fixture
def fs(workspace: WorkspaceRoot) -> LocalFilesystem:
    """A LocalFilesystem bound to the temp workspace."""
    return LocalFilesystem(workspace)