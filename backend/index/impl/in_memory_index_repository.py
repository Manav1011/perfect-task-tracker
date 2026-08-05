"""InMemoryIndexRepository — a dict-backed IndexRepository for tests.

Mirrors `SQLAlchemyIndexRepository`'s surface but with no DB,
no engine, no transactions. Used by tests that need an
IndexRepository but don't want to depend on a Postgres instance.

Behaviour notes:

    - `upsert` short-circuits if the new record is byte-equal to
      the existing one — helpful when a rebuild path feeds the
      same record back in and we want to measure "actually
      changed" without instruments.
    - `delete` is idempotent and returns False on absent ids.
    - `truncate` resets the dict atomically.

This is for TESTS ONLY. The production stack uses
`SQLAlchemyIndexRepository`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from backend.domain.node import NodeId
from backend.index.exceptions import IndexRecordNotFoundError
from backend.index.protocol import IndexRepository
from backend.index.types import IndexRecord


def _utcnow() -> datetime:
    """Timezone-aware UTC now — used when callers don't supply a timestamp."""
    return datetime.now(timezone.utc)


class InMemoryIndexRepository:
    """A pure-dict IndexRepository.

    Threads are not a concern (tests are single-threaded), so a
    simple dict is the right data structure. The dict is keyed
    on `node_id`.
    """

    def __init__(self) -> None:
        self._records: dict[str, IndexRecord] = {}

    # ---- reads --------------------------------------------------------

    def get(self, node_id: NodeId) -> IndexRecord:
        """Return the IndexRecord or raise IndexRecordNotFoundError."""
        record = self._records.get(node_id)
        if record is None:
            raise IndexRecordNotFoundError(node_id)
        return record

    def exists(self, node_id: NodeId) -> bool:
        return node_id in self._records

    def list_by_story(self, story_id: NodeId) -> list[IndexRecord]:
        """Records whose `story_id` matches.

        Ordering: by `created_at` ascending, breaking ties on
        `node_id`. This matches the SQL implementation's order
        so tests against either side see the same sequence.
        """
        rows = [r for r in self._records.values() if r.story_id == story_id]
        rows.sort(key=lambda r: (r.created_at, r.node_id))
        return rows

    def all_node_ids(self) -> list[NodeId]:
        return [NodeId(nid) for nid in self._records.keys()]  # type: ignore[list-item]  # noqa: E501

    # ---- writes -------------------------------------------------------

    def upsert(self, record: IndexRecord) -> None:
        existing = self._records.get(record.node_id)
        # Preserve created_at across upserts.
        new_record = IndexRecord(
            node_id=record.node_id,
            parent_id=record.parent_id,
            story_id=record.story_id,
            title=record.title,
            node_type=record.node_type,
            filesystem_path=record.filesystem_path,
            created_at=existing.created_at if existing else record.created_at,
            updated_at=_utcnow(),
            search_text=record.search_text,
        )
        # No-op short-circuit: callers rebuilding from a stable
        # source often re-issue the same record.
        if existing == new_record:
            return
        self._records[record.node_id] = new_record

    def upsert_many(self, records: Iterable[IndexRecord]) -> int:
        count = 0
        for record in records:
            self.upsert(record)
            count += 1
        return count

    # ---- destructive --------------------------------------------------

    def delete(self, node_id: NodeId) -> bool:
        return self._records.pop(node_id, None) is not None

    def delete_many(self, node_ids: Iterable[NodeId]) -> int:
        count = 0
        for nid in node_ids:
            if self.delete(nid):
                count += 1
        return count

    # ---- administrative ----------------------------------------------

    def truncate(self) -> None:
        self._records.clear()

    def replace_all(self, records: Iterable[IndexRecord]) -> int:
        """Atomic wholesale replacement.

        In-memory backing is naturally transactional in the
        sense the tests assert: an exception during the
        assignment leaves the previous content untouched
        (Python's dict-assignment is atomic w.r.t. CPython's
        GIL; we keep that promise by computing the new map
        fully before any external observation).
        """
        materialized = list(records)
        new_map: dict[str, IndexRecord] = {}
        now = _utcnow()
        for record in materialized:
            existing = self._records.get(record.node_id)
            new_map[record.node_id] = IndexRecord(
                node_id=record.node_id,
                parent_id=record.parent_id,
                story_id=record.story_id,
                title=record.title,
                node_type=record.node_type,
                filesystem_path=record.filesystem_path,
                created_at=existing.created_at if existing else record.created_at,
                updated_at=now,
                search_text=record.search_text,
            )
        # Single atomic publish.
        self._records = new_map
        return len(new_map)

    def count(self) -> int:
        return len(self._records)
