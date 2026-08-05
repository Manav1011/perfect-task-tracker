"""Search API DTOs — wire-shape for GET /api/v1/search.

The DTOs here are the API's contract with the outside
world. They differ from the internal `SearchRequest` /
`SearchResult` dataclasses in three ways:

    1. Query parameters arrive as URL params (strings /
       ints), not Python kwargs. The DTOs accept the
       string-form via FastAPI's `Query(...)` plumbing.
    2. Wire format uses Pydantic models (`BaseModel`)
       so OpenAPI documentation is generated
       automatically.
    3. The response shape is the API's, not the
       service's. Future endpoint changes (adding
       `facets`, `total_estimated`, etc.) land here
       without touching the service.

The schema deliberately does NOT import from
`backend.search` — the API layer translates via
`backend.api.mappers`. This keeps the dependency
direction clean: API → Service, never the reverse.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SearchHitDTO(BaseModel):
    """One row of the search response.

    Mirrors the index record's wire-relevant fields. The
    full `IndexRecord` (with internal metadata like
    `filesystem_path` and `search_text`) is NOT
    exposed — the API only surfaces fields a search
    consumer might want to render.

    Field semantics:

        - `node_id` — the stable UUID.
        - `title` — current display title.
        - `node_type` — string discriminator.
        - `parent_id` — `None` for root Stories.
        - `story_id` — root Story's UUID, or `None`.
        - `created_at` / `updated_at` — ISO 8601
          timestamps. Operators read these to gauge
          index freshness.
        - `score` — Phase 3.1 is constant 1.0. Phase 4
          will populate the rank from a full-text
          scoring function.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str
    title: str
    node_type: str
    parent_id: str | None = None
    story_id: str | None = None
    created_at: str
    updated_at: str
    score: float = 1.0


class SearchPageDTO(BaseModel):
    """Pagination metadata echoed in the response.

    `offset` is the absolute starting index; `limit` is
    the page size requested. The frontend uses these
    to render "Showing 51-100 of 247" controls.
    """

    model_config = ConfigDict(extra="forbid")

    offset: int = Field(..., ge=0, description="Absolute starting index in the result set")
    limit: int = Field(..., gt=0, description="Page size requested")


class SearchResultsResponse(BaseModel):
    """The response body for GET /api/v1/search.

    Three fields:

        - `hits` — the page slice (size <= page_size).
        - `total` — count of matching records BEFORE
          pagination. The frontend uses this to render
          "page 3 of 12".
        - `page` — pagination metadata.

    The response is always `200 OK` with a possibly
    empty `hits` array. A query for an absent node is
    a valid result, not a 404 — per ADR-0019 (search is
    a query boundary; it's never wrong to look for
    something that isn't there).
    """

    model_config = ConfigDict(extra="forbid")

    hits: list[SearchHitDTO] = Field(
        default_factory=list,
        description="Search results for this page (size <= page_size)",
    )
    total: int = Field(
        ...,
        ge=0,
        description="Total matching records, before pagination",
    )
    page: SearchPageDTO = Field(
        ..., description="Pagination metadata for navigation"
    )


__all__ = ["SearchHitDTO", "SearchPageDTO", "SearchResultsResponse"]
