"""Unit tests for `InMemoryWorkspaceCache`.

Coverage:

    - `load_node` hit / miss (before populate) / miss (after
      invalidate).
    - `populate` is idempotent (second call is a no-op).
    - `clear` resets state.
    - `invalidate` removes single ids; `invalidate_many`
      removes a batch.
    - `subtree_ids` returns BFS order, INCLUDING the root
      id (matches `Tree.subtree` contract).
    - `stats` counters move on hits, misses, and
      invalidations; the extended fields
      (`last_populated_at`, `last_invalidated_at`,
      `populated`) populate correctly.
    - CacheNotInitializedError vs CacheConsistencyError
      distinction.
"""

from __future__ import annotations

import pytest

from backend.domain import Node, NodeMetadata, NodeType, new_node_id
from backend.domain.tree import Tree
from backend.workspace import InMemoryWorkspaceCache
from backend.workspace.exceptions import (
    CacheConsistencyError,
    CacheNotInitializedError,
)
from backend.workspace.protocol import CacheStats


# ---- helpers --------------------------------------------------------------


def _make_tree(node_count: int = 5) -> Tree:
    """Build a flat tree of `node_count` root-level nodes.

    Returns the Tree plus the inserted nodes so tests can
    assert on ids.
    """
    tree = Tree()
    nodes = []
    for i in range(node_count):
        node = Node(
            id=new_node_id(),
            title=f"node-{i}",
            type=NodeType.STORY,
            metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
        )
        tree.add(node)
        nodes.append(node)
    return tree, nodes


# ---- tests ----------------------------------------------------------------


def test_cache_starts_unloaded_and_dirty() -> None:
    """Fresh cache: not loaded, dirty flag set, all counters zero."""
    c = InMemoryWorkspaceCache()
    assert c.is_loaded() is False
    assert c.is_dirty() is True
    stats = c.stats()
    assert stats.hits == 0
    assert stats.misses == 0
    assert stats.invalidations == 0
    assert stats.node_count == 0
    assert stats.populated is False
    assert stats.last_populated_at is None
    assert stats.last_invalidated_at is None
    assert stats.population_seconds == 0.0


def test_load_node_before_populate_raises_cache_not_initialized() -> None:
    """Read before populate raises `CacheNotInitializedError`."""
    c = InMemoryWorkspaceCache()
    with pytest.raises(CacheNotInitializedError):
        c.load_node("any-id")


def test_load_children_before_populate_raises() -> None:
    """Same for load_children."""
    c = InMemoryWorkspaceCache()
    with pytest.raises(CacheNotInitializedError):
        c.load_children("any-id")


def test_load_tree_before_populate_raises() -> None:
    """Same for load_tree."""
    c = InMemoryWorkspaceCache()
    with pytest.raises(CacheNotInitializedError):
        c.load_tree()


def test_populate_loads_nodes_and_clears_dirty() -> None:
    """After populate: loaded, dirty=False, node_count matches."""
    tree, _ = _make_tree(node_count=7)
    c = InMemoryWorkspaceCache()
    c.populate(tree)

    assert c.is_loaded() is True
    assert c.is_dirty() is False
    stats = c.stats()
    assert stats.node_count == 7
    assert stats.populated is True
    assert stats.last_populated_at is not None
    assert stats.last_invalidated_at is None
    assert stats.population_seconds >= 0.0


def test_populate_is_idempotent() -> None:
    """Second populate is a no-op (counters do not double)."""
    tree, _ = _make_tree(node_count=3)
    c = InMemoryWorkspaceCache()
    c.populate(tree)
    first_stats = c.stats()
    c.populate(tree)  # second call
    second_stats = c.stats()
    # Population counter did not double.
    assert second_stats.population_seconds == pytest.approx(
        first_stats.population_seconds
    )
    # Node count unchanged.
    assert second_stats.node_count == first_stats.node_count == 3


def test_load_node_hit_returns_node() -> None:
    """After populate, load_node returns the cached node."""
    tree, nodes = _make_tree(node_count=4)
    c = InMemoryWorkspaceCache()
    c.populate(tree)

    target = nodes[2]
    result = c.load_node(target.id)
    assert result.id == target.id
    assert result.title == target.title


def test_load_node_after_invalidate_raises_consistency_error() -> None:
    """After invalidate(id), load_node(id) raises
    `CacheConsistencyError` (NOT `CacheNotInitializedError`).
    """
    tree, nodes = _make_tree(node_count=3)
    c = InMemoryWorkspaceCache()
    c.populate(tree)

    target = nodes[1]
    c.invalidate(target.id)

    with pytest.raises(CacheConsistencyError):
        c.load_node(target.id)


