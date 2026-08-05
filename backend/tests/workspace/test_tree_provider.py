"""Tests for `CacheBackedTreeProvider`.

The adapter satisfies `backend.index.sync.WorkspaceTreeProvider`
structurally. These tests verify:

    - `current_tree()` returns the same Tree the cache holds.
    - The adapter passes through `CacheNotInitializedError`
      when the cache is empty (the synchroniser catches it).
    - The adapter is structurally compatible with the
      synchroniser's Protocol — a `WorkspaceCache` is all
      it requires.
"""

from __future__ import annotations

import pytest

from backend.domain import Node, NodeMetadata, NodeType, new_node_id
from backend.domain.tree import Tree
from backend.workspace import (
    CacheBackedTreeProvider,
    InMemoryWorkspaceCache,
)
from backend.workspace.exceptions import CacheNotInitializedError


def _tree_with_nodes(count: int) -> Tree:
    tree = Tree()
    for i in range(count):
        tree.add(
            Node(
                id=new_node_id(),
                title=f"n-{i}",
                type=NodeType.STORY,
                metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
            )
        )
    return tree


def test_adapter_returns_cached_tree() -> None:
    """current_tree() returns the same Tree the cache holds."""
    tree = _tree_with_nodes(5)
    cache = InMemoryWorkspaceCache()
    cache.populate(tree)

    provider = CacheBackedTreeProvider(cache)
    result = provider.current_tree()
    assert result is tree  # same instance, by-reference contract


def test_adapter_propagates_cache_not_initialized() -> None:
    """If the cache hasn't been populated, current_tree()
    raises CacheNotInitializedError (the synchroniser
    catches it during populate-cache flow).
    """
    cache = InMemoryWorkspaceCache()
    provider = CacheBackedTreeProvider(cache)

    with pytest.raises(CacheNotInitializedError):
        provider.current_tree()


def test_adapter_satisfies_workspace_tree_provider_protocol() -> None:
    """CacheBackedTreeProvider satisfies the synchroniser's
    `WorkspaceTreeProvider` Protocol structurally — no
    inheritance needed.

    We assert on duck-typed surface (method presence + call
    signature) rather than `isinstance`, because the
    Protocol isn't `@runtime_checkable` and we don't want
    to expand scope by modifying the index module's
    Protocol declaration. Structural typing is the point
    of Protocols — the assertion is "has the right
    method that returns the right type."
    """
    cache = InMemoryWorkspaceCache()
    provider = CacheBackedTreeProvider(cache)
    # Has the method.
    assert hasattr(provider, "current_tree")
    # Method returns a Tree when the cache is populated.
    cache.populate(_tree_with_nodes(2))
    result = provider.current_tree()
    assert isinstance(result, Tree)


def test_adapter_after_cache_clear_raises() -> None:
    """After cache.clear(), the adapter propagates the new
    state.
    """
    tree = _tree_with_nodes(2)
    cache = InMemoryWorkspaceCache()
    cache.populate(tree)

    provider = CacheBackedTreeProvider(cache)
    assert len(provider.current_tree()) == 2

    cache.clear()
    with pytest.raises(CacheNotInitializedError):
        provider.current_tree()