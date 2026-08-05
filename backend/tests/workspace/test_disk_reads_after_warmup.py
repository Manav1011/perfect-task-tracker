"""Tests that the cache eliminates disk reads after warmup.

We wrap `LocalFilesystem` with a proxy that counts every
load_node / list_children call. After the cache is populated:

    - `load_node` should not touch the filesystem at all.
    - `load_children` should not touch the filesystem.

If the cache is cleared, the next `load_node` is a cold
read — the proxy counter ticks up once.
"""

from __future__ import annotations

from pathlib import Path

from backend.domain import Node, NodeMetadata, NodeType, new_node_id
from backend.filesystem import LocalFilesystem, WorkspaceRoot
from backend.repositories import LocalWorkspaceRepository
from backend.workspace import InMemoryWorkspaceCache


class _CountingFilesystem:
    """Proxy around `LocalFilesystem` that counts load_node /
    list_children calls.

    We instrument only the read methods (the ones the cache
    short-circuits). Writes pass through unchanged.
    """

    def __init__(self, inner: LocalFilesystem) -> None:
        self._inner = inner
        self.load_node_count = 0
        self.list_children_count = 0

    @property
    def root(self):
        return self._inner.root

    def node_dir(self, node_id):
        return self._inner.node_dir(node_id)

    def load_node(self, node_id):
        self.load_node_count += 1
        return self._inner.load_node(node_id)

    def list_children(self, node_id):
        self.list_children_count += 1
        return self._inner.list_children(node_id)

    # ---- writes (passed through) ----

    def create_node(self, node, parent_id=None):
        return self._inner.create_node(node, parent_id=parent_id)

    def rename_node(self, node_id, new_title):
        return self._inner.rename_node(node_id, new_title)

    def move_node(self, node_id, new_parent_id=None, position=None):
        return self._inner.move_node(
            node_id, new_parent_id=new_parent_id, position=position
        )

    def delete_node(self, node_id):
        return self._inner.delete_node(node_id)

    def read_canvas(self, node_id):
        return self._inner.read_canvas(node_id)

    def write_canvas(self, node_id, content):
        return self._inner.write_canvas(node_id, content)


def _setup(tmp_path: Path, node_count: int = 10):
    """Build a workspace with `node_count` root nodes."""
    workspace = WorkspaceRoot.open(tmp_path, create=True)
    real_fs = LocalFilesystem(workspace)
    fs = _CountingFilesystem(real_fs)
    cache = InMemoryWorkspaceCache()
    repo = LocalWorkspaceRepository(fs, cache=cache)  # type: ignore[arg-type]

    for i in range(node_count):
        node = Node(
            id=new_node_id(),
            title=f"n-{i}",
            type=NodeType.STORY,
            metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
        )
        repo.save_node(node, parent_id=None)

    # Populate the cache.
    cache.populate(repo.load_tree())

    return repo, cache, fs


def test_load_node_does_not_touch_disk_after_warmup(tmp_path: Path) -> None:
    """After warmup, repo.load_node returns the cached node
    without invoking the filesystem.
    """
    repo, cache, fs = _setup(tmp_path, node_count=5)
    target_id = next(iter(cache.load_tree().all_nodes())).id

    # Reset counters after warmup (the warmup itself walked
    # the disk).
    fs.load_node_count = 0
    fs.list_children_count = 0

    # Several reads — should all hit the cache.
    for _ in range(20):
        result = repo.load_node(target_id)
        assert result.id == target_id

    assert fs.load_node_count == 0, (
        f"expected zero disk reads after warmup, got {fs.load_node_count}"
    )


def test_load_children_does_not_touch_disk_after_warmup(
    tmp_path: Path,
) -> None:
    """After warmup, repo.load_children returns the cached
    children list without invoking the filesystem.
    """
    repo, cache, fs = _setup(tmp_path, node_count=3)
    target_id = next(iter(cache.load_tree().all_nodes())).id

    fs.load_node_count = 0
    fs.list_children_count = 0

    for _ in range(20):
        children = repo.load_children(target_id)
        # We seeded only root nodes, so children list is empty.
        assert children == []

    assert fs.list_children_count == 0, (
        f"expected zero disk reads after warmup, got {fs.list_children_count}"
    )


def test_load_node_touches_disk_after_cache_clear(tmp_path: Path) -> None:
    """After cache.clear(), the next load_node goes to disk.
    Self-heal behavior: the repository falls back to fs.
    """
    repo, cache, fs = _setup(tmp_path, node_count=3)
    target_id = next(iter(cache.load_tree().all_nodes())).id

    # Cache clear.
    cache.clear()

    fs.load_node_count = 0
    result = repo.load_node(target_id)
    assert result.id == target_id
    # We count exactly one disk read (the self-heal path).
    assert fs.load_node_count == 1


def test_load_node_after_invalidate_touches_disk(tmp_path: Path) -> None:
    """After cache.invalidate(id), the next load_node for that
    id goes to disk (self-heal).
    """
    repo, cache, fs = _setup(tmp_path, node_count=3)
    target_id = next(iter(cache.load_tree().all_nodes())).id

    cache.invalidate(target_id)

    fs.load_node_count = 0
    result = repo.load_node(target_id)
    assert result.id == target_id
    assert fs.load_node_count == 1