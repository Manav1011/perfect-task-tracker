"""Filesystem-layer exceptions.

These describe conditions observed while interacting with the disk
(missing files, invalid JSON, duplicate ids in a single workspace).
They are distinct from `backend.domain.exceptions.*` because the
domain is supposed to be unaware of the disk — a `FilesystemError`
becomes a domain-level concern only after the filesystem layer
re-raises it as a typed domain exception (a future service-layer
responsibility, not Phase 1.2's).

For now, callers catch by type. The mapping to HTTP status codes is an
API-layer concern (future phase).
"""

from __future__ import annotations


class FilesystemError(Exception):
    """Base for filesystem-layer errors."""


class WorkspaceRootError(FilesystemError):
    """The supplied path is not a valid workspace root."""


class NodeDirectoryMissingError(FilesystemError):
    """A Node directory was expected at a path that does not exist."""

    def __init__(self, path: str) -> None:
        super().__init__(f"node directory missing: {path}")
        self.path = path


class NodeMetadataMissingError(FilesystemError):
    """A Node's `node.json` was expected but not found."""

    def __init__(self, node_dir: str) -> None:
        super().__init__(f"node.json missing in {node_dir}")
        self.node_dir = node_dir


class CanvasMissingError(FilesystemError):
    """A Node's `canvas.md` was expected but not found."""

    def __init__(self, node_dir: str) -> None:
        super().__init__(f"canvas.md missing in {node_dir}")
        self.node_dir = node_dir


class InvalidNodeJSONError(FilesystemError):
    """`node.json` exists but could not be parsed."""

    def __init__(self, node_dir: str, reason: str) -> None:
        super().__init__(f"invalid node.json in {node_dir}: {reason}")
        self.node_dir = node_dir
        self.reason = reason


class DuplicateNodeIdError(FilesystemError):
    """Two Node directories on disk share the same UUID."""

    def __init__(self, node_id: str, paths: list[str]) -> None:
        super().__init__(
            f"duplicate node id {node_id} found at: {', '.join(paths)}"
        )
        self.node_id = node_id
        self.paths = paths


class SiblingNameCollisionError(FilesystemError):
    """A create/rename would collide with an existing sibling directory."""

    def __init__(self, name: str) -> None:
        super().__init__(f"sibling name already exists: {name}")
        self.name = name


class NodeNotFoundOnDiskError(FilesystemError):
    """A Node id is not represented anywhere under the workspace root."""

    def __init__(self, node_id: str) -> None:
        super().__init__(f"node id not found on disk: {node_id}")
        self.node_id = node_id