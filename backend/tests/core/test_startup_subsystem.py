"""Tests for the StartupSubsystem (single owner of bootstrap).

The subsystem wraps the coordinator with deterministic
construction + a single `run()` entrypoint. These tests
verify:

    - The subsystem carries every collaborator the
      application needs (repository, synchroniser,
      index_repo, reconciler, path_provider, coordinator,
      report).
    - `run()` returns a NEW subsystem instance with the
      `report` populated (the original is left empty for
      forensic comparison).
    - The subsystem is the *single* place that owns
      startup orchestration — construction and the
      decision to rebuild live in one method call.
    - `is_healthy / is_degraded / is_recovering` mirror the
      report's outcome.
"""

from __future__ import annotations

import pytest

from backend.index.impl import InMemoryIndexRepository
from backend.index.reconciler import ReconcileReport
from backend.index.startup import StartupOutcome, StartupReport


# ---- minimal stand-ins for the collaborators ------------------------------


class _FakeFilesystem:
    """Stand-in for the Filesystem — the subsystem never calls
    anything on it; it only stores it."""

    class _Root:
        path = "/workspace"

    @property
    def root(self):
        return self._Root()


class _FakeRepository:
    """Stand-in for the WorkspaceRepository Protocol. Stores a
    `current_tree` attribute the subsystem reads once."""

    def __init__(self) -> None:
        self.current_tree = object()

    def load_tree(self):  # pragma: no cover - unused
        return self.current_tree


class _FakeSynchroniser:
    def is_stale(self) -> bool:  # pragma: no cover - unused
        return False

    def clear_staleness(self) -> None:  # pragma: no cover - unused
        pass


class _FakeReconciler:
    def __init__(self, report: ReconcileReport) -> None:
        self._report = report
        self.call_count = 0

    def rebuild(self) -> ReconcileReport:
        self.call_count += 1
        return self._report


class _FakePathProvider:
    def path_for(self, node_id):  # pragma: no cover - unused
        return ""


class _FakeConfig:
    app_name = "test-app"
    app_env = "test"
    log_level = "INFO"


# ---- fixtures ------------------------------------------------------------


@pytest.fixture
def fake_report() -> StartupReport:
    return StartupReport(
        outcome=StartupOutcome.RECOVERING,
        rebuild_attempted=True,
        rebuild_skipped_reason=None,
        rebuild_report=ReconcileReport(
            nodes_scanned=3,
            records_built=3,
            records_inserted=3,
            records_updated=0,
            records_deleted=0,
            errors=(),
            elapsed_seconds=0.01,
        ),
        elapsed_seconds=0.02,
        index_unavailable=False,
        filesystem_unavailable=False,
        stages=(),
        errors=(),
        warnings=(),
    )


def _subsystem(fake_report: StartupReport):
    """Build a subsystem whose coordinator returns `fake_report`."""
    from backend.core.startup_subsystem import StartupSubsystem
    from backend.index.startup import StartupCoordinator
    from backend.workspace import InMemoryWorkspaceCache

    fs = _FakeFilesystem()
    repo = _FakeRepository()
    sync = _FakeSynchroniser()
    index_repo = InMemoryIndexRepository()
    reconciler = _FakeReconciler(fake_report.rebuild_report)
    path_provider = _FakePathProvider()
    cache = InMemoryWorkspaceCache()

    # Build a coordinator that returns fake_report regardless
    # of the dependencies it gets. We do that by subclassing
    # StartupCoordinator and overriding run().
    class _StubCoordinator(StartupCoordinator):
        def __init__(self, *, return_value: StartupReport):
            self._return_value = return_value

        def run(self) -> StartupReport:
            return self._return_value

    coordinator = _StubCoordinator(return_value=fake_report)

    return StartupSubsystem(
        config=_FakeConfig(),  # type: ignore[arg-type]
        fs=fs,  # type: ignore[arg-type]
        repository=repo,  # type: ignore[arg-type]
        synchroniser=sync,  # type: ignore[arg-type]
        index_repo=index_repo,  # type: ignore[arg-type]
        reconciler=reconciler,  # type: ignore[arg-type]
        coordinator=coordinator,  # type: ignore[arg-type]
        path_provider=path_provider,  # type: ignore[arg-type]
        cache=cache,
    )


# ---- tests ----------------------------------------------------------------


def test_subsystem_starts_with_no_report(fake_report: StartupReport) -> None:
    """Before `run()` is called, the subsystem has no report."""
    sub = _subsystem(fake_report)
    assert sub.report is None
    assert sub.outcome() is None


