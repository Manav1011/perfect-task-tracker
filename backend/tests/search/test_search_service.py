"""Tests for the DefaultSearchService.

Coverage:

    - Validation: empty title/prefix, bad page, mutually
      exclusive title/prefix.
    - Exact title lookup (case-insensitive).
    - Prefix lookup (case-insensitive).
    - List mode (no title/prefix).
    - Filter combinations: node_type, parent_id, story_id.
    - Sort: UPDATED_AT_* and TITLE_*.
    - Pagination: total is pre-pagination, page offset
      is computed correctly.
    - Empty result on a fresh index.
    - Eventually-consistent: the service reads the index
      as-is and does not consult the filesystem.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.domain.node import NodeId
from backend.index.impl import InMemoryIndexRepository
from backend.index.types import IndexRecord
from backend.search import (
    DefaultSearchService,
    InvalidSearchQueryError,
    SearchRequest,
    SearchSort,
)


def _ts(offset_seconds: float = 0.0) -> datetime:
    """A timezone-aware UTC timestamp, optionally offset."""
    base = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=offset_seconds)


def _record(
    *,
    node_id: str,
    title: str,
    node_type: str = "story",
    parent_id: str | None = None,
    story_id: str | None = "11111111-1111-1111-1111-111111111111",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> IndexRecord:
    """Build a minimal IndexRecord for test fixtures."""
    return IndexRecord(
        node_id=NodeId(node_id),
        parent_id=NodeId(parent_id) if parent_id is not None else None,
        story_id=NodeId(story_id) if story_id is not None else None,
        title=title,
        node_type=node_type,
        filesystem_path=node_id,
        created_at=created_at or _ts(),
        updated_at=updated_at or _ts(),
        search_text="",
    )


def _seed_minimal(index: InMemoryIndexRepository) -> list[IndexRecord]:
    """Seed an index with five records spanning types/titles/parents.

    Returns the inserted records so tests can compare.
    """
    records = [
        _record(
            node_id="aaaaaaaa-0000-0000-0000-000000000001",
            title="Alpha",
            node_type="story",
            parent_id=None,
            story_id=None,
            created_at=_ts(1),
            updated_at=_ts(1),
        ),
        _record(
            node_id="aaaaaaaa-0000-0000-0000-000000000002",
            title="alpha-prime",
            node_type="story",
            parent_id=None,
            story_id=None,
            created_at=_ts(2),
            updated_at=_ts(2),
        ),
        _record(
            node_id="aaaaaaaa-0000-0000-0000-000000000003",
            title="Bravo",
            node_type="task",
            parent_id="aaaaaaaa-0000-0000-0000-000000000001",
            story_id="aaaaaaaa-0000-0000-0000-000000000001",
            created_at=_ts(3),
            updated_at=_ts(3),
        ),
        _record(
            node_id="aaaaaaaa-0000-0000-0000-000000000004",
            title="Charlie",
            node_type="task",
            parent_id="aaaaaaaa-0000-0000-0000-000000000001",
            story_id="aaaaaaaa-0000-0000-0000-000000000001",
            created_at=_ts(4),
            updated_at=_ts(4),
        ),
        _record(
            node_id="aaaaaaaa-0000-0000-0000-000000000005",
            title="Alpine",
            node_type="note",
            parent_id="aaaaaaaa-0000-0000-0000-000000000002",
            story_id="aaaaaaaa-0000-0000-0000-000000000002",
            created_at=_ts(5),
            updated_at=_ts(5),
        ),
    ]
    for rec in records:
        index.upsert(rec)
    return records


# ---- validation ----------------------------------------------------------


def test_empty_index_returns_empty_result() -> None:
    """No records → empty result, total=0, page offset=0."""
    index = InMemoryIndexRepository()
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest())
    assert result.is_empty()
    assert result.total == 0
    assert result.page.offset == 0
    assert result.page.limit == 50  # default page_size


def test_empty_title_raises_invalid_query() -> None:
    """An empty title (after strip) is invalid."""
    index = InMemoryIndexRepository()
    svc = DefaultSearchService(index)
    with pytest.raises(InvalidSearchQueryError) as exc:
        svc.search(SearchRequest(title="   "))
    assert exc.value.field == "title"


def test_empty_prefix_raises_invalid_query() -> None:
    """An empty prefix (after strip) is invalid."""
    index = InMemoryIndexRepository()
    svc = DefaultSearchService(index)
    with pytest.raises(InvalidSearchQueryError) as exc:
        svc.search(SearchRequest(prefix="   "))
    assert exc.value.field == "prefix"


def test_negative_page_raises_invalid_query() -> None:
    """Negative page is invalid."""
    index = InMemoryIndexRepository()
    svc = DefaultSearchService(index)
    with pytest.raises(InvalidSearchQueryError) as exc:
        svc.search(SearchRequest(page=-1))
    assert exc.value.field == "page"


def test_zero_page_size_raises_invalid_query() -> None:
    """page_size must be > 0."""
    index = InMemoryIndexRepository()
    svc = DefaultSearchService(index)
    with pytest.raises(InvalidSearchQueryError) as exc:
        svc.search(SearchRequest(page_size=0))
    assert exc.value.field == "page_size"


def test_title_and_prefix_are_mutually_exclusive() -> None:
    """Setting both title and prefix is invalid."""
    index = InMemoryIndexRepository()
    svc = DefaultSearchService(index)
    with pytest.raises(InvalidSearchQueryError) as exc:
        svc.search(SearchRequest(title="Alpha", prefix="Al"))
    assert exc.value.field == "title"


def test_page_size_above_max_raises_invalid_query() -> None:
    """page_size > 200 is invalid."""
    index = InMemoryIndexRepository()
    svc = DefaultSearchService(index)
    with pytest.raises(InvalidSearchQueryError) as exc:
        svc.search(SearchRequest(page_size=201))
    assert exc.value.field == "page_size"


# ---- exact title lookup --------------------------------------------------


def test_exact_title_match_returns_single_record() -> None:
    """Case-insensitive exact match returns the matching record."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(title="Alpha"))
    assert result.total == 1
    assert result.hits[0].record.title == "Alpha"


