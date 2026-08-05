"""GET /api/v1/workspace — full workspace tree."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.dependencies import get_workspace_service
from backend.api.mappers import tree_to_workspace_response
from backend.schemas.workspace import WorkspaceTreeResponse
from backend.services import WorkspaceService

router = APIRouter(tags=["workspace"])


@router.get(
    "/workspace",
    response_model=WorkspaceTreeResponse,
    summary="Return the full workspace tree",
)
def get_workspace(
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceTreeResponse:
    """Reconstruct the workspace tree and return it as DTOs.

    The response contains every Node in the workspace plus a list
    of root Stories. The frontend uses the flat list to render the
    tree without N+1 child fetches.
    """
    tree = service.load_workspace_tree()
    return tree_to_workspace_response(tree)
