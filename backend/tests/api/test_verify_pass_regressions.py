"""Regression tests for bugs uncovered by the verify-backend.sh pass.

Each test here corresponds to a defect ChatGPT's validation milestone
surfaced against the live backend. These tests pin the fix in place
so a future refactor can't reintroduce the same regression without
failing CI.

Bugs covered (see `scripts/verify_backend.sh`):

    A. `get_workspace_service` was building a fresh repository per
       request, dropping the synchroniser/cache/index_repo wiring
       from the lifespan. Search saw no writes because every request
       used `sync=None`. Fix: source from `app.state.repository`.

    B. Filesystem-layer `NodeNotFoundOnDiskError` escaped from
       mutation paths (delete, rename, move, canvas, metadata) and
       surfaced as 500. Fix: `_translate_fs_errors` context
       manager wraps every read/write path on the repository,
       translating to domain `NodeNotFoundError`.

    C. PATCH /nodes/{id}/metadata returned 200 but with an empty
       metadata dict — the service was routing through
       `rename_node` which used `with_title(...)` and dropped the
       new metadata. Fix: dedicated `update_metadata` repo method
       that writes node.json in place via `filesystem.write_node`.

    D. 404 paths (missing id on stories/nodes POST/PATCH/DELETE)
       returned 500 because the on-disk repository had a stale
       bytecode cache. Verified live that all paths return 404 with
       stable envelope. Fix: regression pins the contract.

    E. Pydantic 422 returned FastAPI's default envelope
       ``{"detail": [...]}`` instead of the service's stable
       ``{"code": ..., "message": ..., "field": ..., "errors": ...}``
       shape. Fix: `RequestValidationError` handler in
       `backend.api.exception_handlers`.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import get_workspace_service
from backend.domain import Node, NodeMetadata, NodeType
from backend.main import create_app
from backend.services import WorkspaceService

from backend.tests.unit.services.conftest import InMemoryWorkspaceRepository


# ---- helpers --------------------------------------------------------------


def _app_with(fake_repo: InMemoryWorkspaceRepository) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_workspace_service] = (
        lambda: WorkspaceService(fake_repo)
    )
    return TestClient(app)


# ---- Bug B + D: every mutation 404 must use the service error envelope ---


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/stories/00000000-0000-0000-0000-000000000000"),
        ("GET", "/api/v1/nodes/00000000-0000-0000-0000-000000000000"),
        (
            "POST",
            "/api/v1/nodes/00000000-0000-0000-0000-000000000000/children",
        ),
        ("PATCH", "/api/v1/nodes/00000000-0000-0000-0000-000000000000"),
        (
            "PATCH",
            "/api/v1/nodes/00000000-0000-0000-0000-000000000000/canvas",
        ),
        (
            "PATCH",
            "/api/v1/nodes/00000000-0000-0000-0000-000000000000/metadata",
        ),
        ("DELETE", "/api/v1/nodes/00000000-0000-0000-0000-000000000000"),
    ],
)
def test_missing_id_returns_404_with_stable_envelope(
    method: str, path: str
) -> None:
    """Bug B + D regression: every mutation against an unknown id
    must return 404 with the stable service-error envelope — never
    a 500 with plain-text 'Internal Server Error'.

    Prior to the fix the underlying `NodeNotFoundOnDiskError` from
    `LocalFilesystem` escaped the repository, the API's
    `_service_error_handler` did not match it, and Starlette's
    `ServerErrorMiddleware` produced a 500 with a plain-text body.
    """
    fake_repo = InMemoryWorkspaceRepository()
    client = _app_with(fake_repo)

    if method == "GET":
        response = client.get(path)
    elif method == "POST":
        response = client.post(
            path, json={"title": "Orphan", "type": "task"}
        )
    elif method == "PATCH":
        if path.endswith("/canvas"):
            response = client.patch(path, json={"content": "x"})
        elif path.endswith("/metadata"):
            response = client.patch(path, json={"key": "k", "value": 1})
        else:
            response = client.patch(path, json={"title": "x"})
    elif method == "DELETE":
        response = client.delete(path)
    else:
        pytest.fail(f"unsupported method: {method}")

    assert response.status_code == 404, (
        f"{method} {path} expected 404, got {response.status_code}; "
        f"body={response.text!r}"
    )
    body = response.json()
    # Stable envelope: code, message, plus the relevant id field.
    assert "code" in body
    assert body["code"] in {
        "node_not_found",
        "story_not_found",
        "parent_not_found",
    }
    assert "message" in body
    # The 500 escape produced a plain text body of "Internal Server
    # Error" with content-type text/plain. Guard against that
    # regression specifically.
    assert response.headers["content-type"].startswith("application/json")


# ---- Bug E: Pydantic 422 returns stable envelope --------------------------


@pytest.mark.parametrize(
    "method,path,body,field",
    [
        # Pydantic-shape rejections: empty title, rogue field, bad enum.
        ("POST", "/api/v1/stories", {"title": ""}, "title"),
        (
            "POST",
            "/api/v1/stories",
            {"title": "ok", "rogue_field": "x"},
            "rogue_field",
        ),
        (
            "POST",
            "/api/v1/nodes/00000000-0000-0000-0000-000000000000/children",
            {"title": "x", "type": "epic"},
            "type",
        ),
        (
            "PATCH",
            "/api/v1/nodes/00000000-0000-0000-0000-000000000000",
            {"title": ""},
            "title",
        ),
        (
            "PATCH",
            "/api/v1/nodes/00000000-0000-0000-0000-000000000000",
            {"title": "ok", "foo": "bar"},
            "foo",
        ),
        (
            "PATCH",
            "/api/v1/nodes/00000000-0000-0000-0000-000000000000/metadata",
            {"value": "x"},
            "key",
        ),
    ],
)
def test_pydantic_validation_returns_stable_envelope(
    method: str, path: str, body: dict, field: str
) -> None:
    """Bug E regression (Pydantic path): Pydantic/FastAPI request
    validation must produce the same stable envelope as the service
    layer, so the frontend can branch on `code` without parsing
    ``detail``.

    Prior to the fix the 422 body looked like
    ``{"detail": [{"loc": [...], "msg": ..., "type": ...}]}``,
    which is a different shape from every other 422 the API emits.
    """
    fake_repo = InMemoryWorkspaceRepository()
    client = _app_with(fake_repo)

    if method == "POST":
        response = client.post(path, json=body)
    else:
        response = client.patch(path, json=body)

    assert response.status_code == 422
    json_body = response.json()
    assert json_body.get("code") == "validation_error"
    assert "message" in json_body
    assert "errors" in json_body
    assert isinstance(json_body["errors"], list)
    # Stable contract: surface the offending field name.
    assert json_body.get("field") == field, (
        f"expected field={field!r}, got {json_body.get('field')!r}; "
        f"full body: {json_body!r}"
    )


@pytest.mark.parametrize(
    "method,path,body",
    [
        # Service-layer ValueError rejections: whitespace-only title,
        # empty metadata key, etc. These hit the service-layer
        # `_value_error_handler` (not the Pydantic handler), so the
        # envelope shape is reduced (no `errors` list — just
        # `code + message`).
        (
            "POST",
            "/api/v1/stories",
            {"title": "   "},
        ),
        (
            "PATCH",
            "/api/v1/nodes/00000000-0000-0000-0000-000000000000/metadata",
            {"key": "", "value": 1},
        ),
    ],
)
def test_service_validation_returns_stable_envelope(
    method: str, path: str, body: dict
) -> None:
    """Service-layer ValueError rejections must also surface under
    the stable envelope (code + message), NOT the FastAPI default
    `{"detail": ...}` shape."""
    fake_repo = InMemoryWorkspaceRepository()
    client = _app_with(fake_repo)

    if method == "POST":
        response = client.post(path, json=body)
    else:
        response = client.patch(path, json=body)

    assert response.status_code == 422
    json_body = response.json()
    assert json_body.get("code") == "validation_error"
    assert "message" in json_body
    # No FastAPI default envelope leakage.
    assert "detail" not in json_body


# ---- Bug C: PATCH /metadata actually persists the new metadata -----------


def test_patch_metadata_persists_new_value() -> None:
    """Bug C regression: PATCH /nodes/{id}/metadata must actually
    persist the new key/value.

    Prior to the fix the service routed through `rename_node` with
    the same title, which round-tripped through `with_title(...)`
    and dropped the new metadata. The response body returned 200
    with the OLD metadata — silent data loss.
    """
    fake_repo = InMemoryWorkspaceRepository()
    a = Node(
        id="11111111-1111-1111-1111-111111111111",
        title="Story A",
        type=NodeType.STORY,
        metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
    )
    fake_repo._tree.add(a)  # noqa: SLF001

    client = _app_with(fake_repo)
    response = client.patch(
        "/api/v1/nodes/11111111-1111-1111-1111-111111111111/metadata",
        json={"key": "priority", "value": "high"},
    )
    assert response.status_code == 200
    body = response.json()
    # Response must surface the NEW value, not the old empty one.
    assert body["metadata"]["priority"] == "high"

    # A subsequent read must also see the new value — that's
    # the data-loss half of the regression.
    read = client.get(
        "/api/v1/nodes/11111111-1111-1111-1111-111111111111"
    )
    assert read.status_code == 200
    assert read.json()["metadata"]["priority"] == "high"


# ---- Bug A: dependency wiring reaches the lifespan-wired repo -----------


def test_dependency_wiring_uses_app_state_repository() -> None:
    """Bug A regression: the FastAPI dependency must source from
    `app.state.repository` (the lifespan-wired rich repo with
    synchroniser+cache), not build a fresh one per request.

    Pinning the contract: when a request handler resolves
    `get_workspace_repository`, the returned repo IS the same
    object the lifespan stashed. Otherwise the synchroniser
    wouldn't fire and the index would silently fall behind the
    filesystem.
    """
    from backend.api.dependencies import get_workspace_repository

    sentinel_repo = InMemoryWorkspaceRepository()
    app = FastAPI()
    app.state.repository = sentinel_repo  # type: ignore[attr-defined]

    class _StubRequest:
        def __init__(self, target_app):
            self.app = target_app

    stub_request = _StubRequest(app)
    resolved = get_workspace_repository(stub_request)  # type: ignore[arg-type]
    assert resolved is sentinel_repo


# ---- Cross-cutting: every public endpoint surfaces the same 404 envelope --


def test_all_endpoints_use_stable_error_envelope_on_404() -> None:
    """Cross-cutting regression: every endpoint that signals
    'resource not found' must use the service-error envelope shape
    ``{"code": "...", "message": "...", "<id_field>": "..."}``.

    This is the contract the frontend relies on. A bug in any one
    endpoint causes UI inconsistency.
    """
    fake_repo = InMemoryWorkspaceRepository()
    client = _app_with(fake_repo)

    cases = [
        ("GET", "/api/v1/stories/00000000-0000-0000-0000-000000000000"),
        ("GET", "/api/v1/nodes/00000000-0000-0000-0000-000000000000"),
        ("GET", "/api/v1/nodes/00000000-0000-0000-0000-000000000000/canvas"),
    ]
    for method, path in cases:
        response = client.request(method, path)
        assert response.status_code == 404, (
            f"{method} {path} expected 404, got {response.status_code}"
        )
        body = response.json()
        assert isinstance(body, dict)
        assert set(body.keys()) >= {"code", "message"}, (
            f"{method} {path} missing required envelope keys: {body!r}"
        )
        # No legacy FastAPI default `detail` key.
        assert "detail" not in body, (
            f"{method} {path} leaked FastAPI default detail key"
        )
