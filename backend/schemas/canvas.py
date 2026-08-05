"""Canvas-related API DTOs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CanvasResponse(BaseModel):
    """Canvas content for a Node."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    content: str
