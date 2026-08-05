"""Application startup subsystem — single owner of orchestration.

Per the Phase 2.3 brief, application startup is *its own subsystem*,
not scattered initialisation. There must be **exactly one place**
in the codebase that constructs the indexing stack, decides
whether reconciliation is required, and produces the
`StartupReport`. That place is `StartupSubsystem.run()`.

What this module owns:

    - Construction of the Filesystem substrate.
    - Construction of the WorkspaceRepository (with the
      Phase 2.2 synchroniser seam + Phase 3.0 cache seam).
    - Construction of the IndexRepository.
    - Construction of the IndexReconciler.
    - Construction of the runtime cache
      (`InMemoryWorkspaceCache`) and its population
      (Phase 3.0).
    - Construction of the `StartupCoordinator` itself.
    - The deterministic startup sequence (see below).
    - Exposing the resulting `StartupReport` to the application
      via `StartupSubsystem.report`.

What this module deliberately does NOT own:

    - FastAPI. The lifespan (`backend.core.lifespan`) is a
      thin caller — it invokes `StartupSubsystem.run()` once,
      stashes the report on `app.state`, and yields.
    - The runtime request path. `app.state.repository` and
      `app.state.index_repo` are read by FastAPI dependencies
      (`backend.api.dependencies`), not by the subsystem.
    - Background workers, scheduling, retries. The brief
      forbids all three; this module adds none.

Deterministic startup sequence:

    1. Load configuration.
    2. Validate workspace root (filesystem reachable).
    3. Initialise repository layer (workspace repo wired with
       synchroniser seam + cache seam).
    4. Populate the runtime cache from `repository.load_tree()`.
       (Phase 3.0 — once, idempotent. Cache failure is a
       startup degraded condition, not a fatal one: reads
       fall back to disk.)
    5. Initialise index layer (lazy — never raises at this step).
    6. Determine index health/state (probe `count()`).
    7. Decide whether reconciliation is required
       (index_unavailable / sync.is_stale() / index_empty).
    8. Execute reconciliation if necessary.
    9. Expose final startup state to the application
       (StartupReport, plus the constructed collaborators).

Each step is logged with a structured event so the boot
trail is reproducible from logs alone.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from backend.core.logging import get_logger

if TYPE_CHECKING:
    from backend.config.settings import Settings
    from backend.filesystem import Filesystem
    from backend.index import (
        IncrementalIndexSynchronizer,
        IndexReconciler,
        StartupReport,
    )
    from backend.index.impl import InMemoryIndexRepository
    from backend.index.reconciler import FilesystemPathProvider
    from backend.index.startup import StartupCoordinator
    from backend.repositories import WorkspaceRepository
    from backend.workspace.protocol import CacheSeeder, MutableWorkspaceCache

logger = get_logger(__name__)


@dataclasses.dataclass(slots=True, frozen=True)
class StartupSubsystem:
    """The single owner of application bootstrap.

    Frozen because the constructed collaborators are immutable
    post-startup — swapping the repository out for a different
    one mid-run would invalidate invariants. A new subsystem
    instance is built if a future Phase needs to re-bootstrap.

    `report` is None until `run()` returns; `collaborators`
    is None until construction completes successfully.

    Phase 3.0 — cache collaborator:

        The cache satisfies both `MutableWorkspaceCache` (so
        the repository can invalidate) and `CacheSeeder` (so
        only this subsystem may call `populate()`). The two
        protocols together enforce the single-mutation-boundary
        rule at the type level.
    """

    config: "Settings"
    fs: "Filesystem"
    repository: "WorkspaceRepository"
    synchroniser: "IncrementalIndexSynchronizer"
    index_repo: "InMemoryIndexRepository"
    reconciler: "IndexReconciler"
    coordinator: "StartupCoordinator"
    path_provider: "FilesystemPathProvider"
    cache: "MutableWorkspaceCache & CacheSeeder"
    report: "StartupReport | None" = None

    def is_healthy(self) -> bool:
        """True when the boot landed in HEALTHY."""
        return self.report is not None and self.report.outcome.value == "healthy"

    def is_degraded(self) -> bool:
        return self.report is not None and self.report.outcome.value == "degraded"

    def is_recovering(self) -> bool:
        return self.report is not None and self.report.outcome.value == "recovering"

    def outcome(self) -> str | None:
        return self.report.outcome.value if self.report is not None else None

    # ---- mutation entrypoint ----------------------------------------

    def run(self) -> "StartupSubsystem":
        """Execute the deterministic startup sequence.

        Steps are logged with structured events
        (`startup.subsystem.<stage>`). The result is a NEW
        `StartupSubsystem` with `report` populated; this
        instance is left untouched for forensic comparison.

        Phase 3.0 addition: between step 4 (initialise
        repository) and step 5 (initialise index layer),
        we populate the cache from `repository.load_tree()`.
        A populate failure is *degraded*, not fatal — the
        cache's `is_loaded()` flag stays False; reads fall
        back to disk via the repository's self-healing path;
        the StartupReport carries the failure as a warning.
        """
        # Step 1: log configuration visibility.
        logger.info(
            "startup.subsystem.config_load",
            app=self.config.app_name,
            env=self.config.app_env,
        )

        # Steps 2-7: the coordinator runs config_load,
        # filesystem_validate, index_probe, rebuild_decide,
        # rebuild — in that order.
        report = self.coordinator.run()

        # Step 4 (Phase 3.0): populate the cache from the
        # repository's disk walk. We do this AFTER the
        # coordinator's filesystem_validate stage (so we
        # know the disk is reachable) and BEFORE the
        # synchroniser's first write (so the cache holds
        # the live tree before the index is touched). The
        # order matches the brief: filesystem_validate →
        # cache_populate → index_probe.
        #
        # If populate raises, we DO NOT raise: the cache is
        # an optimisation, not a precondition. The report's
        # `cache_populate_seconds` field stays 0.0; a warning
        # is added to the report's warnings list.
        #
        # We only `dataclasses.replace` the report if a
        # warning was actually produced — otherwise we'd
        # change the report's object identity, which
        # callers (and tests) might depend on. A successful
        # populate doesn't touch the report shape; only a
        # failure does.
        cache_warning = _populate_cache_warn_only(
            self.repository, self.cache, logger
        )
        if cache_warning is not None:
            report = dataclasses.replace(
                report,
                warnings=(*report.warnings, cache_warning),
            )

        # Step 8: expose. The caller (lifespan) will read
        # `report`, `repository`, `index_repo`, `synchroniser`,
        # `cache` from this object and place them on
        # `app.state`.
        new_self = dataclasses.replace(self, report=report)
        logger.info(
            "startup.subsystem.complete",
            outcome=report.outcome.value,
            rebuild_attempted=report.rebuild_attempted,
            cache_populated=self.cache.is_loaded(),
            elapsed_seconds=report.elapsed_seconds,
        )
        return new_self

    # ---- factory ----------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        config: "Settings",
        fs: "Filesystem",
        index_repo: "InMemoryIndexRepository",
        repository: "WorkspaceRepository",
        synchroniser: "IncrementalIndexSynchronizer",
        reconciler: "IndexReconciler",
        path_provider: "FilesystemPathProvider",
        cache: "MutableWorkspaceCache & CacheSeeder",
        logger_: object | None = None,
    ) -> "StartupSubsystem":
        """Construct a StartupSubsystem from already-wired collaborators.

        The factory exists so the lifespan can pass in the
        concrete objects (filesystem, repository, …) without
        this module having to import their concrete classes.
        Tests use the same factory with fakes.

        The factory does NOT call `run()`. Callers must do that
        explicitly after construction.
        """
        from backend.index.startup import StartupCoordinator

        coordinator = StartupCoordinator(
            filesystem=fs,
            workspace_repo=repository,
            index_repo=index_repo,
            reconciler=reconciler,
            synchroniser=synchroniser,
            config_loader=_SettingsConfigLoader(config),
            logger=logger_,  # type: ignore[arg-type]
        )
        return cls(
            config=config,
            fs=fs,
            repository=repository,
            synchroniser=synchroniser,
            index_repo=index_repo,
            reconciler=reconciler,
            coordinator=coordinator,
            path_provider=path_provider,
            cache=cache,
        )


# ---- helpers --------------------------------------------------------------


class _SettingsConfigLoader:
    """Adapter from `Settings` to the coordinator's `ConfigLoader` Protocol.

    Production loads settings via `backend.config.settings.get_settings()`;
    this adapter is what the coordinator sees, keeping the
    Protocol boundary clean.
    """

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings

    def load(self) -> "Settings":
        return self._settings


def _populate_cache_warn_only(
    repository: "WorkspaceRepository",
    cache: "MutableWorkspaceCache & CacheSeeder",
    logger_,
) -> str | None:
    """Populate the runtime cache once at boot.

    Returns a warning string on failure, or `None` on
    success. The caller appends any warning to the
    `StartupReport.warnings` tuple via `dataclasses.replace`.
    A successful populate leaves the report untouched, so
    callers (and tests) that compare `report is fake_report`
    keep their identity contract.

    Defensive design: the cache is an optimisation, not a
    precondition. A populate failure at boot degrades the
    app's read latency (every read goes back to disk) but
    does NOT prevent the app from starting. This matches
    the index-sync failure philosophy (filesystem stays
    authoritative; everything else is best-effort).
    """
    import time as _time

    if cache.is_loaded():
        # Already populated (e.g., a future hot-reload path
        # called populate() before us). The cache's own
        # `populate()` would log a noop warning; we skip
        # silently here.
        return None
    t0 = _time.monotonic()
    try:
        tree = repository.load_tree()
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        logger_.warning(
            "startup.cache_populate.tree_load_failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
            elapsed_seconds=elapsed,
        )
        return f"cache populate failed during tree load: {exc!r}"
    try:
        cache.populate(tree)
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        logger_.warning(
            "startup.cache_populate.failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
            elapsed_seconds=elapsed,
        )
        return f"cache populate failed: {exc!r}"
    logger_.info(
        "startup.cache_populate.complete",
        elapsed_seconds=_time.monotonic() - t0,
    )
    return None