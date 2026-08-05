"""Node — the universal entity.

Every Node has:
    - a stable UUID (NodeId) for its entire lifetime (Invariant §6),
    - a title (mutable, display-only),
    - a NodeType (extensible discriminator),
    - an optional parent reference (None for root Stories),
    - an ordered list of child references,
    - a NodeMetadata payload,
    - an optional canvas filename (logical name only — no path).

Nodes are immutable from the outside; structural changes go through
the `with_*` methods or the `Tree` class. The Tree owns the
parent/child wiring, so the Node itself only enforces the invariants
that belong to a single node (UUID stability, no self-parenting).
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field, replace
from typing import NewType

from backend.domain.enums import NodeType
from backend.domain.exceptions import InvalidParentError
from backend.domain.metadata import NodeMetadata

# NodeId is a NewType for type-system clarity — it's still a plain UUID
# at runtime. We deliberately do NOT alias to `uuid.UUID` because we
# want the boundary (filesystem, API, Postgres) to pass strings and
# not assume UUID4 specifically.
NodeId = NewType("NodeId", str)


def new_node_id() -> NodeId:
    """Mint a fresh NodeId.

    Uses `uuid.uuid4()` for global uniqueness without leaking host info.
    UUIDs are never reused (Invariant §6).
    """
    return NodeId(str(_uuid.uuid4()))


@dataclass(slots=True)
class Node:
    """A single Node in the tree.

    `parent_id` is None only for root Stories. `children_ids` is an
    ordered list. `canvas` is the logical filename of the default
    Markdown canvas attached to this Node, or None for pure structural
    nodes.
    """

    id: NodeId
    title: str
    type: NodeType
    metadata: NodeMetadata
    parent_id: NodeId | None = None
    children_ids: list[NodeId] = field(default_factory=list)
    canvas: str | None = None

    def __post_init__(self) -> None:
        # Self-parenting is the most basic invariant; catch it here so
        # the rest of the code can rely on it.
        if self.parent_id is not None and self.parent_id == self.id:
            raise InvalidParentError("a node cannot be its own parent")
        # Title cannot be empty — empty folders break the filesystem
        # representation and have no display value.
        if not self.title or not self.title.strip():
            raise ValueError("node title must be a non-empty string")

    def with_title(self, title: str) -> "Node":
        """Return a copy with a new title."""
        if not title or not title.strip():
            raise ValueError("node title must be a non-empty string")
        return replace(self, title=title)

    def with_parent(self, parent_id: NodeId | None) -> "Node":
        """Return a copy with a new parent reference.

        Cycle detection is the Tree's job — a single Node can't know
        whether its new parent is itself a descendant.
        """
        return replace(self, parent_id=parent_id)

    def with_canvas(self, canvas: str | None) -> "Node":
        """Return a copy with a new canvas filename."""
        return replace(self, canvas=canvas)

    def with_metadata(self, metadata: NodeMetadata) -> "Node":
        """Return a copy with new metadata."""
        return replace(self, metadata=metadata)

    def with_children(self, children_ids: list[NodeId]) -> "Node":
        """Return a copy with a new children list (ordered)."""
        # Defensive copy so callers can't mutate our list later.
        return replace(self, children_ids=list(children_ids))

    def is_root(self) -> bool:
        """True iff this Node has no parent (a root Story)."""
        return self.parent_id is None