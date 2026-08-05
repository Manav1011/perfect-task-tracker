"""NodeMetadata — type-specific freeform fields.

A thin wrapper around a dict that:
    - enforces the schema for known keys (status for tasks),
    - preserves unknown keys (so adding a new type doesn't break older data),
    - is immutable from the outside (mutation goes through `with_*`).

Why a class and not a bare `dict`? So the domain layer can express
invariants ("a task's status must be one of ...") without depending on
SQLAlchemy or pydantic and without leaking dict types across the
service boundary.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from backend.domain.enums import NodeType

# Allowed `status` values for `NodeType.TASK`. Other types have no
# status field at all (and the field is rejected if present).
TASK_STATUSES: frozenset[str] = frozenset({"todo", "doing", "done", "blocked"})


class NodeMetadata:
    """Immutable, validated metadata for a Node.

    Construction validates against the node's `NodeType`; mutations go
    through `with_*` methods that return a new instance.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any] | None, node_type: NodeType) -> None:
        self._data: dict[str, Any] = {}
        if data:
            self._validate(data, node_type)
            self._data = dict(data)

    @staticmethod
    def _validate(data: Mapping[str, Any], node_type: NodeType) -> None:
        """Reject fields that contradict the node's type.

        Currently:
            - `status` is only valid on TASK nodes.
            - `status` must be one of TASK_STATUSES when present.
        """
        if "status" in data:
            if node_type is not NodeType.TASK:
                raise ValueError(
                    f"status is only valid for task nodes, not {node_type.value}"
                )
            status = data["status"]
            if status not in TASK_STATUSES:
                raise ValueError(
                    f"invalid task status {status!r}; must be one of "
                    f"{sorted(TASK_STATUSES)}"
                )

    def with_field(self, key: str, value: Any, node_type: NodeType) -> "NodeMetadata":
        """Return a new metadata instance with one field set/replaced.

        Always validates against `node_type`. The supported caller path
        is `Node.with_metadata(...)`, which routes through here so
        invalid metadata cannot be constructed.
        """
        new_data = {**self._data, key: value}
        NodeMetadata._validate(new_data, node_type)
        return NodeMetadata.from_dict(new_data, node_type=node_type)

    def as_dict(self) -> dict[str, Any]:
        """Return a deep copy of the underlying data.

        The copy prevents external mutation of domain state. Persistence
        layers may serialize this directly.
        """
        return deepcopy(self._data)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, node_type: NodeType | None = None) -> "NodeMetadata":
        """Construct from a mapping. `node_type` is required for validation."""
        if node_type is None:
            raise ValueError("node_type is required to construct NodeMetadata")
        return cls(data, node_type)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, NodeMetadata):
            return NotImplemented
        return self._data == other._data

    def __repr__(self) -> str:
        return f"NodeMetadata({self._data!r})"