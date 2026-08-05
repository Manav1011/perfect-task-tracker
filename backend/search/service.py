"""DefaultSearchService — the concrete search implementation.

Built on top of `IndexRepository`. The service is
*read-only* and *eventually consistent*: it queries the
index, not the filesystem. A write that lands on disk
but hasn't reached the index yet will not appear in
search results (per ADR-0018).

Threading: the service is a passive data structure
holding one `IndexRepository` reference. All filtering
happens in-process over the records the index returns.
The repository's own thread-safety is the repository's
concern; the service assumes the repository is safe to
call from the request thread.

Strategy selection:

    - `request.title` set → exact title lookup
      (case-insensitive). Reads
      `IndexRepository.list_by_story(None)` indirectly
      via a full scan + filter (Phase 3.1 only has
      `list_by_story`; Phase 3.2 will add a
      `find_by_title` repo method).
    - `request.prefix` set → prefix lookup
      (case-insensitive). Full scan + filter.
    - Neither set → list with filters (full scan +
      filter). Small workspaces fit in memory; this
      is fine for Phase 3.1.

Why full-scan in Phase 3.1:

    - The `IndexRepository` Protocol (Phase 2.0) only
      exposes `list_by_story`, `all_node_ids`, `get`, and
      `exists`. There is no `find_by_title` or
      `find_by_prefix` yet. Adding those would expand
      the Protocol for one use case.
    - The SQL implementation can serve a full scan
      cheaply on a small index. Phase 3.2 will add the
      per-query repo methods and the scan path will
      route through them.
    - Tests run against `InMemoryIndexRepository`, which
      is O(n) anyway. The benchmark numbers include the
      scan cost so the ceiling is visible.

Sort + pagination:

    - Sort is applied *after* filtering, *before*
      pagination. The total count is the filtered count
      (i.e., the page size of 50 means 50 of the total
      matching records).
    - Pagination is `offset = page * page_size` clamped
      to the total count.
"""

from __future__ import annotations

from backend.index.protocol import IndexRepository
from backend.index.types import IndexRecord
from backend.search.exceptions import InvalidSearchQueryError
from backend.search.types import (
    SearchHit,
    SearchPage,
    SearchRequest,
    SearchResult,
    SearchSort,
)

# Validation constants. Hard-coded so the service
# never silently pages through gigabytes of results.
MAX_PAGE_SIZE = 200


