"""Workspace — the in-memory mirror of the workspace on disk.

Phase 3.0: runtime workspace cache.

Public surface:

    - `InMemoryWorkspaceCache` — the production cache implementation.
      Satisfies `WorkspaceCache`, `MutableWorkspaceCache`, and
      `CacheSeeder` Protocols structurally.
    - `CacheBackedTreeProvider` — adapter that exposes the cache
      to the index synchroniser's `WorkspaceTreeProvider` Protocol.
    - `WorkspaceCache`, `MutableWorkspaceCache`, `CacheSeeder`,
      `CacheStats` — the Protocols. Most callers should depend on
      these, not the concrete class.
    - `CacheError`, `CacheNotInitializedError`, `CacheConsistencyError`
      — the exception hierarchy.

Import discipline (enforced by `backend/tests/workspace/test_isolation.py`):

    - `backend.api.*` MUST NOT import this package.
    - `backend.index.*` MUST NOT import this package.
    - `backend.services.*` MUST NOT import this package.
    - `backend.core.*` MUST NOT import this package (the subsystem
      depends on Protocols, not the concrete class).
    - Only `backend/repositories/impl/local_workspace_repository.py`
      may import the concrete `InMemoryWorkspaceCache`.
    - Only `StartupSubsystem.run()` may call `populate()`.

The cache is a runtime optimisation, never a source of truth
(ADR-0016 / TECH_SPEC §13d). It may be discarded and rebuilt
at any time without data loss.
"""

from __future__ import annotations

from backend.workspace.cache import InMemoryWorkspaceCache
from backend.workspace.exceptions import (
    CacheConsistencyError,
    CacheError,
    CacheNotInitializedError,
)
from backend.workspace.protocol import (
    CacheSeeder,
    CacheStats,
    MutableWorkspaceCache,
    WorkspaceCache,
)
from backend.workspace.tree_provider import CacheBackedTreeProvider

__all__ = [
    "CacheBackedTreeProvider",
    "CacheConsistencyError",
    "CacheError",
    "CacheNotInitializedError",
    "CacheSeeder",
    "CacheStats",
    "InMemoryWorkspaceCache",
    "MutableWorkspaceCache",
    "WorkspaceCache",
]