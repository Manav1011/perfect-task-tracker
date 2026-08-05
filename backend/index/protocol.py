"""IndexRepository Protocol — the contract for the Postgres-backed index.

Per ADR-0011, the index is a *projection* of the filesystem, not a
second source of truth. The Protocol here is intentionally narrow:
only the operations Phase 2.0 needs.

The Protocol lives in `backend.index.protocol` and depends only on
`backend.index.types` (which depends only on `backend.domain.node`).
No API, no service, no concrete implementation imports cross this
boundary.

What the Protocol will NOT gain:

    - No "search by title regex" — that's a query, not a
      repository operation. Belongs in a future search service.
    - No "watch filesystem for changes" — that's a sync concern;
      belongs behind an event-handler, not a repository call.
    - No "transactional with the WorkspaceRepository" — the two
      repositories are decoupled. Coordination lives in the layer
      above (the future reconciler), which uses the
      WorkspaceRepository as the source of truth and the
      IndexRepository as a derived cache.
"""

from __future__ import annotations

from typing import Iterable, Protocol

from backend.domain.node import NodeId
from backend.index.types import IndexRecord


class IndexRepository(Protocol):
    """The persistence contract for the Postgres-backed index.

    Implementations may be backed by SQLAlchemy (production) or by
    a dict (in-memory test fake). All implementations must:
        - be safe to instantiate without a connection (no network
          in `__init__`),
        - return only `IndexRecord` (or raise) — never raw rows,
        - commit-or-rollback per call (caller never needs to
          manage a transaction).
    """

    # ---- single-record reads ------------------------------------

    def get(self, node_id: NodeId) -> IndexRecord:
        """Return the IndexRecord for `node_id`, or raise.

        Raises `IndexRecordNotFoundError` if the id is absent.
        """

    def exists(self, node_id: NodeId) -> bool:
        """Return True iff a row exists for `node_id`.

        Cheaper than `get()` for callers that only need a yes/no.
        """

    # ---- scoped queries ------------------------------------------

    def list_by_story(self, story_id: NodeId) -> list[IndexRecord]:
        """Return all records whose `story_id` matches.

        The story itself is included. Ordering: undefined in
        Phase 2.0; will become insertion-order when the
        workspace rebuild path knows about stable ordering.
        """

    def all_node_ids(self) -> list[NodeId]:
        """Return every node_id currently in the index.

        Used by the reconciler to compute a precise
        deletion count without a full table scan per row.
        Implementations should make this O(n) without an
        extra round-trip per id.
        """

    # ---- writes --------------------------------------------------

    def upsert(self, record: IndexRecord) -> None:
        """Insert `record` or update the existing row in place.

        `record.node_id` is the key. All other fields are
        overwritten with the new values. The implementation
        owns timestamping `updated_at`.

        No-op if `record.node_id` is already in sync — exact
        semantics are implementation-defined (Postgres will
        hit the row; the in-memory fake can short-circuit by
        value equality).
        """

    def upsert_many(self, records: Iterable[IndexRecord]) -> int:
        """Bulk upsert. Returns the count written.

        Used by the future rebuild path. The default single-row
        implementation in `SQLAlchemyIndexRepository` opens
        one transaction.
        """

    def delete(self, node_id: NodeId) -> bool:
        """Delete the row for `node_id`. Returns True if a row
        was removed, False if the id was already absent.

        Idempotent: callers should not need to check `exists`
        first.
        """

    def delete_many(self, node_ids: Iterable[NodeId]) -> int:
        """Bulk delete. Returns the count removed."""

    # ---- administrative ------------------------------------------

    def truncate(self) -> None:
        """Drop every row. Used by the rebuild path before a
        full re-population. NEVER call this from a normal user
        write path — it's destructive and bypasses the
        filesystem.
        """

    def replace_all(self, records: Iterable[IndexRecord]) -> int:
        """Atomic wholesale replacement of the index contents.

        Implemented as `truncate()` + `upsert_many()` inside a
        single transaction so callers (the Reconciler in
        particular) see one logical operation: the index
        becomes exactly `{records}` — either entirely, or not
        at all.

        Returns the count written. Implementations MUST roll
        back the transaction on any error and re-raise, so a
        half-built index is never observable.
        """

    def count(self) -> int:
        """Total rows. Used by tests and admin tooling."""
