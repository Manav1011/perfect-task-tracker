"""WorkspaceService — the application use-case surface.

The service layer's job is to *coordinate use cases*, not to *know*
how persistence works. Every public method takes domain inputs,
validates them against domain rules, calls exactly one repository
method, and returns a domain object. When something goes wrong,
it raises a service-layer exception (see `exceptions.py`).

Architectural rules (TECH_SPEC §8, ADR-0005, ADR-0006):

    - Depends only on `backend.domain` and the repository Protocol.
    - Does NOT import `backend.filesystem`, `pathlib`,
      `sqlalchemy`, `fastapi`, or any I/O library.
    - Calls at most one repository method per use case. Multi-step
      orchestration lives in the repository, never here.
    - Returns domain objects only — no dicts, no DTOs, no API models.
"""

from __future__ import annotations

from backend.domain import (
    Node,
    NodeId,
    NodeMetadata,
    NodeType,
    new_node_id,
)
from backend.domain.exceptions import (
    InvalidParentError,
    NodeNotFoundError,
    TreeCycleError,
)
from backend.domain.tree import Tree
from backend.repositories import WorkspaceRepository

from backend.services.exceptions import (
    CycleInMoveServiceError,
    InvalidRenameServiceError,
    NodeNotFoundServiceError,
    ParentNotFoundServiceError,
    StoryNotFoundServiceError,
    WorkspaceEmptyServiceError,
)


