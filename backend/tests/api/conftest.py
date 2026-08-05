"""API integration test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.api.dependencies import get_workspace_service
from backend.domain import Node, NodeMetadata, NodeType
from backend.main import create_app
from backend.services import WorkspaceService

from backend.tests.unit.services.conftest import InMemoryWorkspaceRepository


@pytest.fixture
def fake_repo() -> InMemoryWorkspaceRepository:
    return InMemoryWorkspaceRepository()


@pytest.fixture
def seeded_repo(fake_repo: InMemoryWorkspaceRepository) -> InMemoryWorkspaceRepository:
    """An in-memory repo pre-populated with a small workspace.

    Layout:
        Story A
          └─ Child of A (task)
                └─ Grandchild (note)
        Story B
    """
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
    grandchild = Node(
        id="44444444-4444-4444-4444-444444444444",
        title="Grandchild",
        type=NodeType.NOTE,
        metadata=NodeMetadata.from_dict({}, node_type=NodeType.NOTE),
    )
    for n in (a, b, child, grandchild):
        fake_repo._tree.add(n)  # noqa: SLF001 — test fake
    fake_repo._tree.attach(child.id, a.id)  # noqa: SLF001
    fake_repo._tree.attach(grandchild.id, child.id)  # noqa: SLF001
    fake_repo._seeded_ids = {  # type: ignore[attr-defined]
        "a": a.id,
        "b": b.id,
        "child": child.id,
        "grandchild": grandchild.id,
    }
    return fake_repo


@pytest.fixture
def client(seeded_repo: InMemoryWorkspaceRepository) -> Iterator[TestClient]:
    """A TestClient whose WorkspaceService is wired to the seeded repo.

    Per ADR-0007, the only place in the API layer that knows the
    concrete repository implementation is `backend.api.dependencies`.
    We override `get_workspace_service` here so the test bypasses
    that DI and injects a service that talks to the in-memory fake.
    """
    app = create_app()
    fake_service = WorkspaceService(seeded_repo)
    app.dependency_overrides[get_workspace_service] = lambda: fake_service
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def seeded_ids(seeded_repo: InMemoryWorkspaceRepository) -> dict[str, str]:
    """The NodeIds of the seeded workspace."""
    return seeded_repo._seeded_ids  # type: ignore[attr-defined]
