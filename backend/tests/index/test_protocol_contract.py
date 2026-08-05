"""Behavioral contract test for `IndexRepository`.

Runs every assertion against the in-memory fake. The same
contract is asserted against the SQLAlchemy implementation in
`test_sqlalchemy_index_repository.py` (which requires Postgres).

If a new method lands on the Protocol, add a parametrized test
here. Per ADR-0011, the index is "a cache with no business
logic" — the assertions verify that.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.domain.node import NodeId
from backend.index.exceptions import IndexRecordNotFoundError
from backend.index.protocol import IndexRepository
from backend.index.types import IndexRecord


# ---- fixtures --------------------------------------------------------------


def _ts(offset_seconds: int = 0) -> datetime:
    base = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=offset_seconds)


def _record(
    *,
    node_id: str = "11111111-1111-1111-1111-111111111111",
    parent_id: str | None = None,
    story_id: str | None = "11111111-1111-1111-1111-111111111111",
    title: str = "Story A",
    node_type: str = "story",
    filesystem_path: str = "story-a",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    search_text: str = "",
) -> IndexRecord:
    return IndexRecord(
        node_id=NodeId(node_id),
        parent_id=NodeId(parent_id) if parent_id is not None else None,
        story_id=NodeId(story_id) if story_id is not None else None,
        title=title,
        node_type=node_type,
        filesystem_path=filesystem_path,
        created_at=created_at or _ts(),
        updated_at=updated_at or _ts(),
        search_text=search_text,
    )


# ---- contract tests --------------------------------------------------------


def test_get_raises_when_absent(index_repo: IndexRepository) -> None:
    with pytest.raises(IndexRecordNotFoundError):
        index_repo.get(NodeId("00000000-0000-0000-0000-000000000000"))


def test_exists_false_when_absent(index_repo: IndexRepository) -> None:
    assert index_repo.exists(NodeId("00000000-0000-0000-0000-000000000000")) is False


def test_upsert_then_get_round_trip(index_repo: IndexRepository) -> None:
    record = _record(title="Round Trip")
    index_repo.upsert(record)
    fetched = index_repo.get(record.node_id)
    assert fetched.node_id == record.node_id
    assert fetched.title == "Round Trip"
    assert fetched.node_type == "story"


def test_upsert_overwrites_existing(index_repo: IndexRepository) -> None:
    index_repo.upsert(_record(title="Original"))
    index_repo.upsert(_record(title="Renamed"))
    assert index_repo.get(NodeId(_record().node_id)).title == "Renamed"


def test_upsert_preserves_created_at(
    index_repo: IndexRepository,
) -> None:
    first = _record(title="Old Title", created_at=_ts(0))
    second = _record(title="New Title", created_at=_ts(60))
    index_repo.upsert(first)
    index_repo.upsert(second)
    got = index_repo.get(first.node_id)
    # First write owns the created_at; subsequent writes don't
    # move it backwards.
    assert got.created_at == _ts(0)


def test_exists_true_after_upsert(index_repo: IndexRepository) -> None:
    record = _record()
    index_repo.upsert(record)
    assert index_repo.exists(record.node_id) is True


def test_list_by_story_returns_only_that_story(
    index_repo: IndexRepository,
) -> None:
    story_a = _record(
        node_id="11111111-1111-1111-1111-111111111111",
        title="Story A",
        filesystem_path="story-a",
        created_at=_ts(0),
    )
    child_a = _record(
        node_id="22222222-2222-2222-2222-222222222222",
        title="Child A",
        story_id="11111111-1111-1111-1111-111111111111",
        filesystem_path="story-a/child-a",
        created_at=_ts(60),
    )
    story_b = _record(
        node_id="33333333-3333-3333-3333-333333333333",
        story_id="33333333-3333-3333-3333-333333333333",
        title="Story B",
        filesystem_path="story-b",
        created_at=_ts(30),
    )
    index_repo.upsert(story_a)
    index_repo.upsert(child_a)
    index_repo.upsert(story_b)

    listing = index_repo.list_by_story(NodeId(story_a.node_id))
    titles = sorted(r.title for r in listing)
    assert titles == ["Child A", "Story A"]


def test_list_by_story_ordered_by_created_at(
    index_repo: IndexRepository,
) -> None:
    later = _record(
        node_id="22222222-2222-2222-2222-222222222222",
        title="Later",
        created_at=_ts(60),
    )
    earlier = _record(
        node_id="11111111-1111-1111-1111-111111111111",
        title="Earlier",
        created_at=_ts(0),
    )
    index_repo.upsert(later)
    index_repo.upsert(earlier)
    listing = index_repo.list_by_story(NodeId(earlier.story_id))
    assert [r.title for r in listing] == ["Earlier", "Later"]


def test_upsert_many_returns_count(index_repo: IndexRepository) -> None:
    records = [
        _record(node_id=f"{i:08x}-0000-0000-0000-000000000000", title=f"T{i}")
        for i in range(5)
    ]
    n = index_repo.upsert_many(records)
    assert n == 5
    assert index_repo.count() == 5


def test_delete_returns_false_for_absent(index_repo: IndexRepository) -> None:
    assert index_repo.delete(NodeId("00000000-0000-0000-0000-000000000000")) is False


def test_delete_returns_true_and_removes(index_repo: IndexRepository) -> None:
    record = _record()
    index_repo.upsert(record)
    assert index_repo.delete(record.node_id) is True
    assert index_repo.exists(record.node_id) is False


def test_delete_many_returns_count(index_repo: IndexRepository) -> None:
    r1 = _record(node_id="11111111-1111-1111-1111-111111111111")
    r2 = _record(node_id="22222222-2222-2222-2222-222222222222")
    index_repo.upsert(r1)
    index_repo.upsert(r2)
    n = index_repo.delete_many([r1.node_id, r2.node_id])
    assert n == 2
    assert index_repo.count() == 0


def test_truncate_empties_the_repo(index_repo: IndexRepository) -> None:
    for i in range(3):
        index_repo.upsert(_record(node_id=f"{i:08x}-0000-0000-0000-000000000000"))
    assert index_repo.count() == 3
    index_repo.truncate()
    assert index_repo.count() == 0


def test_count_zero_on_empty(index_repo: IndexRepository) -> None:
    assert index_repo.count() == 0


# ---- guard: IndexRecord construction invariants -------------------------


def test_index_record_rejects_empty_title() -> None:
    with pytest.raises(ValueError):
        _record(title="")
