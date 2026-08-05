"""Tests for the StartupCoordinator (Phase 2.3).

Coverage (per the Phase 2.3 brief requirement #10):

    - Fresh startup: index empty, no sync flag → rebuild runs,
      healthy outcome.
    - Empty index: empty workspace, empty index → rebuild runs,
      records_inserted == 0, healthy.
    - Healthy index: non-empty index, sync not stale → skip
      rebuild, healthy.
    - Stale index: sync flag is True → rebuild runs, healthy
      afterwards, sync cleared.
    - Rebuild success: explicit stub of a successful rebuild.
    - Rebuild failure: rebuild returns `is_success == False` →
      degraded, error logged.
    - Index unavailable: `index_repo.count()` raises →
      degraded mode (filesystem still ok).
    - Filesystem unavailable: `fs.root` raises → coordinator
      re-raises (startup fails).

All tests use Protocol-shaped fakes: nothing in the index
package's `__init__` import surface beyond what `index.startup`
itself pulls in.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.domain.enums import NodeType
from backend.domain.node import Node, NodeId
from backend.domain.tree import Tree
from backend.index import (
    StartupCoordinator,
    StartupOutcome,
)
from backend.index.impl import InMemoryIndexRepository
from backend.index.reconciler import ReconcileReport
from backend.index.startup import StartupReport


# ---- fakes ---------------------------------------------------------------


class _FakeFilesystem:
    """Implements the FilesystemLike Protocol surface the
    coordinator uses (just `root`).

    `root_exc`: if set, `self.root` raises it on access.
    """

    def __init__(self, root_path: str = "/workspace", root_exc: Exception | None = None):
        self._root_path = root_path
        self._root_exc = root_exc
        self.root_call_count = 0

    @property
    def root(self):
        self.root_call_count += 1
        if self._root_exc is not None:
            raise self._root_exc
        return _FakeRoot(self._root_path)


class _FakeRoot:
    def __init__(self, path: str) -> None:
        self.path = path


class _FakeWorkspaceRepository:
    """In-memory WorkspaceRepository satisfying the Protocol by
    structure. The coordinator only calls `load_tree()` (it never
    mutates anything), so the read surface is what matters."""

    def __init__(self, *, corrupt_on_load: bool = False) -> None:
        self._tree = Tree()
        self._corrupt_on_load = corrupt_on_load

    def add(self, node: Node, parent_id: NodeId | None = None) -> None:
        self._tree.add(node)
        if parent_id is not None:
            self._tree.attach(node.id, parent_id)

    def load_node(self, node_id: NodeId) -> Node:
        return self._tree.get(node_id)

    def load_children(self, node_id: NodeId) -> list[Node]:
        return self._tree.children_of(node_id)

    def load_tree(self) -> Tree:
        if self._corrupt_on_load:
            raise RuntimeError("simulated corrupt workspace")
        return self._tree

    def save_node(self, node: Node, parent_id: NodeId | None) -> Node:  # pragma: no cover
        self.add(node, parent_id)
        return self._tree.get(node.id)

    def rename_node(self, node_id: NodeId, new_title: str) -> Node:  # pragma: no cover
        existing = self._tree.get(node_id)
        updated = existing.with_title(new_title)
        self._tree._nodes[node_id] = updated  # noqa: SLF001
        return updated

    def move_node(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def delete_node(self, node_id: NodeId) -> None:  # pragma: no cover
        pass

    def read_canvas(self, node_id: NodeId) -> str:  # pragma: no cover
        return ""

    def write_canvas(self, node_id: NodeId, content: str) -> None:  # pragma: no cover
        pass


class _FakeReconciler:
    """Stub IndexReconciler that returns a configurable report."""

    def __init__(
        self,
        *,
        report: ReconcileReport | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._report = report
        self._raise = raise_exc
        self.call_count = 0

    def rebuild(self) -> ReconcileReport:
        self.call_count += 1
        if self._raise is not None:
            raise self._raise
        assert self._report is not None
        return self._report


class _FakeSynchroniser:
    """Stub for the sync flag probe."""

    def __init__(self, *, stale: bool = False) -> None:
        self._stale = stale
        self.is_stale_calls = 0
        self.clear_calls = 0

    def is_stale(self) -> bool:
        self.is_stale_calls += 1
        return self._stale

    def clear_staleness(self) -> None:
        self.clear_calls += 1
        self._stale = False


class _RaisingIndexRepository:
    """Index repo whose `count()` always raises."""

    def count(self) -> int:
        raise ConnectionError("simulated DB outage")

    # The Protocol requires more; the coordinator only calls count().
    def upsert(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def delete(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def get(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def list(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def clear(self) -> None:  # pragma: no cover
        raise NotImplementedError


# ---- helpers -------------------------------------------------------------


def _ok_report(*, inserted: int = 0, scanned: int = 0) -> ReconcileReport:
    return ReconcileReport(
        nodes_scanned=scanned,
        records_built=inserted,
        records_inserted=inserted,
        records_updated=0,
        records_deleted=0,
        errors=(),
        elapsed_seconds=0.001,
    )


def _fail_report() -> ReconcileReport:
    return ReconcileReport(
        nodes_scanned=5,
        records_built=3,
        records_inserted=0,
        records_updated=0,
        records_deleted=0,
        errors=("db unique violation on node X",),
        elapsed_seconds=0.01,
    )


# ---- tests ---------------------------------------------------------------


def test_fresh_startup_rebuilds_and_recovers() -> None:
    """Fresh startup: index empty, sync not stale → rebuild runs,
    app boots in RECOVERING (rebuild succeeded)."""
    fs = _FakeFilesystem()
    workspace = _FakeWorkspaceRepository()
    index_repo = InMemoryIndexRepository()
    sync = _FakeSynchroniser(stale=False)

    # index is empty (count == 0) → rebuild decision: True (index_empty)
    reconciler = _FakeReconciler(report=_ok_report(inserted=0, scanned=0))

    coordinator = StartupCoordinator(
        filesystem=fs,
        workspace_repo=workspace,
        index_repo=index_repo,
        reconciler=reconciler,  # type: ignore[arg-type]
        synchroniser=sync,  # type: ignore[arg-type]
    )
    report = coordinator.run()

    assert isinstance(report, StartupReport)
    assert report.outcome is StartupOutcome.RECOVERING
    assert report.rebuild_attempted is True
    assert report.rebuild_skipped_reason is None
    assert report.rebuild_report is not None
    assert report.rebuild_report.is_success
    assert report.index_unavailable is False
    assert report.filesystem_unavailable is False
    # All four stages should be present, in order.
    stage_names = [s.name for s in report.stages]
    assert stage_names == [
        "filesystem_validate",
        "index_probe",
        "rebuild_decide",
        "rebuild",
    ]
    # All stages ok.
    assert all(s.ok for s in report.stages)
    # No errors or warnings.
    assert report.errors == ()
    assert report.warnings == ()


def test_empty_workspace_healthy_index_skips_rebuild() -> None:
    """Healthy index: non-empty, sync not stale → skip rebuild,
    healthy."""
    fs = _FakeFilesystem()
    workspace = _FakeWorkspaceRepository()
    index_repo = InMemoryIndexRepository()
    index_repo.upsert(_record_for("aaaaaaaa-1111-1111-1111-111111111111"))
    sync = _FakeSynchroniser(stale=False)
    reconciler = _FakeReconciler(report=_ok_report())

    coordinator = StartupCoordinator(
        filesystem=fs,
        workspace_repo=workspace,
        index_repo=index_repo,
        reconciler=reconciler,  # type: ignore[arg-type]
        synchroniser=sync,  # type: ignore[arg-type]
    )
    report = coordinator.run()

    assert report.outcome is StartupOutcome.HEALTHY
    assert report.rebuild_attempted is False
    assert report.rebuild_skipped_reason == "index_healthy"
    assert report.rebuild_report is None
    assert reconciler.call_count == 0
    assert sync.clear_calls == 0  # not stale, nothing to clear
    # index_probe stage succeeded, rebuild stage absent.
    stage_names = [s.name for s in report.stages]
    assert "rebuild" not in stage_names
    assert "index_probe" in stage_names


def test_stale_sync_flag_triggers_rebuild() -> None:
    """Sync flagged stale → rebuild runs, healthy afterwards,
    sync.clear_staleness() invoked."""
    fs = _FakeFilesystem()
    workspace = _FakeWorkspaceRepository()
    index_repo = InMemoryIndexRepository()
    index_repo.upsert(_record_for("aaaaaaaa-1111-1111-1111-111111111111"))
    sync = _FakeSynchroniser(stale=True)
    reconciler = _FakeReconciler(report=_ok_report(inserted=2, scanned=2))

    coordinator = StartupCoordinator(
        filesystem=fs,
        workspace_repo=workspace,
        index_repo=index_repo,
        reconciler=reconciler,  # type: ignore[arg-type]
        synchroniser=sync,  # type: ignore[arg-type]
    )
    report = coordinator.run()

    assert report.outcome is StartupOutcome.RECOVERING
    assert report.rebuild_attempted is True
    assert report.rebuild_skipped_reason is None
    # Sync flag was checked and then cleared after success.
    assert sync.is_stale_calls >= 1
    assert sync.clear_calls == 1
    # Stale flag is now False.
    assert sync.is_stale() is False


def test_rebuild_success_records_in_report() -> None:
    """Successful rebuild surfaces the Reconciler's report fields."""
    fs = _FakeFilesystem()
    workspace = _FakeWorkspaceRepository()
    index_repo = InMemoryIndexRepository()
    sync = _FakeSynchroniser(stale=False)
    reconciler = _FakeReconciler(
        report=ReconcileReport(
            nodes_scanned=10,
            records_built=7,
            records_inserted=7,
            records_updated=0,
            records_deleted=3,
            errors=(),
            elapsed_seconds=0.42,
        ),
    )

    coordinator = StartupCoordinator(
        filesystem=fs,
        workspace_repo=workspace,
        index_repo=index_repo,
        reconciler=reconciler,  # type: ignore[arg-type]
        synchroniser=sync,  # type: ignore[arg-type]
    )
    report = coordinator.run()

    assert report.outcome is StartupOutcome.RECOVERING
    assert report.rebuild_attempted is True
    assert report.rebuild_report is not None
    assert report.rebuild_report.nodes_scanned == 10
    assert report.rebuild_report.records_inserted == 7
    assert report.rebuild_report.records_deleted == 3
    # rebuild stage detail names the counters.
    rebuild_stage = next(s for s in report.stages if s.name == "rebuild")
    assert "scanned=10" in rebuild_stage.detail
    assert "inserted=7" in rebuild_stage.detail
    assert rebuild_stage.ok is True


