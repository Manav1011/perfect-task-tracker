"""Tree — the in-memory collection of Nodes.

The Tree owns the structural invariants that span more than one Node:
    - parent_id references resolve to existing Nodes,
    - no Node is its own ancestor (cycle prevention),
    - child order is preserved across structural changes.

The Tree is the runtime mirror of the workspace on disk (see
TECH_SPEC §4). It is *not* persistence — the filesystem layer and the
Postgres index are. This class only enforces structural rules.
"""

from __future__ import annotations

from collections import OrderedDict

from backend.domain.exceptions import (
    DuplicateNodeIdError,
    InvalidParentError,
    NodeNotFoundError,
    TreeCycleError,
)
from backend.domain.node import Node, NodeId


class Tree:
    """Ordered collection of Nodes with structural invariants.

    `nodes` is keyed by NodeId. Insertion order is not guaranteed
    semantically (children are ordered via `Node.children_ids`), but we
    use an OrderedDict so debug iteration is stable.
    """

    __slots__ = ("_nodes",)

    def __init__(self) -> None:
        self._nodes: "OrderedDict[NodeId, Node]" = OrderedDict()

    # ---- read API ----------------------------------------------------

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, node_id: object) -> bool:
        # NodeId is a NewType over str; runtime check is `isinstance(str)`.
        return isinstance(node_id, str) and node_id in self._nodes

    def get(self, node_id: NodeId) -> Node:
        """Return the Node or raise NodeNotFoundError."""
        try:
            return self._nodes[node_id]
        except KeyError as exc:
            raise NodeNotFoundError(node_id) from exc

    def try_get(self, node_id: NodeId) -> Node | None:
        """Return the Node or None — never raises."""
        return self._nodes.get(node_id)

    def roots(self) -> list[Node]:
        """Return all root Nodes (parent_id is None), preserving order."""
        return [n for n in self._nodes.values() if n.parent_id is None]

    def children_of(self, node_id: NodeId) -> list[Node]:
        """Return the ordered children of `node_id`."""
        parent = self.get(node_id)
        children: list[Node] = []
        for child_id in parent.children_ids:
            children.append(self.get(child_id))
        return children

    def all_nodes(self) -> list[Node]:
        """Return every Node in insertion order."""
        return list(self._nodes.values())

    def subtree(self, node_id: NodeId) -> list[Node]:
        """Return `node_id` and all of its descendants.

        Ordering is breadth-first, deterministic (by NodeId).
        Used by the incremental index synchroniser when a
        `move_node` operation must recompute paths/story_ids for
        the moved subtree. The result includes `node_id` itself
        so callers can iterate without an extra lookup.

        Raises `NodeNotFoundError` if `node_id` is absent.
        """
        if node_id not in self._nodes:
            raise NodeNotFoundError(node_id)
        out: list[Node] = [self._nodes[node_id]]
        frontier: list[NodeId] = list(self._nodes[node_id].children_ids)
        while frontier:
            next_frontier: list[NodeId] = []
            for cid in frontier:
                child = self._nodes.get(cid)
                if child is None:
                    # Defensive: stale children reference.
                    continue
                out.append(child)
                next_frontier.extend(child.children_ids)
            frontier = next_frontier
        return out

    # ---- write API ---------------------------------------------------

    def add(self, node: Node) -> None:
        """Insert a new Node. Refuses if the id already exists.

        Parent linkage is the caller's responsibility — pass a Node
        with `parent_id` set to an existing Node's id (or None for a
        root). Use `attach` to also wire the child into the parent's
        `children_ids`.
        """
        if node.id in self._nodes:
            raise DuplicateNodeIdError(node.id)
        if node.parent_id is not None and node.parent_id not in self._nodes:
            raise InvalidParentError(f"parent {node.parent_id} does not exist")
        self._nodes[node.id] = node

    def attach(self, node_id: NodeId, parent_id: NodeId) -> None:
        """Set node.parent_id and append to parent's children_ids.

        Refuses if it would create a cycle. Order in the parent's
        children list is *append* — for ordered inserts use
        `insert_child`.
        """
        node = self.get(node_id)
        if parent_id not in self._nodes:
            raise InvalidParentError(f"parent {parent_id} does not exist")
        parent = self._nodes[parent_id]
        self._assert_no_cycle(node_id, parent_id)
        # Update both sides atomically.
        self._nodes[node_id] = node.with_parent(parent_id)
        if node_id not in parent.children_ids:
            self._nodes[parent_id] = parent.with_children(
                [*parent.children_ids, node_id]
            )

    def detach(self, node_id: NodeId) -> None:
        """Remove `node_id` from its parent's children list and clear its parent.

        Does not delete the Node from the tree — use `remove` for that.
        """
        node = self.get(node_id)
        if node.parent_id is not None:
            parent_id = node.parent_id
            parent = self.get(parent_id)
            self._nodes[parent_id] = parent.with_children(
                [cid for cid in parent.children_ids if cid != node_id]
            )
        self._nodes[node_id] = node.with_parent(None)

    def insert_child(
        self,
        parent_id: NodeId,
        child_id: NodeId,
        position: int | None = None,
    ) -> None:
        """Insert `child_id` under `parent_id` at `position` (default: end)."""
        self.attach(child_id, parent_id)
        if position is not None:
            self.move_child(parent_id, child_id, position)

    def move_child(self, parent_id: NodeId, child_id: NodeId, position: int) -> None:
        """Reposition `child_id` within `parent_id`'s children list."""
        parent = self.get(parent_id)
        if child_id not in parent.children_ids:
            raise InvalidParentError(
                f"{child_id} is not a child of {parent_id}"
            )
        children = [cid for cid in parent.children_ids if cid != child_id]
        position = max(0, min(position, len(children)))
        children.insert(position, child_id)
        self._nodes[parent_id] = parent.with_children(children)

    def remove(self, node_id: NodeId) -> None:
        """Delete `node_id` and detach it from its parent.

        Does not cascade — caller is responsible for removing descendants
        first. The Tree's policy is "one node at a time" so callers must
        be explicit about subtrees.
        """
        self.detach(node_id)
        del self._nodes[node_id]

    # ---- helpers -----------------------------------------------------

    def replace_children(self, node_id: NodeId, children_ids: list[NodeId]) -> None:
        """Replace `node_id`'s `children_ids` wholesale.

        Intended for reconstruction from disk: the persistence layer
        may have authoritative ordering that doesn't match what
        `attach` produces, and may have stale ids that need to be
        pruned. This is the only way to set the list to an arbitrary
        ordering without going through `insert_child` one-by-one.

        Does NOT touch the children's `parent_id` field — that's the
        caller's responsibility.
        """
        node = self.get(node_id)
        self._nodes[node_id] = node.with_children(list(children_ids))

    def _assert_no_cycle(self, node_id: NodeId, new_parent_id: NodeId) -> None:
        """Raise TreeCycleError if `new_parent_id` is `node_id` or a descendant."""
        if node_id == new_parent_id:
            raise TreeCycleError(node_id)
        cursor: NodeId | None = new_parent_id
        seen: set[NodeId] = set()
        while cursor is not None:
            if cursor == node_id:
                raise TreeCycleError(node_id)
            if cursor in seen:
                # Defensive: an existing cycle in the tree shouldn't
                # happen, but refuse to loop forever if it does.
                break
            seen.add(cursor)
            cursor = self._nodes[cursor].parent_id