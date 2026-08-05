"""Service-layer exceptions.

Distinct from domain and repository exceptions so the API layer can
catch use-case failures (which are user-correctable) separately from
infrastructure failures (which may be transient).

Mapping rules:

    Domain errors → ServiceError subclasses, where the subclass name
    signals the use-case failure (e.g. StoryNotFoundServiceError).
    We never re-export a domain exception class — the API layer
    depends only on ServiceError and its subclasses.

    Repository errors → also wrapped. Today the repository passes
    domain errors through, so the wrapping layer is thin. When
    Postgres is added (Phase 4) we'll have real RepositoryError
    cases to map.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base for service-layer errors."""


class WorkspaceEmptyServiceError(ServiceError):
    """A use case was invoked on an empty / uninitialized workspace."""


class NodeNotFoundServiceError(ServiceError):
    """The requested Node does not exist on disk."""

    def __init__(self, node_id: str) -> None:
        super().__init__(f"node not found: {node_id}")
        self.node_id = node_id


class StoryNotFoundServiceError(ServiceError):
    """The requested Story (root) does not exist."""

    def __init__(self, story_id: str) -> None:
        super().__init__(f"story not found: {story_id}")
        self.story_id = story_id


class InvalidRenameServiceError(ServiceError):
    """The requested rename is not allowed (empty title, etc.)."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"invalid rename: {reason}")


class CycleInMoveServiceError(ServiceError):
    """The requested move would create a cycle in the tree."""

    def __init__(self, node_id: str) -> None:
        super().__init__(f"move would create a cycle: {node_id}")
        self.node_id = node_id


class ParentNotFoundServiceError(ServiceError):
    """The requested parent does not exist."""

    def __init__(self, parent_id: str) -> None:
        super().__init__(f"parent not found: {parent_id}")
        self.parent_id = parent_id
