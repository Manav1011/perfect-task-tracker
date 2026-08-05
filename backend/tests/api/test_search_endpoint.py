"""End-to-end integration tests for GET /api/v1/search.

These tests use FastAPI's `TestClient` with a seeded
in-memory index. The `SearchService` is wired through
the dependency override so we don't need a real Postgres
or filesystem.

Covered:

    - HTTP semantics: 200 OK, JSON body shape.
    - Query parameter parsing and validation.
    - Exact title lookup, prefix lookup, list mode.
    - All filters (node_type, parent_id, story_id).
    - All sort orders.
    - Pagination metadata.
    - Error mapping: 422 for invalid query.
    - Empty result is `200 OK` with empty `hits`, not 404.
    - OpenAPI surface exposes the endpoint.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.api.dependencies import (
    get_index_repository,
    get_search_service,
)
from backend.domain.node import NodeId
from backend.index.impl import InMemoryIndexRepository
from backend.index.types import IndexRecord
from backend.main import create_app
from backend.search import DefaultSearchService


def _ts(offset_seconds: float = 0.0) -> datetime:
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


def _seed_index(index: InMemoryIndexRepository) -> dict[str, str]:
    """Seed the index. Returns an `ids` map for assertions."""
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
    return {
        "alpha": "aaaaaaaa-0000-0000-0000-000000000001",
        "alpha_prime": "aaaaaaaa-0000-0000-0000-000000000002",
        "bravo": "aaaaaaaa-0000-0000-0000-000000000003",
        "charlie": "aaaaaaaa-0000-0000-0000-000000000004",
        "alpine": "aaaaaaaa-0000-0000-0000-000000000005",
    }


@pytest.fixture
def seeded_index() -> InMemoryIndexRepository:
    index = InMemoryIndexRepository()
    _seed_index(index)
    return index


@pytest.fixture
def client(seeded_index: InMemoryIndexRepository) -> Iterator[TestClient]:
    """A TestClient whose get_index_repository is wired to the seeded index.

    Per ADR-0019, the API layer depends only on the
    IndexRepository Protocol. The dependency override
    injects an in-memory index so the test runs without
    real Postgres.
    """
    app = create_app()
    app.dependency_overrides[get_index_repository] = lambda: seeded_index
    app.dependency_overrides[get_search_service] = lambda: DefaultSearchService(
        seeded_index
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---- HTTP semantics ------------------------------------------------------


def test_get_search_returns_200_with_empty_results(
    client: TestClient,
) -> None:
    """GET /api/v1/search returns 200 with empty hits when no records match."""
    response = client.get("/api/v1/search", params={"title": "Zulu"})
    assert response.status_code == 200
    body = response.json()
    assert body["hits"] == []
    assert body["total"] == 0
    assert body["page"] == {"offset": 0, "limit": 50}


def test_get_search_returns_200_for_empty_index() -> None:
    """Empty index → 200 with empty hits, NOT 404."""
    empty_index = InMemoryIndexRepository()
    app = create_app()
    app.dependency_overrides[get_index_repository] = lambda: empty_index
    with TestClient(app) as c:
        response = c.get("/api/v1/search")
        assert response.status_code == 200
        body = response.json()
        assert body["hits"] == []
        assert body["total"] == 0


# ---- exact title lookup --------------------------------------------------


def test_get_search_exact_title(client: TestClient) -> None:
    """Exact title lookup returns the matching record."""
    response = client.get("/api/v1/search", params={"title": "Alpha"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["hits"][0]["title"] == "Alpha"
    assert body["hits"][0]["node_id"] == "aaaaaaaa-0000-0000-0000-000000000001"


def test_get_search_exact_title_case_insensitive(client: TestClient) -> None:
    """Query parameter `title=alpha` matches uppercase title."""
    response = client.get("/api/v1/search", params={"title": "alpha"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["hits"][0]["title"] == "Alpha"


def test_get_search_exact_title_no_match_returns_empty(
    client: TestClient,
) -> None:
    """Title not present returns 200 with empty hits."""
    response = client.get("/api/v1/search", params={"title": "Zulu"})
    assert response.status_code == 200
    body = response.json()
    assert body["hits"] == []
    assert body["total"] == 0


# ---- prefix lookup -------------------------------------------------------


def test_get_search_prefix(client: TestClient) -> None:
    """Prefix lookup returns all matching titles."""
    response = client.get("/api/v1/search", params={"prefix": "Al"})
    assert response.status_code == 200
    body = response.json()
    titles = {hit["title"] for hit in body["hits"]}
    assert titles == {"Alpha", "alpha-prime", "Alpine"}
    assert body["total"] == 3


# ---- filters -------------------------------------------------------------


def test_get_search_filter_by_node_type(client: TestClient) -> None:
    """`node_type=task` returns only tasks."""
    response = client.get("/api/v1/search", params={"node_type": "task"})
    assert response.status_code == 200
    body = response.json()
    titles = {hit["title"] for hit in body["hits"]}
    assert titles == {"Bravo", "Charlie"}
    assert body["total"] == 2


def test_get_search_filter_by_parent_id(client: TestClient) -> None:
    """`parent_id` filters by parent."""
    response = client.get(
        "/api/v1/search",
        params={"parent_id": "aaaaaaaa-0000-0000-0000-000000000001"},
    )
    assert response.status_code == 200
    body = response.json()
    titles = {hit["title"] for hit in body["hits"]}
    assert titles == {"Bravo", "Charlie"}


def test_get_search_filter_by_story_id(client: TestClient) -> None:
    """`story_id` filters by story."""
    response = client.get(
        "/api/v1/search",
        params={"story_id": "aaaaaaaa-0000-0000-0000-000000000001"},
    )
    assert response.status_code == 200
    body = response.json()
    titles = {hit["title"] for hit in body["hits"]}
    assert titles == {"Bravo", "Charlie"}


def test_get_search_combined_filters(client: TestClient) -> None:
    """All filters AND together."""
    response = client.get(
        "/api/v1/search",
        params={
            "node_type": "task",
            "parent_id": "aaaaaaaa-0000-0000-0000-000000000001",
            "story_id": "aaaaaaaa-0000-0000-0000-000000000001",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2


# ---- sort ----------------------------------------------------------------


def test_get_search_default_sort_is_updated_at_desc(
    client: TestClient,
) -> None:
    """Default sort is updated_at_desc — newest first."""
    response = client.get("/api/v1/search")
    body = response.json()
    titles = [hit["title"] for hit in body["hits"]]
    # Seeded with increasing updated_at (1..5 seconds).
    # DESC → Alpine (5) first.
    assert titles[0] == "Alpine"
    assert titles[-1] == "Alpha"


def test_get_search_sort_title_asc(client: TestClient) -> None:
    """`sort=title_asc` sorts case-insensitively ascending."""
    response = client.get("/api/v1/search", params={"sort": "title_asc"})
    body = response.json()
    titles = [hit["title"] for hit in body["hits"]]
    assert titles == ["Alpha", "alpha-prime", "Alpine", "Bravo", "Charlie"]


def test_get_search_sort_title_desc(client: TestClient) -> None:
    """`sort=title_desc` reverses the alphabetic order."""
    response = client.get("/api/v1/search", params={"sort": "title_desc"})
    body = response.json()
    titles = [hit["title"] for hit in body["hits"]]
    assert titles == ["Charlie", "Bravo", "Alpine", "alpha-prime", "Alpha"]


def test_get_search_sort_updated_at_asc(client: TestClient) -> None:
    """`sort=updated_at_asc` returns oldest first."""
    response = client.get("/api/v1/search", params={"sort": "updated_at_asc"})
    body = response.json()
    titles = [hit["title"] for hit in body["hits"]]
    assert titles[0] == "Alpha"
    assert titles[-1] == "Alpine"


# ---- pagination ----------------------------------------------------------


def test_get_search_pagination_total_is_pre_pagination(
    client: TestClient,
) -> None:
    """`total` is the count of matching records BEFORE pagination."""
    response = client.get("/api/v1/search", params={"page_size": 2})
    body = response.json()
    assert body["total"] == 5
    assert len(body["hits"]) == 2
    assert body["page"] == {"offset": 0, "limit": 2}


def test_get_search_pagination_page_one(client: TestClient) -> None:
    """`page=1` returns the second page."""
    response = client.get(
        "/api/v1/search", params={"page": 1, "page_size": 2}
    )
    body = response.json()
    assert body["total"] == 5
    assert body["page"] == {"offset": 2, "limit": 2}


def test_get_search_pagination_offset_clamps_to_total(
    client: TestClient,
) -> None:
    """Out-of-range page returns empty hits, total unchanged."""
    response = client.get(
        "/api/v1/search", params={"page": 10, "page_size": 2}
    )
    body = response.json()
    assert body["total"] == 5
    assert body["hits"] == []
    assert body["page"]["offset"] == 5  # clamped


# ---- validation: 422 errors ----------------------------------------------


def test_get_search_empty_title_returns_422(client: TestClient) -> None:
    """Empty title query is invalid → 422."""
    response = client.get("/api/v1/search", params={"title": ""})
    # FastAPI's Query(min_length=1) rejects it before the
    # service sees it → 422.
    assert response.status_code == 422


def test_get_search_title_and_prefix_mutually_exclusive(
    client: TestClient,
) -> None:
    """Both title and prefix → 422 from the search service."""
    response = client.get(
        "/api/v1/search",
        params={"title": "Alpha", "prefix": "Al"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_search_query"
    assert body["field"] == "title"


def test_get_search_negative_page_returns_422(client: TestClient) -> None:
    """Negative page → 422 from FastAPI's Query(ge=0)."""
    response = client.get("/api/v1/search", params={"page": -1})
    assert response.status_code == 422