def test_rebuild_failure_degrades_and_logs_errors() -> None:
    """Reconciler returns `is_success == False` → DEGRADED,
    errors propagated to report."""
    fs = _FakeFilesystem()
    workspace = _FakeWorkspaceRepository()
    index_repo = InMemoryIndexRepository()
    sync = _FakeSynchroniser(stale=False)
    reconciler = _FakeReconciler(report=_fail_report())

    coordinator = StartupCoordinator(
        filesystem=fs,
        workspace_repo=workspace,
        index_repo=index_repo,
        reconciler=reconciler,  # type: ignore[arg-type]
        synchroniser=sync,  # type: ignore[arg-type]
    )
    report = coordinator.run()

    assert report.outcome is StartupOutcome.DEGRADED
    assert report.rebuild_attempted is True
    assert report.rebuild_report is not None
    assert not report.rebuild_report.is_success
    # Errors surface.
    assert any("db unique violation" in e for e in report.errors)
    # Rebuild stage flagged as not-ok.
    rebuild_stage = next(s for s in report.stages if s.name == "rebuild")
    assert rebuild_stage.ok is False
    # Sync NOT cleared (rebuild failed; leave flag set so next
    # boot retries).
    assert sync.clear_calls == 0


def test_rebuild_raises_degrades_and_logs_exception() -> None:
    """If the Reconciler itself raises (unhandled exception), the
    coordinator degrades and surfaces the error."""
    fs = _FakeFilesystem()
    workspace = _FakeWorkspaceRepository()
    index_repo = InMemoryIndexRepository()
    sync = _FakeSynchroniser(stale=False)
    reconciler = _FakeReconciler(raise_exc=RuntimeError("disk full"))

    coordinator = StartupCoordinator(
        filesystem=fs,
        workspace_repo=workspace,
        index_repo=index_repo,
        reconciler=reconciler,  # type: ignore[arg-type]
        synchroniser=sync,  # type: ignore[arg-type]
    )
    report = coordinator.run()

    assert report.outcome is StartupOutcome.DEGRADED
    assert report.rebuild_attempted is True
    assert report.rebuild_report is None
    assert any("disk full" in e for e in report.errors)


