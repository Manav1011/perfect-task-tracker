"""InMemoryWorkspaceCache — the Phase 3.0 cache implementation.

The cache holds a Tree reference plus a parallel `dict[NodeId, Node]`
mirror for O(1) lookup. Both are populated exactly once at startup
(by `StartupSubsystem.run()`), then mutated by the repository on
every write via the invalidation API.

Threading
---------
A single `threading.RLock` guards all internal state. `RLock`
(re-entrant) is chosen because:

    - `populate()` may acquire the lock then call helpers that
      also acquire it (e.g., internal counters).
    - The synchroniser's read-side `load_tree()` may acquire
      the lock while inside a larger repo operation; the
      same thread must be allowed to re-enter.

Reads are O(1) under the lock. Writes are O(1) under the lock.
`populate()` is O(N) under the lock (held for the entire populate
call) — startup holds it during boot and releases once.

The single global lock is deliberate. Per-Phase-3.0 brief
"design for correctness, optimise later": the cost is one
acquire/release per cache call, which is dwarfed by the
filesystem round-trip the cache replaces. Ponytail ceiling:
if a future profiler flags the lock as a hot path, the cache
could split into per-shard locks — but the simpler model is
correct first.

Invalidation semantics
----------------------
`invalidate(id)` removes the id from the dict mirror. The
backing `Tree` reference is NOT mutated — it stays as the
"intended" snapshot for any reader who walks the tree (e.g.,
`subtree_ids`). When `invalidate` removes the last reference
to a node id, the cache's `node_count` reflects that.

A read after invalidate raises `CacheConsistencyError`. The
repository catches this and self-heals by reading from disk.

`clear()` empties everything (used by the test suite and as
an escape hatch for ops). After `clear()`, `is_loaded()`
returns False until `populate()` runs again.

Idempotent populate
-------------------
A second `populate(tree)` after a successful populate is a
no-op with a warning log. Only the StartupSubsystem calls
`populate()`, and it calls it exactly once, but the
idempotency guard makes the cache safe against accidental
re-entry from any future caller.

Health flag
-----------
The cache maintains its OWN `_dirty` flag — independent of
the synchroniser's staleness flag (per ChatGPT's refinement
that cache and index are independent subsystems). The flag
is set True on construction and after `clear()`; cleared by
a successful `populate()`. The repository never reads it
(reads self-heal), so the flag's only consumer is the
startup subsystem and diagnostic logs.
"""

from __future__ import annotations

import threading
import time

from backend.core.logging import get_logger
from backend.domain.node import Node, NodeId
from backend.domain.tree import Tree

from backend.workspace.exceptions import (
    CacheConsistencyError,
    CacheNotInitializedError,
)
from backend.workspace.protocol import CacheStats

logger = get_logger(__name__)


