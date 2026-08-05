"""Pydantic schemas — API request/response models.

These are API-layer concerns. They live here so the API can evolve
its wire format (versioning, deprecation, field renames) without
touching the Domain.

Schemas MUST NOT import from `backend.domain` directly — they live
on the API side of the boundary. Domain → DTO mapping happens in
`backend.api.mappers`. (Mappers import Domain; DTOs do not.)
"""

from backend.schemas.canvas import CanvasResponse
from backend.schemas.node import NodeResponse, NodeTypeEnum
from backend.schemas.requests import (
    CreateChildRequest,
    CreateStoryRequest,
    MoveNodeRequest,
    PatchCanvasRequest,
    PatchMetadataRequest,
    PatchNodeRequest,
)
from backend.schemas.search import (
    SearchHitDTO,
    SearchPageDTO,
    SearchResultsResponse,
)
from backend.schemas.story import StoryListResponse, StoryResponse
from backend.schemas.workspace import WorkspaceTreeResponse

__all__ = [
    "CanvasResponse",
    "CreateChildRequest",
    "CreateStoryRequest",
    "MoveNodeRequest",
    "NodeResponse",
    "NodeTypeEnum",
    "PatchCanvasRequest",
    "PatchMetadataRequest",
    "PatchNodeRequest",
    "SearchHitDTO",
    "SearchPageDTO",
    "SearchResultsResponse",
    "StoryListResponse",
    "StoryResponse",
    "WorkspaceTreeResponse",
]