def test_index_unavailable_best_effort_rebuild_recovers() -> None:
    """Index repo `count()` raises → coordinator attempts a
    best-effort rebuild; if the rebuild succeeds (e.g., transient
    outage recovered), outcome is RECOVERING. The warning
    about the index probe failure is still recorded."""
    fs = _FakeFilesystem()
    workspace = _FakeWorkspaceRepository()
    sync = _FakeSynchroniser(stale=False)
    index_repo = _RaisingIndexRepository()
    reconciler = _FakeReconciler(report=_ok_report())

    coordinator = StartupCoordinator(
        filesystem=fs,
        workspace_repo=workspace,
        index_repo=index_repo,  # type: ignore[arg-type]
        reconciler=reconciler,  # type: ignore[arg-type]
        synchroniser=sync,  # type: ignore[arg-type]
    )
    report = coordinator.run()

    # Rebuild succeeded → RECOVERING (the app is now usable).
    assert report.outcome is StartupOutcome.RECOVERING
    assert report.index_unavailable is True
    assert report.filesystem_unavailable is False
    assert report.rebuild_attempted is True
    # The probe-stage warning is preserved on the report.
    assert any("index unavailable" in w for w in report.warnings)
    # index_probe stage flagged not-ok.
    index_probe_stage = next(s for s in report.stages if s.name == "index_probe")
    assert index_probe_stage.ok is False


