"""WorkspaceRepository — the contract between the service layer and persistence.

Sits between the service layer and the Filesystem Protocol (Phase 1.2).
Responsibilities:

    - Translate filesystem results into domain objects.
    - Reconstruct the tree (the set of Nodes + their parent/child
      wiring) deterministically from disk.
    - Coordinate multi-step writes (e.g. delete node + rewire parent)
      so the service layer sees one logical operation.
    - Re-raise filesystem errors as repository errors where
      appropriate, so the service layer doesn't depend on the
      filesystem module.

Not responsibilities:

    - Business rules. (Domain owns them.)
    - HTTP / FastAPI. (API layer.)
    - PostgreSQL indexing. (Future.)
    - The in-memory runtime tree. (Workspace module, Phase 3+.)

Methods return only domain types (Node, Tree). No filesystem paths,
no dicts.
"""

from __future__ import annotations

from typing import Protocol

from backend.domain.node import Node, NodeId
from backend.domain.node import NodeMetadata
from backend.domain.tree import Tree


class WorkspaceRepository(Protocol):
    """The persistence contract the service layer depends on."""

    # ---- single-node reads -----------------------------------------

    def load_node(self, node_id: NodeId) -> Node:
        """Return the Node for `node_id` or raise a domain error."""
        ...

    def load_children(self, node_id: NodeId) -> list[Node]:
        """Return the ordered children of `node_id`."""
        ...

    # ---- tree reconstruction ---------------------------------------

    def load_tree(self) -> Tree:
        """Reconstruct the full tree from disk.

        Deterministic: order is preserved by each Node's
        `children_ids` list (which the filesystem serializes in
        order). The resulting Tree has every Node exactly once.
        """
        ...

    # ---- writes -----------------------------------------------------

    def save_node(self, node: Node, parent_id: NodeId | None) -> Node:
        """Create or update a Node on disk under `parent_id`.

        If `node.parent_id` is None and `parent_id` is provided, the
        new parent is wired in. Returns the persisted Node.
        """
        ...

    def rename_node(self, node_id: NodeId, new_title: str) -> Node:
        """Rename a Node on disk. UUID is preserved."""
        ...

    def update_metadata(
        self, node_id: NodeId, metadata: "NodeMetadata"
    ) -> Node:
        """Update a Node's metadata in place. UUID is preserved.

        Implementation note: the on-disk layout is one file
        (node.json) for the entity + one file (canvas.md) for
        the canvas. Metadata lives inside node.json; this
        method rewrites node.json without renaming the directory
        or touching canvas.md.

        Returns the updated Node with the new metadata applied.
        """
        ...

    def move_node(
        self,
        node_id: NodeId,
        new_parent_id: NodeId | None,
        position: int | None = None,
    ) -> Node:
        """Move a Node under a new parent. Refuses cycles."""
        ...

    def delete_node(self, node_id: NodeId) -> None:
        """Recursively delete a Node and its descendants."""
        ...

    # ---- canvas -----------------------------------------------------

    def read_canvas(self, node_id: NodeId) -> str:
        """Read canvas.md for the Node."""
        ...

    def write_canvas(self, node_id: NodeId, content: str) -> None:
        """Atomically overwrite canvas.md."""
        ...

    # ---- TODO(phase-4): dedicated update_metadata --------------------
    # Today the Service layer updates metadata by calling
    # `rename_node(node_id, current_title)` — a workaround because
    # the on-disk layout is one file (node.json) and we don't want
    # a partial-update path that bypasses the repository's
    # full-entity contract.
    #
    # The right shape is a dedicated method here:
    #
    #   def update_metadata(
    #       self, node_id: NodeId, metadata: NodeMetadata,
    #   ) -> Node: ...
    #
    # …which writes only node.json (no directory rename, no canvas
    # touch). The Service should call *this* method instead of
    # round-tripping through rename_node. Scheduled for the
    # persistence/indexing phase (TECH_SPEC §18, technical debt).