class InMemoryWorkspaceCache:
    """In-process workspace cache. Single-threaded (RLock) per worker.

    Holds a `Tree` snapshot and a parallel dict for O(1) reads.
    Mutated via `MutableWorkspaceCache` (invalidate, invalidate_many,
    subtree_ids, clear). Populated exactly once at startup via the
    `CacheSeeder` Protocol surface (populate, is_loaded).

    The cache never reaches the filesystem. The repository is the
    single boundary that talks to both fs and cache; this class
    is a passive data structure with locking.
    """

    __slots__ = (
        "_tree",
        "_nodes",
        "_loaded",
        "_dirty",
        "_lock",
        "_stats",
        "_populate_started",
    )

    def __init__(self) -> None:
        self._tree: Tree | None = None
        self._nodes: dict[NodeId, Node] = {}
        self._loaded: bool = False
        # Cache-owned dirty flag. Independent of the synchroniser's
        # staleness flag — cache and index are independent subsystems
        # (per ChatGPT Phase 3.0 refinement). The startup subsystem
        # clears this after a successful populate; ops can also
        # read it via `is_dirty()` for diagnostics.
        self._dirty: bool = True
        self._lock: threading.RLock = threading.RLock()
        self._stats: CacheStats = CacheStats()
        # Started_at stamp is monotonic; the cache's `last_populated_at`
        # uses wall-clock time so ops can correlate it with logs.
        self._populate_started: float | None = None

    # ---- CacheSeeder -------------------------------------------------

    def populate(self, tree: Tree) -> None:
        """Load `tree` into the cache. Idempotent on success.

        Called by `StartupSubsystem.run()` exactly once. A second
        call is a no-op with a structured warning — not an error,
        because the contract is "idempotent on success," and we'd
        rather let a misbehaving caller log noise than crash the
        boot.

        Time and lock: the entire populate runs under the lock.
        At boot, the lock is held by the startup thread; nothing
        else can race because the application isn't serving
        requests yet.
        """
        with self._lock:
            if self._loaded:
                logger.warning(
                    "cache.populate.noop",
                    reason="already_populated",
                    node_count=self._stats.node_count,
                )
                return
            t0 = time.monotonic()
            self._populate_started = t0
            # Seed the dict from every node in the tree. `all_nodes`
            # is the canonical iteration order; tests assert on
            # insertion order only for diagnostics, never for
            # correctness.
            self._nodes = {n.id: n for n in tree.all_nodes()}
            # The Tree reference is stored as-is. `Tree` is
            # immutable in spirit but mutable in Python — we trust
            # the repository not to mutate it because the cache
            # is a passive mirror. See `subtree_ids` for the
            # walk that uses it.
            self._tree = tree
            self._loaded = True
            self._dirty = False  # healthy after a successful populate
            elapsed = time.monotonic() - t0
            # Single source of truth for stats mutation.
            self._stamp_population(elapsed_seconds=elapsed)
            logger.info(
                "cache.populate.ok",
                node_count=len(self._nodes),
                elapsed_seconds=elapsed,
            )

    def is_loaded(self) -> bool:
        """True iff `populate()` has run at least once successfully."""
        return self._loaded

    def is_dirty(self) -> bool:
        """Cache-owned dirty flag.

        Independent of the synchroniser's staleness flag. Cleared
        by a successful `populate()`. Set True at construction
        and after `clear()`. The repository doesn't read this —
        it self-heals on miss — but the startup subsystem may
        consult it (currently it doesn't; it always populates).
        """
        return self._dirty

    # ---- WorkspaceCache (read) ---------------------------------------

    def load_node(self, node_id: NodeId) -> Node:
        """O(1) node lookup.

        Raises:
            CacheNotInitializedError: if `populate()` hasn't run.
            CacheConsistencyError: if the id was invalidated or
                was never present in the populated tree.
        """
        with self._lock:
            self._bump_hit_or_miss(found=node_id in self._nodes)
            if not self._loaded:
                raise CacheNotInitializedError(
                    "load_node called before populate()"
                )
            try:
                return self._nodes[node_id]
            except KeyError as exc:
                raise CacheConsistencyError(
                    f"node {node_id} not in cache (invalidated or never present)"
                ) from exc

    def load_children(self, node_id: NodeId) -> list[Node]:
        """Children of `node_id`, in tree order.

        Walks the `Tree` snapshot — not the dict mirror — so the
        result respects the structural children ordering even
        after some ids have been invalidated.
        """
        with self._lock:
            self._bump_hit_or_miss(found=True)  # cheap always-hit
            if not self._loaded or self._tree is None:
                raise CacheNotInitializedError(
                    "load_children called before populate()"
                )
            return self._tree.children_of(node_id)

    def load_tree(self) -> Tree:
        """Return the cached `Tree` reference.

        Returned by-reference so the synchroniser's `current_tree()`
        adapter can return the same instance without copying. The
        synchroniser's Protocol contract says it should return a
        *fresh* Tree, but the existing `_FakeTreeProvider` and
        `LocalWorkspaceRepository` tests show that the contract is
        a copy — see the wrapper at `backend/workspace/tree_provider.py`.
        """
        with self._lock:
            self._bump_hit_or_miss(found=True)
            if not self._loaded or self._tree is None:
                raise CacheNotInitializedError(
                    "load_tree called before populate()"
                )
            return self._tree

    def stats(self) -> CacheStats:
        """Return a snapshot of the cache's operational counters."""
        with self._lock:
            # Single source of truth for stats snapshotting.
            # `_rebuild_stats` reads through to the current
            # `_stats` values for any unspecified field, so
            # future fields appear in the snapshot
            # automatically without touching this method.
            return self._rebuild_stats()

    # ---- MutableWorkspaceCache (write) -------------------------------

    def invalidate(self, node_id: NodeId) -> None:
        """Remove `node_id` from the cache's dict mirror.

        The cached `Tree` is NOT mutated — it remains the snapshot.
        Future reads of this id raise `CacheConsistencyError`; the
        repository's self-healing miss path handles that.

        Idempotent: invalidating an already-absent id is a no-op.

        Single source of truth: delegates stats counter
        mutation to `_bump_invalidation`.
        """
        with self._lock:
            self._bump_invalidation(count=1)
            self._nodes.pop(node_id, None)

    def invalidate_many(self, node_ids) -> None:  # type: ignore[no-untyped-def]
        """Invalidate a batch of ids in one lock acquire.

        Single source of truth: delegates stats counter
        mutation to `_bump_invalidation`.
        """
        with self._lock:
            count = 0
            for nid in node_ids:
                if nid in self._nodes:
                    self._nodes.pop(nid, None)
                    count += 1
            self._bump_invalidation(count=count)

    def subtree_ids(self, root_id: NodeId) -> list[NodeId]:
        """Return `root_id` + all descendants in BFS order.

        Mirrors `Tree.subtree(root_id)` semantics so the repository
        can ask "which ids do I need to invalidate when I move
        or delete this subtree?" with one cache call instead of
        a disk walk.
        """
        with self._lock:
            if not self._loaded or self._tree is None:
                raise CacheNotInitializedError(
                    "subtree_ids called before populate()"
                )
            return [n.id for n in self._tree.subtree(root_id)]

    def clear(self) -> None:
        """Empty the cache. After this, `is_loaded()` returns False.

        Reserved for the test suite and ops escape hatches. The
        repository never calls this; the synchroniser doesn't
        call this. The startup subsystem may in a future Phase
        if a forced cold-boot is requested.

        Single source of truth: delegates stats reset to
        `_reset_stats`.
        """
        with self._lock:
            self._nodes = {}
            self._tree = None
            self._loaded = False
            self._dirty = True
            self._reset_stats()

    # ---- helpers -----------------------------------------------------

    def _bump_hit_or_miss(self, *, found: bool) -> None:
        """Increment hit or miss counters under the held lock.

        Single source of truth for read-counter mutation. The
        cache's other public methods call this rather than
        reconstructing the stats dataclass inline — that way
        future stat fields (memory_estimate, rebuild_count, …)
        only need to be threaded through one place.
        """
        if found:
            self._stats = self._rebuild_stats(
                hits=self._stats.hits + 1,
            )
        else:
            self._stats = self._rebuild_stats(
                misses=self._stats.misses + 1,
            )

    def _bump_invalidation(self, *, count: int) -> None:
        """Increment invalidation counters and stamp
        `last_invalidated_at` under the held lock.

        Called by `invalidate` and `invalidate_many`. The
        `count` argument is the number of *actual* nodes
        removed (invalidate_many uses hits; single invalidate
        uses 1 to capture the call even if the id was absent).

        Single source of truth for invalidation-counter mutation.
        """
        self._stats = self._rebuild_stats(
            invalidations=self._stats.invalidations + max(count, 1),
            last_invalidated_at=time.time(),
        )

    def _stamp_population(self, *, elapsed_seconds: float) -> None:
        """Mark a successful populate. Called by `populate()`
        after the lock is acquired and the dict is seeded.
        """
        self._stats = self._rebuild_stats(
            node_count=len(self._nodes),
            populated=True,
            last_populated_at=time.time(),
            population_seconds=self._stats.population_seconds + elapsed_seconds,
            rebuild_count=self._stats.rebuild_count + 1,
        )

    def _reset_stats(self) -> None:
        """Reset stats to a fresh Defaults snapshot. Called by
        `clear()` and the constructor's stateless path. The
        frozen dataclass means we can't mutate in place —
        we replace the `_stats` reference with a new defaults
        instance.
        """
        self._stats = CacheStats()

    def _rebuild_stats(
        self,
        *,
        hits: int | None = None,
        misses: int | None = None,
        invalidations: int | None = None,
        node_count: int | None = None,
        populated: bool | None = None,
        last_populated_at: float | None = None,
        last_invalidated_at: float | None = None,
        population_seconds: float | None = None,
        rebuild_count: int | None = None,
        memory_estimate_bytes: int | None = None,
        last_refresh_at: float | None = None,
    ) -> CacheStats:
        """Build a new `CacheStats` snapshot, falling back to
        the current `_stats` values for any unspecified field.

        Centralising the rebuild means **one** place to add
        a new field: every caller picks the new field up
        automatically by extending the parameter list and
        adding a `self._stats.<field> if x is None else x`
        line.

        Per ChatGPT's Phase 3.0 refinement #1: `CacheStats`
        is the extensible observability object. Future fields
        (memory footprint estimate, last_refresh_at,
        rebuild_count, etc.) are added here without touching
        any of the cache's read or write methods.
        """
        return CacheStats(
            hits=self._stats.hits if hits is None else hits,
            misses=self._stats.misses if misses is None else misses,
            invalidations=(
                self._stats.invalidations
                if invalidations is None
                else invalidations
            ),
            node_count=(
                len(self._nodes) if node_count is None else node_count
            ),
            populated=(
                self._stats.populated if populated is None else populated
            ),
            last_populated_at=(
                self._stats.last_populated_at
                if last_populated_at is None
                else last_populated_at
            ),
            last_invalidated_at=(
                self._stats.last_invalidated_at
                if last_invalidated_at is None
                else last_invalidated_at
            ),
            population_seconds=(
                self._stats.population_seconds
                if population_seconds is None
                else population_seconds
            ),
            rebuild_count=(
                self._stats.rebuild_count
                if rebuild_count is None
                else rebuild_count
            ),
            memory_estimate_bytes=(
                self._stats.memory_estimate_bytes
                if memory_estimate_bytes is None
                else memory_estimate_bytes
            ),
            last_refresh_at=(
                self._stats.last_refresh_at
                if last_refresh_at is None
                else last_refresh_at
            ),
        )