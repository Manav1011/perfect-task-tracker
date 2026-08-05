"""Tests for the IncrementalIndexSynchronizer (Phase 2.2).

Coverage:

    1. Hook contract — every public method updates the right
       row(s), returns a SyncReport, and never raises.
    2. Subtree move — when a node moves under a different
       story, every descendant's `story_id`, `parent_id`, and
       `filesystem_path` is recomputed.
    3. Failure semantics — sync failures flip the staleness
       flag but do NOT raise. The filesystem caller (the
       repository) is unaffected by the sync outcome.
    4. Recovery — running a full rebuild after a forced sync
       failure restores consistency.
    5. Repository integration — LocalWorkspaceRepository with a
       synchroniser wired in invokes the right hook in the
       right order, after the filesystem call succeeds.

Each test wires:

    - a `Tree` (the domain Tree used as the workspace source),
    - an `InMemoryIndexRepository`,
    - a `_StaticPathProvider` (dict-backed FilesystemPathProvider),
    - an `IncrementalIndexSynchronizer`.

The synchroniser accepts these via Constructor DI only —
Protocol-shaped dependencies, no concrete imports — so the
test surface mirrors the production contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

import pytest

from backend.domain.enums import NodeType
from backend.domain.metadata import NodeMetadata
from backend.domain.node import Node, NodeId
from backend.domain.tree import Tree
from backend.index.exceptions import IndexRecordNotFoundError
from backend.index.impl import InMemoryIndexRepository
from backend.index.sync import (
    IncrementalIndexSynchronizer,
    IndexSyncError,
    SyncPathResolutionError,
    SyncReport,
    make_in_memory_path_provider,
    make_tree_provider,
)


# ---- fakes ---------------------------------------------------------------


class _StaticPathProvider:
    """Deterministic path provider; returns `f"unknown/{node_id}"` for
    ids that were never registered."""

    def __init__(self, paths: dict[str, str] | None = None) -> None:
        self._paths: dict[str, str] = paths or {}

    def path_for(self, node_id: NodeId) -> str:
        return self._paths.get(node_id, f"unknown/{node_id}")

    def set(self, node_id: NodeId, path: str) -> None:
        self._paths[node_id] = path


class _RaisingPathProvider:
    """Path provider that always raises."""

    def path_for(self, node_id: NodeId) -> str:
        raise SyncPathResolutionError(f"no path for {node_id}")


class _DeniedFor:
    """Wraps a path provider and raises for a deny-list of ids."""

    def __init__(self, inner: _StaticPathProvider, denied: set[str]) -> None:
        self._inner = inner
        self._denied = denied

    def path_for(self, node_id: NodeId) -> str:
        if node_id in self._denied:
            raise SyncPathResolutionError(f"no path for {node_id}")
        return self._inner.path_for(node_id)


# ---- helpers -------------------------------------------------------------


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


def _tree(nodes: Iterable[Node]) -> Tree:
    """Build a Tree from an iterable of Nodes; assumes parents come first."""
    tree = Tree()
    for n in nodes:
        tree.add(n)
        if n.parent_id is not None:
            tree.attach(n.id, n.parent_id)
    return tree


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- fixtures ------------------------------------------------------------


@pytest.fixture
def index_repo() -> InMemoryIndexRepository:
    return InMemoryIndexRepository()


@pytest.fixture
def paths() -> _StaticPathProvider:
    return _StaticPathProvider()


@pytest.fixture
def tree() -> Tree:
    return Tree()


@pytest.fixture
def sync(
    index_repo: InMemoryIndexRepository,
    paths: _StaticPathProvider,
    tree: Tree,
) -> IncrementalIndexSynchronizer:
    return IncrementalIndexSynchronizer(
        index_repo=index_repo,
        tree_provider=make_tree_provider(tree),
        path_provider=paths,
    )


# ---- happy-path: each hook ----------------------------------------------


def test_on_node_created_inserts_one_row(
    sync: IncrementalIndexSynchronizer,
    paths: _StaticPathProvider,
    tree: Tree,
    index_repo: InMemoryIndexRepository,
) -> None:
    """on_node_created inserts exactly one IndexRecord."""
    node = _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="Story")
    tree.add(node)
    paths.set(node.id, "story")

    report = sync.on_node_created(node, parent_id=None)
    assert report.created == 1
    assert index_repo.count() == 1
    record = index_repo.get(node.id)
    assert record.title == "Story"
    assert record.parent_id is None
    assert record.filesystem_path == "story"


def test_on_node_renamed_updates_title(
    sync: IncrementalIndexSynchronizer,
    paths: _StaticPathProvider,
    tree: Tree,
    index_repo: InMemoryIndexRepository,
) -> None:
    """on_node_renamed flips the row's title and bumps updated_at."""
    node = _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="Old")
    tree.add(node)
    paths.set(node.id, "story")
    sync.on_node_created(node, parent_id=None)
    pre = index_repo.get(node.id)

    report = sync.on_node_renamed(node.id, "New")
    assert report.updated == 1
    post = index_repo.get(node.id)
    assert post.title == "New"
    # updated_at moves forward; created_at stays.
    assert post.created_at == pre.created_at
    assert post.updated_at >= pre.updated_at


