"""CacheBackedTreeProvider — adapts the cache to the synchroniser's Protocol.

The synchroniser (Phase 2.2) depends on a `WorkspaceTreeProvider`
Protocol — a single method `current_tree() -> Tree`. Phase 3.0
routes that through the cache:

    synchroniser.current_tree() → CacheBackedTreeProvider → cache.load_tree()

Why this lives here, not in `backend.index.sync`:

    - The cache must not depend on the index (forbidden by
      the workspace isolation rule).
    - But the adapter needs to satisfy the index's Protocol.
    - So we keep the adapter here: it imports the Protocol
      only. The Protocol is structural (typing.Protocol),
      so we don't pull in any concrete index code.

This replaces the lifespan's earlier `ponytail:` seam
(`synchroniser._tree_provider = repository`) with a clean
constructor injection — see `backend/core/lifespan.py`.

Ponytail: the Tree is returned by reference, not by copy. The
synchroniser is contractually allowed to receive a copy
(its Protocol docstring says so), but in practice the
synchroniser only reads. If a future writer mutates the
returned Tree, we'd need to add a `.copy()` here. Documented
ceiling; not needed today.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.domain.tree import Tree

    from backend.workspace.protocol import WorkspaceCache


class CacheBackedTreeProvider:
    """Adapter from `WorkspaceCache` to `index.sync.WorkspaceTreeProvider`.

    Implements the synchroniser's read-side Protocol by returning
    `cache.load_tree()` directly. Construction takes any object
    that satisfies `WorkspaceCache` structurally; tests pass a
    fake cache, production passes `InMemoryWorkspaceCache`.
    """

    __slots__ = ("_cache",)

    def __init__(self, cache: "WorkspaceCache") -> None:
        self._cache = cache

    def current_tree(self) -> "Tree":
        """Return the cached Tree.

        The Protocol docstring says this should return a *fresh*
        Tree (so callers cannot mutate the live one). The cache
        returns the same instance by reference; if a future
        caller mutates the result, add a `Tree()` copy here.
        """
        return self._cache.load_tree()