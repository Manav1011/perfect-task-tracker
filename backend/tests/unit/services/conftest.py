"""Service test fixtures — fake WorkspaceRepository."""

from __future__ import annotations

import pytest

from backend.domain import Tree
from backend.domain.exceptions import NodeNotFoundError
from backend.services import WorkspaceService


class InMemoryWorkspaceRepository:
    """An in-memory WorkspaceRepository for fast service tests.

    Mirrors the LocalWorkspaceRepository's surface but with no disk
    I/O. Behaviour matches the real repo on the happy paths we test
    here; corruption tolerance and ordering subtleties live in the
    repository test suite, not here.
    """

    def __init__(self) -> None:
        self._tree = Tree()
        self._canvases: dict[str, str] = {}

    # ---- single-node reads -----------------------------------------

    def load_node(self, node_id):
        return self._tree.get(node_id)

    def load_children(self, node_id):
        return self._tree.children_of(node_id)

    # ---- tree reconstruction ---------------------------------------

    def load_tree(self) -> Tree:
        return self._tree

    # ---- writes -----------------------------------------------------

    def save_node(self, node, parent_id):
        self._tree.add(node)
        if parent_id is not None:
            self._tree.attach(node.id, parent_id)
        return self._tree.get(node.id)

    def rename_node(self, node_id, new_title):
        existing = self._tree.get(node_id)
        updated = existing.with_title(new_title)
        # Replace in-place (Tree uses an OrderedDict).
        self._tree._nodes[node_id] = updated  # noqa: SLF001 — test fake
        return updated

    def update_metadata(self, node_id, metadata):
        existing = self._tree.get(node_id)
        updated = existing.with_metadata(metadata)
        # Replace in-place (Tree uses an OrderedDict).
        self._tree._nodes[node_id] = updated  # noqa: SLF001 — test fake
        return updated

    def move_node(self, node_id, new_parent_id, position=None):
        existing = self._tree.get(node_id)
        if new_parent_id == node_id:
            from backend.domain.exceptions import InvalidParentError

            raise InvalidParentError("cannot move a node into itself")
        if new_parent_id is not None:
            # Cycle check.
            if self._is_descendant(node_id, new_parent_id):
                from backend.domain.exceptions import InvalidParentError

                raise InvalidParentError("cannot move a node into its own descendant")
            try:
                self._tree.detach(node_id)
            except NodeNotFoundError:
                pass
            self._tree.attach(node_id, new_parent_id)
            if position is not None:
                self._tree.move_child(new_parent_id, node_id, position)
        else:
            self._tree.detach(node_id)
        return self._tree.get(node_id)

    def delete_node(self, node_id):
        # Recursive delete.
        try:
            node = self._tree.get(node_id)
        except NodeNotFoundError as exc:
            raise NodeNotFoundError(node_id) from exc
        # Detach children first.
        for cid in list(node.children_ids):
            self.delete_node(cid)
        self._tree.remove(node_id)

    # ---- canvas -----------------------------------------------------

    def read_canvas(self, node_id):
        # Raise NodeNotFoundError if the Node doesn't exist.
        self._tree.get(node_id)
        if node_id not in self._canvases:
            raise NodeNotFoundError(node_id)
        return self._canvases[node_id]

    def write_canvas(self, node_id, content):
        # Confirm the Node exists, then write.
        self._tree.get(node_id)
        self._canvases[node_id] = content

    # ---- helpers ---------------------------------------------------

    def _is_descendant(self, ancestor_id, candidate_id) -> bool:
        ancestor = self._tree.get(ancestor_id)
        stack = list(ancestor.children_ids)
        seen: set = set()
        while stack:
            cid = stack.pop()
            if cid in seen:
                continue
            seen.add(cid)
            if cid == candidate_id:
                return True
            try:
                stack.extend(self._tree.get(cid).children_ids)
            except NodeNotFoundError:
                continue
        return False


@pytest.fixture
def fake_repo() -> InMemoryWorkspaceRepository:
    return InMemoryWorkspaceRepository()


@pytest.fixture
def service(fake_repo: InMemoryWorkspaceRepository) -> WorkspaceService:
    return WorkspaceService(fake_repo)