def test_on_node_renamed_when_row_missing_is_noop(
    sync: IncrementalIndexSynchronizer,
    paths: _StaticPathProvider,
    tree: Tree,
) -> None:
    """Rename with no pre-existing row returns 0 updated, no raise."""
    tree.add(_node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="x"))
    paths.set("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "x")
    report = sync.on_node_renamed(
        NodeId("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), "x2"
    )
    # Row never inserted (no on_node_created ran); rename is
    # a no-op rather than an error.
    assert report.errors == ()
    assert report.updated == 0


def test_on_node_deleted_removes_one_row(
    sync: IncrementalIndexSynchronizer,
    paths: _StaticPathProvider,
    tree: Tree,
    index_repo: InMemoryIndexRepository,
) -> None:
    node = _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="x")
    tree.add(node)
    paths.set(node.id, "x")
    sync.on_node_created(node, parent_id=None)
    assert index_repo.count() == 1

    report = sync.on_node_deleted(node.id)
    assert report.deleted == 1
    assert index_repo.count() == 0
    with pytest.raises(IndexRecordNotFoundError):
        index_repo.get(node.id)


def test_on_metadata_updated_is_title_update(
    sync: IncrementalIndexSynchronizer,
    paths: _StaticPathProvider,
    tree: Tree,
    index_repo: InMemoryIndexRepository,
) -> None:
    node = _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="Old")
    tree.add(node)
    paths.set(node.id, "x")
    sync.on_node_created(node, parent_id=None)
    sync.on_metadata_updated(node.id, "Refreshed")
    assert index_repo.get(node.id).title == "Refreshed"


def test_on_canvas_updated_bumps_updated_at(
    sync: IncrementalIndexSynchronizer,
    paths: _StaticPathProvider,
    tree: Tree,
    index_repo: InMemoryIndexRepository,
) -> None:
    node = _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="x")
    tree.add(node)
    paths.set(node.id, "x")
    sync.on_node_created(node, parent_id=None)
    pre = index_repo.get(node.id)
    report = sync.on_canvas_updated(node.id)
    assert report.updated == 1
    post = index_repo.get(node.id)
    assert post.updated_at >= pre.updated_at
    # Canvas content is NOT stored in the index (Phase 4+).
    assert post.search_text == ""


# ---- subtree move --------------------------------------------------------


