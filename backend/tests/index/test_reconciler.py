"""Reconciler tests.

Each test wires:
    - a fake `WorkspaceRepository` (dict-backed Tree),
    - the `InMemoryIndexRepository`,
    - a deterministic `dict`-backed path provider.

The reconciler accepts these via Constructor DI only — Protocol-
shaped dependencies, no concrete imports — so the test surfaces
the production contract directly.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

import pytest

from backend.domain.enums import NodeType
from backend.domain.metadata import NodeMetadata
from backend.domain.node import Node, NodeId
from backend.domain.tree import Tree
from backend.index.impl import InMemoryIndexRepository
from backend.index.reconciler import (
    FilesystemPathProvider,
    IndexReconciler,
    ReconcileReport,
)
from backend.index.types import IndexRecord


# ---- fakes ---------------------------------------------------------------


class _FakeWorkspaceRepository:
    """In-memory WorkspaceRepository satisfying the Protocol by structure.

    Persists Nodes via the real `Tree`. Optional `corrupt_on_load`
    flag raises a `RuntimeError` from `load_tree` so tests can
    exercise the corrupt-repository path.
    """

    def __init__(self, *, corrupt_on_load: bool = False) -> None:
        self._tree = Tree()
        self._corrupt_on_load = corrupt_on_load

    def add(self, node: Node, parent_id: NodeId | None = None) -> None:
        self._tree.add(node)
        if parent_id is not None:
            self._tree.attach(node.id, parent_id)

    # ---- Protocol surface ----

    def load_node(self, node_id: NodeId) -> Node:
        return self._tree.get(node_id)

    def load_children(self, node_id: NodeId) -> list[Node]:
        return self._tree.children_of(node_id)

    def load_tree(self) -> Tree:
        if self._corrupt_on_load:
            raise RuntimeError("simulated corrupt workspace")
        return self._tree

    def save_node(self, node: Node, parent_id: NodeId | None) -> Node:  # pragma: no cover - unused
        self.add(node, parent_id)
        return self._tree.get(node.id)

    def rename_node(self, node_id: NodeId, new_title: str) -> Node:  # pragma: no cover - unused
        existing = self._tree.get(node_id)
        updated = existing.with_title(new_title)
        self._tree._nodes[node_id] = updated  # noqa: SLF001
        return updated

    def move_node(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    def delete_node(self, node_id: NodeId) -> None:  # pragma: no cover - unused
        pass

    def read_canvas(self, node_id: NodeId) -> str:  # pragma: no cover - unused
        return ""

    def write_canvas(self, node_id: NodeId, content: str) -> None:  # pragma: no cover - unused
        pass


class _StaticPathProvider:
    """Path provider: returns one path per node_id, deterministic.

    If a node_id was never registered, returns
    `f"unknown/{node_id}"` (so a production-shaped provider
    doesn't have to know every id at construction). For
    "must raise" tests use `_RaisingPathProvider` directly,
    or wrap this one in `_DeniedFor` to deny specific ids.
    """

    def __init__(self, paths: dict[str, str] | None = None) -> None:
        self._paths: dict[str, str] = paths or {}

    def path_for(self, node_id: NodeId) -> str:
        return self._paths.get(node_id, f"unknown/{node_id}")

    def set(self, node_id: NodeId, path: str) -> None:
        self._paths[node_id] = path


class _DeniedFor:
    """Wraps a path provider and raises for a deny-list of ids."""

    def __init__(
        self,
        inner: _StaticPathProvider,
        denied: set[str],
    ) -> None:
        self._inner = inner
        self._denied = denied

    def path_for(self, node_id: NodeId) -> str:
        if node_id in self._denied:
            raise KeyError(f"no path for {node_id}")
        return self._inner.path_for(node_id)


class _RaisingPathProvider:
    """Path provider that always raises — used to exercise the
    project-failure branch."""

    def path_for(self, node_id: NodeId) -> str:
        raise KeyError(f"no path for {node_id}")


# ---- fixtures / helpers --------------------------------------------------


def _node(
    *,
    id: str,
    title: str,
    type: NodeType = NodeType.STORY,
    parent_id: str | None = None,
) -> Node:
    return Node(
        id=NodeId(id),
        title=title,
        type=type,
        metadata=NodeMetadata.from_dict({}, node_type=type),
        parent_id=NodeId(parent_id) if parent_id is not None else None,
    )


@pytest.fixture
def index_repo() -> InMemoryIndexRepository:
    return InMemoryIndexRepository()


@pytest.fixture
def workspace() -> _FakeWorkspaceRepository:
    return _FakeWorkspaceRepository()


@pytest.fixture
def paths() -> _StaticPathProvider:
    return _StaticPathProvider()


@pytest.fixture
def reconciler(
    workspace: _FakeWorkspaceRepository,
    index_repo: InMemoryIndexRepository,
    paths: _StaticPathProvider,
) -> IndexReconciler:
    return IndexReconciler(workspace, index_repo, paths)


# ---- happy-path tests -----------------------------------------------------


def test_rebuild_empty_workspace_returns_zero_report(
    reconciler: IndexReconciler,
    index_repo: InMemoryIndexRepository,
) -> None:
    report = reconciler.rebuild()
    assert report.records_inserted == 0
    assert report.records_built == 0
    assert index_repo.count() == 0
    assert report.is_success


def test_rebuild_single_story() -> None:
    """One root story becomes one record with story_id == self."""
    workspace = _FakeWorkspaceRepository()
    workspace.add(_node(
        id="aaaaaaaa-1111-1111-1111-111111111111",
        title="Story A",
    ))
    paths = _StaticPathProvider({
        "aaaaaaaa-1111-1111-1111-111111111111": "story-a",
    })
    index_repo = InMemoryIndexRepository()
    report = IndexReconciler(workspace, index_repo, paths).rebuild()

    assert report.is_success
    assert report.records_inserted == 1
    assert index_repo.count() == 1
    record = index_repo.get(NodeId("aaaaaaaa-1111-1111-1111-111111111111"))
    assert record.title == "Story A"
    assert record.parent_id is None
    assert record.story_id == NodeId("aaaaaaaa-1111-1111-1111-111111111111")
    assert record.filesystem_path == "story-a"


def test_rebuild_deep_hierarchy_resolves_story_id_at_each_level(
    workspace: _FakeWorkspaceRepository, paths: _StaticPathProvider,
    index_repo: InMemoryIndexRepository,
) -> None:
    """Three-level tree: every Node has the root's id as `story_id`."""
    root = _node(id="11111111-1111-1111-1111-111111111111", title="Root")
    mid = _node(
        id="22222222-2222-2222-2222-222222222222",
        title="Mid",
        parent_id=root.id,
    )
    leaf = _node(
        id="33333333-3333-3333-3333-333333333333",
        title="Leaf",
        parent_id=mid.id,
    )
    workspace.add(root)
    workspace.add(mid, root.id)
    workspace.add(leaf, mid.id)
    paths.set(root.id, "root")
    paths.set(mid.id, "root/mid")
    paths.set(leaf.id, "root/mid/leaf")

    report = IndexReconciler(workspace, index_repo, paths).rebuild()
    assert report.is_success
    assert index_repo.count() == 3

    root_id = root.id
    leaf_record = index_repo.get(leaf.id)
    assert leaf_record.story_id == root_id
    mid_record = index_repo.get(mid.id)
    assert mid_record.story_id == root_id


def test_rebuild_is_idempotent(
    workspace: _FakeWorkspaceRepository,
    paths: _StaticPathProvider,
    index_repo: InMemoryIndexRepository,
) -> None:
    """Two rebuilds against the same workspace produce the same records."""
    root = _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="R")
    child = _node(
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        title="C",
        parent_id=root.id,
    )
    workspace.add(root)
    workspace.add(child, root.id)
    paths.set(root.id, "r")
    paths.set(child.id, "r/c")

    rec = IndexReconciler(workspace, index_repo, paths)
    r1 = rec.rebuild()
    snapshot = sorted(
        (rec_id.title, rec_id.parent_id, rec_id.filesystem_path)
        for rec_id in (
            index_repo.get(root.id),
            index_repo.get(child.id),
        )
    )
    r2 = rec.rebuild()
    snapshot_2 = sorted(
        (rec_id.title, rec_id.parent_id, rec_id.filesystem_path)
        for rec_id in (
            index_repo.get(root.id),
            index_repo.get(child.id),
        )
    )
    assert r1.records_inserted == r2.records_inserted == 2
    assert snapshot == snapshot_2


