"""Search-layer exception hierarchy.

Search errors are a *service-layer* concept: the search
service validates queries and applies filter rules.
The underlying `IndexRepository` raises its own errors
(`IndexRecordNotFoundError`, ...); the search service
catches and re-raises them as `SearchError` subclasses
only when the failure is a search-domain concern
(invalid query, out-of-range page, etc.).

This module deliberately mirrors the structure of
`backend.services.exceptions` so the API layer can
distinguish "search input was bad" from "search
infrastructure failed".
"""

from __future__ import annotations


class SearchError(Exception):
    """Base class for all search-layer errors.

    The API layer (when it lands) will catch this and
    translate to HTTP 4xx/5xx as appropriate. For Phase
    3.1, callers use the concrete subclasses for
    programmatic dispatch.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidSearchQueryError(SearchError):
    """The search request failed validation.

    Raised when:
        - title query is empty after `.strip()` for exact
          lookup,
        - prefix is empty after `.strip()`,
        - page number is negative,
        - page size is <= 0 or above the configured max,
        - sort field is unknown.

    The error message names the offending field so the
    API layer can include it in the HTTP response.
    """

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field
