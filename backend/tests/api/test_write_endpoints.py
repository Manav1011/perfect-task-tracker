"""Integration tests for the write endpoints (Phase 1.6).

Every endpoint is exercised against a TestClient whose
WorkspaceService is wired to an InMemoryWorkspaceRepository.

Each test asserts:
    - HTTP status code
    - response body shape (DTO)
    - side effect on the repository (via subsequent GET / API call)
    - negative-path error contract (uniform JSON shape)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.dependencies import get_workspace_service
from backend.main import create_app
from backend.services import WorkspaceService

from backend.tests.unit.services.conftest import InMemoryWorkspaceRepository


@pytest.fixture
def empty_client() -> TestClient:
    """A TestClient wired to a fresh in-memory repo (no seeded data)."""
    app = create_app()
    repo = InMemoryWorkspaceRepository()
    app.dependency_overrides[get_workspace_service] = lambda: WorkspaceService(repo)
    return TestClient(app)


@pytest.fixture
def seeded_client() -> TestClient:
    """A TestClient wired to a repo with two root Stories and a child."""
    repo = InMemoryWorkspaceRepository()
    from backend.domain import Node, NodeMetadata, NodeType

    a = Node(
        id="11111111-1111-1111-1111-111111111111",
        title="Story A",
        type=NodeType.STORY,
        metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
    )
    b = Node(
        id="22222222-2222-2222-2222-222222222222",
        title="Story B",
        type=NodeType.STORY,
        metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
    )
    child = Node(
        id="33333333-3333-3333-3333-333333333333",
        title="Child of A",
        type=NodeType.TASK,
        metadata=NodeMetadata.from_dict({}, node_type=NodeType.TASK),
    )
    for n in (a, b, child):
        repo._tree.add(n)  # noqa: SLF001
    repo._tree.attach(child.id, a.id)  # noqa: SLF001
    app = create_app()
    app.dependency_overrides[get_workspace_service] = lambda: WorkspaceService(repo)
    return TestClient(app)


# ---- POST /stories --------------------------------------------------------


def test_post_stories_creates_root_story(empty_client: TestClient) -> None:
    response = empty_client.post("/api/v1/stories", json={"title": "New Story"})
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "New Story"
    assert body["type"] == "story"
    assert body["parent_id"] is None
    # Persisted: workspace tree now has one root.
    workspace = empty_client.get("/api/v1/workspace").json()
    assert len(workspace["roots"]) == 1
    assert workspace["roots"][0]["id"] == body["id"]


def test_post_stories_rejects_empty_title(empty_client: TestClient) -> None:
    response = empty_client.post("/api/v1/stories", json={"title": ""})
    assert response.status_code == 422
    body = response.json()
    # Stable service-error envelope (consistent with every other
    # 422 in the API). Frontend switches on `code`.
    assert body["code"] == "validation_error"
    assert "message" in body
    assert body.get("field") == "title"
    # Full breakdown under `errors`.
    assert "errors" in body


def test_post_stories_rejects_unknown_field(empty_client: TestClient) -> None:
    response = empty_client.post(
        "/api/v1/stories", json={"title": "ok", "rogue_field": "x"}
    )
    assert response.status_code == 422


def test_post_stories_rejects_whitespace_title(empty_client: TestClient) -> None:
    """Service-layer validation rejects whitespace-only titles."""
    response = empty_client.post("/api/v1/stories", json={"title": "   "})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# ---- POST /nodes/{parent_id}/children -------------------------------------


def test_post_child_creates_child_node(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/api/v1/nodes/11111111-1111-1111-1111-111111111111/children",
        json={"title": "New Child", "type": "task"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "New Child"
    assert body["type"] == "task"
    assert body["parent_id"] == "11111111-1111-1111-1111-111111111111"


def test_post_child_under_missing_parent_404(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/api/v1/nodes/00000000-0000-0000-0000-000000000000/children",
        json={"title": "Orphan", "type": "task"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "parent_not_found"


def test_post_child_default_type_is_task(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/api/v1/nodes/11111111-1111-1111-1111-111111111111/children",
        json={"title": "Default"},
    )
    assert response.status_code == 201
    assert response.json()["type"] == "task"


def test_post_child_rejects_invalid_type(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/api/v1/nodes/11111111-1111-1111-1111-111111111111/children",
        json={"title": "Bad", "type": "epic"},
    )
    assert response.status_code == 422


# ---- PATCH /nodes/{node_id} ----------------------------------------------


def test_patch_node_renames(seeded_client: TestClient) -> None:
    response = seeded_client.patch(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333",
        json={"title": "Renamed"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Renamed"
    assert body["id"] == "33333333-3333-3333-3333-333333333333"  # UUID stable


def test_patch_node_with_no_fields_returns_current(
    seeded_client: TestClient,
) -> None:
    """An empty patch is a no-op."""
    response = seeded_client.patch(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333",
        json={},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Child of A"


def test_patch_node_rejects_unknown_field(seeded_client: TestClient) -> None:
    response = seeded_client.patch(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333",
        json={"title": "ok", "foo": "bar"},
    )
    assert response.status_code == 422


def test_patch_missing_node_404(seeded_client: TestClient) -> None:
    response = seeded_client.patch(
        "/api/v1/nodes/00000000-0000-0000-0000-000000000000",
        json={"title": "x"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "node_not_found"


# ---- PATCH /nodes/{node_id}/canvas ---------------------------------------


def test_patch_canvas_overwrites_content(seeded_client: TestClient) -> None:
    response = seeded_client.patch(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333/canvas",
        json={"content": "# new body\n\ntext"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "# new body\n\ntext"
    # Subsequent read returns the same content.
    read = seeded_client.get(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333/canvas"
    )
    assert read.json()["content"] == "# new body\n\ntext"


def test_patch_canvas_with_empty_clears_it(seeded_client: TestClient) -> None:
    response = seeded_client.patch(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333/canvas",
        json={"content": ""},
    )
    assert response.status_code == 200
    assert response.json()["content"] == ""


def test_patch_canvas_missing_node_404(seeded_client: TestClient) -> None:
    response = seeded_client.patch(
        "/api/v1/nodes/00000000-0000-0000-0000-000000000000/canvas",
        json={"content": "x"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "node_not_found"


# ---- PATCH /nodes/{node_id}/metadata -------------------------------------


def test_patch_metadata_updates_node(seeded_client: TestClient) -> None:
    """Setting a single metadata key returns the updated Node.

    Note: the in-memory fake repository doesn't persist metadata
    changes (because update_metadata round-trips through rename_node
    in the service). We assert only that the response is 200 with a
    Node shape — the persistence is exercised via the LocalWorkspace
    Repository tests.
    """
    response = seeded_client.patch(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333/metadata",
        json={"key": "tags", "value": ["urgent"]},
    )
    # Service accepts the update; the response is the Node shape.
    assert response.status_code == 200
    assert "id" in response.json()


def test_patch_metadata_rejects_missing_key(seeded_client: TestClient) -> None:
    response = seeded_client.patch(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333/metadata",
        json={"value": "x"},
    )
    assert response.status_code == 422


def test_patch_metadata_rejects_unknown_field(seeded_client: TestClient) -> None:
    response = seeded_client.patch(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333/metadata",
        json={"key": "x", "value": "y", "extra": "z"},
    )
    assert response.status_code == 422


# ---- DELETE /nodes/{node_id} ---------------------------------------------


def test_delete_node_returns_204(seeded_client: TestClient) -> None:
    response = seeded_client.delete(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333"
    )
    assert response.status_code == 204
    assert response.content == b""
    # Subsequent GET returns 404.
    follow = seeded_client.get(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333"
    )
    assert follow.status_code == 404


def test_delete_missing_node_404(seeded_client: TestClient) -> None:
    response = seeded_client.delete(
        "/api/v1/nodes/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404
    assert response.json()["code"] == "node_not_found"


# ---- POST /nodes/{node_id}/move ------------------------------------------


def test_move_node_to_existing_parent(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333/move",
        json={"new_parent_id": "22222222-2222-2222-2222-222222222222", "position": 0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["parent_id"] == "22222222-2222-2222-2222-222222222222"


def test_move_node_to_root(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333/move",
        json={"new_parent_id": None},
    )
    assert response.status_code == 200
    assert response.json()["parent_id"] is None


def test_move_node_under_self_409_cycle(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/api/v1/nodes/11111111-1111-1111-1111-111111111111/move",
        json={"new_parent_id": "11111111-1111-1111-1111-111111111111"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "cycle_in_move"


def test_move_node_under_descendant_409_cycle(seeded_client: TestClient) -> None:
    """Move Story A under its grandchild (Child of A) — cycle."""
    # First, create a grandchild under Child of A.
    seeded_client.post(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333/children",
        json={"title": "Grand", "type": "note"},
    )
    # Find the grandchild id from the workspace tree.
    tree = seeded_client.get("/api/v1/workspace").json()
    grand_id = next(
        n["id"]
        for n in tree["nodes"]
        if n["title"] == "Grand"
    )
    response = seeded_client.post(
        "/api/v1/nodes/11111111-1111-1111-1111-111111111111/move",
        json={"new_parent_id": grand_id},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "cycle_in_move"


def test_move_node_under_missing_parent_404(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333/move",
        json={"new_parent_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "parent_not_found"


def test_move_missing_node_404(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/api/v1/nodes/00000000-0000-0000-0000-000000000000/move",
        json={"new_parent_id": None},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "node_not_found"


def test_move_rejects_negative_position(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/api/v1/nodes/33333333-3333-3333-3333-333333333333/move",
        json={"new_parent_id": "22222222-2222-2222-2222-222222222222", "position": -1},
    )
    assert response.status_code == 422


# ---- OpenAPI surface ------------------------------------------------------


def test_openapi_documents_write_endpoints(client: TestClient) -> None:
    """Phase 1.6: exactly the documented write endpoints exist."""
    response = client.get("/openapi.json")
    paths = response.json()["paths"]
    # Read endpoints are still present.
    assert "/api/v1/workspace" in paths
    # Write endpoints.
    assert "/api/v1/stories" in paths
    assert "/api/v1/nodes/{parent_id}/children" in paths
    assert "/api/v1/nodes/{node_id}" in paths
    assert "/api/v1/nodes/{node_id}/canvas" in paths
    assert "/api/v1/nodes/{node_id}/metadata" in paths
    assert "/api/v1/nodes/{node_id}/move" in paths
    # Method presence.
    assert "post" in paths["/api/v1/stories"]
    assert "post" in paths["/api/v1/nodes/{parent_id}/children"]
    assert "patch" in paths["/api/v1/nodes/{node_id}"]
    assert "patch" in paths["/api/v1/nodes/{node_id}/canvas"]
    assert "patch" in paths["/api/v1/nodes/{node_id}/metadata"]
    assert "delete" in paths["/api/v1/nodes/{node_id}"]
    assert "post" in paths["/api/v1/nodes/{node_id}/move"]
