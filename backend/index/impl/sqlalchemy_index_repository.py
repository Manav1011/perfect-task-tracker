"""SQLAlchemyIndexRepository — Postgres-backed IndexRepository.

Owns the lifecycle of one short transaction per public method.
The Protocol documents this contract: callers don't manage
sessions, don't roll back, don't flush — just call.

Sessions are pulled from `backend.database.session.get_session_factory`
through a per-call context manager so the engine doesn't need
to be touched at import time. That keeps the test path
"context that doesn't talk to Postgres" viable even if a
test process happens to have a `DATABASE_URL` pointing at a
dead instance.

Methods:

    - `get` / `exists` / `list_by_story`     — reads (one tx, read-only).
    - `upsert` / `upsert_many`               — writes (one tx each).
    - `delete` / `delete_many`               — destructive (one tx).
    - `truncate`                             — table-level wipe.
    - `count`                                — admin.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.database.session import get_session_factory
from backend.domain.node import NodeId
from backend.index.exceptions import IndexRecordNotFoundError
from backend.index.protocol import IndexRepository
from backend.index.types import IndexRecord
from backend.models.node_index import NodeIndexRow


def _record_to_row(record: IndexRecord) -> NodeIndexRow:
    """Map an IndexRecord (dataclass) to a NodeIndexRow (ORM).

    IDs and timestamps are taken verbatim — the repository trusts
    callers to produce stable UUIDs and to leave the
    `updated_at` field for the DB to manage via `onupdate`.
    """
    return NodeIndexRow(
        node_id=record.node_id,
        parent_id=record.parent_id,
        story_id=record.story_id,
        title=record.title,
        node_type=record.node_type,
        filesystem_path=record.filesystem_path,
        created_at=record.created_at,
        updated_at=record.updated_at,
        search_text=record.search_text,
    )


def _row_to_record(row: NodeIndexRow) -> IndexRecord:
    """Inverse of `_record_to_row` — ORM → dataclass."""
    return IndexRecord(
        node_id=NodeId(row.node_id),
        parent_id=NodeId(row.parent_id) if row.parent_id is not None else None,
        story_id=NodeId(row.story_id) if row.story_id is not None else None,
        title=row.title,
        node_type=row.node_type,
        filesystem_path=row.filesystem_path,
        created_at=row.created_at,
        updated_at=row.updated_at,
        search_text=row.search_text or "",
    )


class SQLAlchemyIndexRepository:
    """Postgres-backed IndexRepository.

    Constructor takes no arguments — the engine is fetched lazily
    via `get_session_factory()`. This matches the
    WorkspaceRepository pattern (no I/O in `__init__`).
    """

    @contextmanager
    def _session(self) -> Iterator[Session]:
        factory = get_session_factory()
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ---- single-record reads --------------------------------------

    def get(self, node_id: NodeId) -> IndexRecord:
        """Return the IndexRecord for `node_id`.

        Raises `IndexRecordNotFoundError` if absent. (Returning
        None would force every caller to write the same `if x is
        None: ...` dance; the exception makes "not found" a
        single line at every call site.)
        """
        with self._session() as session:
            row = session.get(NodeIndexRow, node_id)
            if row is None:
                raise IndexRecordNotFoundError(node_id)
            return _row_to_record(row)

    def exists(self, node_id: NodeId) -> bool:
        """Cheap yes/no — one PK lookup, no row construction."""
        with self._session() as session:
            row = session.get(NodeIndexRow, node_id)
            return row is not None

    # ---- scoped queries --------------------------------------------

    def list_by_story(self, story_id: NodeId) -> list[IndexRecord]:
        """Return all rows whose `story_id` matches.

        Ordering is by `created_at` ascending, breaking ties on
        `node_id`. This gives a stable, filesystem-clock-aligned
        order — useful during the rebuild path, where records
        arrive in insertion order.
        """
        with self._session() as session:
            stmt = (
                select(NodeIndexRow)
                .where(NodeIndexRow.story_id == story_id)
                .order_by(NodeIndexRow.created_at, NodeIndexRow.node_id)
            )
            rows = session.execute(stmt).scalars().all()
            return [_row_to_record(r) for r in rows]

    def all_node_ids(self) -> list[NodeId]:
        """Return every `node_id` currently in the index.

        Implemented as `SELECT node_id FROM node_index`. Index
        size is small (one row per Node), so this is
        acceptable for the rebuild path. For very large
        indexes a future phase might stream ids in batches.
        """
        with self._session() as session:
            stmt = select(NodeIndexRow.node_id)
            return [NodeId(row[0]) for row in session.execute(stmt).all()]

    # ---- writes ----------------------------------------------------

    def upsert(self, record: IndexRecord) -> None:
        """Insert or update.

        The transaction reads first; if a row exists, we update
        its mutable fields in place, preserving `created_at`.
        Otherwise we insert with the caller's `created_at`.

        We deliberately do NOT call `session.merge()` — that
        round-trips through a SELECT anyway, but it also
        obscures the read-then-write shape from `prefetch`.
        """
        with self._session() as session:
            existing = session.get(NodeIndexRow, record.node_id)
            if existing is None:
                # Fresh insert — caller-supplied timestamps.
                row = _record_to_row(record)
                session.add(row)
            else:
                existing.parent_id = record.parent_id
                existing.story_id = record.story_id
                existing.title = record.title
                existing.node_type = record.node_type
                existing.filesystem_path = record.filesystem_path
                existing.search_text = record.search_text
                # created_at stays.
                # updated_at is handled by the onupdate= trigger.

    def upsert_many(self, records: Iterable[IndexRecord]) -> int:
        """Bulk write in one transaction. Returns the count."""
        count = 0
        with self._session() as session:
            for record in records:
                existing = session.get(NodeIndexRow, record.node_id)
                if existing is None:
                    session.add(_record_to_row(record))
                else:
                    existing.parent_id = record.parent_id
                    existing.story_id = record.story_id
                    existing.title = record.title
                    existing.node_type = record.node_type
                    existing.filesystem_path = record.filesystem_path
                    existing.search_text = record.search_text
                count += 1
        return count

    # ---- destructive -----------------------------------------------

    def delete(self, node_id: NodeId) -> bool:
        """Delete one row. Returns True if it existed."""
        with self._session() as session:
            row = session.get(NodeIndexRow, node_id)
            if row is None:
                return False
            session.delete(row)
            return True

    def delete_many(self, node_ids: Iterable[NodeId]) -> int:
        """Bulk delete in one transaction. Returns the count removed."""
        ids = list(node_ids)
        if not ids:
            return 0
        with self._session() as session:
            stmt = delete(NodeIndexRow).where(
                NodeIndexRow.node_id.in_(ids)
            )
            result = session.execute(stmt)
            # SQLAlchemy 2.x: rowcount lives on the CursorResult
            # reached via result; fall back to 0 when the driver
            # doesn't support it (e.g. SQLite in some configs).
            try:
                return int(result.rowcount or 0)  # type: ignore[attr-defined]
            except AttributeError:
                return 0

    # ---- administrative --------------------------------------------

    def truncate(self) -> None:
        """Drop every row. Idempotent.

        The implementation uses `delete()` rather than
        `TRUNCATE` because (a) TRUNCATE in Postgres forces
        `AUTOCOMMIT` and bypasses ORM session lifecycle, and
        (b) we have no sequence columns to restart.
        """
        with self._session() as session:
            session.execute(
                NodeIndexRow.__table__.delete().execution_options(
                    synchronize_session=False
                )
            )

    def replace_all(self, records: Iterable[IndexRecord]) -> int:
        """Atomic wholesale replacement.

        Truncates the table and inserts every record in one
        transaction. The truncate is unconditional: a partial
        rebuild that sees zero records still empties the
        index (this matches the design invariant — the index
        is exactly a projection of disk; if disk is empty,
        the index must be too).
        """
        materialized = list(records)
        with self._session() as session:
            session.execute(
                NodeIndexRow.__table__.delete().execution_options(
                    synchronize_session=False
                )
            )
            for record in materialized:
                session.add(_record_to_row(record))
            # Commit happens in `_session`'s context manager.
        return len(materialized)

    def count(self) -> int:
        """Total rows. Uses `SELECT count(*)` so the DB returns
        a scalar without shipping every node_id over the wire."""
        with self._session() as session:
            stmt = select(func.count()).select_from(NodeIndexRow)
            return int(session.execute(stmt).scalar_one())
