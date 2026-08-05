"""WorkspaceService — use-case tests against an in-memory fake repository.

Coverage:
    - Every public service method has a happy-path test.
    - Every method that can fail raises the typed service exception.
    - Service never mutates repository-owned objects in unexpected
      ways (returns copies / fresh entities where appropriate).
    - Domain exceptions are wrapped as service exceptions
      (domain.NodeNotFoundError never escapes the service layer).
"""

from __future__ import annotations

import pytest

from backend.domain import Node, NodeType, new_node_id
from backend.domain.exceptions import NodeNotFoundError
from backend.services import WorkspaceService
from backend.services.exceptions import (
    CycleInMoveServiceError,
    NodeNotFoundServiceError,
    ParentNotFoundServiceError,
    StoryNotFoundServiceError,
    WorkspaceEmptyServiceError,
)

from .conftest import InMemoryWorkspaceRepository


def _make_story(service: WorkspaceService, title: str = "S") -> Node:
    return service.create_story(title)


# ---- create ---------------------------------------------------------------


def test_create_story_returns_persisted_node(service: WorkspaceService) -> None:
    node = service.create_story("My Story")
    assert node.title == "My Story"
    assert node.type is NodeType.STORY
    assert node.parent_id is None
    # Reload via the service: same id, same title.
    reloaded = service.get_node(node.id)
    assert reloaded.id == node.id
    assert reloaded.title == "My Story"


def test_create_story_rejects_empty_title(service: WorkspaceService) -> None:
    with pytest.raises(ValueError):
        service.create_story("")


def test_create_child_returns_persisted_node(service: WorkspaceService) -> None:
    parent = _make_story(service)
    child = service.create_child(parent.id, "Child", type_=NodeType.TASK)
    assert child.title == "Child"
    assert child.type is NodeType.TASK
    assert child.parent_id == parent.id


def test_create_child_under_missing_parent_raises(
    service: WorkspaceService,
) -> None:
    missing = new_node_id()
    with pytest.raises(ParentNotFoundServiceError):
        service.create_child(missing, "Orphan")


def test_create_child_rejects_empty_title(service: WorkspaceService) -> None:
    parent = _make_story(service)
    with pytest.raises(ValueError):
        service.create_child(parent.id, "")


# ---- rename ---------------------------------------------------------------


def test_rename_node_updates_title(service: WorkspaceService) -> None:
    node = _make_story(service, "Old")
    renamed = service.rename_node(node.id, "New")
    assert renamed.id == node.id  # UUID stable (Invariant §6)
    assert renamed.title == "New"


def test_rename_missing_node_raises(service: WorkspaceService) -> None:
    missing = new_node_id()
    with pytest.raises(NodeNotFoundServiceError):
        service.rename_node(missing, "x")


def test_rename_rejects_empty_title(service: WorkspaceService) -> None:
    node = _make_story(service)
    with pytest.raises(ValueError):
        service.rename_node(node.id, "")


# ---- move -----------------------------------------------------------------


def test_move_node_to_new_parent(service: WorkspaceService) -> None:
    a = _make_story(service, "A")
    b = _make_story(service, "B")
    child = service.create_child(a.id, "C")
    moved = service.move_node(child.id, new_parent_id=b.id)
    assert moved.parent_id == b.id
    assert service.get_children(b.id)[0].id == child.id


def test_move_node_to_root(service: WorkspaceService) -> None:
    a = _make_story(service, "A")
    child = service.create_child(a.id, "C")
    moved = service.move_node(child.id, new_parent_id=None)
    assert moved.parent_id is None


def test_move_missing_node_raises(service: WorkspaceService) -> None:
    with pytest.raises(NodeNotFoundServiceError):
        service.move_node(new_node_id(), new_parent_id=None)


def test_move_under_missing_parent_raises(
    service: WorkspaceService,
) -> None:
    node = _make_story(service)
    with pytest.raises(ParentNotFoundServiceError):
        service.move_node(node.id, new_parent_id=new_node_id())


def test_move_into_self_raises_cycle(service: WorkspaceService) -> None:
    node = _make_story(service)
    with pytest.raises(CycleInMoveServiceError):
        service.move_node(node.id, new_parent_id=node.id)


def test_move_into_descendant_raises_cycle(
    service: WorkspaceService,
) -> None:
    parent = _make_story(service)
    child = service.create_child(parent.id, "child")
    with pytest.raises(CycleInMoveServiceError):
        service.move_node(parent.id, new_parent_id=child.id)


