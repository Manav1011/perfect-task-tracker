"""Workspace API DTOs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from backend.schemas.node import NodeResponse


class WorkspaceTreeResponse(BaseModel):
    """The full workspace tree.

    Returned by GET /api/v1/workspace. Contains:
      - roots: the Story-type root nodes, in tree order
      - nodes: every Node in the workspace (flat), for efficient
               client-side rendering without N+1 child fetches.
    """

    model_config = ConfigDict(extra="forbid")

    roots: list[NodeResponse]
    nodes: list[NodeResponse]
