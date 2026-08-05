"""API integration tests — read-only endpoints.

Every endpoint is exercised against a TestClient whose WorkspaceService
is wired to an InMemoryWorkspaceRepository via dependency_overrides.
This proves the API layer is HTTP-only and that every persistence
detail is hidden behind the service.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.dependencies import get_workspace_service


# ---- GET /api/v1/workspace ------------------------------------------------


def test_get_workspace_returns_full_tree(
    client: TestClient, seeded_ids: dict[str, str]
) -> None:
    response = client.get("/api/v1/workspace")
    assert response.status_code == 200
    body = response.json()
    assert len(body["roots"]) == 2
    titles = sorted(r["title"] for r in body["roots"])
    assert titles == ["Story A", "Story B"]
    # Flat nodes list contains all 4 nodes.
    node_titles = sorted(n["title"] for n in body["nodes"])
    assert node_titles == ["Child of A", "Grandchild", "Story A", "Story B"]
    # Spot-check: child has parent_id = A's id.
    child = next(n for n in body["nodes"] if n["title"] == "Child of A")
    assert child["parent_id"] == seeded_ids["a"]
    assert child["type"] == "task"


def test_get_workspace_empty_raises_404(client: TestClient) -> None:
    """Empty workspace surfaces a stable error code."""
    # We need a fresh, empty client for this.
    from backend.api.dependencies import get_workspace_service
    from backend.main import create_app
    from backend.services import WorkspaceService

    from backend.tests.unit.services.conftest import InMemoryWorkspaceRepository

    empty_repo = InMemoryWorkspaceRepository()
    app = create_app()
    app.dependency_overrides[get_workspace_service] = lambda: WorkspaceService(
        empty_repo
    )
    with TestClient(app) as c:
        response = c.get("/api/v1/workspace")
        assert response.status_code == 404
        assert response.json()["code"] == "workspace_empty"


# ---- GET /api/v1/stories/{id} ---------------------------------------------


def test_get_story_returns_node(client: TestClient, seeded_ids: dict[str, str]) -> None:
    response = client.get(f"/api/v1/stories/{seeded_ids['a']}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == seeded_ids["a"]
    assert body["title"] == "Story A"
    assert body["parent_id"] is None


def test_get_story_404_for_missing(client: TestClient) -> None:
    response = client.get("/api/v1/stories/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["code"] == "story_not_found"


def test_get_story_404_for_non_root(
    client: TestClient, seeded_ids: dict[str, str]
) -> None:
    # Child of A is not a root Story.
    response = client.get(f"/api/v1/stories/{seeded_ids['child']}")
    assert response.status_code == 404
    assert response.json()["code"] == "story_not_found"


# ---- GET /api/v1/nodes/{id} -----------------------------------------------


def test_get_node_returns_any_node(
    client: TestClient, seeded_ids: dict[str, str]
) -> None:
    response = client.get(f"/api/v1/nodes/{seeded_ids['child']}")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Child of A"
    assert body["parent_id"] == seeded_ids["a"]


def test_get_node_404_for_missing(client: TestClient) -> None:
    response = client.get("/api/v1/nodes/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["code"] == "node_not_found"


# ---- GET /api/v1/nodes/{id}/children --------------------------------------


def test_get_node_children_returns_ordered_children(
    client: TestClient, seeded_ids: dict[str, str]
) -> None:
    response = client.get(f"/api/v1/nodes/{seeded_ids['a']}/children")
    assert response.status_code == 200
    body = response.json()
    assert [n["title"] for n in body] == ["Child of A"]


def test_get_node_children_empty_for_leaf(
    client: TestClient, seeded_ids: dict[str, str]
) -> None:
    response = client.get(f"/api/v1/nodes/{seeded_ids['grandchild']}/children")
    assert response.status_code == 200
    assert response.json() == []


def test_get_node_children_404_for_missing(client: TestClient) -> None:
    response = client.get(
        "/api/v1/nodes/00000000-0000-0000-0000-000000000000/children"
    )
    assert response.status_code == 404
    assert response.json()["code"] == "node_not_found"


# ---- GET /api/v1/nodes/{id}/canvas ----------------------------------------


def test_get_canvas_returns_content(
    client: TestClient, seeded_ids: dict[str, str]
) -> None:
    # Patch the fake service to return canned canvas content — the
    # in-memory fake doesn't model canvas, and Phase 1.5 has no write
    # endpoint to seed one.
    service = client.app.dependency_overrides[get_workspace_service]()
    service.read_canvas = lambda _node_id: "# hello"  # type: ignore[assignment]

    response = client.get(f"/api/v1/nodes/{seeded_ids['a']}/canvas")
    assert response.status_code == 200
    body = response.json()
    assert body == {"node_id": seeded_ids["a"], "content": "# hello"}


def test_get_canvas_404_for_missing(client: TestClient) -> None:
    response = client.get(
        "/api/v1/nodes/00000000-0000-0000-0000-000000000000/canvas"
    )
    assert response.status_code == 404
    assert response.json()["code"] == "node_not_found"


# ---- OpenAPI surface ------------------------------------------------------


def test_openapi_documents_all_endpoints(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    # Read paths are present.
    assert "/api/v1/workspace" in paths
    assert "/api/v1/stories/{story_id}" in paths
    assert "/api/v1/nodes/{node_id}" in paths
    assert "/api/v1/nodes/{node_id}/children" in paths
    assert "/api/v1/nodes/{node_id}/canvas" in paths
    # Strictly read paths must be GET-only.
    strictly_read = (
        "/api/v1/workspace",
        "/api/v1/stories/{story_id}",
        "/api/v1/nodes/{node_id}/children",
    )
    for read_path in strictly_read:
        methods = paths[read_path]
        assert "get" in methods, f"{read_path} missing GET"
        assert set(methods.keys()).issubset({"get", "options", "parameters"}), (
            f"unexpected methods on {read_path}: {set(methods.keys())}"
        )
    # Read+write paths: /nodes/{node_id} has GET + PATCH (rename),
    # /nodes/{node_id}/canvas has GET + PATCH (overwrite).
    for mixed_path, expected in (
        ("/api/v1/nodes/{node_id}", {"get", "patch"}),
        ("/api/v1/nodes/{node_id}/canvas", {"get", "patch"}),
    ):
        methods = paths[mixed_path]
        assert expected.issubset(set(methods.keys())), (
            f"{mixed_path} missing one of {expected}: "
            f"got {set(methods.keys())}"
        )
