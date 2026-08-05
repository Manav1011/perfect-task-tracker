"""Tree structural invariants."""

from __future__ import annotations

import pytest

from backend.domain import (
    DuplicateNodeIdError,
    InvalidParentError,
    Node,
    NodeMetadata,
    NodeNotFoundError,
    NodeType,
    Tree,
    TreeCycleError,
    new_node_id,
)


def _node(title: str, type_: NodeType = NodeType.TASK) -> Node:
    return Node(id=new_node_id(), title=title, type=type_, metadata=NodeMetadata({}, type_))


def test_add_and_get() -> None:
    t = Tree()
    n = _node("root")
    t.add(n)
    assert t.get(n.id) is n


def test_get_missing_raises() -> None:
    t = Tree()
    with pytest.raises(NodeNotFoundError):
        t.get(new_node_id())


def test_duplicate_id_rejected() -> None:
    t = Tree()
    n = _node("a")
    t.add(n)
    with pytest.raises(DuplicateNodeIdError):
        t.add(n)


def test_attach_links_parent_and_child() -> None:
    t = Tree()
    parent = _node("p", type_=NodeType.STORY)
    child = _node("c")
    t.add(parent)
    t.add(child)
    t.attach(child.id, parent.id)
    # Re-fetch from the tree; attach returns new parent/child instances.
    assert t.get(child.id).parent_id == parent.id
    assert t.get(parent.id).children_ids == [child.id]
    fetched_children = t.children_of(parent.id)
    assert [n.id for n in fetched_children] == [child.id]
    assert fetched_children[0].parent_id == parent.id


def test_attach_rejects_cycle() -> None:
    t = Tree()
    parent = _node("p", type_=NodeType.STORY)
    child = _node("c")
    t.add(parent)
    t.add(child)
    t.attach(child.id, parent.id)
    # Attempting to make parent a child of its own child → cycle.
    with pytest.raises(TreeCycleError):
        t.attach(parent.id, child.id)


def test_attach_rejects_self_parent() -> None:
    t = Tree()
    n = _node("x")
    t.add(n)
    with pytest.raises(TreeCycleError):
        t.attach(n.id, n.id)


def test_attach_to_unknown_parent_rejected() -> None:
    t = Tree()
    n = _node("y")
    t.add(n)
    with pytest.raises(InvalidParentError):
        t.attach(n.id, new_node_id())


def test_move_child_repositions() -> None:
    t = Tree()
    p = _node("p", type_=NodeType.STORY)
    a = _node("a")
    b = _node("b")
    t.add(p)
    t.add(a)
    t.add(b)
    t.attach(a.id, p.id)
    t.attach(b.id, p.id)
    assert t.get(p.id).children_ids == [a.id, b.id]
    t.move_child(p.id, b.id, 0)
    assert t.get(p.id).children_ids == [b.id, a.id]


def test_remove_detaches_from_parent() -> None:
    t = Tree()
    p = _node("p", type_=NodeType.STORY)
    c = _node("c")
    t.add(p)
    t.add(c)
    t.attach(c.id, p.id)
    t.remove(c.id)
    assert c.id not in t
    assert t.get(p.id).children_ids == []