def test_index_unavailable_and_rebuild_fails_is_degraded() -> None:
    """Index probe fails AND the rebuild also fails →
    DEGRADED (the app cannot rely on the index at all)."""
    fs = _FakeFilesystem()
    workspace = _FakeWorkspaceRepository()
    sync = _FakeSynchroniser(stale=False)
    index_repo = _RaisingIndexRepository()
    reconciler = _FakeReconciler(report=_fail_report())

    coordinator = StartupCoordinator(
        filesystem=fs,
        workspace_repo=workspace,
        index_repo=index_repo,  # type: ignore[arg-type]
        reconciler=reconciler,  # type: ignore[arg-type]
        synchroniser=sync,  # type: ignore[arg-type]
    )
    report = coordinator.run()

    assert report.outcome is StartupOutcome.DEGRADED
    assert report.index_unavailable is True
    assert report.rebuild_attempted is True
    # Both errors surfaced.
    assert any("db unique violation" in e for e in report.errors)
    assert any("index unavailable" in w for w in report.warnings)


def test_filesystem_unavailable_raises_and_kills_startup() -> None:
    """Filesystem `root` raises → coordinator re-raises; the
    app cannot start."""
    fs = _FakeFilesystem(root_exc=PermissionError("read-only volume"))
    workspace = _FakeWorkspaceRepository()
    index_repo = InMemoryIndexRepository()
    sync = _FakeSynchroniser(stale=False)
    reconciler = _FakeReconciler(report=_ok_report())

    coordinator = StartupCoordinator(
        filesystem=fs,
        workspace_repo=workspace,
        index_repo=index_repo,
        reconciler=reconciler,  # type: ignore[arg-type]
        synchroniser=sync,  # type: ignore[arg-type]
    )

    with pytest.raises(PermissionError, match="read-only volume"):
        coordinator.run()

    # No rebuild attempted (we never got past filesystem_validate).
    assert reconciler.call_count == 0


