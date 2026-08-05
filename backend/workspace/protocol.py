"""Workspace cache Protocols.

The cache is a *runtime optimisation, never a source of truth*
(ADR-0016 / TECH_SPEC §13d). Its Protocols define exactly what
the rest of the codebase may assume — nothing more.

Three Protocols, deliberately split:

    - **`WorkspaceCache`** — read-only surface. The service
      layer and (eventually) API handlers depend on this. It
      exposes only the operations that do NOT mutate state.
    - **`MutableWorkspaceCache`** — extends the read surface
      with invalidation methods. Only the repository may
      import this. Splitting it out prevents the service /
      API layer from accidentally invalidating entries
      out-of-band (the repository is the single mutation
      boundary).
    - **`CacheSeeder`** — startup-only constructor API. Only
      the `StartupSubsystem` may import it; `populate()` is
      never on the runtime path.

The three-way split is enforced by structural isolation tests
(see `backend/tests/workspace/test_isolation.py`):

    - API and service layers must not import the concrete
      `InMemoryWorkspaceCache` (they should only see the
      Protocols, and at present they should see no cache at
      all — the repository is the seam).
    - Only `backend/repositories/impl/local_workspace_repository.py`
      may import the concrete cache.
    - Only `StartupSubsystem.run()` may call `populate()`.

Why Protocols over ABCs:

    - Multiple implementations are plausible (in-process now,
      distributed later). Structural typing makes the swap
      a drop-in replacement without inheritance hierarchies.
    - Tests pass lightweight stand-ins (a Mock, a real
      `InMemoryWorkspaceCache`) — duck typing keeps test
      friction low.

Note on canvas: the cache deliberately does NOT cover canvas
content. Canvas is a file, not a Node attribute, and its reads
have an RPC-style on-demand pattern that would balloon the
cache for little benefit. `read_canvas` / `write_canvas`
continue to hit the filesystem.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterable

    from backend.domain.node import Node, NodeId
    from backend.domain.tree import Tree


# ---- stats ----------------------------------------------------------------


@dataclasses.dataclass(slots=True, frozen=True)
class CacheStats:
    """Operational counters for the cache.

    Frozen + slots so a snapshot can be passed around without
    accidental mutation. Per the Phase 3.0 brief, the stats
    object carries enough information to diagnose production
    cache behaviour without reading the implementation:

        - `hits` / `misses` — every read increments one of
          these. Self-healing misses (where the repository
          catches a `CacheConsistencyError` and falls back
          to disk) count as misses.
        - `invalidations` — total invalidation calls across
          the cache's lifetime.
        - `node_count` — number of distinct node ids held in
          the cache right now (after the most recent
          invalidate/populate).
        - `populated` — bool. True if `populate()` has run
          at least once successfully. The repository treats
          `populated == False` as a programming error at
          startup.
        - `last_populated_at` / `last_invalidated_at` —
          wall-clock timestamps for the most recent populate
          and invalidation. None if the event hasn't
          happened yet.
        - `population_seconds` — total time spent inside
          `populate()` across the cache's lifetime. Lets a
          operator see "first boot cost 0.8s; subsequent
          boots cost 0ms because nothing changed."

    The protocol does not pin the implementation's
    threading model. In-process counts are atomic via a
    `threading.Lock` (per implementation).
    """

    hits: int = 0
    misses: int = 0
    invalidations: int = 0
    node_count: int = 0
    populated: bool = False
    last_populated_at: float | None = None
    last_invalidated_at: float | None = None
    population_seconds: float = 0.0
    # Reserved for future use; defined here so the field
    # exists in the API surface from day one, the constructor
    # has a default, and existing callers don't break when
    # implementations begin populating it. Per ChatGPT's
    # Phase 3.0 refinement #1: CacheStats is the extensible
    # observability object.
    rebuild_count: int = 0
    memory_estimate_bytes: int | None = None
    last_refresh_at: float | None = None


# ---- read protocol --------------------------------------------------------


@runtime_checkable
class WorkspaceCache(Protocol):  # pragma: no cover - protocol
    """Read-only surface over the cache.

    Implementations raise `CacheNotInitializedError` from
    `load_node` / `load_children` / `load_tree` if called
    before `populate()` runs. The repository's read path
    treats this as a programming error at startup, a warning
    at runtime (with disk fallback).

    Implementations are NOT required to be thread-safe at
    the Protocol level — the concrete `InMemoryWorkspaceCache`
    uses an `RLock` internally. Tests passing Mock objects
    satisfy the Protocol structurally.
    """

    def load_node(self, node_id: "NodeId") -> "Node": ...

    def load_children(self, node_id: "NodeId") -> list["Node"]: ...

    def load_tree(self) -> "Tree": ...

    def is_loaded(self) -> bool: ...

    def stats(self) -> CacheStats: ...


@runtime_checkable
class MutableWorkspaceCache(Protocol):  # pragma: no cover - protocol
    """Cache with invalidation methods.

    Only the repository may import this. Services and the
    API layer must depend on `WorkspaceCache` (read-only)
    if they ever reach the cache at all.

    `subtree_ids` returns the descendants of `root_id` in
    BFS order, INCLUDING `root_id` itself, so the repository
    can invalidate the moved/deleted subtree in a single
    call. Mirrors the contract of `Tree.subtree`.
    """

    def invalidate(self, node_id: "NodeId") -> None: ...

    def invalidate_many(self, node_ids: "Iterable[NodeId]") -> None: ...

    def subtree_ids(self, root_id: "NodeId") -> list["NodeId"]: ...

    def clear(self) -> None: ...


@runtime_checkable
class CacheSeeder(Protocol):  # pragma: no cover - protocol
    """Startup-only API: populate the cache once.

    `populate()` is idempotent. Calling it a second time
    after a successful populate is a no-op (with a warning
    log) — only the StartupSubsystem may call it, and the
    subsystem calls it exactly once.

    `is_loaded()` mirrors the read Protocol — but the
    seeder surface exists so a future test of the subsystem
    can verify "was populate() actually called?" without
    taking a read-side dependency.
    """

    def populate(self, tree: "Tree") -> None: ...

    def is_loaded(self) -> bool: ...


# Concrete implementations satisfy all three Protocols by
# structure. We don't use ABC inheritance here — that would
# couple the Protocols to one threading model and make future
# distributed implementations heavier to write.
__all__ = [
    "CacheSeeder",
    "CacheStats",
    "MutableWorkspaceCache",
    "WorkspaceCache",
]