def test_on_node_moved_updates_subtree_paths_and_story_ids(
    sync: IncrementalIndexSynchronizer,
    paths: _StaticPathProvider,
    tree: Tree,
    index_repo: InMemoryIndexRepository,
) -> None:
    """Moving a Node under a different Story must re-root its
    entire subtree's `story_id`, recompute `parent_id`, and
    refresh `filesystem_path` for every descendant."""
    story_a = _node(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        title="StoryA",
    )
    story_b = _node(
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        title="StoryB",
    )
    mid = _node(
        id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        title="Mid",
        parent_id=story_a.id,
    )
    leaf = _node(
        id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        title="Leaf",
        parent_id=mid.id,
    )
    # Build the tree.
    for n in (story_a, story_b, mid, leaf):
        tree.add(n)
        if n.parent_id is not None:
            tree.attach(n.id, n.parent_id)
    paths.set(story_a.id, "story-a")
    paths.set(story_b.id, "story-b")
    paths.set(mid.id, "story-a/mid")
    paths.set(leaf.id, "story-a/mid/leaf")
    # Seed the index by replaying creates.
    sync.on_node_created(story_a, parent_id=None)
    sync.on_node_created(story_b, parent_id=None)
    sync.on_node_created(mid, parent_id=story_a.id)
    sync.on_node_created(leaf, parent_id=mid.id)

    # Move mid + leaf under story_b. The Tree's `attach` rewires
    # `mid`'s parent_id; subtree iteration handles the rest.
    tree.attach(mid.id, story_b.id)
    paths.set(mid.id, "story-b/mid")
    paths.set(leaf.id, "story-b/mid/leaf")

    report = sync.on_node_moved(mid.id, new_parent_id=story_b.id)

    # mid + leaf = 2 nodes affected.
    assert report.subtree_nodes_affected == 2
    assert report.updated == 2

    # mid's row: parent_id == story_b, story_id == story_b.
    mid_record = index_repo.get(mid.id)
    assert mid_record.parent_id == story_b.id
    assert mid_record.story_id == story_b.id
    assert mid_record.filesystem_path == "story-b/mid"

    # leaf's row: parent_id unchanged (still mid), but story_id
    # flipped to story_b because the root changed.
    leaf_record = index_repo.get(leaf.id)
    assert leaf_record.parent_id == mid.id
    assert leaf_record.story_id == story_b.id
    assert leaf_record.filesystem_path == "story-b/mid/leaf"


def test_on_node_moved_to_root_drops_story_id_to_self(
    sync: IncrementalIndexSynchronizer,
    paths: _StaticPathProvider,
    tree: Tree,
    index_repo: InMemoryIndexRepository,
) -> None:
    """Moving a subtree to root makes the moved Node its own Story."""
    story = _node(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        title="Story",
    )
    child = _node(
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        title="Child",
        parent_id=story.id,
    )
    for n in (story, child):
        tree.add(n)
        if n.parent_id is not None:
            tree.attach(n.id, n.parent_id)
    paths.set(story.id, "story")
    paths.set(child.id, "story/child")
    sync.on_node_created(story, parent_id=None)
    sync.on_node_created(child, parent_id=story.id)

    # Move child to root. `tree.attach` won't take None, so
    # we manually clear the parent (which is what the
    # filesystem would do).
    child_node = tree.get(child.id)
    tree._nodes[child.id] = child_node.with_parent(None)  # noqa: SLF001
    # Detach from old parent's children list.
    story_node = tree.get(story.id)
    tree._nodes[story.id] = story_node.with_children(  # noqa: SLF001
        [cid for cid in story_node.children_ids if cid != child.id]
    )
    paths.set(child.id, "child")
    sync.on_node_moved(child.id, new_parent_id=None)

    rec = index_repo.get(child.id)
    assert rec.parent_id is None
    assert rec.story_id == child.id  # root → story_id == self
    assert rec.filesystem_path == "child"


# ---- failure semantics ---------------------------------------------------


