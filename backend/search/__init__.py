"""Search layer — query the index.

Per ADR-0018, the search subsystem is built on top of the
PostgreSQL index, NOT on top of the workspace tree. This is
a deliberate architectural choice: search is a *query* over
a derived projection, which gives us O(log n) lookups and
eventual consistency for free.

The search layer is the boundary between the application
service layer and the index layer. It depends only on:

    - `backend.domain`           (entities, value objects)
    - `backend.index.protocol`   (the IndexRepository contract)

It does NOT depend on:

    - `backend.filesystem`       (search is index-only)
    - `backend.repositories`     (search never writes)
    - `backend.workspace`        (the cache is irrelevant to search)
    - `backend.api`              (no HTTP boundary yet)
    - SQLAlchemy or any I/O library directly.

The API layer will reach search through `SearchService`
once the HTTP endpoints land (Phase 3.2 or later). The
internal seam is exposed as a Protocol so a future
search-specific index (e.g., a dedicated search database)
can drop in without touching the API.
"""

from backend.search.exceptions import (
    InvalidSearchQueryError,
    SearchError,
)
from backend.search.protocol import SearchService
from backend.search.service import DefaultSearchService
from backend.search.types import (
    SearchHit,
    SearchPage,
    SearchRequest,
    SearchResult,
    SearchSort,
)

__all__ = [
    "DefaultSearchService",
    "InvalidSearchQueryError",
    "SearchError",
    "SearchHit",
    "SearchPage",
    "SearchRequest",
    "SearchResult",
    "SearchService",
    "SearchSort",
]