def test_exact_title_is_case_insensitive() -> None:
    """Lowercase query matches uppercase title."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(title="alpha"))
    assert result.total == 1
    assert result.hits[0].record.title == "Alpha"


def test_exact_title_no_match_returns_empty() -> None:
    """Title not present returns empty result."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(title="Zulu"))
    assert result.is_empty()
    assert result.total == 0


def test_exact_title_matches_first_only() -> None:
    """`title="alpha"` does NOT match `alpha-prime`."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(title="alpha"))
    assert result.total == 1
    assert result.hits[0].record.title == "Alpha"


# ---- prefix lookup -------------------------------------------------------


def test_prefix_returns_all_matching() -> None:
    """`prefix="Al"` matches Alpha, alpha-prime, Alpine."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(prefix="Al"))
    titles = [h.record.title for h in result.hits]
    assert set(titles) == {"Alpha", "alpha-prime", "Alpine"}
    assert result.total == 3


def test_prefix_is_case_insensitive() -> None:
    """Lowercase prefix matches uppercase titles."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(prefix="al"))
    titles = [h.record.title for h in result.hits]
    assert set(titles) == {"Alpha", "alpha-prime", "Alpine"}


def test_prefix_no_match_returns_empty() -> None:
    """No titles start with the prefix → empty."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(prefix="Zulu"))
    assert result.is_empty()


# ---- list mode + filters -------------------------------------------------


def test_list_returns_all_records() -> None:
    """No title/prefix → return every record."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest())
    assert result.total == 5


def test_filter_by_node_type() -> None:
    """node_type=task returns only tasks."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(node_type="task"))
    assert result.total == 2
    titles = {h.record.title for h in result.hits}
    assert titles == {"Bravo", "Charlie"}


def test_filter_by_parent_id() -> None:
    """parent_id=alpha returns its children."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(
        SearchRequest(parent_id="aaaaaaaa-0000-0000-0000-000000000001")
    )
    assert result.total == 2
    titles = {h.record.title for h in result.hits}
    assert titles == {"Bravo", "Charlie"}


def test_filter_by_story_id() -> None:
    """story_id filter scopes to a workspace region."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(
        SearchRequest(story_id="aaaaaaaa-0000-0000-0000-000000000001")
    )
    # Bravo + Charlie are children of Alpha (story 0001).
    assert result.total == 2
    titles = {h.record.title for h in result.hits}
    assert titles == {"Bravo", "Charlie"}


def test_combined_filters_are_anded() -> None:
    """All filters together must AND, not OR."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(
        SearchRequest(
            node_type="task",
            parent_id="aaaaaaaa-0000-0000-0000-000000000001",
            story_id="aaaaaaaa-0000-0000-0000-000000000001",
        )
    )
    assert result.total == 2


def test_filter_by_node_type_no_match_returns_empty() -> None:
    """node_type that's not present returns empty."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(node_type="epic"))
    assert result.is_empty()


# ---- sort ---------------------------------------------------------------