class DefaultSearchService:
    """The concrete `SearchService`.

    Constructor-injected with an `IndexRepository`
    (the Protocol). Tests pass an `InMemoryIndexRepository`;
    production wires up the `SQLAlchemyIndexRepository`.

    The service is a small wrapper over the index:

        - it validates the request,
        - it picks the strategy (exact / prefix / list),
        - it filters, sorts, paginates, and returns a
          `SearchResult`.

    No I/O. No state. No caching. The cache (Phase 3.0)
    is irrelevant to search because search reads the
    index, not the tree.
    """

    def __init__(self, index: IndexRepository) -> None:
        self._index = index

    def search(self, request: SearchRequest) -> SearchResult:
        """Execute the query.

        Validates the request, picks the strategy, applies
        filters, sorts, paginates, returns a `SearchResult`.

        Raises:
            InvalidSearchQueryError: request validation
                failed (empty title/prefix, bad page, etc.).
        """
        self._validate(request)

        # Strategy: title vs prefix vs list. The two
        # text-match modes are mutually exclusive.
        if request.title is not None:
            records = self._scan_match_title(request.title)
        elif request.prefix is not None:
            records = self._scan_match_prefix(request.prefix)
        else:
            records = self._scan_all()

        # Apply the structural filters.
        records = self._apply_filters(records, request)

        # Sort. The sort is stable: sorted() in Python
        # is stable, and the secondary key on `node_id`
        # gives a deterministic tie-break.
        records = self._apply_sort(records, request.sort)

        # Total is the count AFTER filters, BEFORE pagination.
        total = len(records)

        # Pagination. Offset is clamped to the total.
        offset = request.page * request.page_size
        if offset > total:
            offset = total
        page_records = records[offset : offset + request.page_size]

        # Build the response.
        hits = tuple(SearchHit(record=rec) for rec in page_records)
        page = SearchPage(offset=offset, limit=request.page_size)
        return SearchResult(hits=hits, total=total, page=page)

    # ---- validation -------------------------------------------------

    def _validate(self, request: SearchRequest) -> None:
        """Validate the request fields.

        Raises `InvalidSearchQueryError` with `field` set
        so the API layer can echo the offending field
        back to the caller.
        """
        if request.page < 0:
            raise InvalidSearchQueryError(
                f"page must be >= 0 (got {request.page})",
                field="page",
            )
        if request.page_size <= 0:
            raise InvalidSearchQueryError(
                f"page_size must be > 0 (got {request.page_size})",
                field="page_size",
            )
        if request.page_size > MAX_PAGE_SIZE:
            raise InvalidSearchQueryError(
                f"page_size must be <= {MAX_PAGE_SIZE} (got {request.page_size})",
                field="page_size",
            )
        if request.title is not None and not request.title.strip():
            raise InvalidSearchQueryError(
                "title must be non-empty when provided",
                field="title",
            )
        if request.prefix is not None and not request.prefix.strip():
            raise InvalidSearchQueryError(
                "prefix must be non-empty when provided",
                field="prefix",
            )
        if request.title is not None and request.prefix is not None:
            raise InvalidSearchQueryError(
                "title and prefix are mutually exclusive — pick one",
                field="title",
            )

    # ---- strategy: scan helpers ------------------------------------

    def _scan_all(self) -> list[IndexRecord]:
        """Return every record in the index.

        Full scan. Phase 3.2 will add a more efficient
        path for the no-filter list.
        """
        return [self._index.get(nid) for nid in self._index.all_node_ids()]

    def _scan_match_title(self, title: str) -> list[IndexRecord]:
        """Return records whose title exactly matches `title`.

        Case-insensitive. The index doesn't expose a
        per-title lookup method yet (Phase 3.2), so we
        scan and filter. The benchmark reports the cost.
        """
        needle = title.strip().casefold()
        return [
            rec
            for rec in self._scan_all()
            if rec.title.casefold() == needle
        ]

    def _scan_match_prefix(self, prefix: str) -> list[IndexRecord]:
        """Return records whose title starts with `prefix`.

        Case-insensitive. Scan + filter.
        """
        needle = prefix.strip().casefold()
        return [
            rec
            for rec in self._scan_all()
            if rec.title.casefold().startswith(needle)
        ]

    # ---- filters ----------------------------------------------------

    def _apply_filters(
        self,
        records: list[IndexRecord],
        request: SearchRequest,
    ) -> list[IndexRecord]:
        """Apply the structural filters (node_type, parent, story).

        Each filter is independent. The list is rebuilt
        once per filter (no chained comprehensions) so
        the order of evaluation is visible in stack traces.
        """
        out = records
        if request.node_type is not None:
            out = [r for r in out if r.node_type == request.node_type]
        if request.parent_id is not None:
            out = [r for r in out if r.parent_id == request.parent_id]
        if request.story_id is not None:
            out = [r for r in out if r.story_id == request.story_id]
        return out

    # ---- sort -------------------------------------------------------

    def _apply_sort(
        self,
        records: list[IndexRecord],
        sort: SearchSort,
    ) -> list[IndexRecord]:
        """Stable sort per `sort`.

        The secondary key on `node_id` (a string) guarantees
        a deterministic order across runs. Without it,
        two updates with the same `updated_at` would
        swap on each call.
        """
        if sort == SearchSort.UPDATED_AT_DESC:
            key = lambda r: (r.updated_at, r.node_id)  # noqa: E731
            records = sorted(records, key=key, reverse=True)
        elif sort == SearchSort.UPDATED_AT_ASC:
            key = lambda r: (r.updated_at, r.node_id)  # noqa: E731
            records = sorted(records, key=key)
        elif sort == SearchSort.TITLE_ASC:
            key = lambda r: (r.title.casefold(), r.node_id)  # noqa: E731
            records = sorted(records, key=key)
        elif sort == SearchSort.TITLE_DESC:
            key = lambda r: (r.title.casefold(), r.node_id)  # noqa: E731
            records = sorted(records, key=key, reverse=True)
        else:  # pragma: no cover - defensive
            raise InvalidSearchQueryError(
                f"unknown sort: {sort}",
                field="sort",
            )
        return records


__all__ = ["DefaultSearchService", "MAX_PAGE_SIZE"]
