"""Service layer — application use cases.

Public API:
    - WorkspaceService — the application use case surface.
    - ServiceError     — the service-layer exception hierarchy.

Architectural role (TECH_SPEC §8):

    API → Services (this layer) → Domain → Persistence

Services are the *only* layer that the API layer talks to. Services
depend on:
    - backend.domain     (entities, value objects)
    - backend.repositories.protocol  (the persistence contract)

Services MUST NOT import:
    - backend.filesystem  (concrete or Protocol)
    - sqlalchemy, fastapi, pathlib, or any I/O library.

Per ADR-0006, services never sequence persistence — every use case
calls *one* repository method. The repository is the only place
multi-step persistence orchestration lives.
"""

from backend.services.exceptions import (
    CycleInMoveServiceError,
    InvalidRenameServiceError,
    NodeNotFoundServiceError,
    ParentNotFoundServiceError,
    ServiceError,
    StoryNotFoundServiceError,
    WorkspaceEmptyServiceError,
)
from backend.services.workspace_service import WorkspaceService

__all__ = [
    "CycleInMoveServiceError",
    "InvalidRenameServiceError",
    "NodeNotFoundServiceError",
    "ParentNotFoundServiceError",
    "ServiceError",
    "StoryNotFoundServiceError",
    "WorkspaceEmptyServiceError",
    "WorkspaceService",
]
