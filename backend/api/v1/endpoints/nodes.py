"""GET /api/v1/nodes/{id} and /api/v1/nodes/{id}/children — Node reads."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.dependencies import get_workspace_service
from backend.api.mappers import node_to_dto
from backend.domain import NodeId
from backend.schemas.node import NodeResponse
from backend.services import WorkspaceService

router = APIRouter(tags=["nodes"])


@router.get(
    "/nodes/{node_id}",
    response_model=NodeResponse,
    summary="Return a single Node by id",
)
def get_node(
    node_id: str,
    service: WorkspaceService = Depends(get_workspace_service),
) -> NodeResponse:
    """Fetch any Node by id."""
    node = service.get_node(NodeId(node_id))
    return node_to_dto(node)


@router.get(
    "/nodes/{node_id}/children",
    response_model=list[NodeResponse],
    summary="Return the ordered children of a Node",
)
def get_node_children(
    node_id: str,
    service: WorkspaceService = Depends(get_workspace_service),
) -> list[NodeResponse]:
    """Return the ordered children of `node_id`."""
    children = service.get_children(NodeId(node_id))
    return [node_to_dto(c) for c in children]
