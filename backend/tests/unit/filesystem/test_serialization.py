"""Serialization round-trip tests."""

from __future__ import annotations

import json

import pytest

from backend.domain import Node, NodeMetadata, NodeType, new_node_id
from backend.filesystem.exceptions import InvalidNodeJSONError
from backend.filesystem.serialization import dict_to_node, node_to_dict


def _node() -> Node:
    return Node(
        id=new_node_id(),
        title="Sample",
        type=NodeType.TASK,
        metadata=NodeMetadata({"status": "doing"}, NodeType.TASK),
        parent_id=None,
        children_ids=[],
    )


def test_round_trip() -> None:
    n = _node()
    payload = node_to_dict(n)
    # Stored shape should be JSON-serializable.
    json.dumps(payload)
    n2 = dict_to_node(payload)
    assert n2.id == n.id
    assert n2.title == n.title
    assert n2.type == n.type
    assert n2.metadata.as_dict() == {"status": "doing"}


def test_missing_key_raises() -> None:
    with pytest.raises(InvalidNodeJSONError):
        dict_to_node({"id": "abc"})


def test_unknown_type_raises() -> None:
    payload = node_to_dict(_node())
    payload["type"] = "bogus"
    with pytest.raises(InvalidNodeJSONError):
        dict_to_node(payload)


def test_extra_keys_preserved_on_disk_but_not_in_node() -> None:
    """Forward-compat: extra keys survive serialization.

    The dict_to_node parser ignores unknown keys (they live only on
    disk); node_to_dict does not write extras. This documents the
    behavior so future readers know what to expect.
    """
    n = _node()
    payload = node_to_dict(n)
    payload["future_field"] = "untouched"
    n2 = dict_to_node(payload)
    # Node is parsed; the extra key isn't on the domain object.
    assert n2.title == n.title
    assert not hasattr(n2, "future_field")