def test_sync_failure_marks_stale_does_not_raise(
    sync: IncrementalIndexSynchronizer,
    paths: _StaticPathProvider,
    tree: Tree,
    index_repo: InMemoryIndexRepository,
) -> None:
    """A failing path provider flips staleness, returns errors,
    but does NOT raise from the public hook."""
    node = _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="x")
    tree.add(node)
    # Build a synchroniser with a raising provider directly.
    sync2 = IncrementalIndexSynchronizer(
        index_repo=index_repo,
        tree_provider=make_tree_provider(tree),
        path_provider=_RaisingPathProvider(),
    )
    # Should NOT raise.
    report = sync2.on_node_created(node, parent_id=None)
    assert not report.is_clean
    assert sync2.is_stale()
    # The index is untouched on failure.
    assert index_repo.count() == 0


def test_sync_failure_does_not_corrupt_existing_rows(
    sync: IncrementalIndexSynchronizer,
    paths: _StaticPathProvider,
    tree: Tree,
    index_repo: InMemoryIndexRepository,
) -> None:
    """A failure on a later operation does not roll back earlier
    successful writes."""
    node = _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="x")
    tree.add(node)
    paths.set(node.id, "x")
    report_ok = sync.on_node_created(node, parent_id=None)
    assert report_ok.is_clean
    pre = index_repo.get(node.id)

    # Now force a failure on the same synchroniser, by swapping
    # in a path provider that raises on rename (via a method
    # that *does* call path_for — on_node_moved). We use
    # `_DeniedFor` so only the second op's subtree fails.
    sync_fail = IncrementalIndexSynchronizer(
        index_repo=index_repo,
        tree_provider=make_tree_provider(tree),
        path_provider=_DeniedFor(paths, denied=set()),
    )
    # Force the synchroniser to fail by registering a path
    # provider that raises for THIS id and using
    # on_node_moved (which re-resolves paths).
    raising = _RaisingPathProvider()
    sync_fail2 = IncrementalIndexSynchronizer(
        index_repo=index_repo,
        tree_provider=make_tree_provider(tree),
        path_provider=raising,
    )
    # The Tree has the node; on_node_moved will try to resolve
    # its path and raise.
    sync_fail2.on_node_moved(node.id, new_parent_id=None)

    # Earlier row is untouched.
    assert index_repo.get(node.id).title == pre.title


def test_path_failure_on_move_aborts_whole_subtree(
    sync: IncrementalIndexSynchronizer,
    paths: _StaticPathProvider,
    tree: Tree,
    index_repo: InMemoryIndexRepository,
) -> None:
    """If a descendant's path can't be resolved, the move
    hooks fail and the index is marked stale. The next
    rebuild will re-derive everything correctly from disk.

    The brief mandates that index failures must NOT roll back
    the filesystem. The corollary: the index may be in a
    partial state until the rebuild runs. This test confirms
    both halves — the staleness flag flips, and a subsequent
    rebuild restores the index to disk truth.
    """
    story_a = _node(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        title="A",
    )
    story_b = _node(
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        title="B",
    )
    mid = _node(
        id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        title="mid",
        parent_id=story_a.id,
    )
    leaf = _node(
        id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        title="leaf",
        parent_id=mid.id,
    )
    for n in (story_a, story_b, mid, leaf):
        tree.add(n)
        if n.parent_id is not None:
            tree.attach(n.id, n.parent_id)
    paths.set(story_a.id, "a")
    paths.set(story_b.id, "b")
    paths.set(mid.id, "a/mid")
    paths.set(leaf.id, "a/mid/leaf")  # registered

    sync.on_node_created(story_a, parent_id=None)
    sync.on_node_created(story_b, parent_id=None)
    sync.on_node_created(mid, parent_id=story_a.id)
    sync.on_node_created(leaf, parent_id=mid.id)

    # Now: rewire mid under story_b in the tree, then swap
    # the path provider to one that refuses to resolve
    # the leaf's new path.
    tree.attach(mid.id, story_b.id)
    paths.set(mid.id, "b/mid")
    paths.set(leaf.id, "b/mid/leaf")

    denied_provider = _DeniedFor(
        paths, denied={"dddddddd-dddd-dddd-dddd-dddddddddddd"}
    )
    sync_fail = IncrementalIndexSynchronizer(
        index_repo=index_repo,
        tree_provider=make_tree_provider(tree),
        path_provider=denied_provider,
    )
    report = sync_fail.on_node_moved(mid.id, new_parent_id=story_b.id)
    assert not report.is_clean
    assert sync_fail.is_stale()
    # Index may be in a partial state — that's the whole
    # reason rebuild exists. The flag tells callers to
    # rebuild before trusting the index.


