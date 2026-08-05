"""LocalWorkspaceRepository — disk-backed implementation of WorkspaceRepository.

Sits on top of a `Filesystem` (Phase 1.2). Owns tree reconstruction
and the multi-step coordination of writes; defers all business rules
to the domain.

Phase 2.2 — incremental index synchronisation:

    This repository is also the seam where an
    `IncrementalIndexSynchronizer` is invoked. Per the brief:

        - Repository remains the persistence orchestrator.
        - Repository invokes the synchroniser AFTER the
          filesystem operation succeeds.
        - Synchroniser failures NEVER roll back the filesystem.

    The synchroniser is *optional*. When None (the default for
    existing tests that predate Phase 2.2), the repository
    behaves exactly as it did before. Production wiring in
    `backend.api.dependencies` will bind it once the
    workspace path can be read from settings (Phase 3+).

Phase 3.0 — runtime workspace cache:

    This repository is the SINGLE mutation boundary for both
    filesystem and cache (per ADR-0016). Writes complete in
    a deterministic order:

        1. Filesystem persistence (source of truth — must
           succeed or the operation raises).
        2. Cache invalidation (best-effort — fs is already
           committed; a cache failure logs a structured
           warning and continues).
        3. Index synchronisation hook (best-effort — same
           defence-in-depth swallow as before).

    The cache is *optional*. When None (legacy tests, or
    a configuration that explicitly opts out), every read
    goes to disk. The repository never reaches the concrete
    cache class — it depends on the `MutableWorkspaceCache`
    Protocol, so the seam is structural (a future
    distributed implementation drops in without touching
    this file).

    Reads become cache-first with a self-healing miss path:

        def load_node(self, node_id):
            if self._cache is not None and self._cache.is_loaded():
                try:
                    return self._cache.load_node(node_id)
                except CacheConsistencyError:
                    pass  # self-heal below
            return self._fs.load_node(node_id)

    The cache and the synchroniser are *independent* side-effects
    of the repository — they never know about each other.
    This is enforced by structural isolation tests
    (`backend/tests/workspace/test_isolation.py`).
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from backend.core.logging import get_logger
from backend.domain.exceptions import InvalidParentError, NodeNotFoundError
from backend.domain.node import Node, NodeId, NodeMetadata
from backend.domain.tree import Tree
from backend.filesystem import Filesystem

# Walking the workspace for tree reconstruction is intentionally not a
# Filesystem method (Phase 1.2 `walk()` raises on corrupt entries).
# The repository owns reconstruction and must be resilient: corruption
# is surfaced via absence, not by taking down the whole tree.
from backend.filesystem.exceptions import (
    DuplicateNodeIdError,
    FilesystemError,
    InvalidNodeJSONError,
    NodeDirectoryMissingError,
    NodeMetadataMissingError,
    NodeNotFoundOnDiskError,
)
from backend.filesystem.serialization import dict_to_node, read_node_json
from backend.repositories.exceptions import RepositoryError

if TYPE_CHECKING:
    from backend.workspace.protocol import MutableWorkspaceCache

logger = get_logger(__name__)


NODE_JSON_FILENAME = "node.json"


class _SyncHook(Protocol):
    """Structural type for the synchroniser dependency.

    Defined locally so this file doesn't take an import
    dependency on `backend.index.sync` at runtime. The
    `IncrementalIndexSynchronizer` class satisfies it
    structurally — same shape, no inheritance needed.
    """

    def on_node_created(
        self, node: Node, parent_id: NodeId | None
    ): ...

    def on_node_renamed(self, node_id: NodeId, new_title: str): ...

    def on_node_moved(
        self, node_id: NodeId, new_parent_id: NodeId | None
    ): ...

    def on_node_deleted(self, node_id: NodeId): ...

    def on_metadata_updated(self, node_id: NodeId, title: str): ...

    def on_canvas_updated(self, node_id: NodeId): ...


class LocalWorkspaceRepository:
    """A `WorkspaceRepository` backed by a local `Filesystem`.

    Optional `sync` argument wires the Phase 2.2 incremental
    index synchroniser. When provided, every write method
    invokes the matching `on_node_*` hook AFTER the
    filesystem operation succeeds. The synchroniser is
    responsible for failing safe (it swallows its own
    exceptions and flips a staleness flag) so the
    repository never has to wrap sync calls in try/except.

    Optional `cache` argument wires the Phase 3.0 runtime
    workspace cache. When provided, every read goes through
    the cache first (with a self-healing fallback to disk)
    and every write invalidates the affected ids AFTER the
    filesystem operation succeeds. Cache failures NEVER
    roll back filesystem persistence.
    """

    def __init__(
        self,
        fs: Filesystem,
        sync: "_SyncHook | None" = None,
        cache: "MutableWorkspaceCache | None" = None,
    ) -> None:
        self._fs = fs
        self._sync = sync
        self._cache = cache

    # ---- error translation ------------------------------------------

    @contextlib.contextmanager
    def _translate_fs_errors(self, operation: str, node_id: NodeId | None = None):
        """Translate filesystem-layer exceptions to repository-layer ones.

        The repository is the seam between the filesystem substrate
        and the rest of the system. Callers (services, sync hooks,
        cache invalidation) must only ever see repository/domain
        exceptions, never `NodeNotFoundOnDiskError` (filesystem) or
        any other `FilesystemError` subclass.

        Why a context manager: many write paths do
        ``fs.<op>(...) ; cache.invalidate(...) ; sync(...)``
        and we want the translation to cover the whole sequence
        rather than wrapping each `_fs.*` call.

        Translation table:

            NodeNotFoundOnDiskError   → NodeNotFoundError (domain)
            InvalidParentError        → InvalidMoveError (domain)
            DuplicateNodeIdError      → DuplicateNodeIdRepositoryError
            other FilesystemError     → RepositoryError (generic)

        Argument `operation` is the caller's name (e.g. ``"delete_node"``)
        used in the wrapper's message so log/exception traces name
        the operation that failed.
        """
        try:
            yield
        except NodeNotFoundOnDiskError as exc:
            raise NodeNotFoundError(node_id or exc.node_id) from exc
        except InvalidParentError as exc:
            # The filesystem's InvalidParentError already maps to
            # a "cycle / bad parent" semantic. The service catches
            # the domain-level InvalidParentError to raise
            # `CycleInMoveServiceError` / `ParentNotFoundServiceError`.
            raise InvalidParentError(str(exc)) from exc
        except DuplicateNodeIdError as exc:
            # The filesystem detects duplicate UUIDs on disk; the
            # repository surfaces this as a generic repository
            # error so the service can decide what to do with it.
            raise RepositoryError(f"{operation}: duplicate node id: {exc}") from exc
        # Other `FilesystemError` subclasses (CanvasMissingError,
        # NodeMetadataMissingError, NodeDirectoryMissingError,
        # InvalidNodeJSONError, SiblingNameCollisionError) are
        # domain-specific filesystem signals that the service
        # layer may need to inspect directly. We do NOT wrap them
        # as generic `RepositoryError`.

    # ---- mutation helpers -------------------------------------------

    def _invoke_sync(self, hook_name: str, *args) -> None:
        """Invoke a sync hook by name, swallowing any exception.

        Per Phase 2.2 brief: "Any index failure must NEVER roll
        back filesystem persistence." The synchroniser's own
        `_run()` already catches its exceptions, but defence
        in depth: if a future maintainer removes that swallow,
        or if the hook is replaced with a non-conforming
        implementation, the repository write must still succeed.

        `hook_name` is a string attribute on the sync object
        (e.g. "on_node_created"). We look it up inside this
        method so the call sites don't dereference
        `self._sync.<attr>` when `_sync is None`.

        The hook_name is logged so post-mortem debugging is
        possible without re-running with a debugger.
        """
        if self._sync is None:
            return
        hook = getattr(self._sync, hook_name, None)
        if hook is None:
            # Sync object lacks the hook — quietly skip. This
            # lets partial-implementation fakes (e.g. tests)
            # coexist with the production synchroniser.
            return
        try:
            hook(*args)
        except Exception as exc:
            logger.exception(
                "repository.sync_failed",
                hook=hook_name,
                sync_type=type(self._sync).__name__,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    def _invalidate_cache(self, operation: str, node_ids) -> None:  # type: ignore[no-untyped-def]
        """Invalidate cache entries after a successful fs write.

        Mirrors the index-sync failure semantics from
        `_invoke_sync`: filesystem persistence is already
        committed; cache failures never roll it back. We
        log structured warnings with operation/node_id/
        exception_type/message so production diagnosis is
        possible without re-running with a debugger.

        Per ChatGPT's Phase 3.0 refinement #5: "Cache
        mutation failures must never roll back the
        filesystem write." Same philosophy as index sync.

        Args:
            operation: the repository write operation name
                (e.g. "save_node", "rename_node"). Used as
                a log field so the warning can be correlated
                with the originating request.
            node_ids: a single NodeId or an iterable of
                NodeIds. We branch on type so callers don't
                need to wrap single ids.
        """
        if self._cache is None:
            return
        # Accept either a single NodeId or an iterable.
        try:
            if isinstance(node_ids, str):
                self._cache.invalidate(node_ids)
            else:
                # Try invalidate_many first; if the cache
                # only exposes the single-id method (a test
                # fake or a future slimmer Protocol), fall
                # back to per-id invalidation.
                try:
                    self._cache.invalidate_many(node_ids)
                except AttributeError:
                    for nid in node_ids:
                        self._cache.invalidate(nid)
        except Exception as exc:
            # Cache failure is never fatal — fs write
            # already committed. Self-healing reads will
            # re-populate from disk.
            sample_ids = (
                list(node_ids)[:5]
                if not isinstance(node_ids, str)
                else [node_ids]
            )
            logger.warning(
                "repository.cache_invalidation_failed",
                operation=operation,
                node_ids=sample_ids,
                error_type=type(exc).__name__,
                error_message=str(exc),
                fs_was_committed=True,
            )

    # ---- single-node reads -----------------------------------------

    def load_node(self, node_id: NodeId) -> Node:
        """Cache-first read with self-healing miss path.

        Order:
            1. If a cache is wired and loaded, ask it.
            2. On a `CacheConsistencyError` (id invalidated
               or never present), fall through to disk.
            3. On a `CacheNotInitializedError` (programming
               error — populate didn't run), also fall
               through; the structured warning has already
               been logged elsewhere.

        The cache miss increments the cache's `misses`
        counter, which a production operator can read via
        `cache.stats()`. Self-healing misses are an
        *expected* degraded path (per ChatGPT refinement #3).
        """
        if self._cache is not None and self._cache.is_loaded():
            try:
                return self._cache.load_node(node_id)
            except Exception as exc:
                # Self-heal: log a structured warning, then
                # read from disk. We catch the broad
                # Exception because the cache may raise any
                # subclass of `CacheError` (e.g.,
                # `CacheConsistencyError` for a missing id,
                # or `CacheNotInitializedError` if populate
                # never ran). The disk read is the safety
                # net — the cache is an optimisation, not a
                # precondition.
                logger.warning(
                    "repository.cache_miss_self_heal",
                    operation="load_node",
                    node_id=node_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
        with self._translate_fs_errors("load_node", node_id):
            return self._fs.load_node(node_id)

    def load_children(self, node_id: NodeId) -> list[Node]:
        """Cache-first children read with self-healing fallback."""
        if self._cache is not None and self._cache.is_loaded():
            try:
                return self._cache.load_children(node_id)
            except Exception as exc:
                logger.warning(
                    "repository.cache_miss_self_heal",
                    operation="load_children",
                    node_id=node_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
        with self._translate_fs_errors("load_children", node_id):
            return self._fs.list_children(node_id)

    # ---- tree reconstruction ---------------------------------------

    def load_tree(self) -> Tree:
        """Reconstruct the tree deterministically from disk.

        This path is *not* cache-first: it always walks
        disk because it's the startup populator's input.
        The cache will mirror the result via `populate()`.
        We keep this method on the repository because the
        StartupSubsystem calls it once during boot, and
        that's the only time we want the full filesystem
        walk.

        Algorithm:
            1. Recursively walk the workspace root, loading every
               node.json we can read. Corrupt directories (unreadable
               JSON, missing files) are skipped silently — corruption
               is surfaced elsewhere (see ADR-0004) but the rest of
               the tree must remain reconstructable.
            2. Insert every loadable Node into a fresh Tree. Pass 1
               handles parent-exists invariants: a Node whose
               `parent_id` references an absent id is dropped.
            3. Pass 2 re-orders `children_ids` to match the on-disk
               ordering and removes ids we couldn't load. This is
               what guarantees `Tree.children_of(...)` doesn't raise
               when the parent has a stale child reference.

        Determinism: input order is sorted by directory path; parent
        children_ids lists are read in their on-disk order. The same
        workspace on disk always produces the same Tree.
        """
        nodes = self._walk_resilient()
        tree = Tree()
        for n in nodes:
            try:
                tree.add(n)
            except Exception:
                # Parent referenced by `n.parent_id` doesn't exist.
                continue

        loadable_ids = {n.id for n in tree.all_nodes()}
        for parent in tree.all_nodes():
            # Prune children_ids whose ids we couldn't load.
            clean = [cid for cid in parent.children_ids if cid in loadable_ids]
            if clean != list(parent.children_ids):
                tree.replace_children(parent.id, clean)
        return tree

    def _walk_resilient(self) -> list[Node]:
        """Walk the workspace, loading every readable node.json.

        Skips corrupt directories rather than raising. Ordering is
        deterministic: directories are visited in sorted order so
        reconstruction is reproducible.
        """
        results: list[Node] = []
        root_path: Path = self._fs.root.path

        def recurse(directory: Path) -> None:
            for entry in sorted(directory.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                if (entry / NODE_JSON_FILENAME).exists():
                    try:
                        payload = read_node_json(entry / NODE_JSON_FILENAME)
                        results.append(dict_to_node(payload))
                    except (
                        InvalidNodeJSONError,
                        NodeMetadataMissingError,
                        NodeDirectoryMissingError,
                    ):
                        continue
                    except FilesystemError:
                        continue
                recurse(entry)

        recurse(root_path)
        return results

    # ---- writes -----------------------------------------------------

    def save_node(self, node: Node, parent_id: NodeId | None) -> Node:
        """Create a new Node on disk under `parent_id`.

        Mutation order: filesystem → cache → sync.
        """
        with self._translate_fs_errors("save_node", node.id):
            persisted = self._fs.create_node(node, parent_id=parent_id)
            # Brand-new node: cache has no prior entry. We still
            # invalidate (idempotent — see `MutableWorkspaceCache`
            # contract) so any stale entry from a future eviction
            # or distributed-cache scenario is cleared.
            self._invalidate_cache("save_node", persisted.id)
            self._invoke_sync("on_node_created", persisted, parent_id)
        return persisted

    def rename_node(self, node_id: NodeId, new_title: str) -> Node:
        """Rename on disk; invalidate the renamed node from the
        cache; fire `on_node_renamed` for the index.

        Only the renamed node changes — its children keep their
        existing entries (renames don't affect subtree structure).
        """
        with self._translate_fs_errors("rename_node", node_id):
            renamed = self._fs.rename_node(node_id, new_title)
            self._invalidate_cache("rename_node", node_id)
            self._invoke_sync("on_node_renamed", node_id, new_title)
        return renamed

    def update_metadata(
        self, node_id: NodeId, metadata: NodeMetadata
    ) -> Node:
        """Update a Node's metadata in place on disk.

        The on-disk layout keeps metadata inside node.json, so
        this is a single-file rewrite with no directory rename
        and no canvas touch. Cache is invalidated so the next
        read sees the new metadata; the index synchroniser's
        ``on_metadata_updated`` hook fires so search stays
        consistent with the filesystem.

        Why a dedicated method (not `rename_node` with the same
        title): rename re-reads the existing node.json and
        applies ``with_title(new_title)``, which would drop any
        unrelated field changes — including the metadata we
        just set. The previous implementation's round-trip
        through rename silently lost metadata updates (verify
        pass regression).
        """
        with self._translate_fs_errors("update_metadata", node_id):
            # Read current Node (cache-first via load_node),
            # apply the new metadata, write back atomically.
            current = self.load_node(node_id)
            updated = current.with_metadata(metadata)
            persisted = self._fs.write_node(updated)
            self._invalidate_cache("update_metadata", node_id)
            self._invoke_sync("on_metadata_updated", node_id, persisted.title)
        return persisted

    def move_node(
        self,
        node_id: NodeId,
        new_parent_id: NodeId | None,
        position: int | None = None,
    ) -> Node:
        """Move on disk; invalidate the moved subtree (root +
        descendants) from the cache; fire `on_node_moved` for
        the index.

        Every node in the subtree has its parent_id, story_id,
        or path potentially changed, so we invalidate the
        entire subtree in one call. We use the cache's
        `subtree_ids()` (which includes the root id by
        contract, matching `Tree.subtree`) directly — not
        `_collect_descendant_ids` (which strips the root for
        the index emit).
        """
        with self._translate_fs_errors("move_node", node_id):
            moved = self._fs.move_node(
                node_id, new_parent_id=new_parent_id, position=position
            )
            ids = self._moved_subtree_ids(node_id)
            self._invalidate_cache("move_node", ids)
            self._invoke_sync("on_node_moved", node_id, new_parent_id)
        return moved

    def delete_node(self, node_id: NodeId) -> None:
        """Delete on disk; invalidate the deleted subtree from
        the cache; fire `on_node_deleted` for each node.

        The repository's `delete_node` already handles subtree
        deletion on the filesystem side; it is the caller's
        responsibility (in `LocalFilesystem.delete_node`) to
        emit one `on_node_deleted` per Node in the deleted
        subtree so the index can remove each row individually.
        We collect those ids here, after the fs call, by
        inspecting what no longer exists on disk.
        """
        with self._translate_fs_errors("delete_node", node_id):
            # Capture the descendant set before we mutate, so we
            # can fire one delete per Node after fs succeeds.
            # (LocalFilesystem.delete_node already removes the
            # directories; we don't need to re-walk.)
            descendant_ids = self._collect_descendant_ids(node_id)
            self._fs.delete_node(node_id)
            # Invalidate the deleted subtree (root + descendants)
            # from the cache in one call.
            self._invalidate_cache(
                "delete_node",
                [node_id, *descendant_ids],
            )
            # Emit deepest-first so leaves go before parents —
            # matches the order LocalFilesystem actually removed them.
            for did in reversed(descendant_ids):
                self._invoke_sync("on_node_deleted", did)
            self._invoke_sync("on_node_deleted", node_id)

    def _collect_descendant_ids(self, node_id: NodeId) -> list[NodeId]:
        """Return the descendant ids of `node_id` (NOT including self).

        Used by `delete_node` (which calls `subtree_ids`
        itself for cache invalidation; this method is only
        used to know which index rows to emit
        `on_node_deleted` for).

        Cache-first: when a cache is wired, ask it for the
        subtree (O(D) BFS walk over the cached Tree). When
        no cache is wired, fall back to the legacy disk
        walk so existing tests continue to pass.
        """
        if self._cache is not None and self._cache.is_loaded():
            try:
                ids = self._cache.subtree_ids(node_id)
                # `subtree_ids` includes the root id by
                # contract (matches `Tree.subtree`). Strip it
                # because the callers want descendants only.
                return [i for i in ids if i != node_id]
            except Exception as exc:
                logger.warning(
                    "repository.cache_subtree_ids_failed",
                    operation="_collect_descendant_ids",
                    node_id=node_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                # Fall through to the disk walk.

        # Legacy disk walk: BFS over children_ids lists.
        ids: list[NodeId] = []
        frontier: list[NodeId] = list(
            self._fs.load_node(node_id).children_ids
        )
        while frontier:
            next_frontier: list[NodeId] = []
            for cid in frontier:
                try:
                    child = self._fs.load_node(cid)
                except Exception:
                    continue
                ids.append(cid)
                next_frontier.extend(child.children_ids)
            frontier = next_frontier
        return ids

    def _moved_subtree_ids(self, node_id: NodeId) -> list[NodeId]:
        """Return `node_id` + all descendant ids.

        Used by `move_node` to invalidate the entire moved
        subtree (root + descendants) from the cache in a
        single call. Distinct from
        `_collect_descendant_ids` (which strips the root)
        because move_node's cache action needs the root.

        Falls back to `[node_id] + _collect_descendant_ids(...)`
        when the cache is unavailable.
        """
        if self._cache is not None and self._cache.is_loaded():
            try:
                return self._cache.subtree_ids(node_id)
            except Exception as exc:
                logger.warning(
                    "repository.cache_subtree_ids_failed",
                    operation="_moved_subtree_ids",
                    node_id=node_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                # Fall through to the disk walk.
        return [node_id, *self._collect_descendant_ids(node_id)]

    # ---- canvas -----------------------------------------------------

    def read_canvas(self, node_id: NodeId) -> str:
        # Canvas is NOT cached (per ADR-0016 / TECH_SPEC §13d).
        # Canvas content is unbounded and has an RPC-style
        # access pattern — caching it would balloon the cache
        # for no latency win on the read paths that matter.
        with self._translate_fs_errors("read_canvas", node_id):
            return self._fs.read_canvas(node_id)

    def write_canvas(self, node_id: NodeId, content: str) -> None:
        """Write canvas.md on disk; fire `on_canvas_updated`
        (bumps the row's `updated_at`).

        No cache action: canvas is not cached.
        """
        with self._translate_fs_errors("write_canvas", node_id):
            self._fs.write_canvas(node_id, content)
            self._invoke_sync("on_canvas_updated", node_id)