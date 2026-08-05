"""GET /api/v1/stories/{id} — fetch a single root Story."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.dependencies import get_workspace_service
from backend.api.mappers import node_to_dto
from backend.domain import NodeId
from backend.schemas.node import NodeResponse
from backend.services import WorkspaceService

router = APIRouter(tags=["stories"])


@router.get(
    "/stories/{story_id}",
    response_model=NodeResponse,
    summary="Return a single root Story",
)
def get_story(
    story_id: str,
    service: WorkspaceService = Depends(get_workspace_service),
) -> NodeResponse:
    """Fetch a single root Story by id.

    Returns 404 with code `story_not_found` if the id is missing or
    isn't a root Story. The handler is registered in
    `backend.api.exception_handlers`.
    """
    story = service.get_story(NodeId(story_id))
    return node_to_dto(story)