def test_rebuild_thousands_of_nodes(
    workspace: _FakeWorkspaceRepository,
    paths: _StaticPathProvider,
    index_repo: InMemoryIndexRepository,
) -> None:
    """Scale smoke test: a chain of 2000 nodes must rebuild in one pass."""
    head = _node(id="00000000-0000-0000-0000-000000000001", title="Head")
    workspace.add(head)
    paths.set(head.id, "head")
    cursor = head
    for i in range(2, 2001):
        nid = f"{i:08x}-0000-0000-0000-000000000000"
        next_node = _node(id=nid, title=f"N{i}", parent_id=cursor.id)
        workspace.add(next_node, cursor.id)
        paths.set(next_node.id, f"head/n{i}")
        cursor = next_node

    report = IndexReconciler(workspace, index_repo, paths).rebuild()
    assert report.is_success
    assert index_repo.count() == 2000
    assert report.records_inserted == 2000


# ---- negative-path tests -------------------------------------------------


def test_rebuild_with_corrupt_workspace_marks_error(
    index_repo: InMemoryIndexRepository,
    paths: _StaticPathProvider,
) -> None:
    """A repository that fails `load_tree` produces an error report."""
    workspace = _FakeWorkspaceRepository(corrupt_on_load=True)
    rec = IndexReconciler(workspace, index_repo, paths)
    report = rec.rebuild()

    assert not report.is_success
    assert report.errors
    # Index is untouched.
    assert index_repo.count() == 0