class WorkspaceService:
    """Application service for workspace operations.

    Constructor-injected with a `WorkspaceRepository` (the Protocol).
    Tests pass an in-memory fake; production wires up the
    `LocalWorkspaceRepository`.

    Future extension points (documented but not implemented):
        - Event publishing: every mutation method could emit a
          domain event (e.g. `node.created`) by passing an `EventBus`
          to the constructor. The shape of the bus is intentionally
          not pinned yet — Phase 4 will pick a concrete protocol
          after the indexing design is finalized.
        - Caching: a future `WorkspaceCache` could be injected to
          short-circuit `load_workspace_tree()` calls. The cache
          must be invalidated by the same repository operations
          that mutate state, so it would live alongside the
          repository, not here.
    """

    def __init__(self, repository: WorkspaceRepository) -> None:
        self._repository = repository

    # ---- create ----------------------------------------------------

    def create_story(self, title: str) -> Node:
        """Create a new root Story.

        Returns the persisted Node. The repository assigns an id,
        a slug-derived directory name, and appends the Story to the
        workspace root.

        Raises:
            ValueError: If `title` is empty.
        """
        self._validate_title(title)
        node = Node(
            id=new_node_id(),
            title=title,
            type=NodeType.STORY,
            metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
        )
        return self._repository.save_node(node, parent_id=None)

    def create_child(self, parent_id: NodeId, title: str, type_: NodeType = NodeType.TASK) -> Node:
        """Create a new Node under `parent_id`.

        Args:
            parent_id: id of the parent Node. Must exist.
            title: non-empty title.
            type_: NodeType; defaults to TASK because most children
                   of an existing story are actionable items.

        Returns:
            The persisted child Node.

        Raises:
            ValueError: If `title` is empty.
            NodeNotFoundServiceError: If `parent_id` doesn't exist.
        """
        self._validate_title(title)
        node = Node(
            id=new_node_id(),
            title=title,
            type=type_,
            metadata=NodeMetadata.from_dict({}, node_type=type_),
        )
        try:
            return self._repository.save_node(node, parent_id=parent_id)
        except InvalidParentError as exc:
            raise ParentNotFoundServiceError(str(parent_id)) from exc
        except NodeNotFoundError as exc:
            raise ParentNotFoundServiceError(str(parent_id)) from exc

    # ---- rename ----------------------------------------------------

    def rename_node(self, node_id: NodeId, new_title: str) -> Node:
        """Rename a Node.

        The id and parent are preserved (Invariant §6: UUIDs are
        stable for the Node's lifetime). The on-disk directory slug
        may change as a side effect.

        Raises:
            ValueError: If `new_title` is empty.
            NodeNotFoundServiceError: If the Node doesn't exist.
        """
        self._validate_title(new_title)
        try:
            return self._repository.rename_node(node_id, new_title)
        except NodeNotFoundError as exc:
            raise NodeNotFoundServiceError(str(node_id)) from exc

    # ---- move ------------------------------------------------------

    def move_node(
        self,
        node_id: NodeId,
        new_parent_id: NodeId | None,
        position: int | None = None,
    ) -> Node:
        """Move a Node under `new_parent_id` (or to root if None).

        The repository enforces cycle prevention. If the requested
        move would create one, we surface it as a typed service
        error so the API can render it cleanly.

        Args:
            node_id: id of the Node to move.
            new_parent_id: id of the new parent, or None to make
                           the Node a root Story.
            position: zero-based index in the new parent's
                      children list. None = append.

        Raises:
            NodeNotFoundServiceError: If `node_id` doesn't exist.
            ParentNotFoundServiceError: If `new_parent_id` doesn't exist.
            CycleInMoveServiceError: If the move would create a cycle.
        """
        try:
            return self._repository.move_node(
                node_id, new_parent_id=new_parent_id, position=position
            )
        except NodeNotFoundError as exc:
            raise NodeNotFoundServiceError(str(node_id)) from exc
        except InvalidParentError as exc:
            # Ambiguous between "parent missing" and "cycle."
            # Tree.is_self_descendant tests let us disambiguate.
            if self._would_cycle(node_id, new_parent_id):
                raise CycleInMoveServiceError(str(node_id)) from exc
            raise ParentNotFoundServiceError(str(new_parent_id)) from exc
        except TreeCycleError as exc:
            raise CycleInMoveServiceError(str(node_id)) from exc

    # ---- delete ----------------------------------------------------

    def delete_node(self, node_id: NodeId) -> None:
        """Recursively delete a Node and all of its descendants.

        Idempotent at the call-site level: deleting a non-existent
        Node raises NodeNotFoundServiceError so callers don't
        silently lose data. If you need "delete if exists"
        semantics, check via `load_workspace_tree()` first.

        Raises:
            NodeNotFoundServiceError: If the Node doesn't exist.
        """
        try:
            self._repository.delete_node(node_id)
        except NodeNotFoundError as exc:
            raise NodeNotFoundServiceError(str(node_id)) from exc

    # ---- read ------------------------------------------------------

    def load_workspace_tree(self) -> Tree:
        """Reconstruct the full workspace tree from disk.

        Returns:
            A `Tree` containing every loadable Node in the workspace.
            See ADR-0004 for what happens when corruption is
            encountered.

        Raises:
            WorkspaceEmptyServiceError: If the workspace has no
                root Stories. This is distinct from "empty tree" —
                we surface it because most use cases assume at
                least one Story exists.
        """
        tree = self._repository.load_tree()
        if not tree.roots():
            raise WorkspaceEmptyServiceError("workspace has no root stories")
        return tree

    def get_story(self, story_id: NodeId) -> Node:
        """Return a single Story (root) by id.

        Distinct from `load_node` because it enforces "must be a
        Story, must be a root" — two structural invariants a plain
        load wouldn't.

        Raises:
            StoryNotFoundServiceError: If the Story doesn't exist
                or isn't a root Story.
        """
        try:
            node = self._repository.load_node(story_id)
        except NodeNotFoundError as exc:
            raise StoryNotFoundServiceError(str(story_id)) from exc
        if node.parent_id is not None or node.type is not NodeType.STORY:
            raise StoryNotFoundServiceError(str(story_id))
        return node

    def get_node(self, node_id: NodeId) -> Node:
        """Return a single Node by id.

        Raises:
            NodeNotFoundServiceError: If the Node doesn't exist.
        """
        try:
            return self._repository.load_node(node_id)
        except NodeNotFoundError as exc:
            raise NodeNotFoundServiceError(str(node_id)) from exc

    def get_children(self, node_id: NodeId) -> list[Node]:
        """Return the ordered children of `node_id`.

        Raises:
            NodeNotFoundServiceError: If the Node doesn't exist.
        """
        try:
            return self._repository.load_children(node_id)
        except NodeNotFoundError as exc:
            raise NodeNotFoundServiceError(str(node_id)) from exc

    # ---- canvas ----------------------------------------------------

    def read_canvas(self, node_id: NodeId) -> str:
        """Read the canvas.md content for a Node.

        Raises:
            NodeNotFoundServiceError: If the Node doesn't exist.
        """
        try:
            return self._repository.read_canvas(node_id)
        except NodeNotFoundError as exc:
            raise NodeNotFoundServiceError(str(node_id)) from exc

    def write_canvas(self, node_id: NodeId, content: str) -> None:
        """Overwrite the canvas.md for a Node.

        Empty content is allowed (clears the canvas). The repository
        performs an atomic write.

        Raises:
            NodeNotFoundServiceError: If the Node doesn't exist.
        """
        try:
            self._repository.write_canvas(node_id, content)
        except NodeNotFoundError as exc:
            raise NodeNotFoundServiceError(str(node_id)) from exc

    def update_metadata(
        self, node_id: NodeId, key: str, value: object
    ) -> Node:
        """Set a metadata key on a Node, returning the updated Node.

        Validates the metadata against the Node's type. If the
        resulting metadata is invalid (e.g. setting `status` on a
        non-TASK Node), raises `ValueError` from the domain layer.

        Raises:
            NodeNotFoundServiceError: If the Node doesn't exist.
            ValueError: If the metadata is invalid for the Node's type.
        """
        try:
            node = self._repository.load_node(node_id)
        except NodeNotFoundError as exc:
            raise NodeNotFoundServiceError(str(node_id)) from exc
        # Domain validation happens inside NodeMetadata.with_field.
        new_metadata = node.metadata.with_field(key, value, node.type)
        # Persist via the dedicated `update_metadata` repository
        # method — it rewrites node.json in place without renaming
        # the directory. The previous implementation routed through
        # `rename_node(node_id, current_title)`, but rename
        # re-applies `with_title(...)` and dropped every other
        # field, including the new metadata (verify pass regression).
        try:
            return self._repository.update_metadata(node_id, new_metadata)
        except NodeNotFoundError as exc:
            raise NodeNotFoundServiceError(str(node_id)) from exc

    # ---- helpers ---------------------------------------------------

    @staticmethod
    def _validate_title(title: str) -> None:
        """Reject empty / whitespace-only titles.

        Mirrors the domain-level check in `Node.__post_init__`. We
        raise `ValueError` (a builtin) so callers can catch it with
        the standard library — it's a programmer/usage error, not a
        business failure.
        """
        if not title or not title.strip():
            raise ValueError("title must be a non-empty string")

    def _would_cycle(
        self, node_id: NodeId, new_parent_id: NodeId | None
    ) -> bool:
        """Best-effort check: would moving `node_id` under
        `new_parent_id` create a cycle?

        The repository also enforces this — this is purely a hint
        for the service-layer exception type. We use the
        reconstructed tree, so this is O(subtree size).
        """
        if new_parent_id is None:
            return False
        if new_parent_id == node_id:
            return True
        tree = self._repository.load_tree()
        try:
            node = tree.get(node_id)
        except NodeNotFoundError:
            return False
        # Walk descendants of node; if new_parent_id is one, it's a cycle.
        stack: list[Node] = [node]
        seen: set[NodeId] = set()
        while stack:
            current = stack.pop()
            for cid in current.children_ids:
                if cid in seen:
                    continue
                seen.add(cid)
                if cid == new_parent_id:
                    return True
                try:
                    stack.append(tree.get(cid))
                except NodeNotFoundError:
                    continue
        return False
