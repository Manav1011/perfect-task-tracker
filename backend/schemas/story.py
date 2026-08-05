"""Story-related API DTOs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from backend.schemas.node import NodeResponse


class StoryResponse(BaseModel):
    """A single root Story."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    type: str = "story"


class StoryListResponse(BaseModel):
    """List of root Stories — used by the workspace tree response."""

    model_config = ConfigDict(extra="forbid")

    stories: list[StoryResponse]
    # Flat list of every Node in the workspace. The frontend can use
    # this to render the tree without re-fetching children one-by-one.
    nodes: list[NodeResponse]
