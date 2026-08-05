"""Node-related API DTOs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NodeTypeEnum(str):
    """String-typed enum mirror of backend.domain.enums.NodeType.

    We avoid a real pydantic.Enum so the wire format stays a plain
    string — easier for the frontend and for future API-versioning
    where we might want to accept values that are no longer in the
    enum (forward compatibility).
    """

    STORY = "story"
    TASK = "task"
    NOTE = "note"


class NodeResponse(BaseModel):
    """API representation of a Node.

    Mirrors the domain entity but with two differences:

    1. `type` is a string (not the domain Enum) so the wire format
       is stable across Python upgrades.
    2. `metadata` is a free-form dict; validation lives in the
       domain, not the API.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Stable UUID (Invariant §6)")
    title: str
    type: str = Field(..., description="story | task | note")
    parent_id: str | None = Field(None, description="None for root Stories")
    children_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    canvas: str | None = None
