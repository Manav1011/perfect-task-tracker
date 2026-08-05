"""Domain ↔ API DTO mappers.

These functions are the *only* place where the API layer meets the
Domain layer. They live in `backend.api` (not `backend.schemas`)
because they're an API concern: the shape and wire format are
API-side decisions, not domain decisions.

Why explicit mappers instead of returning domain objects directly:

    - The Domain `Node` is a `dataclass(slots=True)` — pydantic
      can't serialize it without going through this layer.
    - API versioning: if we ever need to add/remove fields, the
      mapper is the one place that changes.
    - DTOs use plain strings for `type`, not the domain Enum, so
      the wire format doesn't leak Python enum semantics.
    - Domain exceptions never reach the API — the mapper is
      pure-value translation.
"""

from __future__ import annotations

from backend.domain import Node
from backend.domain.tree import Tree
from backend.domain.node import NodeId
from backend.index.types import IndexRecord
from backend.schemas.node import NodeResponse
from backend.schemas.search import SearchHitDTO, SearchPageDTO, SearchResultsResponse
from backend.schemas.story import StoryListResponse, StoryResponse
from backend.schemas.workspace import WorkspaceTreeResponse
from backend.search import SearchRequest, SearchResult, SearchSort


def node_to_dto(node: Node) -> NodeResponse:
    """Convert a domain Node into the API NodeResponse."""
    return NodeResponse(
        id=str(node.id),
        title=node.title,
        type=node.type.value,
        parent_id=str(node.parent_id) if node.parent_id is not None else None,
        children_ids=[str(cid) for cid in node.children_ids],
        metadata=node.metadata.as_dict(),
        canvas=node.canvas,
    )


def tree_to_workspace_response(tree: Tree) -> WorkspaceTreeResponse:
    """Convert a domain Tree into a workspace tree response.

    Returns:
        WorkspaceTreeResponse with `roots` (root stories in tree
        order) and `nodes` (every node flat, for client-side tree
        rendering).
    """
    roots = [node_to_dto(n) for n in tree.roots()]
    nodes = [node_to_dto(n) for n in tree.all_nodes()]
    return WorkspaceTreeResponse(roots=roots, nodes=nodes)


def tree_to_story_list_response(tree: Tree) -> StoryListResponse:
    """Convert a domain Tree into a story list response."""
    stories = [
        StoryResponse(id=str(n.id), title=n.title)
        for n in tree.roots()
    ]
    nodes = [node_to_dto(n) for n in tree.all_nodes()]
    return StoryListResponse(stories=stories, nodes=nodes)


# ---- Search --------------------------------------------------------------


# Wire-side sort values. These are the strings the API accepts
# on the query string and emits in OpenAPI documentation.
# The keys must match `SearchSort` values exactly.
_SORT_FROM_WIRE: dict[str, SearchSort] = {
    "updated_at_desc": SearchSort.UPDATED_AT_DESC,
    "updated_at_asc": SearchSort.UPDATED_AT_ASC,
    "title_asc": SearchSort.TITLE_ASC,
    "title_desc": SearchSort.TITLE_DESC,
}


def search_request_from_query(
    *,
    title: str | None = None,
    prefix: str | None = None,
    node_type: str | None = None,
    parent_id: str | None = None,
    story_id: str | None = None,
    sort: str | None = None,
    page: int = 0,
    page_size: int = 50,
) -> SearchRequest:
    """Build a `SearchRequest` from query parameters.

    Pure translator — no validation logic. Validation lives
    in `SearchService._validate`. The translation:

        - `parent_id` / `story_id` strings → `NodeId` (only
          when non-empty; empty string becomes None).
        - `sort` string → `SearchSort` enum (unknown values
          raise `InvalidSearchQueryError` from the service).
        - `page` / `page_size` are passed through; the
          service validates the bounds.
    """
    sort_enum = _SORT_FROM_WIRE.get(sort) if sort is not None else None
    return SearchRequest(
        title=title,
        prefix=prefix,
        node_type=node_type,
        parent_id=NodeId(parent_id) if parent_id else None,
        story_id=NodeId(story_id) if story_id else None,
        sort=sort_enum or SearchSort.UPDATED_AT_DESC,
        page=page,
        page_size=page_size,
    )


def index_record_to_hit_dto(record: IndexRecord, score: float = 1.0) -> SearchHitDTO:
    """Convert an `IndexRecord` into a `SearchHitDTO`."""
    return SearchHitDTO(
        node_id=str(record.node_id),
        title=record.title,
        node_type=record.node_type,
        parent_id=str(record.parent_id) if record.parent_id is not None else None,
        story_id=str(record.story_id) if record.story_id is not None else None,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
        score=score,
    )


def search_result_to_response(result: SearchResult) -> SearchResultsResponse:
    """Convert a `SearchResult` into a `SearchResultsResponse`.

    Used by the endpoint to render the HTTP body. The
    response shape is the API's; the search layer's
    shape (`SearchResult`) does not leak to the wire.
    """
    hits = [
        index_record_to_hit_dto(hit.record, score=hit.score)
        for hit in result.hits
    ]
    page = SearchPageDTO(offset=result.page.offset, limit=result.page.limit)
    return SearchResultsResponse(hits=hits, total=result.total, page=page)