def test_get_search_page_size_too_large_returns_422(
    client: TestClient,
) -> None:
    """page_size > 200 → 422 from FastAPI's Query(le=200)."""
    response = client.get("/api/v1/search", params={"page_size": 201})
    assert response.status_code == 422


def test_get_search_invalid_node_type_returns_422(
    client: TestClient,
) -> None:
    """`node_type` not in the enum → 422 from FastAPI's Query(pattern)."""
    response = client.get(
        "/api/v1/search", params={"node_type": "epic"}
    )
    assert response.status_code == 422


def test_get_search_invalid_sort_returns_422(client: TestClient) -> None:
    """Unknown sort value → 422 from FastAPI's Query(pattern)."""
    response = client.get("/api/v1/search", params={"sort": "foo"})
    assert response.status_code == 422


# ---- response shape ------------------------------------------------------


def test_get_search_response_shape_includes_pagination_metadata(
    client: TestClient,
) -> None:
    """Response shape includes `hits`, `total`, `page` with `offset`+`limit`."""
    response = client.get("/api/v1/search", params={"page_size": 2})
    body = response.json()
    assert set(body.keys()) == {"hits", "total", "page"}
    assert set(body["page"].keys()) == {"offset", "limit"}


def test_get_search_hit_includes_iso_timestamps(client: TestClient) -> None:
    """Hit timestamps are ISO 8601 strings."""
    response = client.get("/api/v1/search", params={"title": "Alpha"})
    body = response.json()
    hit = body["hits"][0]
    # ISO 8601 ends with `+00:00` for UTC datetimes.
    assert hit["created_at"].endswith("+00:00")
    assert hit["updated_at"].endswith("+00:00")


