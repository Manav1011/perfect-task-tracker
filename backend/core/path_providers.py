"""Filesystem-backed adapter implementations for index-layer Protocols.

These live in `backend.core` (not `backend.index`) because
they need to import the concrete `Filesystem` class. The
index layer never reaches them; the coordinator accepts them
as Protocol values.

The split keeps the index layer free of filesystem imports
(see `backend/tests/index/test_isolation.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.filesystem import Filesystem


class FilesystemDirPathProvider:
    """Filesystem-backed `FilesystemPathProvider`.

    Returns the relative path of each Node's directory under
    the workspace root (forward-slash normalised for cross-OS
    stability).
    """

    def __init__(self, fs: "Filesystem") -> None:
        self._fs = fs

    def path_for(self, node_id) -> str:
        directory = self._fs.node_dir(node_id)
        rel = directory.relative_to(self._fs.root.path)
        return str(rel).replace("\\", "/")