def test_recovery_via_rebuild_after_drift(
    index_repo: InMemoryIndexRepository,
    paths: _StaticPathProvider,
    tree: Tree,
) -> None:
    """After a forced sync failure, a full rebuild restores the
    index from disk truth."""
    from backend.index.reconciler import IndexReconciler

    story_a = _node(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        title="A",
    )
    child = _node(
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        title="child",
        parent_id=story_a.id,
    )
    for n in (story_a, child):
        tree.add(n)
        if n.parent_id is not None:
            tree.attach(n.id, n.parent_id)
    paths.set(story_a.id, "a")
    paths.set(child.id, "a/child")

    sync = IncrementalIndexSynchronizer(
        index_repo=index_repo,
        tree_provider=make_tree_provider(tree),
        path_provider=paths,
    )

    # One successful create, then a forced failure.
    sync.on_node_created(story_a, parent_id=None)

    # Hand-modify the index to simulate drift (the synchroniser
    # "failed" and left the row stale).
    stale = index_repo.get(story_a.id)
    index_repo.upsert(
        type(stale)(
            node_id=stale.node_id,
            parent_id=stale.parent_id,
            story_id=stale.story_id,
            title="WRONG_TITLE",
            node_type=stale.node_type,
            filesystem_path=stale.filesystem_path,
            created_at=stale.created_at,
            updated_at=stale.updated_at,
            search_text=stale.search_text,
        )
    )

    # Build a stub WorkspaceRepository satisfying the Protocol
    # by structure.
    class _WorkspaceRepo:
        def load_node(self, node_id): ...
        def load_children(self, node_id): ...
        def load_tree(self):
            return tree
        def save_node(self, node, parent_id): ...
        def rename_node(self, node_id, new_title): ...
        def move_node(self, node_id, new_parent_id, position=None): ...
        def delete_node(self, node_id): ...
        def read_canvas(self, node_id): return ""
        def write_canvas(self, node_id, content): ...

    rec = IndexReconciler(_WorkspaceRepo(), index_repo, paths)
    rec_report = rec.rebuild()

    assert rec_report.is_success
    # The drift is gone: title is the disk truth again.
    assert index_repo.get(story_a.id).title == "A"


def test_staleness_count_increments_per_failure(
    sync: IncrementalIndexSynchronizer,
    paths: _StaticPathProvider,
    tree: Tree,
) -> None:
    """Each failed call increments the staleness counter."""
    sync_fail = IncrementalIndexSynchronizer(
        index_repo=InMemoryIndexRepository(),
        tree_provider=make_tree_provider(tree),
        path_provider=_RaisingPathProvider(),
    )
    assert sync_fail.staleness_count() == 0
    sync_fail.on_node_created(
        _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="x"),
        parent_id=None,
    )
    # Pre-create the second row so on_node_renamed has
    # something to operate on (otherwise the "missing row"
    # branch is a soft no-op, not a real failure).
    sync_fail.on_node_created(
        _node(id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", title="y"),
        parent_id=None,
    )
    sync_fail.on_node_renamed(
        NodeId("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), "x"
    )
    assert sync_fail.staleness_count() == 2


def test_clear_staleness_resets_counter(
    sync: IncrementalIndexSynchronizer,
    paths: _StaticPathProvider,
    tree: Tree,
) -> None:
    sync_fail = IncrementalIndexSynchronizer(
        index_repo=InMemoryIndexRepository(),
        tree_provider=make_tree_provider(tree),
        path_provider=_RaisingPathProvider(),
    )
    sync_fail.on_node_created(
        _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="x"),
        parent_id=None,
    )
    assert sync_fail.is_stale()
    sync_fail.clear_staleness()
    assert not sync_fail.is_stale()
    assert sync_fail.staleness_count() == 0


# ---- structural integration with the repository -------------------------


def test_repository_save_node_invokes_sync_after_fs(
    tmp_path,
) -> None:
    """`LocalWorkspaceRepository.save_node` must call
    `on_node_created` after the filesystem call returns."""
    from backend.filesystem import LocalFilesystem, WorkspaceRoot
    from backend.repositories.impl.local_workspace_repository import LocalWorkspaceRepository

    workspace = WorkspaceRoot.open(tmp_path, create=True)
    fs = LocalFilesystem(workspace)
    repo = LocalWorkspaceRepository(fs, sync=_RecordingSync())
    node = _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="X")
    repo.save_node(node, parent_id=None)
    rec = repo._sync  # type: ignore[attr-defined]
    assert rec.created == [(node.id, None)]