def test_get_search_hit_includes_score(client: TestClient) -> None:
    """Each hit carries a `score` field (currently 1.0)."""
    response = client.get("/api/v1/search", params={"title": "Alpha"})
    body = response.json()
    assert body["hits"][0]["score"] == 1.0


# ---- OpenAPI surface ------------------------------------------------------


def test_search_endpoint_is_in_openapi_schema(client: TestClient) -> None:
    """The OpenAPI schema lists the search endpoint."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    paths = schema["paths"]
    assert "/api/v1/search" in paths
    assert "get" in paths["/api/v1/search"]


def test_search_endpoint_openapi_documents_query_params(
    client: TestClient,
) -> None:
    """OpenAPI documents all the query parameters."""
    response = client.get("/openapi.json")
    schema = response.json()
    operation = schema["paths"]["/api/v1/search"]["get"]
    params = {p["name"] for p in operation.get("parameters", [])}
    assert "title" in params
    assert "prefix" in params
    assert "node_type" in params
    assert "parent_id" in params
    assert "story_id" in params
    assert "sort" in params
    assert "page" in params
    assert "page_size" in params


def test_search_endpoint_openapi_documents_responses(
    client: TestClient,
) -> None:
    """OpenAPI documents 200 and 422 responses."""
    response = client.get("/openapi.json")
    schema = response.json()
    operation = schema["paths"]["/api/v1/search"]["get"]
    responses = operation["responses"]
    assert "200" in responses
    assert "422" in responses
