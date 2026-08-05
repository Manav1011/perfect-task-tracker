"""Index layer — Postgres-backed projection of the workspace.

Per ADR-0011 and TECH_SPEC §9, the index is a *derived* view
of the filesystem: it stores queryable metadata only and can
be dropped and rebuilt at any time without losing user data.

This package owns:

    - `IndexRecord`         — the wire-shape between the
                              domain and the index.
    - `IndexRepository`     — the Protocol that everything
                              above the index depends on.
    - `IndexReconciler`     — the full rebuild pass.
    - `ReconcileReport`     — the result of a rebuild.
    - `IncrementalIndexSynchronizer` — incremental sync.
    - `SyncReport`          — counters from one sync pass.
    - `IndexSyncError`      — sync-side failures.
    - `StartupCoordinator`  — application bootstrap.
    - `StartupReport`       — the result of one bootstrap.
    - `IndexError`          — the index-layer exception
                              hierarchy.
    - Concrete implementations (under `.impl`).

It does NOT contain:

    - Business logic. The index is a cache.
    - I/O outside the database session.
"""

from backend.index.exceptions import IndexError, IndexRecordNotFoundError
from backend.index.protocol import IndexRepository
from backend.index.reconciler import (
    FilesystemPathProvider,
    IndexReconciler,
    ReconcileReport,
)
from backend.index.startup import (
    StageTiming,
    StartupCoordinator,
    StartupOutcome,
    StartupReport,
)
from backend.index.sync import (
    FilesystemPathProvider as SyncFilesystemPathProvider,
    IncrementalIndexSynchronizer,
    IndexSyncError,
    SyncPathResolutionError,
    SyncReport,
    WorkspaceTreeProvider,
    make_in_memory_path_provider,
    make_tree_provider,
)
from backend.index.types import IndexRecord

__all__ = [
    "FilesystemPathProvider",
    "IncrementalIndexSynchronizer",
    "IndexError",
    "IndexRecord",
    "IndexRecordNotFoundError",
    "IndexReconciler",
    "IndexRepository",
    "IndexSyncError",
    "ReconcileReport",
    "StageTiming",
    "StartupCoordinator",
    "StartupOutcome",
    "StartupReport",
    "SyncFilesystemPathProvider",
    "SyncPathResolutionError",
    "SyncReport",
    "WorkspaceTreeProvider",
    "make_in_memory_path_provider",
    "make_tree_provider",
]