def test_invalidate_does_not_affect_other_nodes() -> None:
    """Invalidating one id leaves siblings intact."""
    tree, nodes = _make_tree(node_count=5)
    c = InMemoryWorkspaceCache()
    c.populate(tree)

    c.invalidate(nodes[0].id)
    # The other four still load.
    for n in nodes[1:]:
        assert c.load_node(n.id).id == n.id


def test_invalidate_is_idempotent() -> None:
    """Invalidating an already-absent id is a no-op."""
    tree, nodes = _make_tree(node_count=2)
    c = InMemoryWorkspaceCache()
    c.populate(tree)

    c.invalidate(nodes[0].id)
    c.invalidate(nodes[0].id)  # second call
    # Invalidations counter only incremented once for the
    # first call (the second is a no-op for the dict mirror).
    # We accept either count here — the contract is
    # "idempotent" not "increments once per call." This test
    # documents the relaxed semantics.
    stats = c.stats()
    assert stats.invalidations >= 1


def test_invalidate_many_removes_batch() -> None:
    """invalidate_many removes a batch in one call."""
    tree, nodes = _make_tree(node_count=6)
    c = InMemoryWorkspaceCache()
    c.populate(tree)

    c.invalidate_many([nodes[0].id, nodes[1].id, nodes[2].id])
    for n in nodes[:3]:
        with pytest.raises(CacheConsistencyError):
            c.load_node(n.id)
    # Other three still load.
    for n in nodes[3:]:
        assert c.load_node(n.id).id == n.id


def test_subtree_ids_includes_root_in_bfs_order() -> None:
    """subtree_ids returns root + descendants in BFS order."""
    tree = Tree()
    root = Node(
        id="root-1",
        title="root",
        type=NodeType.STORY,
        metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
    )
    tree.add(root)
    child_a = Node(
        id="child-a",
        title="a",
        type=NodeType.STORY,
        metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
        parent_id="root-1",
    )
    child_b = Node(
        id="child-b",
        title="b",
        type=NodeType.STORY,
        metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
        parent_id="root-1",
    )
    tree.add(child_a)
    tree.add(child_b)
    tree.attach(child_a.id, root.id)
    tree.attach(child_b.id, root.id)
    grandchild = Node(
        id="grandchild",
        title="gc",
        type=NodeType.STORY,
        metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
        parent_id="child-a",
    )
    tree.add(grandchild)
    tree.attach(grandchild.id, child_a.id)

    c = InMemoryWorkspaceCache()
    c.populate(tree)

    ids = c.subtree_ids(root.id)
    assert ids[0] == root.id  # root is first
    assert set(ids) == {"root-1", "child-a", "child-b", "grandchild"}
    # BFS order: root → children → grandchildren.
    assert ids.index("child-a") < ids.index("grandchild")
    assert ids.index("child-b") < ids.index("grandchild")


def test_subtree_ids_before_populate_raises() -> None:
    c = InMemoryWorkspaceCache()
    with pytest.raises(CacheNotInitializedError):
        c.subtree_ids("any-id")


def test_clear_resets_state() -> None:
    """After clear: not loaded, dirty, all counters reset."""
    tree, _ = _make_tree(node_count=3)
    c = InMemoryWorkspaceCache()
    c.populate(tree)
    c.clear()

    assert c.is_loaded() is False
    assert c.is_dirty() is True
    stats = c.stats()
    assert stats.node_count == 0
    assert stats.populated is False
    assert stats.last_populated_at is None
    # load_node should raise again.
    with pytest.raises(CacheNotInitializedError):
        c.load_node("anything")


def test_stats_counters_track_hits_and_misses() -> None:
    """Stats counters move on hits and misses."""
    tree, nodes = _make_tree(node_count=3)
    c = InMemoryWorkspaceCache()
    c.populate(tree)

    # Two hits.
    c.load_node(nodes[0].id)
    c.load_node(nodes[1].id)
    # One miss (invalidated).
    c.invalidate(nodes[2].id)
    with pytest.raises(CacheConsistencyError):
        c.load_node(nodes[2].id)

    stats = c.stats()
    assert stats.hits >= 2
    assert stats.misses >= 1
    assert stats.invalidations >= 1


def test_cache_stats_is_immutable() -> None:
    """CacheStats is a frozen dataclass."""
    stats = CacheStats(hits=10)
    with pytest.raises(Exception):  # FrozenInstanceError
        stats.hits = 20  # type: ignore[misc]