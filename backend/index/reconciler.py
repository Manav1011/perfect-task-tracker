"""Index reconciler — full rebuild of the Postgres index from disk.

The reconciler is the only component in Phase 2.1 with both
Repositories as dependencies. It owns one job:

    1. Pull a complete domain `Tree` from `WorkspaceRepository`.
    2. Traverse it deterministically (sorted by node_id).
    3. Project every `Node` to an `IndexRecord`.
    4. Replace the index contents atomically via
       `IndexRepository.replace_all`.
    5. Return a `ReconcileReport` describing what happened.

Per the orchestrator's "Do NOT" list:

    - NEVER imports Filesystem (concrete or Protocol).
    - NEVER imports API, Services, or ORM models.
    - NEVER incrementally syncs; this is a *full* rebuild only.

Per ADR-0006: the index repository's `replace_all` is the
transaction boundary; the reconciler is the orchestration that
owns the sequence *and* the project-from-domain logic.
The repository does NOT know about domain objects; the
reconciler does NOT touch the database.

Determinism strategy:

    - Input: a `Tree` from `WorkspaceRepository.load_tree()`.
      The WorkspaceRepository already constructs the Tree in a
      stable way (children_ids lists order themselves on disk).
    - Traversal: we walk the Tree via `tree.all_nodes()`,
      then sort by `node_id` lexically. Sorting isolates the
      output from whatever internal order the repository used.
    - Projection: a pure function of `(tree, node, path)`.
    - Storage: `replace_all` writes exactly the records given.

Two runs against the same workspace therefore produce
identical IndexRecord sets. Wall-clock duration and per-row
`updated_at` timestamps vary; row count and content do not.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from backend.domain.node import Node, NodeId
from backend.domain.tree import Tree
from backend.index.protocol import IndexRepository
from backend.index.types import IndexRecord
from backend.repositories.protocol import WorkspaceRepository


# ---- public types ---------------------------------------------------------


@dataclass(slots=True, frozen=True)
class ReconcileReport:
    """Result of a single rebuild pass.

    Stats are kept as plain ints so the caller can render them
    anywhere (logs, JSON, Prometheus) without further
    unmarshalling.

    Field meanings:

        - `nodes_scanned`         — every domain Node observed.
        - `records_built`         — count of IndexRecords produced.
        - `records_inserted`      — count committed by `replace_all`.
                                    `records_inserted == records_built`
                                    on success; `0` on mid-rebuild
                                    failure if the transaction rolled
                                    back.
        - `records_updated`       — 0 for full rebuild (no diff);
                                    reserved for future incremental.
        - `records_deleted`       — `pre_count - after_count` so
                                    callers see "an orphan
                                    disappeared" clearly.
        - `elapsed_seconds`       — wall-clock duration.
        - `warnings`              — soft issues encountered (e.g.
                                    a Node that the project step
                                    skipped).
        - `errors`                — hard issues. When non-empty,
                                    no rows have been committed.
    """

    nodes_scanned: int
    records_built: int
    records_inserted: int
    records_updated: int
    records_deleted: int
    elapsed_seconds: float
    warnings: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_success(self) -> bool:
        """True iff the rebuild committed without errors.

        The index contents are either fully consistent with the
        rebuild input, or entirely untouched — there is no
        in-between observable to the caller.
        """
        return not self.errors


class FilesystemPathProvider(Protocol):
    """Just enough surface to look up a Node's filesystem path.

    The Reconciler accepts this hook explicitly so it never
    imports a Filesystem implementation. Production wiring
    will plug in the filesystem-tree repository's path
    accessor (Phase 3+ will move this into a proper seam).
    For Phase 2.1, a static map is sufficient for tests and
    a tiny CLI smoke test.
    """

    def path_for(self, node_id: NodeId) -> str: ...


# ---- mapping: domain Node → IndexRecord ----------------------------------


def _now_utc() -> datetime:
    """One shared timestamp per rebuild.

    Centralised so the *whole* rebuild produces one timestamp,
    not one per row. (Future incremental phases may give each
    row its own `updated_at`, but for full rebuild the single
    timestamp is the only correct value.)
    """
    return datetime.now(timezone.utc)


def _resolve_story_id(tree: Tree, node: Node) -> NodeId | None:
    """Walk parent links until we find the root Story.

    Returns the root Node's id. For a root Node, returns
    `node.id`. Returns `None` only if the walk reaches a
    dead end (which the filesystem layer is responsible for
    preventing — this is a defensive fallback for fakes
    constructed outside the Tree's add/attach path).
    """
    cursor = node.parent_id
    seen: set[NodeId] = set()
    while cursor is not None:
        if cursor in seen:
            # Defensive: Tree forbids cycles; bail out if the
            # fake bypassed that invariant.
            return None
        seen.add(cursor)
        parent = tree.try_get(cursor)
        if parent is None:
            return None
        if parent.parent_id is None:
            return parent.id
        cursor = parent.parent_id
    # node was a root.
    return node.id


def _node_type_to_string(node: Node) -> str:
    """Render a NodeType to its wire string.

    Centralised so adding a future NodeType enum value is a
    one-line change here, not three.
    """
    return node.type.value if hasattr(node.type, "value") else str(node.type)


def _project_node(
    tree: Tree,
    node: Node,
    path: str,
    now: datetime,
) -> IndexRecord:
    """Map one domain Node to one IndexRecord.

    Pure function. Returns an IndexRecord suitable for the
    IndexRepository. `search_text` is empty in Phase 2.1;
    FTS belongs to Phase 4.
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


# ---- reconciler -----------------------------------------------------------


class IndexReconciler:
    """Rebuilds the Postgres index from the workspace tree.

    Constructor dependency inversion: only Protocols. No
    imports of FastAPI, Filesystem (concrete or Protocol),
    API, Services, or ORM models.
    """

    def __init__(
        self,
        workspace_repo: WorkspaceRepository,
        index_repo: IndexRepository,
        path_provider: FilesystemPathProvider,
    ) -> None:
        self._workspace = workspace_repo
        self._index = index_repo
        self._paths = path_provider

    # ---- public API --------------------------------------------------

    def rebuild(self) -> ReconcileReport:
        """Run a full rebuild. Returns a report describing it.

        Idempotent: running it twice on the same workspace
        produces the same final index.

        Failure modes:

            - `load_tree()` raises: report carries an `errors`
              tuple, index is untouched.
            - Path lookup fails for a Node: that Node is
              treated as a *hard* error and the rebuild
              aborts (we never silently drop a Node from
              the index — that would mislead callers about
              coverage).
            - `replace_all` raises: the index repository's
              transaction rolls back; report carries an
              `errors` tuple.
        """
        started_at = time.monotonic()
        errors: list[str] = []
        warnings: list[str] = []

        # ---- step 1: pull the tree -------------------------------
        try:
            tree = self._workspace.load_tree()
        except Exception as exc:  # explicit failure behaviour
            return ReconcileReport(
                nodes_scanned=0,
                records_built=0,
                records_inserted=0,
                records_updated=0,
                records_deleted=0,
                elapsed_seconds=time.monotonic() - started_at,
                errors=(f"workspace load failed: {exc!r}",),
            )

        # ---- step 2/3: project -------------------------------
        now = _now_utc()
        records, aborted = self._project_tree(tree, now, errors, warnings)
        ordered = sorted(records, key=lambda r: r.node_id)
        # If any Node could not be projected (path lookup
        # failed, project raised), the rebuild is aborted
        # before touching the index. We never produce a partial
        # index that silently drops unresolvable nodes.
        if aborted:
            return ReconcileReport(
                nodes_scanned=len(ordered),
                records_built=len(ordered),
                records_inserted=0,
                records_updated=0,
                records_deleted=0,
                elapsed_seconds=time.monotonic() - started_at,
                warnings=tuple(warnings),
                errors=tuple(errors),
            )

        # ---- pre-flight: read pre-existing ids for the diff ----
        # We need *intersection* information to compute deletions
        # precisely. `count()` is too coarse; `all_node_ids()`
        # gives us the set.
        try:
            pre_ids = set(self._index.all_node_ids())
        except Exception as exc:
            errors.append(f"index id listing failed: {exc!r}")
            pre_ids = set()

        # ---- step 4: atomic replace -------------------------------
        try:
            inserted = self._index.replace_all(ordered)
        except Exception as exc:
            # Transaction rolled back: index is untouched
            # (per the IndexRepository.replace_all contract).
            errors.append(f"replace_all failed: {exc!r}")
            return ReconcileReport(
                nodes_scanned=len(ordered),
                records_built=len(ordered),
                records_inserted=0,
                records_updated=0,
                records_deleted=0,
                elapsed_seconds=time.monotonic() - started_at,
                warnings=tuple(warnings),
                errors=tuple(errors),
            )

        # ---- step 5: report ----------------------------------------
        # `records_deleted` is the precise count of pre-existing
        # ids that did NOT survive the rebuild:
        new_ids = {r.node_id for r in ordered}
        deleted = sum(1 for nid in pre_ids if nid not in new_ids)

        return ReconcileReport(
            nodes_scanned=len(ordered),
            records_built=len(ordered),
            records_inserted=inserted,
            records_updated=0,
            records_deleted=deleted,
            elapsed_seconds=time.monotonic() - started_at,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )

    # ---- internals ----------------------------------------------------

    def _project_tree(
        self,
        tree: Tree,
        now: datetime,
        errors: list[str],
        warnings: list[str],
    ) -> tuple[list[IndexRecord], bool]:
        """Walk every Node in `tree` and produce an IndexRecord.

        Returns `(records, aborted)` — `aborted` is True if any
        Node could not be projected (path lookup failed, project
        raised). When `aborted`, the caller MUST NOT call
        `replace_all`; partial indexes are never an option.

        Traversal order is **node_id sorted lexically**, not the
        Tree's natural iteration order. Sorting makes the result
        a function only of the set of Nodes — the same workspace
        yields the same record stream no matter how the
        underlying filesystem was walked.
        """
        records: list[IndexRecord] = []
        nodes: Iterable[Node] = tree.all_nodes()
        ordered_ids = sorted((n.id for n in nodes))
        aborted = False
        for node_id in ordered_ids:
            node = tree.try_get(node_id)
            if node is None:
                warnings.append(
                    f"node {node_id}: disappeared between "
                    f"all_nodes() and get(); skipping"
                )
                continue
            try:
                path = self._paths.path_for(node_id)
            except Exception as exc:
                # Path resolution failure is a *hard* error: a
                # Node we can't locate on disk must never be
                # indexed silently.
                errors.append(
                    f"node {node_id}: path lookup failed "
                    f"({exc!r})"
                )
                aborted = True
                continue
            try:
                records.append(_project_node(tree, node, path, now))
            except Exception as exc:
                errors.append(
                    f"node {node_id}: project failed ({exc!r})"
                )
                aborted = True
        return records, aborted
