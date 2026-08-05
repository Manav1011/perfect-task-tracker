"""Repository-layer exceptions.

Distinct from filesystem and domain exceptions so the service layer
can catch persistence failures (which may be transient — disk full,
permission denied) separately from domain violations (caller did
something wrong).
"""

from __future__ import annotations


class RepositoryError(Exception):
    """Base for repository-layer errors."""


class WorkspaceNotInitializedError(RepositoryError):
    """The repository was asked to operate on an empty workspace."""