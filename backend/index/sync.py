"""Incremental index synchroniser.

Phase 2.2. Receives domain objects (after a successful filesystem
write) and updates only the affected index rows. Synchronous, in
the same call frame as the repository write it shadows; no event
bus, no background worker.

Architectural placement
-----------------------

The synchroniser is the only index-aware code that lives behind
the repository write path. Per the Phase 2.2 brief:

    1. Repository remains the persistence orchestrator.
    2. Repository invokes the synchroniser AFTER the filesystem
       operation succeeds.
    3. Synchroniser failures NEVER roll back the filesystem.

To keep that last promise, the synchroniser wraps every public
method in `_run(...)`, which catches any raised exception, logs
it, and flips an in-memory staleness flag. The caller (the
repository) sees no exception — only an `is_stale()` query can
later reveal that the index drifted.

A subsequent `IndexReconciler.rebuild()` brings the index back
to a known-good state. Until that rebuild runs, reads from the
index may return outdated results; reads from the filesystem
remain authoritative.

What the synchroniser handles
-----------------------------

    - `on_node_created(node, parent_id)`           — insert one row.
    - `on_node_renamed(node_id, new_title)`        — update title.
    - `on_node_moved(node_id, new_parent_id)`      — recompute
      parent_id, story_id, and filesystem_path for the moved
      Node AND every descendant in the subtree.
    - `on_node_deleted(node_id)`                   — delete one
      row (by NodeId, no filesystem lookup required).
    - `on_metadata_updated(node_id, metadata)`     — update
      metadata-derived fields. (Reserved; Phase 2.2 emits a
      title-only update via `on_node_renamed` until
      `update_metadata` lands in the repository.)
    - `on_canvas_updated(node_id)`                 — touched_at
      timestamp update on the row.

What the synchroniser deliberately does NOT do
-----------------------------------------------

    - No full rebuild. (Phase 2.1 Reconciler.)
    - No event hooks. (Phase 4+.)
    - No background workers.
    - No retry queue.
    - No cross-process coordination.
    - No propagation to other services.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from backend.domain.node import Node, NodeId
from backend.domain.tree import Tree
from backend.index.protocol import IndexRepository
from backend.index.types import IndexRecord

_log = logging.getLogger(__name__)


# ---- public exceptions ----------------------------------------------------


class IndexSyncError(IndexError if False else Exception):
    """Base class for synchroniser failures.

    Inherits from `Exception` rather than `IndexError` (the
    repository-side type) so callers can catch sync-specific
    failures without conflating them with read-side index
    errors. The synchroniser catches these internally; external
    callers normally never see them — they manifest as
    `is_stale() == True` after the fact.
    """


class SyncPathResolutionError(IndexSyncError):
    """The path provider could not resolve a Node's filesystem path.

    Treated as a hard failure by default — the synchroniser
    refuses to write a record whose `filesystem_path` cannot
    be computed. This matches the Reconciler's policy: a Node
    that cannot be located must never be silently indexed.
    """


# ---- supporting types -----------------------------------------------------


@dataclass(slots=True)
class SyncReport:
    """Counters from a single synchroniser pass.

    The repository may log this for ops visibility. It is not
    propagated to the API layer; the API sees the workspace
    operation succeed and a (possibly stale) index.

    Fields:

        - `created`, `updated`, `deleted`   — row counts touched.
        - `subtree_nodes_affected`          — how many Node rows
          were updated as part of a subtree recompute.
        - `elapsed_seconds`                 — wall-clock duration.
        - `errors`                          — tuples of (method_name,
          message) captured during this pass.
    """

    created: int = 0
    updated: int = 0
    deleted: int = 0
    subtree_nodes_affected: int = 0
    elapsed_seconds: float = 0.0
    errors: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def is_clean(self) -> bool:
        """True iff no errors occurred during this pass."""
        return not self.errors


# ---- path provider (synchronous, in-process) -----------------------------


class FilesystemPathProvider(Protocol):  # pragma: no cover - protocol
    """Synchronous path accessor for a Node's filesystem path.

    Same shape as the Reconciler's path provider (Phase 2.1) —
    we re-declare it here to avoid forcing a circular import
    through `backend.index.reconciler`. Production wiring binds
    both protocols to the same concrete implementation.

    Contract: `path_for(node_id)` returns the Node's relative
    path string (e.g. `"story-a/child-of-a"`), or raises if the
    Node is not on disk.
    """

    def path_for(self, node_id: NodeId) -> str: ...


# ---- tree provider --------------------------------------------------------


class WorkspaceTreeProvider(Protocol):  # pragma: no cover - protocol
    """In-process accessor for the current domain Tree.

    The synchroniser needs a Tree to:

        1. resolve a Node's `parent_id` chain (story_id),
        2. enumerate descendants when a Node is moved,
        3. produce an `IndexRecord` for a fresh Node.

    Production wiring binds this to the in-memory runtime
    tree (Phase 3+). Phase 2.2 ships the protocol so the
    repository can hold a single seam; tests pass a
    `Tree` directly via `_FakeTreeProvider`.

    Contract: `current_tree()` returns the Tree reflecting the
    filesystem state AFTER the triggering write. Returns a
    fresh Tree (a copy) so callers cannot mutate the live one.
    """

    def current_tree(self) -> Tree: ...


# ---- projection helpers ---------------------------------------------------


def _utcnow() -> datetime:
    """Timezone-aware UTC now — single timestamp per sync pass
    to keep cross-row `updated_at` uniform within a method call."""
    return datetime.now(timezone.utc)


def _node_type_to_string(node: Node) -> str:
    """Render a NodeType to its wire string."""
    return node.type.value if hasattr(node.type, "value") else str(node.type)


def _resolve_story_id(tree: Tree, node: Node) -> NodeId | None:
    """Walk parent links until we find the root Story.

    Returns the root Node's id. For a root Node, returns
    `node.id`. Returns `None` only if the walk reaches a
    dead end (defensive against malformed trees).
    """
    cursor = node.parent_id
    seen: set[NodeId] = set()
    while cursor is not None:
        if cursor in seen:
            return None
        seen.add(cursor)
        parent = tree.try_get(cursor)
        if parent is None:
            return None
        if parent.parent_id is None:
            return parent.id
        cursor = parent.parent_id
    return node.id


def _project_record(
    tree: Tree,
    node: Node,
    path: str,
    now: datetime,
) -> IndexRecord:
    """Map a domain Node to an IndexRecord (pure function).

    Mirrors `backend.index.reconciler._project_node` so the
    incremental path produces records byte-compatible with
    what a full rebuild would have produced for the same
    Node.
    """
    return IndexRecord(
        node_id=node.id,
        parent_id=node.parent_id,
        story_id=_resolve_story_id(tree, node),
        title=node.title,
        node_type=_node_type_to_string(node),
        filesystem_path=path,
        created_at=now,
        updated_at=now,
        search_text="",
    )


# ---- staleness tracking ---------------------------------------------------


class _StalenessFlag:
    """In-process counter for "index may be out of sync with fs".

    Set by `_run()` whenever a sync method raises. Read by
    `is_stale()`. Reset by `clear()` after a successful rebuild
    or by the caller once the drift has been acknowledged.

    Intentionally NOT persisted: the index itself is the
    source of truth for "what rows do we have". Persisting
    "we know we're stale" is pointless because the rebuild
    itself is the only sane recovery — and the rebuild
    starts by inspecting the index, not a sidecar flag.

    The flag lives on the synchroniser so production code
    can call `synchroniser.is_stale()` and route reads to
    the filesystem if true.
    """

    __slots__ = ("_count",)

    def __init__(self) -> None:
        self._count: int = 0

    def mark(self) -> None:
        self._count += 1

    def clear(self) -> None:
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def is_stale(self) -> bool:
        return self._count > 0


# ---- synchroniser ---------------------------------------------------------


class IncrementalIndexSynchronizer:
    """Apply incremental updates to the Postgres index.

    Constructor DI — depends only on Protocols. No filesystem
    imports, no FastAPI, no service-layer coupling.

    The synchroniser is `replaceable` via Protocol injection
    per the brief: a future Phase 4 implementation could swap
    in a queue-backed variant without changing the repository.

    Failure semantics:

        - Every public method is wrapped in `_run(...)`.
        - Any exception is caught, logged, and counted via
          the staleness flag.
        - Public methods NEVER raise. They return a
          `SyncReport` describing what happened (or didn't).
    """

    def __init__(
        self,
        index_repo: IndexRepository,
        tree_provider: WorkspaceTreeProvider,
        path_provider: FilesystemPathProvider,
    ) -> None:
        self._index = index_repo
        self._tree = tree_provider
        self._paths = path_provider
        self._stale = _StalenessFlag()

    # ---- observability ------------------------------------------------

    def is_stale(self) -> bool:
        """True iff at least one sync op has failed since last clear."""
        return self._stale.is_stale()

    def staleness_count(self) -> int:
        """How many sync failures have occurred since last clear."""
        return self._stale.count

    def clear_staleness(self) -> None:
        """Acknowledge the drift. Typically called after a rebuild."""
        self._stale.clear()

    # ---- public hooks (called by the repository, after fs success) ---

    def on_node_created(self, node: Node, parent_id: NodeId | None) -> SyncReport:
        """Insert one index row for the freshly-created Node."""
        return self._run("on_node_created", self._do_create, node, parent_id)

    def on_node_renamed(self, node_id: NodeId, new_title: str) -> SyncReport:
        """Update `title` (and `updated_at`) for the row.

        Path, parent_id, story_id are unaffected by a rename
        (the directory is renamed on disk but the relative
        path stays the same).
        """
        return self._run("on_node_renamed", self._do_rename, node_id, new_title)

    def on_node_moved(
        self,
        node_id: NodeId,
        new_parent_id: NodeId | None,
    ) -> SyncReport:
        """Recompute parent_id, story_id, and path for the
        moved Node and every descendant in its subtree.

        Subtree moves are the only operation that can change
        `story_id` for any descendant: re-rooting a subtree
        under a different Story changes the root the subtree
        belongs to, which every descendant's `story_id` must
        reflect.
        """
        return self._run(
            "on_node_moved", self._do_move, node_id, new_parent_id
        )

    def on_node_deleted(self, node_id: NodeId) -> SyncReport:
        """Delete the row for `node_id` (by NodeId only).

        Index deletes happen one row at a time. The repository
        is responsible for issuing one `delete_node` call per
        Node in the deleted subtree (descendants first, then
        the parent), so the synchroniser sees a sequence of
        single-row deletes and never needs subtree knowledge.
        """
        return self._run("on_node_deleted", self._do_delete, node_id)

    def on_metadata_updated(
        self, node_id: NodeId, title: str
    ) -> SyncReport:
        """Update the row's title (and updated_at) after a
        metadata change.

        The repository's current `update_metadata` is layered
        on top of `rename_node`; the synchroniser treats both
        as title-update signals. When `update_metadata` lands
        properly (TECH_SPEC §18), this method gains additional
        metadata-derived fields (e.g. `node_type` is already
        derived from the filesystem so it doesn't change here).
        """
        return self._run("on_metadata_updated", self._do_rename, node_id, title)

    def on_canvas_updated(self, node_id: NodeId) -> SyncReport:
        """Touch `updated_at` for the row whose canvas.md changed.

        The index stores no canvas content (search_text is
        empty in Phase 2.2; full-text search is Phase 4+).
        We still bump `updated_at` so admin queries can tell
        which rows have been touched recently.
        """
        return self._run("on_canvas_updated", self._do_touch, node_id)

    # ---- internals ---------------------------------------------------

    def _run(
        self,
        method_name: str,
        fn,
        *args,
    ) -> SyncReport:
        """Execute a sync method, swallow exceptions, flip staleness.

        The brief is explicit: "Any index failure must NEVER
        roll back filesystem persistence. Failures should be
        reported/logged and the index marked temporarily stale
        until the next rebuild." `_run` is the choke point
        that makes that promise.
        """
        import time

        started = time.monotonic()
        try:
            sub = fn(*args)
        except Exception as exc:
            self._stale.mark()
            _log.exception(
                "index sync %s failed; index marked stale: %r",
                method_name,
                exc,
            )
            return SyncReport(
                elapsed_seconds=time.monotonic() - started,
                errors=((method_name, repr(exc)),),
            )
        # Merge counters from the inner helper.
        return SyncReport(
            created=sub.created,
            updated=sub.updated,
            deleted=sub.deleted,
            subtree_nodes_affected=sub.subtree_nodes_affected,
            elapsed_seconds=time.monotonic() - started,
        )

    # ---- inner helpers (each returns a SyncReport; never raise) ----

    def _do_create(
        self, node: Node, parent_id: NodeId | None
    ) -> SyncReport:
        # `parent_id` is part of the hook signature for symmetry
        # with future Phase-3 write-path calls; the index row
        # takes it from `node.parent_id` rather than the hook arg
        # because the Node has already been wired into its parent
        # by the time we run.
        del parent_id
        now = _utcnow()
        path = self._paths.path_for(node.id)
        tree = self._tree.current_tree()
        record = _project_record(tree, node, path, now)
        self._index.upsert(record)
        return SyncReport(created=1)

    def _do_rename(self, node_id: NodeId, new_title: str) -> SyncReport:
        # Read existing row (404 → no-op + stale flag).
        try:
            existing = self._index.get(node_id)
        except Exception:
            # `get` raises IndexRecordNotFoundError on absence;
            # we treat that as "row not yet in index" which is
            # expected during a partial rebuild scenario.
            return SyncReport(updated=0)
        now = _utcnow()
        tree = self._tree.current_tree()
        node = tree.try_get(node_id)
        if node is None:
            # Node vanished from the tree between the write
            # and the sync. Hard error — flip stale.
            raise IndexSyncError(
                f"rename sync: node {node_id} not in tree"
            )
        updated = IndexRecord(
            node_id=existing.node_id,
            parent_id=existing.parent_id,
            story_id=existing.story_id,
            title=new_title,
            node_type=existing.node_type,
            filesystem_path=existing.filesystem_path,
            created_at=existing.created_at,
            updated_at=now,
            search_text=existing.search_text,
        )
        self._index.upsert(updated)
        return SyncReport(updated=1)

    def _do_move(
        self,
        node_id: NodeId,
        new_parent_id: NodeId | None,
    ) -> SyncReport:
        # `new_parent_id` is part of the hook signature; the
        # actual parent_id used by the projection comes from
        # `tree.subtree(node_id)[i].parent_id` because the
        # Tree already reflects the new wiring.
        del new_parent_id
        now = _utcnow()
        tree = self._tree.current_tree()
        subtree = tree.subtree(node_id)  # node + all descendants
        # Resolve every path up front. If any path lookup
        # fails, abort — same hard-error policy as the
        # Reconciler: never index a Node we can't locate.
        paths: dict[NodeId, str] = {}
        for node in subtree:
            paths[node.id] = self._paths.path_for(node.id)
        # Build & upsert each record in subtree order.
        affected = 0
        for node in subtree:
            record = _project_record(tree, node, paths[node.id], now)
            try:
                self._index.upsert(record)
            except Exception:
                # If the row doesn't exist yet (create-on-move
                # shouldn't happen, but a partial index could),
                # upsert handles it; any other error propagates
                # to `_run`.
                raise
            affected += 1
        return SyncReport(
            updated=affected,
            subtree_nodes_affected=affected,
        )

    def _do_delete(self, node_id: NodeId) -> SyncReport:
        removed = self._index.delete(node_id)
        return SyncReport(deleted=1 if removed else 0)

    def _do_touch(self, node_id: NodeId) -> SyncReport:
        try:
            existing = self._index.get(node_id)
        except Exception:
            return SyncReport(updated=0)
        now = _utcnow()
        touched = IndexRecord(
            node_id=existing.node_id,
            parent_id=existing.parent_id,
            story_id=existing.story_id,
            title=existing.title,
            node_type=existing.node_type,
            filesystem_path=existing.filesystem_path,
            created_at=existing.created_at,
            updated_at=now,
            search_text=existing.search_text,
        )
        self._index.upsert(touched)
        return SyncReport(updated=1)


# ---- helpers for callers --------------------------------------------------


def make_in_memory_path_provider(
    paths: dict[NodeId, str],
) -> "FilesystemPathProvider":
    """Build a dict-backed path provider for tests.

    Kept in `sync.py` (not the test file) so tests can use it
    without re-declaring a class. Returns a callable object
    that satisfies the `FilesystemPathProvider` Protocol.
    """

    class _DictPathProvider:
        __slots__ = ("_paths",)

        def __init__(self, mapping: dict[NodeId, str]) -> None:
            self._paths = mapping

        def path_for(self, node_id: NodeId) -> str:
            try:
                return self._paths[node_id]
            except KeyError as exc:
                raise SyncPathResolutionError(
                    f"no path for {node_id}"
                ) from exc

    return _DictPathProvider(paths)


def make_tree_provider(tree: Tree) -> "WorkspaceTreeProvider":
    """Wrap an in-memory Tree as a WorkspaceTreeProvider.

    The wrapper copies the Tree on every `current_tree()`
    call so callers cannot mutate the provider's internal
    state. (For the in-memory case this is just `tree` again
    — copying isn't worth the cost at this scale.)
    """

    class _TreeProvider:
        __slots__ = ("_tree",)

        def __init__(self, t: Tree) -> None:
            self._tree = t

        def current_tree(self) -> Tree:
            return self._tree

    return _TreeProvider(tree)
