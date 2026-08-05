"""Filesystem layer — the only persistence adapter that touches the disk.

Public API:
    - Filesystem       — the protocol every adapter implements
    - LocalFilesystem  — the disk-backed implementation
    - WorkspaceRoot    — validated root path
    - *Error types     — see `backend.filesystem.exceptions`

Architectural role (TECH_SPEC §6):
    API → Services → Domain → Persistence (this layer).

The filesystem knows about the on-disk layout but not about the
service layer's mutation ordering, not about Postgres, not about HTTP.
"""

from backend.domain.exceptions import InvalidParentError
from backend.filesystem.exceptions import (
    CanvasMissingError,
    DuplicateNodeIdError,
    FilesystemError,
    InvalidNodeJSONError,
    NodeDirectoryMissingError,
    NodeMetadataMissingError,
    NodeNotFoundOnDiskError,
    SiblingNameCollisionError,
    WorkspaceRootError,
)
from backend.filesystem.impl.local_filesystem import LocalFilesystem
from backend.filesystem.protocol import Filesystem
from backend.filesystem.workspace_root import WorkspaceRoot

__all__ = [
    "CanvasMissingError",
    "DuplicateNodeIdError",
    "Filesystem",
    "FilesystemError",
    "InvalidNodeJSONError",
    "InvalidParentError",
    "LocalFilesystem",
    "NodeDirectoryMissingError",
    "NodeMetadataMissingError",
    "NodeNotFoundOnDiskError",
    "SiblingNameCollisionError",
    "WorkspaceRoot",
    "WorkspaceRootError",
]