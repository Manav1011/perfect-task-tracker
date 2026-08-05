"""Startup coordinator — Phase 2.3 application bootstrap.

The StartupCoordinator runs once during application startup
(inside the FastAPI lifespan) and is responsible for:

    1. Validating the workspace root (the filesystem substrate).
    2. Initialising the workspace repository.
    3. Initialising the index repository (best-effort — the
       index may be unreachable without taking the app down).
    4. Deciding whether to run the offline Reconciler:
        - if the index is *empty* → rebuild from disk.
        - if the synchroniser's staleness flag is True → rebuild.
        - if the index is reachable AND non-empty AND the
          synchroniser is not stale → skip rebuild (the index
          is healthy).
    5. Returning a `StartupReport` describing what happened
       (which stages succeeded, which were skipped, whether
       the app is in degraded mode).

Failure classification (per the Phase 2.3 brief):

    - **Filesystem unavailable** → startup raises. The app
      cannot operate without disk; degrade-mode would be
      meaningless.
    - **Index unavailable** → app starts in degraded mode.
      The synchroniser remains wired but every sync op will
      flag stale. Reads from the index will fail; production
      callers must handle that.
    - **Rebuild failure** → app starts in degraded mode with
      the error logged. The filesystem remains authoritative;
      the index is empty or partial.

Architectural placement:

    - Depends ONLY on Protocols (Filesystem, WorkspaceRepository,
      IndexRepository, IndexReconciler constructor, sync
      flag). No FastAPI, no settings, no logging pipeline
      imports.
    - The lifespan (`backend.core.lifespan`) constructs the
      coordinator and feeds its return value into the logging
      pipeline. The coordinator itself emits structured log
      events for every stage via the injected logger.
    - The `StartupReport` is the single deliverable. Every
      other side-effect is observable through `app.state`
      (the lifespan places it there for handlers to inspect).

What the coordinator deliberately does NOT do:

    - **No background workers.** Single synchronous pass.
    - **No automatic retries.** A failed rebuild is logged
      and surfaced; the next boot (or manual admin call)
      is the recovery path.
    - **No scheduling.** Boot-time only.
    - **No periodic health checks.** Boot-time only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from backend.index.protocol import IndexRepository
from backend.index.reconciler import ReconcileReport
from backend.repositories.protocol import WorkspaceRepository

if TYPE_CHECKING:
    from backend.index.reconciler import IndexReconciler


# Default logger — structlog's BoundLogger satisfies the
# `_StructuredLogger` Protocol below. Production uses the
# configured structlog pipeline; tests inject a mock or a
# real bound logger. We do NOT use stdlib `logging.Logger`
# as the default because it doesn't accept arbitrary kwargs
# in its `info()` signature.
_log = structlog.get_logger(__name__)


class _StructuredLogger(Protocol):  # pragma: no cover - protocol
    """Structural type for the logger the coordinator uses.

    We accept anything whose `info` / `warning` / `error`
    / `exception` methods take arbitrary keyword args.
    That's exactly the structlog `BoundLogger` shape, and
    the production wiring uses structlog's logger. Tests
    can pass any object satisfying this Protocol (a plain
    Mock, a structlog bound logger, etc.).
    """

    def info(self, event: str, **kwargs: Any) -> None: ...
    def warning(self, event: str, **kwargs: Any) -> None: ...
    def error(self, event: str, **kwargs: Any) -> None: ...
    def exception(self, event: str, **kwargs: Any) -> None: ...


# ---- result classification ------------------------------------------------


class StartupOutcome(str, Enum):
    """Four-state classification of the bootstrap result.

    The enum is intentionally narrow: callers can branch on
    four states and treat every other concern via the
    `errors` / `warnings` fields.

        HEALTHY    — workspace ok, index ok, no rebuild needed.
        RECOVERING — a rebuild was required and was attempted.
                     The result is in `rebuild_report`; the
                     app starts in this state until verification
                     of the rebuilt index (Phase 4+).
        DEGRADED   — app started, but the index is unhealthy
                     (either unreachable or the rebuild failed).
                     Reads that need the index may fail.
        FAILED     — startup raised; the app cannot run.
    """

    HEALTHY = "healthy"
    RECOVERING = "recovering"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(slots=True, frozen=True)
class StageTiming:
    """One stage's wall-clock duration.

    Stages are emitted in `StartupReport.stages` in the order
    they ran. Adding a new stage is a one-line append; the
    report shape is stable.
    """

    name: str
    elapsed_seconds: float
    ok: bool
    detail: str = ""


# ---- result type ----------------------------------------------------------


@dataclass(slots=True, frozen=True)
class StartupReport:
    """The coordinator's deliverable.

    `outcome` is the headline. The other fields are evidence:
    timings, errors, warnings, and what was decided at the
    rebuild gate.

    Frozen so post-mortem introspection cannot accidentally
    rewrite history.

    Phase 3.0 addition: `cache_populate_seconds` records
    how long the runtime cache took to populate at boot.
    Set by `StartupSubsystem.run()` after the coordinator
    runs (the cache is the subsystem's collaborator, not
    the coordinator's — see ADR-0016). A value of 0.0
    means the cache was never populated (legacy startup
    without Phase 3.0 wiring, or a populate failure that
    did not prevent startup).
    """

    outcome: StartupOutcome
    rebuild_attempted: bool
    rebuild_skipped_reason: str | None
    rebuild_report: ReconcileReport | None
    elapsed_seconds: float
    index_unavailable: bool
    filesystem_unavailable: bool
    stages: tuple[StageTiming, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    cache_populate_seconds: float = 0.0

    @property
    def is_healthy(self) -> bool:
        return self.outcome is StartupOutcome.HEALTHY

    @property
    def is_recovering(self) -> bool:
        return self.outcome is StartupOutcome.RECOVERING

    @property
    def is_degraded(self) -> bool:
        return self.outcome is StartupOutcome.DEGRADED

    @property
    def is_failed(self) -> bool:
        return self.outcome is StartupOutcome.FAILED


# ---- collaborator Protocols ----------------------------------------------


class FilesystemLike(Protocol):  # pragma: no cover - protocol
    """Structural type for the Filesystem dependency.

    The coordinator needs only the `root` accessor to confirm
    the workspace is reachable; the rest of the Filesystem
    surface is exercised by the WorkspaceRepository. We declare
    it as a local Protocol so this file has no runtime
    dependency on `backend.filesystem` (matches the index's
    isolation rule).
    """

    @property
    def root(self): ...


class _SyncFlagProbe(Protocol):  # pragma: no cover - protocol
    """Read-side surface the coordinator uses to gate the rebuild.

    `is_stale()` and `clear_staleness()` together are the
    *whole* sync contract the coordinator needs. The
    `IncrementalIndexSynchronizer` satisfies it structurally;
    declaring it locally keeps the import surface narrow.
    """

    def is_stale(self) -> bool: ...

    def clear_staleness(self) -> None: ...


class ConfigLoader(Protocol):  # pragma: no cover - protocol
    """Read-side surface the coordinator uses to load configuration.

    The coordinator calls `load()` once at the very start of
    bootstrap to honour the deterministic ordering documented
    in TECH_SPEC §13c:

        1. Load configuration.
        2. Validate workspace root.
        …

    Production uses a thin wrapper around `backend.config.settings`;
    tests pass a dict-backed loader.
    """

    def load(self) -> object: ...


# ---- the coordinator ------------------------------------------------------


class StartupCoordinator:
    """One-pass application bootstrap.

    Constructor DI — depends on Protocols only. The lifespan
    passes concrete objects (filesystem, repository, index
    repository, reconciler constructor, synchroniser) into
    the coordinator; tests pass fakes.

    The coordinator is intentionally *replaceable*: a future
    Phase 3+ might add per-tenant bootstrapping, dry-run mode,
    or stricter health checks. None of those would change
    the constructor or the report shape — they'd add fields.
    """

    def __init__(
        self,
        *,
        filesystem: FilesystemLike,
        workspace_repo: WorkspaceRepository,
        index_repo: IndexRepository,
        reconciler: "IndexReconciler",
        synchroniser: "_SyncFlagProbe",
        config_loader: "ConfigLoader | None" = None,
        logger: "_StructuredLogger | None" = None,
    ) -> None:
        self._fs = filesystem
        self._workspace = workspace_repo
        self._index = index_repo
        self._reconciler = reconciler
        self._sync = synchroniser
        self._config_loader = config_loader
        self._log: "_StructuredLogger" = logger or _log

    # ---- public API --------------------------------------------------

    def run(self) -> StartupReport:
        """Run the bootstrap. Always returns a `StartupReport`;
        raises only when the filesystem is unavailable.

        The brief distinguishes three failure shapes:

            - Filesystem unavailable → startup fails (raises).
            - Index unavailable → app starts in degraded mode.
            - Rebuild failure → degraded mode with logged error.

        We honor that ordering: filesystem is checked first;
        if it raises, the coordinator re-raises; if it does
        not raise but the index fails, we degrade; if both
        pass but the rebuild fails, we degrade.
        """
        started = time.monotonic()
        stages: list[StageTiming] = []
        errors: list[str] = []
        warnings: list[str] = []
        fs_unavailable = False
        index_unavailable = False
        rebuild_attempted = False
        rebuild_report: ReconcileReport | None = None
        rebuild_skipped_reason: str | None = None
        outcome: StartupOutcome = StartupOutcome.HEALTHY  # placeholder; final classifier overrides

        # ---- stage 0: load configuration ----------------------------
        if self._config_loader is not None:
            t0 = time.monotonic()
            try:
                self._config_loader.load()
                stages.append(
                    StageTiming(
                        name="config_load",
                        elapsed_seconds=time.monotonic() - t0,
                        ok=True,
                        detail="loaded",
                    )
                )
                self._log.info(
                    "startup.config_load",
                    ok=True,
                )
            except Exception as exc:
                stages.append(
                    StageTiming(
                        name="config_load",
                        elapsed_seconds=time.monotonic() - t0,
                        ok=False,
                        detail=repr(exc),
                    )
                )
                self._log.exception(
                    "startup.config_load",
                    ok=False,
                    error=repr(exc),
                )
                # A config-load failure is fatal — we cannot proceed
                # without configuration. Re-raise to fail startup.
                raise

        # ---- stage 1: validate filesystem ----------------------------
        t0 = time.monotonic()
        try:
            root = self._fs.root
            root_path = getattr(root, "path", None)
            detail = f"workspace root = {root_path}" if root_path else "ok"
            stages.append(
                StageTiming(
                    name="filesystem_validate",
                    elapsed_seconds=time.monotonic() - t0,
                    ok=True,
                    detail=detail,
                )
            )
            self._log.info(
                "startup.filesystem_validate",
                ok=True,
                detail=detail,
            )
        except Exception as exc:
            fs_unavailable = True
            stages.append(
                StageTiming(
                    name="filesystem_validate",
                    elapsed_seconds=time.monotonic() - t0,
                    ok=False,
                    detail=repr(exc),
                )
            )
            self._log.exception(
                "startup.filesystem_validate",
                ok=False,
                error=repr(exc),
            )
            raise

        # ---- stage 2: probe index -------------------------------------
        t0 = time.monotonic()
        index_row_count: int | None = None
        try:
            index_row_count = self._index.count()
            stages.append(
                StageTiming(
                    name="index_probe",
                    elapsed_seconds=time.monotonic() - t0,
                    ok=True,
                    detail=f"row count = {index_row_count}",
                )
            )
            self._log.info(
                "startup.index_probe",
                ok=True,
                row_count=index_row_count,
            )
        except Exception as exc:
            index_unavailable = True
            stages.append(
                StageTiming(
                    name="index_probe",
                    elapsed_seconds=time.monotonic() - t0,
                    ok=False,
                    detail=repr(exc),
                )
            )
            self._log.exception(
                "startup.index_probe",
                ok=False,
                error=repr(exc),
            )
            warnings.append(f"index unavailable: {exc!r}")

        # ---- stage 3: decide rebuild ---------------------------------
        should_rebuild, reason = self._should_rebuild(
            index_unavailable=index_unavailable,
            index_row_count=index_row_count,
        )
        stages.append(
            StageTiming(
                name="rebuild_decide",
                elapsed_seconds=0.0,
                ok=True,
                detail=(
                    f"rebuild={should_rebuild} reason={reason!r}"
                ),
            )
        )
        self._log.info(
            "startup.rebuild_decide",
            should_rebuild=should_rebuild,
            reason=reason,
        )

        # ---- stage 4: rebuild (conditional) --------------------------
        if should_rebuild:
            rebuild_attempted = True
            t0 = time.monotonic()
            try:
                rebuild_report = self._reconciler.rebuild()
                # Reconciler.rebuild() is contractually non-None
                # (it always returns a ReconcileReport, success
                # or otherwise). The assert narrows the type for
                # the static checker and documents the invariant.
                assert rebuild_report is not None
                rebuild_succeeded = rebuild_report.is_success
                stages.append(
                    StageTiming(
                        name="rebuild",
                        elapsed_seconds=time.monotonic() - t0,
                        ok=rebuild_succeeded,
                        detail=(
                            f"scanned={rebuild_report.nodes_scanned} "
                            f"inserted={rebuild_report.records_inserted}"
                        ),
                    )
                )
                if rebuild_report is not None and rebuild_report.is_success:
                    self._log.info(
                        "startup.rebuild",
                        ok=True,
                        nodes_scanned=rebuild_report.nodes_scanned,
                        records_inserted=rebuild_report.records_inserted,
                        records_deleted=rebuild_report.records_deleted,
                        elapsed_seconds=rebuild_report.elapsed_seconds,
                    )
                    # Clear the in-process staleness flag now
                    # that the rebuild has succeeded.
                    try:
                        self._sync.clear_staleness()
                    except Exception:
                        # clear_staleness is best-effort; never
                        # fails the startup because of it.
                        pass
                elif rebuild_report is not None:
                    errors.extend(rebuild_report.errors)
                    self._log.error(
                        "startup.rebuild",
                        ok=False,
                        errors=rebuild_report.errors,
                    )
            except Exception as exc:
                stages.append(
                    StageTiming(
                        name="rebuild",
                        elapsed_seconds=time.monotonic() - t0,
                        ok=False,
                        detail=repr(exc),
                    )
                )
                errors.append(f"rebuild raised: {exc!r}")
                self._log.exception(
                    "startup.rebuild",
                    ok=False,
                    error=repr(exc),
                )
        else:
            rebuild_skipped_reason = reason

        # ---- final classification ------------------------------------
        # The classification order matches the brief:
        #
        #     - filesystem raised                  → FAILED (already raised above)
        #     - rebuild attempted AND succeeded    → RECOVERING
        #     - rebuild attempted AND failed/raised → DEGRADED
        #     - index unreachable, no rebuild      → DEGRADED
        #     - no rebuild needed                  → HEALTHY
        if rebuild_attempted:
            if rebuild_report is not None and rebuild_report.is_success:
                outcome = StartupOutcome.RECOVERING
            else:
                # rebuild either returned is_success=False or raised
                # (rebuild_report is None in the latter case; the
                # except-block already appended to `errors`).
                outcome = StartupOutcome.DEGRADED
        elif index_unavailable:
            outcome = StartupOutcome.DEGRADED
            warnings.append("index unreachable; running in degraded mode")
            self._log.warning(
                "startup.degraded",
                reason="index_unavailable",
            )
        # else: outcome is HEALTHY (skip path)

        # One final marker: a healthy startup with no rebuild
        # is logged as such (the most common case in steady-state).
        self._log.info(
            "startup.summary",
            outcome=outcome.value,
            rebuild_attempted=rebuild_attempted,
            elapsed_seconds=time.monotonic() - started,
        )

        return StartupReport(
            outcome=outcome,
            rebuild_attempted=rebuild_attempted,
            rebuild_skipped_reason=rebuild_skipped_reason,
            rebuild_report=rebuild_report,
            elapsed_seconds=time.monotonic() - started,
            index_unavailable=index_unavailable,
            filesystem_unavailable=fs_unavailable,
            stages=tuple(stages),
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    # ---- internals ---------------------------------------------------

    def _should_rebuild(
        self,
        *,
        index_unavailable: bool,
        index_row_count: int | None,
    ) -> tuple[bool, str | None]:
        """Decide whether to invoke the Reconciler.

        Decision tree:

            1. If the index is unreachable → rebuild (we have
               nothing else to do; the rebuild is best-effort
               against the same failing endpoint, but we
               still try because some failures are transient).
            2. If the synchroniser flagged stale → rebuild.
            3. If the index is empty (count == 0) → rebuild.
            4. Otherwise → skip (index is healthy).

        Returns `(should_rebuild, reason)`. The reason is a
        human-readable string for logs; pass through to the
        `StartupReport.rebuild_skipped_reason` field.
        """
        if index_unavailable:
            return True, "index_unavailable"
        try:
            stale = self._sync.is_stale()
        except Exception:
            stale = False
        if stale:
            return True, "sync_flagged_stale"
        if index_row_count is not None and index_row_count == 0:
            return True, "index_empty"
        return False, "index_healthy"
