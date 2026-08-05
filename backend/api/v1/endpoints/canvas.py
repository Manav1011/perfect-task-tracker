"""GET /api/v1/nodes/{id}/canvas — read a Node's canvas."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.dependencies import get_workspace_service
from backend.domain import NodeId
from backend.schemas.canvas import CanvasResponse
from backend.services import WorkspaceService

router = APIRouter(tags=["canvas"])


@router.get(
    "/nodes/{node_id}/canvas",
    response_model=CanvasResponse,
    summary="Return the canvas content for a Node",
)
def get_canvas(
    node_id: str,
    service: WorkspaceService = Depends(get_workspace_service),
) -> CanvasResponse:
    """Return the canvas.md content for a Node.

    Returns the raw Markdown body. Empty canvases are returned with
    `content: ""`. Missing canvases surface as 404 with code
    `node_not_found` (the underlying filesystem error wraps as a
    service-layer node-not-found).
    """
    content = service.read_canvas(NodeId(node_id))
    return CanvasResponse(node_id=node_id, content=content)