def test_rebuild_with_failing_path_provider_marks_error(
    workspace: _FakeWorkspaceRepository,
    index_repo: InMemoryIndexRepository,
) -> None:
    """A path provider that always raises aborts the rebuild."""
    node = _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="X")
    workspace.add(node)
    rec = IndexReconciler(workspace, index_repo, _RaisingPathProvider())
    report = rec.rebuild()
    assert not report.is_success
    assert any("path lookup failed" in e for e in report.errors)


def test_rebuild_on_populated_index_replaces(
    workspace: _FakeWorkspaceRepository,
    paths: _StaticPathProvider,
    index_repo: InMemoryIndexRepository,
) -> None:
    """A pre-populated index is fully replaced (deleted_count > 0)."""
    # Seed phantom ids that aren't on disk.
    now = datetime.now(timezone.utc)
    index_repo.upsert(
        IndexRecord(
            node_id=NodeId("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            parent_id=None,
            story_id=NodeId("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            title="Phantom",
            node_type="story",
            filesystem_path="phantom",
            created_at=now,
            updated_at=now,
        )
    )
    real = _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="Real")
    workspace.add(real)
    paths.set(real.id, "real")
    report = IndexReconciler(workspace, index_repo, paths).rebuild()
    assert report.is_success
    assert report.records_deleted == 1
    assert index_repo.count() == 1
    with pytest.raises(Exception):
        index_repo.get(NodeId("cccccccc-cccc-cccc-cccc-cccccccccccc"))


# ---- determinism ---------------------------------------------------------


def test_rebuild_is_deterministic_across_construction_order(
    paths: _StaticPathProvider,
) -> None:
    """Two workspaces built in valid-add orders but with different
    path registrations produce the same final index
    (because the reconciler sorts by node_id)."""
    a = _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="A")
    b = _node(id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", title="B", parent_id=a.id)
    c = _node(
        id="cccccccc-cccc-cccc-cccc-cccccccccccc", title="C", parent_id=b.id
    )

    # Sequence 1 — paths registered in node-id order.
    seq1_workspace = _FakeWorkspaceRepository()
    seq1_workspace.add(a)
    seq1_workspace.add(b, a.id)
    seq1_workspace.add(c, b.id)
    p1 = _StaticPathProvider({
        a.id: "a",
        b.id: "a/b",
        c.id: "a/b/c",
    })
    r1_index = InMemoryIndexRepository()
    IndexReconciler(seq1_workspace, r1_index, p1).rebuild()

    # Sequence 2 — paths registered in reverse order.
    seq2_workspace = _FakeWorkspaceRepository()
    seq2_workspace.add(a)
    seq2_workspace.add(b, a.id)
    seq2_workspace.add(c, b.id)
    p2 = _StaticPathProvider({
        c.id: "a/b/c",
        b.id: "a/b",
        a.id: "a",
    })
    r2_index = InMemoryIndexRepository()
    IndexReconciler(seq2_workspace, r2_index, p2).rebuild()

    for nid in (a.id, b.id, c.id):
        a_rec = r1_index.get(nid)
        b_rec = r2_index.get(nid)
        assert a_rec.title == b_rec.title
        assert a_rec.parent_id == b_rec.parent_id
        assert a_rec.story_id == b_rec.story_id
        assert a_rec.filesystem_path == b_rec.filesystem_path


# ---- partial failures ----------------------------------------------------


def test_rebuild_aborts_on_unresolvable_node(
    workspace: _FakeWorkspaceRepository,
    paths: _StaticPathProvider,
    index_repo: InMemoryIndexRepository,
) -> None:
    """If one Node's path can't be found, the rebuild is aborted
    (the failure surfaces in `errors`) — we never silently drop
    a Node from the index."""
    good = _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="Good")
    bad = _node(id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", title="Bad")
    workspace.add(good)
    workspace.add(bad)
    paths.set(good.id, "good")
    # `bad` is in the deny-list → path_for raises.
    provider = _DeniedFor(paths, denied={"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"})

    rec = IndexReconciler(workspace, index_repo, provider)
    report = rec.rebuild()
    assert not report.is_success
    assert any("path lookup failed" in e for e in report.errors)
    # Index is empty: replace_all rolled back.
    assert index_repo.count() == 0


# ---- helpers for one-shot tests -----------------------------------------


def _setup_tree(nodes: Iterable[Node]) -> Tree:
    """Build a Tree containing exactly the listed Nodes (caller
    must pre-link parents; this helper only adds them)."""
    tree = Tree()
    for n in nodes:
        if n.parent_id is not None:
            tree.add(n.with_children([]))  # ensure parent present first
        # correct add order:
    # The above approach is fragile; build deterministically:
    tree = Tree()
    sorted_nodes = sorted(nodes, key=lambda nn: (nn.parent_id or "", nn.id))
    for n in sorted_nodes:
        if n.parent_id is None:
            tree.add(n)
        else:
            tree.add(n)
            tree.attach(n.id, n.parent_id)
    return tree
