"""Metadata invariants."""

from __future__ import annotations

import pytest

from backend.domain import NodeMetadata, NodeType


def test_task_status_accepted() -> None:
    m = NodeMetadata({"status": "todo"}, node_type=NodeType.TASK)
    assert m.as_dict() == {"status": "todo"}


def test_task_status_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        NodeMetadata({"status": "maybe"}, node_type=NodeType.TASK)


def test_status_rejected_on_non_task() -> None:
    with pytest.raises(ValueError):
        NodeMetadata({"status": "done"}, node_type=NodeType.STORY)


def test_with_field_returns_new_instance() -> None:
    m1 = NodeMetadata({"status": "todo"}, node_type=NodeType.TASK)
    m2 = m1.with_field("status", "doing", NodeType.TASK)
    assert m1.as_dict() == {"status": "todo"}
    assert m2.as_dict() == {"status": "doing"}