def test_run_returns_new_subsystem_with_report(fake_report: StartupReport) -> None:
    """`run()` is non-mutating — it returns a new subsystem with
    the report populated.

    Phase 3.0 note: the subsystem may legitimately replace
    the report via `dataclasses.replace` to append a cache
    warning (test above exercises the failure path).
    The carry-over fields (outcome, rebuild_attempted,
    warnings' *original* contents) are preserved; only
    the warnings tuple may grow.
    """
    sub = _subsystem(fake_report)
    after = sub.run()
    # `run()` returned a fresh instance.
    assert after is not sub
    assert sub.report is None  # original is untouched
    # The new subsystem carries the report's outcome +
    # rebuild fields. Identity is intentionally not
    # asserted because the cache-populate failure path
    # now appends a warning via `dataclasses.replace`.
    assert after.report.outcome is fake_report.outcome
    assert after.report.rebuild_attempted == fake_report.rebuild_attempted
    assert after.report.rebuild_report is fake_report.rebuild_report
    # Original warnings tuple is preserved as a prefix.
    for original in fake_report.warnings:
        assert original in after.report.warnings


def test_run_populates_outcome_property(fake_report: StartupReport) -> None:
    """`subsystem.outcome()` returns the report's outcome value."""
    sub = _subsystem(fake_report)
    after = sub.run()
    assert after.outcome() == StartupOutcome.RECOVERING.value


def test_is_recovering_property(fake_report: StartupReport) -> None:
    after = _subsystem(fake_report).run()
    assert after.is_recovering() is True
    assert after.is_healthy() is False
    assert after.is_degraded() is False


def test_is_healthy_property(fake_report: StartupReport) -> None:
    healthy = StartupReport(
        outcome=StartupOutcome.HEALTHY,
        rebuild_attempted=False,
        rebuild_skipped_reason="index_healthy",
        rebuild_report=None,
        elapsed_seconds=0.0,
        index_unavailable=False,
        filesystem_unavailable=False,
        stages=(),
        errors=(),
        warnings=(),
    )
    sub = _subsystem(healthy)
    after = sub.run()
    assert after.is_healthy() is True
    assert after.is_recovering() is False


def test_is_degraded_property(fake_report: StartupReport) -> None:
    degraded = StartupReport(
        outcome=StartupOutcome.DEGRADED,
        rebuild_attempted=True,
        rebuild_skipped_reason=None,
        rebuild_report=None,
        elapsed_seconds=0.0,
        index_unavailable=True,
        filesystem_unavailable=False,
        stages=(),
        errors=("rebuild failed",),
        warnings=("index unavailable",),
    )
    sub = _subsystem(degraded)
    after = sub.run()
    assert after.is_degraded() is True
    assert after.is_healthy() is False
    assert after.is_recovering() is False


def test_subsystem_is_frozen(fake_report: StartupReport) -> None:
    """Frozen dataclass — collaborators cannot be swapped out
    after construction (would invalidate invariants)."""
    from dataclasses import FrozenInstanceError

    sub = _subsystem(fake_report)
    with pytest.raises(FrozenInstanceError):
        sub.report = fake_report  # type: ignore[misc]


def test_subsystem_carries_every_collaborator(fake_report: StartupReport) -> None:
    """The subsystem surfaces every collaborator the application
    might need: filesystem, repository, synchroniser, index_repo,
    reconciler, path_provider, coordinator."""
    sub = _subsystem(fake_report)
    assert sub.fs is not None
    assert sub.repository is not None
    assert sub.synchroniser is not None
    assert sub.index_repo is not None
    assert sub.reconciler is not None
    assert sub.path_provider is not None
    assert sub.coordinator is not None
    assert sub.config is not None


def test_build_factory_wires_coordinator(fake_report: StartupReport) -> None:
    """`StartupSubsystem.build()` produces a coordinator that
    the subsystem will run."""
    from backend.core.startup_subsystem import StartupSubsystem
    from backend.workspace import InMemoryWorkspaceCache

    sub = StartupSubsystem.build(
        config=_FakeConfig(),  # type: ignore[arg-type]
        fs=_FakeFilesystem(),  # type: ignore[arg-type]
        index_repo=InMemoryIndexRepository(),
        repository=_FakeRepository(),  # type: ignore[arg-type]
        synchroniser=_FakeSynchroniser(),  # type: ignore[arg-type]
        reconciler=_FakeReconciler(fake_report.rebuild_report),
        path_provider=_FakePathProvider(),
        cache=InMemoryWorkspaceCache(),
    )
    # The factory bound a coordinator; the subsystem hasn't run
    # yet, so the report is still None.
    assert sub.report is None
    assert sub.coordinator is not None