"""Node invariants."""

from __future__ import annotations

import pytest

from backend.domain import (
    InvalidParentError,
    Node,
    NodeMetadata,
    NodeType,
    new_node_id,
)


def _node(title: str = "n", type_: NodeType = NodeType.TASK, parent: str | None = None) -> Node:
    return Node(
        id=new_node_id(),
        title=title,
        type=type_,
        metadata=NodeMetadata({}, type_),
        parent_id=parent,  # type: ignore[arg-type]
    )


def test_node_rejects_empty_title() -> None:
    with pytest.raises(ValueError):
        _node(title="")


def test_node_rejects_self_parent() -> None:
    nid = new_node_id()
    with pytest.raises(InvalidParentError):
        Node(
            id=nid,
            title="loop",
            type=NodeType.TASK,
            metadata=NodeMetadata({}, NodeType.TASK),
            parent_id=nid,
        )


def test_with_methods_return_new_instances() -> None:
    n = _node()
    n2 = n.with_title("renamed").with_canvas("notes.md")
    assert n is not n2
    assert n.title == "n"
    assert n2.title == "renamed"
    assert n2.canvas == "notes.md"