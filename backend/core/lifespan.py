"""Application startup/shutdown lifecycle.

FastAPI lifespan handler — runs once on startup and once on shutdown.

This module is intentionally thin. The single owner of
startup orchestration is `backend.core.startup_subsystem`.
The lifespan only:

    1. Configures logging from settings.
    2. Constructs the indexing collaborators (the FastAPI
       dependency factory is the only place concrete classes
       are wired — same rule as ADR-0007).
    3. Hands them to `StartupSubsystem.run()`.
    4. Stashes the resulting `StartupReport` and collaborators
       on `app.state` for handlers to read.
    5. Yields the FastAPI request lifetime.
    6. Logs a goodbye line on shutdown.

If you find yourself adding bootstrap logic here, it probably
belongs in `StartupSubsystem` instead — keeping the lifespan
narrow is what enforces the "single owner of startup"
architectural rule from ADR-0014.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.core.logging import configure_logging, get_logger
from backend.core.startup_subsystem import StartupSubsystem

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Thin FastAPI lifespan — defers orchestration to StartupSubsystem."""
    from backend.config.settings import get_settings

    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "application.startup",
        app=settings.app_name,
        env=settings.app_env,
        log_level=settings.log_level,
    )

    try:
        subsystem = _build_subsystem(settings)
        subsystem = subsystem.run()
        app.state.startup_subsystem = subsystem
        app.state.startup_report = subsystem.report
        app.state.repository = subsystem.repository
        app.state.index_repo = subsystem.index_repo
        app.state.synchroniser = subsystem.synchroniser
    except Exception as exc:
        logger.exception("application.startup_failed", error=repr(exc))
        raise

    try:
        yield
    finally:
        logger.info("application.shutdown", app=settings.app_name)


def _build_subsystem(settings) -> StartupSubsystem:
    """Construct the StartupSubsystem from production collaborators.

    Imports are kept inside this function so test fixtures can
    monkeypatch the underlying modules before the lifespan runs.

    Phase 3.0 — cache wiring:

        1. Build the `InMemoryWorkspaceCache` first (no
           dependencies).
        2. Build the repository with `cache=cache` so every
           read is cache-first and every write invalidates.
        3. Build the synchroniser's `WorkspaceTreeProvider`
           as `CacheBackedTreeProvider(cache)` — this
           replaces the earlier ponytail seam where the
           synchroniser held a back-reference to the
           repository. The synchroniser now reads through
           the cache, which is the only place that holds
           the live Tree.
        4. Hand the cache to `StartupSubsystem.build()` so
           `run()` can populate it once at boot.
    """
    from backend.api.dependencies import build_filesystem
    from backend.core.path_providers import FilesystemDirPathProvider
    from backend.index import IncrementalIndexSynchronizer, IndexReconciler
    from backend.index.impl import SQLAlchemyIndexRepository
    from backend.repositories.impl.local_workspace_repository import (
        LocalWorkspaceRepository,
    )
    from backend.workspace import (
        CacheBackedTreeProvider,
        InMemoryWorkspaceCache,
    )

    fs = build_filesystem()
    path_provider = FilesystemDirPathProvider(fs)

    # Step 1: build the cache. No dependencies yet — the cache
    # is a passive data structure until populate() runs.
    cache = InMemoryWorkspaceCache()

    # Step 2: build the synchroniser first (no tree provider
    # yet), then the repository, then the tree provider
    # adapter that wraps the cache. The order matters because
    # the synchroniser needs a tree_provider at construction,
    # and the tree_provider needs the cache.
    index_repo = SQLAlchemyIndexRepository()
    tree_provider = CacheBackedTreeProvider(cache)
    synchroniser = IncrementalIndexSynchronizer(
        index_repo=index_repo,
        tree_provider=tree_provider,
        path_provider=path_provider,
    )

    repository = LocalWorkspaceRepository(fs, sync=synchroniser, cache=cache)

    reconciler = IndexReconciler(repository, index_repo, path_provider)

    return StartupSubsystem.build(
        config=settings,
        fs=fs,
        index_repo=index_repo,  # type: ignore[arg-type]
        repository=repository,
        synchroniser=synchroniser,
        reconciler=reconciler,
        path_provider=path_provider,
        cache=cache,
        logger_=logger,
    )