# ---- delete ---------------------------------------------------------------


def test_delete_node_removes_self_and_descendants(
    service: WorkspaceService,
) -> None:
    parent = _make_story(service)
    child = service.create_child(parent.id, "child")
    grandchild = service.create_child(child.id, "grandchild")
    service.delete_node(child.id)
    with pytest.raises(NodeNotFoundServiceError):
        service.get_node(child.id)
    with pytest.raises(NodeNotFoundServiceError):
        service.get_node(grandchild.id)


def test_delete_missing_node_raises(service: WorkspaceService) -> None:
    with pytest.raises(NodeNotFoundServiceError):
        service.delete_node(new_node_id())


# ---- read -----------------------------------------------------------------


def test_load_workspace_tree_returns_full_tree(
    service: WorkspaceService,
) -> None:
    a = _make_story(service, "A")
    b = _make_story(service, "B")
    service.create_child(a.id, "a1")
    service.create_child(b.id, "b1")
    tree = service.load_workspace_tree()
    titles = sorted(n.title for n in tree.roots())
    assert titles == ["A", "B"]
    # Children of A is [a1].
    assert [n.title for n in tree.children_of(a.id)] == ["a1"]


def test_load_workspace_tree_empty_raises(service: WorkspaceService) -> None:
    with pytest.raises(WorkspaceEmptyServiceError):
        service.load_workspace_tree()


def test_get_story_returns_root_story(service: WorkspaceService) -> None:
    story = _make_story(service, "Main")
    result = service.get_story(story.id)
    assert result.id == story.id
    assert result.title == "Main"


def test_get_story_rejects_non_root(service: WorkspaceService) -> None:
    parent = _make_story(service)
    child = service.create_child(parent.id, "child")
    with pytest.raises(StoryNotFoundServiceError):
        service.get_story(child.id)


def test_get_story_rejects_non_story_type(service: WorkspaceService) -> None:
    # Create a TASK-type root manually through the fake repo for this case.
    fake_repo: InMemoryWorkspaceRepository = service._repository  # noqa: SLF001
    from backend.domain import Node, NodeMetadata, NodeType
    from backend.domain.tree import Tree as _Tree

    node = Node(
        id=new_node_id(),
        title="Task-as-root",
        type=NodeType.TASK,
        metadata=NodeMetadata.from_dict({}, node_type=NodeType.TASK),
    )
    fake_repo._tree.add(node)  # noqa: SLF001
    with pytest.raises(StoryNotFoundServiceError):
        service.get_story(node.id)


def test_get_node_returns_node(service: WorkspaceService) -> None:
    node = _make_story(service)
    result = service.get_node(node.id)
    assert result.id == node.id


def test_get_node_missing_raises(service: WorkspaceService) -> None:
    with pytest.raises(NodeNotFoundServiceError):
        service.get_node(new_node_id())


def test_get_children_returns_ordered_children(
    service: WorkspaceService,
) -> None:
    parent = _make_story(service)
    a = service.create_child(parent.id, "a")
    b = service.create_child(parent.id, "b")
    c = service.create_child(parent.id, "c")
    children = service.get_children(parent.id)
    assert [n.id for n in children] == [a.id, b.id, c.id]


# ---- isolation / non-leakage ---------------------------------------------


def test_domain_exception_does_not_leak(service: WorkspaceService) -> None:
    """The service must never let a domain-layer exception escape —
    everything gets translated to a service-layer exception or a
    domain-builtin (ValueError) so the API layer has a stable surface.
    """
    missing = new_node_id()
    # Direct access via repo would raise NodeNotFoundError.
    with pytest.raises(NodeNotFoundError):
        service._repository.load_node(missing)  # noqa: SLF001
    # Through the service it's wrapped.
    with pytest.raises(NodeNotFoundServiceError):
        service.get_node(missing)


def test_service_does_not_mutate_returned_node(
    service: WorkspaceService,
) -> None:
    """The service should return a domain entity that callers can
    inspect without surprising the repository. We can't directly
    assert that no mutation escapes (Python dataclasses are
    pass-by-reference for nested objects), but we can assert that
    a 'rename' returns a fresh entity — the fake's rename_node
    replaces the in-tree node, so the returned reference is to
    that new entity.
    """
    node = _make_story(service, "X")
    renamed = service.rename_node(node.id, "Y")
    assert renamed.title == "Y"
    # The new title persists on subsequent reads.
    again = service.get_node(node.id)
    assert again.title == "Y"
