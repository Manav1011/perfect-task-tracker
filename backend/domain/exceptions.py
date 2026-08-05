"""Domain exceptions.

Custom error types instead of generic `ValueError` so the API layer can
map them to HTTP status codes and the service layer can catch them
without inspecting message strings.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base for all domain-layer errors.

    Subclasses describe *what kind* of invariant or rule was violated.
    Callers handle by type, not by message.
    """


class NodeNotFoundError(DomainError):
    """A Node with the requested UUID does not exist in the tree."""

    def __init__(self, node_id: str) -> None:
        super().__init__(f"Node not found: {node_id}")
        self.node_id = node_id


class DuplicateNodeIdError(DomainError):
    """An attempt was made to insert a Node whose UUID already exists."""

    def __init__(self, node_id: str) -> None:
        super().__init__(f"Duplicate node id: {node_id}")
        self.node_id = node_id


class InvalidParentError(DomainError):
    """A parent reference is invalid (unknown id, wrong type, etc.)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class TreeCycleError(DomainError):
    """An operation would introduce a cycle in the parent/child graph."""

    def __init__(self, node_id: str) -> None:
        super().__init__(f"Operation would create a cycle at node {node_id}")
        self.node_id = node_id