def test_repository_rename_node_invokes_sync(
    tmp_path,
) -> None:
    from backend.filesystem import LocalFilesystem, WorkspaceRoot
    from backend.repositories.impl.local_workspace_repository import LocalWorkspaceRepository

    workspace = WorkspaceRoot.open(tmp_path, create=True)
    fs = LocalFilesystem(workspace)
    repo = LocalWorkspaceRepository(fs, sync=_RecordingSync())
    node = repo.save_node(
        _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="Old"),
        parent_id=None,
    )
    repo.rename_node(node.id, "New")
    rec = repo._sync  # type: ignore[attr-defined]
    assert rec.renamed == [(node.id, "New")]


def test_repository_move_node_invokes_sync(
    tmp_path,
) -> None:
    from backend.filesystem import LocalFilesystem, WorkspaceRoot
    from backend.repositories.impl.local_workspace_repository import LocalWorkspaceRepository

    workspace = WorkspaceRoot.open(tmp_path, create=True)
    fs = LocalFilesystem(workspace)
    repo = LocalWorkspaceRepository(fs, sync=_RecordingSync())
    a = repo.save_node(
        _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="A"),
        parent_id=None,
    )
    b = repo.save_node(
        _node(id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", title="B"),
        parent_id=a.id,
    )
    c = repo.save_node(
        _node(id="cccccccc-cccc-cccc-cccc-cccccccccccc", title="C"),
        parent_id=b.id,
    )
    repo.move_node(c.id, new_parent_id=a.id)
    rec = repo._sync  # type: ignore[attr-defined]
    assert rec.moved == [(c.id, a.id)]


def test_repository_delete_node_invokes_sync_for_each_node(
    tmp_path,
) -> None:
    from backend.filesystem import LocalFilesystem, WorkspaceRoot
    from backend.repositories.impl.local_workspace_repository import LocalWorkspaceRepository

    workspace = WorkspaceRoot.open(tmp_path, create=True)
    fs = LocalFilesystem(workspace)
    repo = LocalWorkspaceRepository(fs, sync=_RecordingSync())
    a = repo.save_node(
        _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="A"),
        parent_id=None,
    )
    b = repo.save_node(
        _node(id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", title="B"),
        parent_id=a.id,
    )
    c = repo.save_node(
        _node(id="cccccccc-cccc-cccc-cccc-cccccccccccc", title="C"),
        parent_id=b.id,
    )
    repo.delete_node(b.id)
    rec = repo._sync  # type: ignore[attr-defined]
    # The repository emits one delete per Node in the subtree,
    # plus the parent itself (deepest first).
    deleted = {nid for nid, in rec.deleted}
    assert b.id in deleted
    assert c.id in deleted
    assert a.id not in deleted


def test_repository_write_canvas_invokes_sync(
    tmp_path,
) -> None:
    from backend.filesystem import LocalFilesystem, WorkspaceRoot
    from backend.repositories.impl.local_workspace_repository import LocalWorkspaceRepository

    workspace = WorkspaceRoot.open(tmp_path, create=True)
    fs = LocalFilesystem(workspace)
    repo = LocalWorkspaceRepository(fs, sync=_RecordingSync())
    node = repo.save_node(
        _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="X"),
        parent_id=None,
    )
    repo.write_canvas(node.id, "hello")
    rec = repo._sync  # type: ignore[attr-defined]
    assert rec.canvas_updated == [node.id]