# ---- additional structural tests -----------------------------------------


def test_structured_logging_called_per_stage() -> None:
    """The coordinator emits one info/log per stage; we count
    events captured by a fake logger."""
    events: list[tuple[str, dict[str, Any]]] = []

    class _Log:
        def info(self, event: str, **kw: Any) -> None:
            events.append((event, kw))

        def warning(self, event: str, **kw: Any) -> None:
            events.append((event, kw))

        def error(self, event: str, **kw: Any) -> None:
            events.append((event, kw))

        def exception(self, event: str, **kw: Any) -> None:
            events.append((event, kw))

    fs = _FakeFilesystem()
    workspace = _FakeWorkspaceRepository()
    index_repo = InMemoryIndexRepository()
    sync = _FakeSynchroniser(stale=False)
    reconciler = _FakeReconciler(report=_ok_report())

    coordinator = StartupCoordinator(
        filesystem=fs,
        workspace_repo=workspace,
        index_repo=index_repo,
        reconciler=reconciler,  # type: ignore[arg-type]
        synchroniser=sync,  # type: ignore[arg-type]
        logger=_Log(),  # type: ignore[arg-type]
    )
    coordinator.run()

    event_names = [e[0] for e in events]
    assert "startup.filesystem_validate" in event_names
    assert "startup.index_probe" in event_names
    assert "startup.rebuild_decide" in event_names
    assert "startup.rebuild" in event_names
    assert "startup.summary" in event_names
    # Summary carries the outcome string. In this test the
    # rebuild succeeded, so the outcome is RECOVERING.
    summary = next(e for e in events if e[0] == "startup.summary")
    assert summary[1]["outcome"] == StartupOutcome.RECOVERING.value


def test_outcome_properties() -> None:
    """StartupReport properties are consistent with `outcome`.

    A successful rebuild lands the boot in RECOVERING (not
    HEALTHY); the no-rebuild path lands in HEALTHY. Both have
    dedicated properties."""
    fs = _FakeFilesystem()
    workspace = _FakeWorkspaceRepository()
    index_repo = InMemoryIndexRepository()
    sync = _FakeSynchroniser(stale=False)
    reconciler = _FakeReconciler(report=_ok_report())

    coordinator = StartupCoordinator(
        filesystem=fs,
        workspace_repo=workspace,
        index_repo=index_repo,
        reconciler=reconciler,  # type: ignore[arg-type]
        synchroniser=sync,  # type: ignore[arg-type]
    )
    report = coordinator.run()

    # Rebuild ran and succeeded → RECOVERING.
    assert report.is_recovering is True
    assert report.is_healthy is False
    assert report.is_degraded is False
    assert report.is_failed is False


# ---- minimal helpers used in tests ---------------------------------------


def _record_for(node_id: str):
    """Build an IndexRecord for a given node_id (used to make
    the index non-empty in healthy-index tests)."""
    from datetime import datetime, timezone

    from backend.index.types import IndexRecord

    now = datetime.now(timezone.utc)
    return IndexRecord(
        node_id=NodeId(node_id),
        parent_id=None,
        story_id=NodeId(node_id),
        title="seed",
        node_type=NodeType.STORY.value,
        filesystem_path="seed",
        created_at=now,
        updated_at=now,
    )