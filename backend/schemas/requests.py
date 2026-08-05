"""Request DTOs — Pydantic models that parse incoming JSON.

Every DTO uses `extra="forbid"` so unknown fields fail validation
loudly. This is a security boundary — a frontend that sends an
unexpected field gets a 422, not a silent ignore.

Field constraints:
    - titles: 1-200 chars, no whitespace-only
    - type: one of "story" | "task" | "note" (string, not enum, to
      keep the wire format stable across Python upgrades)
    - metadata: free-form dict, validated downstream by the
      domain layer (the API does not pre-validate per-key
      semantics, only the request shape)
    - move position: optional int, must be >= 0 if provided
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---- create --------------------------------------------------------------


class CreateStoryRequest(BaseModel):
    """POST /stories body."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200)


class CreateChildRequest(BaseModel):
    """POST /nodes/{parent_id}/children body."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200)
    type: Literal["story", "task", "note"] = "task"


# ---- patch ----------------------------------------------------------------


class PatchNodeRequest(BaseModel):
    """PATCH /nodes/{node_id} body.

    Today only `title` is patchable; the schema leaves room for
    future fields without breaking old clients (unknown fields
    are rejected, but missing fields are fine — the service
    preserves whatever it doesn't change).
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, min_length=1, max_length=200)


class PatchCanvasRequest(BaseModel):
    """PATCH /nodes/{node_id}/canvas body."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field("")


class PatchMetadataRequest(BaseModel):
    """PATCH /nodes/{node_id}/metadata body.

    The body is a single-key patch: callers send {"key": ..., "value": ...}.
    Multi-key patches would require either a bulk update repository
    method or a partial-update contract that bypasses the full-entity
    persistence. Both are Phase 4 concerns; today we patch one key at
    a time and re-route through rename_node in the service (see
    TODO in WorkspaceService.update_metadata and the technical-debt
    row in TECH_SPEC §17).
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., min_length=1, max_length=64)
    value: Any


# ---- move ----------------------------------------------------------------


class MoveNodeRequest(BaseModel):
    """POST /nodes/{node_id}/move body.

    `new_parent_id` is None (serialized as JSON null) for moving a
    Node to the workspace root.
    `position` is the zero-based index in the new parent's children
    list; None means append.
    """

    model_config = ConfigDict(extra="forbid")

    new_parent_id: str | None = None
    position: int | None = Field(None, ge=0)
