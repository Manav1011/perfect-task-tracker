"""SearchService Protocol — the contract for query operations.

The Protocol is intentionally narrow: exactly one method
(`search`). Future extensions (autocomplete, faceted
counts) will land as new methods, not by extending
`SearchRequest`.

Why a Protocol and not a concrete class:

    - The service layer (`backend.services`) and the
      API layer (when it lands) depend on the Protocol.
    - The implementation (`DefaultSearchService`) depends
      on the `IndexRepository` Protocol.
    - Tests pass lightweight fakes (a Mock, a hand-built
      `IndexRepository`) without inheriting from a heavy
      base class.

Per ADR-0018, the search subsystem depends ONLY on the
index. The Protocol makes this dependency explicit: the
type signature of `search` takes an `IndexRepository`,
not a `WorkspaceRepository`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.search.types import SearchRequest, SearchResult


@runtime_checkable
class SearchService(Protocol):  # pragma: no cover - protocol
    """The search use-case surface.

    Exactly one method: `search(request) -> SearchResult`.
    The method is read-only; the service never mutates
    the index. The index is updated by the repository
    (via the synchroniser), not by search.
    """

    def search(self, request: SearchRequest) -> SearchResult: ...