def test_default_sort_is_updated_at_desc() -> None:
    """Default sort → most recent first."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest())
    titles = [h.record.title for h in result.hits]
    # The records were seeded with increasing updated_at
    # (1, 2, 3, 4, 5 seconds). DESC → Alpine first.
    assert titles[0] == "Alpine"
    assert titles[-1] == "Alpha"


def test_sort_updated_at_asc() -> None:
    """UPDATED_AT_ASC → oldest first."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(sort=SearchSort.UPDATED_AT_ASC))
    titles = [h.record.title for h in result.hits]
    assert titles[0] == "Alpha"
    assert titles[-1] == "Alpine"


def test_sort_title_asc_is_case_insensitive() -> None:
    """TITLE_ASC sorts case-insensitively."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(sort=SearchSort.TITLE_ASC))
    titles = [h.record.title for h in result.hits]
    # Case-insensitive: Alpha, alpha-prime, Alpine, Bravo, Charlie
    assert titles == ["Alpha", "alpha-prime", "Alpine", "Bravo", "Charlie"]


def test_sort_title_desc_is_case_insensitive() -> None:
    """TITLE_DESC reverses the alphabetic order."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(sort=SearchSort.TITLE_DESC))
    titles = [h.record.title for h in result.hits]
    assert titles == ["Charlie", "Bravo", "Alpine", "alpha-prime", "Alpha"]


# ---- pagination ---------------------------------------------------------


def test_pagination_total_is_pre_pagination() -> None:
    """`total` is the count of matching records BEFORE pagination."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(page=0, page_size=2))
    assert result.total == 5  # all 5 match
    assert len(result.hits) == 2


def test_pagination_page_zero_returns_first_slice() -> None:
    """page=0 → first page."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(page=0, page_size=2))
    # DESC sort: Alpine, Charlie, Bravo, alpha-prime, Alpha
    # Page 0 of 2 → Alpine, Charlie
    titles = [h.record.title for h in result.hits]
    assert titles == ["Alpine", "Charlie"]


def test_pagination_page_one_returns_second_slice() -> None:
    """page=1 → second page."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(page=1, page_size=2))
    titles = [h.record.title for h in result.hits]
    assert titles == ["Bravo", "alpha-prime"]


def test_pagination_offset_clamps_to_total() -> None:
    """Out-of-range page returns empty hits, total unchanged."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(page=10, page_size=2))
    assert result.total == 5
    assert result.hits == ()
    assert result.page.offset == 5  # clamped


def test_page_size_one_returns_single_record() -> None:
    """page_size=1 returns exactly one record per page."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(page=0, page_size=1))
    assert len(result.hits) == 1
    assert result.total == 5


# ---- service-shape contract ---------------------------------------------


def test_service_satisfies_search_service_protocol() -> None:
    """`DefaultSearchService` implements the `SearchService` Protocol."""
    from backend.search import SearchService

    svc = DefaultSearchService(InMemoryIndexRepository())
    assert isinstance(svc, SearchService)


def test_search_result_is_immutable() -> None:
    """`SearchResult` is frozen — accidental mutation raises."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest())
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        result.total = 999  # type: ignore[misc]


def test_search_request_is_immutable() -> None:
    """`SearchRequest` is frozen."""
    from dataclasses import FrozenInstanceError

    req = SearchRequest(title="x")
    with pytest.raises(FrozenInstanceError):
        req.title = "y"  # type: ignore[misc]


def test_search_with_combined_title_and_filter() -> None:
    """Title + filter scope the result to the title match."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(
        SearchRequest(title="Bravo", node_type="task")
    )
    assert result.total == 1
    assert result.hits[0].record.title == "Bravo"


def test_search_does_not_consult_filesystem() -> None:
    """Search is index-only. The service doesn't read the filesystem.

    We assert this by passing an IndexRepository that has
    *no* filesystem backing (the in-memory variant) and
    verifying the search returns records that came ONLY
    from `index.upsert(...)` calls.
    """
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    # `Alpine` is a record we seeded. It's not backed by
    # any filesystem; the search returns it because the
    # index has it.
    result = svc.search(SearchRequest(title="Alpine"))
    assert result.total == 1
    assert result.hits[0].record.title == "Alpine"


def test_search_result_iter_protocol() -> None:
    """`for hit in result` works without .hits."""
    index = InMemoryIndexRepository()
    _seed_minimal(index)
    svc = DefaultSearchService(index)
    result = svc.search(SearchRequest(page_size=3))
    titles = [h.record.title for h in result]
    assert len(titles) == 3
