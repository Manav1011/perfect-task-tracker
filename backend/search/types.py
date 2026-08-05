"""Search value types — wire-shape between the API and the search service.

These are dataclasses, not Pydantic models, because the
search layer is internal in Phase 3.1 — the API layer will
wrap these in Pydantic DTOs when the HTTP endpoints land
(Phase 3.2). Frozen + slots so callers can't accidentally
mutate a query that the service is processing.

Terminology:

    - `SearchRequest` — the *query* (filters, ordering, page).
    - `SearchResult`  — the *response* (page, total, hits).
    - `SearchHit`     — one row of the response.
    - `SearchSort`    — enum-like switch for ordering.
    - `SearchPage`    — offset + limit, derived from
      `SearchRequest.page` / `page_size`.

Why one `SearchRequest` and not three ("exact", "prefix",
"list"):

    - One object keeps the API surface uniform. The
      fields are all optional; the rule is "if title is
      set, search by title; if prefix is set, search by
      prefix; otherwise list with filters."
    - The service validates the request and picks the
      strategy internally. Callers don't need to know
      which strategy is in use.

Per ADR-0018, search is *eventually consistent* by design:
the index lags the filesystem by the synchroniser's
flush cadence. The types here carry no consistency
metadata — the staleness is observable via the index's
own `updated_at` timestamps.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Iterable

from backend.domain.node import NodeId
from backend.index.types import IndexRecord


class SearchSort(str, enum.Enum):
    """How `SearchResult.hits` is ordered.

    Default: `UPDATED_AT_DESC` (most-recently-touched first).
    The string value is the canonical wire form so a
    future Pydantic DTO can serialise it directly.
    """

    UPDATED_AT_DESC = "updated_at_desc"
    UPDATED_AT_ASC = "updated_at_asc"
    TITLE_ASC = "title_asc"
    TITLE_DESC = "title_desc"


@dataclasses.dataclass(slots=True, frozen=True)
class SearchRequest:
    """One search query.

    All fields are optional. The validation rules:

        - `title`     — exact-match (case-insensitive). Empty
          after `.strip()` is invalid for the "search by title"
          path; not invalid for the "list" path.
        - `prefix`    — case-insensitive prefix on `title`.
          Empty after `.strip()` is invalid.
        - `node_type` — filter by `IndexRecord.node_type`.
        - `parent_id` — filter by `IndexRecord.parent_id`.
        - `story_id`  — filter by `IndexRecord.story_id`.
        - `sort`      — field + direction. Defaults to
          `UPDATED_AT_DESC`.
        - `page`      — 0-indexed page number. Must be >= 0.
        - `page_size` — must be 1..MAX_PAGE_SIZE.

    The "search by title" path is mutually exclusive with
    "search by prefix" — passing both raises
    `InvalidSearchQueryError`.
    """

    title: str | None = None
    prefix: str | None = None
    node_type: str | None = None
    parent_id: NodeId | None = None
    story_id: NodeId | None = None
    sort: SearchSort = SearchSort.UPDATED_AT_DESC
    page: int = 0
    page_size: int = 50


@dataclasses.dataclass(slots=True, frozen=True)
class SearchHit:
    """One row of the search result.

    Carries the `IndexRecord` *and* a derived score. For
    Phase 3.1, the score is a constant (all hits are equal);
    when full-text search lands in Phase 4, the score will
    be the rank.
    """

    record: IndexRecord
    score: float = 1.0


@dataclasses.dataclass(slots=True, frozen=True)
class SearchPage:
    """Slice of a search result.

    `offset` is the absolute starting index in the
    sorted-and-filtered result set; `limit` is the page
    size. Carries no `hits` — those are in `SearchResult`.

    The page is derived from `SearchRequest.page` and
    `page_size`; the service builds this internally. It
    is exposed as part of `SearchResult` so callers can
    navigate (e.g., "next page" = `page + 1`).
    """

    offset: int
    limit: int


@dataclasses.dataclass(slots=True, frozen=True)
class SearchResult:
    """The response to a `SearchRequest`.

    `total` is the count of matching records *before*
    pagination — this is what the API uses to render
    "page 3 of 12". `hits` is the page slice (size <=
    `page_size`). `page` echoes the offset/limit the
    service used so callers can confirm pagination.
    """

    hits: tuple[SearchHit, ...]
    total: int
    page: SearchPage

    def is_empty(self) -> bool:
        return len(self.hits) == 0

    def __iter__(self) -> Iterable[SearchHit]:  # type: ignore[override]
        # Convenience: `for hit in result:` works without
        # .hits. Returning the iterable is enough; the
        # dataclass generator is fine.
        return iter(self.hits)
