"""Filesystem protocol — the interface the rest of the app depends on.

Defined with `typing.Protocol` so any class with matching methods
satisfies it structurally — no inheritance required. This is what
makes the filesystem implementation replaceable (Architecture
Requirement §2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from backend.domain.node import Node, NodeId
from backend.filesystem.workspace_root import WorkspaceRoot


class Filesystem(Protocol):
    """All disk operations on a workspace.

    Implementations:
        - LocalFilesystem (real disk; default).
        - InMemoryFilesystem (tests; future).
        - RemoteFilesystem (future, out of scope for Phase 1.2).

    Every method returns a domain `Node` (or path / content) — never
    a raw dict (Architecture Requirement §10).
    """

    @property
    def root(self) -> WorkspaceRoot:
        """The validated workspace root this filesystem operates on."""
        ...

    # ---- Node CRUD ---------------------------------------------------

    def load_node(self, node_id: NodeId) -> Node:
        """Load a Node by id. Raises NodeNotFoundOnDiskError if missing."""
        ...

    def create_node(
        self,
        node: Node,
        parent_id: NodeId | None,
    ) -> Node:
        """Create the Node on disk under `parent_id` (None = root).

        The Node's id is taken from the passed-in entity (so the
        service layer mints ids via the domain). The directory name
        is a slug derived from the title; the JSON `id` is the source
        of truth for identity.

        Raises:
            SiblingNameCollisionError: If a directory with the slugged
                                       name already exists under the parent.
            InvalidParentError: If `parent_id` does not exist on disk.
        """
        ...

    def rename_node(self, node_id: NodeId, new_title: str) -> Node:
        """Rename the Node on disk.

        Updates title in node.json and renames the directory to the
        new slug. UUID is preserved (Invariant §6).
        """
        ...

    def write_node(self, node: Node) -> Node:
        """Rewrite node.json in place with the given Node entity.

        Used for partial updates that don't change the directory
        name (metadata-only edits). The directory is not renamed —
        callers MUST pass a Node whose `title` matches the existing
        directory slug (the slug is derived from the title).

        Raises:
            NodeNotFoundOnDiskError: If the Node doesn't exist.
        """
        ...

    def move_node(
        self,
        node_id: NodeId,
        new_parent_id: NodeId | None,
        position: int | None = None,
    ) -> Node:
        """Move the Node under a new parent. Updates node.json + children lists."""
        ...

    def delete_node(self, node_id: NodeId) -> None:
        """Recursively delete the Node and its descendants from disk."""
        ...

    # ---- Reads -------------------------------------------------------

    def list_children(self, node_id: NodeId) -> list[Node]:
        """Return the ordered children of `node_id`."""
        ...

    def walk(self) -> list[Node]:
        """Return every Node in the workspace, in arbitrary order."""
        ...

    # ---- Canvas ------------------------------------------------------

    def read_canvas(self, node_id: NodeId) -> str:
        """Read the canvas.md content for the Node."""
        ...

    def write_canvas(self, node_id: NodeId, content: str) -> None:
        """Atomically overwrite canvas.md for the Node."""
        ...

    # ---- Paths -------------------------------------------------------

    def node_dir(self, node_id: NodeId) -> Path:
        """Absolute path to the Node's directory.

        Useful for diagnostics and for external tools that want to
        know where a Node lives.
        """
        ...