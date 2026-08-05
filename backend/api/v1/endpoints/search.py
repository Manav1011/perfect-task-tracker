"""GET /api/v1/search — query the index.

Per ADR-0019, this endpoint is a thin translation layer
over the `SearchService` Protocol. It exposes:

    - exact title lookup (case-insensitive),
    - prefix lookup (case-insensitive),
    - filter by node_type, parent_id, story_id,
    - sort by updated_at or title (asc/desc),
    - pagination via page / page_size.

The endpoint accepts the request as query parameters
because search is a GET (idempotent, cacheable, and
side-effect-free). The mapping from query params to
`SearchRequest` lives in `backend.api.mappers`.

Wire format:

    GET /api/v1/search?title=Alpha&page=0&page_size=20
    GET /api/v1/search?prefix=Alpha&node_type=story
    GET /api/v1/search?parent_id=...&story_id=...&sort=title_asc

Returns `200 OK` with a `SearchResultsResponse`. A
query that matches nothing is a valid result (empty
`hits`, `total=0`) — never a 404.

Failure modes:

    - `InvalidSearchQueryError` (from the search
      service) → 422 with the search error envelope.
    - Anything else → 500 (FastAPI default).

This endpoint depends ONLY on the `SearchService`
Protocol. It does NOT import:

    - `backend.repositories` (no filesystem writes),
    - `backend.workspace` (the cache is irrelevant),
    - `backend.index.impl` (no concrete index),
    - `backend.search.service` (no concrete impl).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from backend.api.dependencies import get_search_service
from backend.api.mappers import (
    search_request_from_query,
    search_result_to_response,
)
from backend.schemas.search import SearchResultsResponse
from backend.search import SearchService

router = APIRouter(tags=["search"])


@router.get(
    "/search",
    response_model=SearchResultsResponse,
    summary="Search the workspace index",
    description=(
        "Search the workspace by title (exact match), prefix, "
        "or filter by node type, parent, or story. "
        "Results are returned in pages with pagination metadata. "
        "Search is built on the index — it is eventually consistent "
        "with the filesystem. See ADR-0018 / ADR-0019."
    ),
    responses={
        200: {
            "description": "Search results (may be empty)",
            "model": SearchResultsResponse,
        },
        422: {
            "description": "Invalid query parameters (e.g., empty title, page out of range)",
        },
    },
)
def search(
    title: str | None = Query(
        None,
        description="Exact title match (case-insensitive). Mutually exclusive with `prefix`.",
        min_length=1,
        max_length=200,
    ),
    prefix: str | None = Query(
        None,
        description="Title prefix match (case-insensitive). Mutually exclusive with `title`.",
        min_length=1,
        max_length=200,
    ),
    node_type: str | None = Query(
        None,
        description="Filter by node type (story | task | note)",
        pattern="^(story|task|note)$",
    ),
    parent_id: str | None = Query(
        None,
        description="Filter by parent node UUID",
        max_length=64,
    ),
    story_id: str | None = Query(
        None,
        description="Filter by workspace story (root Story) UUID",
        max_length=64,
    ),
    sort: str | None = Query(
        None,
        description=(
            "Sort order: updated_at_desc (default) | updated_at_asc | "
            "title_asc | title_desc"
        ),
        pattern="^(updated_at_desc|updated_at_asc|title_asc|title_desc)$",
    ),
    page: int = Query(
        0,
        ge=0,
        description="0-indexed page number",
    ),
    page_size: int = Query(
        50,
        ge=1,
        le=200,
        description="Number of results per page (1..200)",
    ),
    service: SearchService = Depends(get_search_service),
) -> SearchResultsResponse:
    """Execute the search and return the results.

    Translation steps:

        1. Translate the query parameters into a
           `SearchRequest` (via `search_request_from_query`).
        2. Call `service.search(request)`.
        3. Translate the `SearchResult` into a
           `SearchResultsResponse` (via `search_result_to_response`).

    Validation is delegated to the search service. The
    endpoint does not duplicate validation rules; this
    keeps the API and the service in sync.
    """
    request = search_request_from_query(
        title=title,
        prefix=prefix,
        node_type=node_type,
        parent_id=parent_id,
        story_id=story_id,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    result = service.search(request)
    return search_result_to_response(result)
