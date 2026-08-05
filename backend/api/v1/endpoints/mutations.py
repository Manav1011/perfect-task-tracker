"""Write endpoints — POST / PATCH / DELETE.

Every endpoint follows the same shape:

    1. Parse the request body via the request DTO (Pydantic).
       Validation is at the boundary; the service trusts its inputs.
    2. Call exactly one WorkspaceService method.
    3. Map the returned Domain entity to a response DTO.
    4. Return the appropriate HTTP status code (201 for create, 200
       otherwise; 204 for delete-with-no-body).

The endpoints do not catch service exceptions — those propagate to
the registered exception handlers in `backend.api.exception_handlers`,
which translate them to stable JSON error payloads.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.api.dependencies import get_workspace_service
from backend.api.mappers import node_to_dto
from backend.domain import NodeId, NodeType
from backend.schemas.canvas import CanvasResponse
from backend.schemas.node import NodeResponse
from backend.schemas.requests import (
    CreateChildRequest,
    CreateStoryRequest,
    MoveNodeRequest,
    PatchCanvasRequest,
    PatchMetadataRequest,
    PatchNodeRequest,
)
from backend.services import WorkspaceService

router = APIRouter(tags=["mutations"])


# ---- create --------------------------------------------------------------


@router.post(
    "/stories",
    response_model=NodeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new root Story",
)
def create_story(
    body: CreateStoryRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> NodeResponse:
    return node_to_dto(service.create_story(body.title))


@router.post(
    "/nodes/{parent_id}/children",
    response_model=NodeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a child Node under a parent",
)
def create_child(
    parent_id: str,
    body: CreateChildRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> NodeResponse:
    node_type = NodeType(body.type)
    child = service.create_child(NodeId(parent_id), body.title, type_=node_type)
    return node_to_dto(child)


# ---- patch ---------------------------------------------------------------


@router.patch(
    "/nodes/{node_id}",
    response_model=NodeResponse,
    summary="Patch a Node (currently: rename)",
)
def patch_node(
    node_id: str,
    body: PatchNodeRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> NodeResponse:
    """Patch a Node. Today only `title` is supported.

    Unknown fields in the request body are rejected by Pydantic
    (`extra='forbid'`), so a future field added here is opt-in for
    every client.
    """
    if body.title is None:
        # Empty patch is a no-op. Return the current Node.
        node = service.get_node(NodeId(node_id))
        return node_to_dto(node)
    updated = service.rename_node(NodeId(node_id), body.title)
    return node_to_dto(updated)


@router.patch(
    "/nodes/{node_id}/canvas",
    response_model=CanvasResponse,
    summary="Overwrite a Node's canvas content",
)
def patch_canvas(
    node_id: str,
    body: PatchCanvasRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> CanvasResponse:
    service.write_canvas(NodeId(node_id), body.content)
    return CanvasResponse(node_id=node_id, content=body.content)


@router.patch(
    "/nodes/{node_id}/metadata",
    response_model=NodeResponse,
    summary="Set a single metadata key on a Node",
)
def patch_metadata(
    node_id: str,
    body: PatchMetadataRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> NodeResponse:
    """Set a single metadata key/value pair.

    Currently routes through WorkspaceService.update_metadata,
    which uses rename_node internally (see TODO(phase-4) in
    WorkspaceRepository). The wire shape (single-key patch) is
    stable; the persistence shape changes when Phase 4 lands.
    """
    updated = service.update_metadata(NodeId(node_id), body.key, body.value)
    return node_to_dto(updated)


# ---- delete --------------------------------------------------------------


@router.delete(
    "/nodes/{node_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Recursively delete a Node",
)
def delete_node(
    node_id: str,
    service: WorkspaceService = Depends(get_workspace_service),
) -> None:
    """Delete a Node and all of its descendants.

    Returns 204 No Content on success. 404 if the Node doesn't
    exist (handled by the registered exception handler).
    """
    service.delete_node(NodeId(node_id))
    return None


# ---- move ----------------------------------------------------------------


@router.post(
    "/nodes/{node_id}/move",
    response_model=NodeResponse,
    summary="Move a Node under a new parent",
)
def move_node(
    node_id: str,
    body: MoveNodeRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> NodeResponse:
    """Move a Node to a new parent.

    `new_parent_id` of `null` (JSON null) moves to the workspace
    root. `position` is the zero-based index in the new parent's
    children list; `null` means append. Cycles are rejected with
    HTTP 409.
    """
    new_parent = NodeId(body.new_parent_id) if body.new_parent_id is not None else None
    moved = service.move_node(NodeId(node_id), new_parent_id=new_parent, position=body.position)
    return node_to_dto(moved)
