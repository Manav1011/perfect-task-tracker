"""FastAPI dependency injection wiring.

This module is the *only* place in the API layer that knows about
concrete repository implementations. Per ADR-0007, services depend
on the repository Protocol; this module is where we bind the
Protocol to a concrete class for production use.

Tests override `get_workspace_service` with `app.dependency_overrides`
to inject a fake service.

Phase 3.3 — Configuration boundary:
    `build_filesystem()` reads the workspace path from
    `Settings().workspace_path` (resolved from `WORKSPACE_PATH`
    env var / `.env`). Tests pass an explicit `workspace_path`
    to decouple from the default.

    Previously the path was hardcoded to `./data/workspace` with
    a TODO; the verify harness worked around it via a CWD-relative
    symlink trick. That hack is gone — the harness now exports
    `WORKSPACE_PATH=...` and we honor it here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, Request

from backend.config.settings import Settings, get_settings
from backend.filesystem import LocalFilesystem, WorkspaceRoot
from backend.index import IndexRepository
from backend.repositories import LocalWorkspaceRepository, WorkspaceRepository
from backend.search import DefaultSearchService, SearchService
from backend.services import WorkspaceService


def build_filesystem(
    workspace_path: Path | str | None = None,
    settings: Settings | None = None,
) -> LocalFilesystem:
    """Construct the default Filesystem substrate.

    `workspace_path` (when provided) overrides the default. If neither
    is provided, falls through to `Settings().workspace_path` (which
    itself reads `WORKSPACE_PATH` from env or `.env`).

    Phase 1.5 kept this simple: a fixed workspace under the project
    data directory. Phase 3.3 wires the path through Settings
    (ADR-0021). Lifespan pulls `workspace_path` from
    `settings.workspace_path`; unit tests pass a tmp dir explicitly.
    """
    if workspace_path is None:
        if settings is None:
            settings = get_settings()
        workspace_path = settings.workspace_path
    root = WorkspaceRoot.open(workspace_path, create=True)
    return LocalFilesystem(root)


def get_repository(
    sync: object | None = None,
) -> WorkspaceRepository:
    """Build a default repository for the running app.

    Optional `sync` argument wires the Phase 2.2 incremental
    index synchroniser. The lifespan (Phase 2.3) constructs
    the synchroniser and passes it here; request-scoped
    FastAPI dependencies call without arguments.

    NOTE (verify_backend pass): per-request construction of a
    fresh repository here was a latent bug — every API request
    built a new repository with `sync=None`, so writes never
    reached the index. Production uses
    `get_workspace_repository(request)` (below), which returns
    the rich `app.state.repository` wired by the lifespan.
    This builder is kept for backwards compatibility with
    unit tests that don't run inside a FastAPI app.
    """
    fs = build_filesystem()
    return LocalWorkspaceRepository(fs, sync=sync)


def get_workspace_repository(request: Request) -> WorkspaceRepository:
    """Return the lifespan-wired repository for the running app.

    The lifespan (Phase 2.3 + Phase 3.0) constructs a single
    `LocalWorkspaceRepository` with the synchroniser AND the
    runtime cache wired in, and stashes it on `app.state.repository`.
    Per-request FastAPI dependencies MUST source from there so
    that:

        1. Every write hits the synchroniser (so the index
           reflects the new state).
        2. Every read is served from the in-memory cache
           (Phase 3.0 optimisation).
        3. State is consistent across requests — there is only
           one repository, not a new one per request.

    Tests that need a custom repository override this
    dependency via `app.dependency_overrides`.
    """
    return request.app.state.repository  # type: ignore[no-any-return]


def get_workspace_service(
    repository: WorkspaceRepository = Depends(get_workspace_repository),
) -> WorkspaceService:
    """Resolve the WorkspaceService for a single request.

    FastAPI caches the dependency within a request scope, so a
    request that resolves `service` twice gets the same instance.
    Tests use `app.dependency_overrides[get_workspace_service] = ...`
    to inject a fake.
    """
    return WorkspaceService(repository)


def get_index_repository(request: Request) -> IndexRepository:
    """Resolve the `IndexRepository` for a single request.

    The lifespan (Phase 2.3) populates `app.state.index_repo`
    with the production `SQLAlchemyIndexRepository`. The search
    endpoint reads it via this dependency so the API layer
    doesn't import a concrete index implementation.

    Tests override `get_index_repository` with
    `app.dependency_overrides[get_index_repository] = ...`
    to inject an in-memory fake.
    """
    return request.app.state.index_repo  # type: ignore[no-any-return]


def get_search_service(
    index: IndexRepository = Depends(get_index_repository),
) -> SearchService:
    """Resolve the `SearchService` for a single request.

    Per ADR-0019, the search service depends only on the
    `IndexRepository` Protocol. The dependency wires the
    Protocol to `DefaultSearchService`. Tests inject
    a fake by overriding `get_search_service` directly
    (no need to mock the index).
    """
    return DefaultSearchService(index)
