"""Repository layer — bridges domain and persistence.

Public API:
    - WorkspaceRepository    — the Protocol the service layer depends on
    - LocalWorkspaceRepository — disk-backed implementation
    - exceptions             — RepositoryError hierarchy

Architectural role (TECH_SPEC §6):

    API → Services → Domain → Persistence (filesystem, Phase 1.2)
                    ↑
              Repository (this layer)

The repository is the *only* component that calls
`LocalFilesystem`. Services depend on the repository Protocol; tests
can swap in a fake.
"""

from backend.filesystem import (
    # Re-exported because the repository's __init__ signature depends
    # on them; they're cheap and stable.
    Filesystem,
)
from backend.repositories.exceptions import RepositoryError, WorkspaceNotInitializedError
from backend.repositories.impl.local_workspace_repository import LocalWorkspaceRepository
from backend.repositories.protocol import WorkspaceRepository

__all__ = [
    "Filesystem",
    "LocalWorkspaceRepository",
    "RepositoryError",
    "WorkspaceNotInitializedError",
    "WorkspaceRepository",
]