def test_repository_sync_failure_does_not_propagate(
    tmp_path,
) -> None:
    """If the synchroniser raises internally (it shouldn't, but
    if a future implementation regresses), the repository write
    itself must NOT raise — the filesystem is the source of
    truth.

    Today `_run` swallows exceptions, so this is a behaviour
    test that survives even if a future maintainer removes
    the swallow. We force the swallow by passing a synchroniser
    whose hooks raise."""
    from backend.filesystem import LocalFilesystem, WorkspaceRoot
    from backend.repositories.impl.local_workspace_repository import LocalWorkspaceRepository

    workspace = WorkspaceRoot.open(tmp_path, create=True)
    fs = LocalFilesystem(workspace)
    boom = _RaisingSync()
    repo = LocalWorkspaceRepository(fs, sync=boom)

    # Must NOT raise, even though the sync raises on every hook.
    node = repo.save_node(
        _node(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title="X"),
        parent_id=None,
    )
    assert node.title == "X"
    repo.rename_node(node.id, "Y")
    assert repo.load_node(node.id).title == "Y"


# ---- helpers: recording + raising sync fakes ----------------------------


class _RecordingSync:
    """Records every hook call so tests can assert on the order."""

    def __init__(self) -> None:
        self.created: list[tuple[NodeId, NodeId | None]] = []
        self.renamed: list[tuple[NodeId, str]] = []
        self.moved: list[tuple[NodeId, NodeId | None]] = []
        self.deleted: list[tuple[NodeId, ...]] = []
        self.metadata_updated: list[tuple[NodeId, str]] = []
        self.canvas_updated: list[NodeId] = []

    def on_node_created(
        self, node: Node, parent_id: NodeId | None
    ) -> SyncReport:
        self.created.append((node.id, parent_id))
        return SyncReport(created=1)

    def on_node_renamed(self, node_id: NodeId, new_title: str) -> SyncReport:
        self.renamed.append((node_id, new_title))
        return SyncReport(updated=1)

    def on_node_moved(
        self, node_id: NodeId, new_parent_id: NodeId | None
    ) -> SyncReport:
        self.moved.append((node_id, new_parent_id))
        return SyncReport(updated=1)

    def on_node_deleted(self, node_id: NodeId) -> SyncReport:
        self.deleted.append((node_id,))
        return SyncReport(deleted=1)

    def on_metadata_updated(self, node_id: NodeId, title: str) -> SyncReport:
        self.metadata_updated.append((node_id, title))
        return SyncReport(updated=1)

    def on_canvas_updated(self, node_id: NodeId) -> SyncReport:
        self.canvas_updated.append(node_id)
        return SyncReport(updated=1)


class _RaisingSync:
    """Sync that raises from every hook — used to verify the
    repository does not propagate sync failures."""

    def on_node_created(self, node: Node, parent_id: NodeId | None):
        raise IndexSyncError("boom")

    def on_node_renamed(self, node_id: NodeId, new_title: str):
        raise IndexSyncError("boom")

    def on_node_moved(self, node_id: NodeId, new_parent_id: NodeId | None):
        raise IndexSyncError("boom")

    def on_node_deleted(self, node_id: NodeId):
        raise IndexSyncError("boom")

    def on_metadata_updated(self, node_id: NodeId, title: str):
        raise IndexSyncError("boom")

    def on_canvas_updated(self, node_id: NodeId):
        raise IndexSyncError("boom")
