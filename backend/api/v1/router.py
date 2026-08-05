"""v1 API router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from backend.api.v1.endpoints import (
    canvas,
    health,
    mutations,
    nodes,
    search,
    stories,
    workspace,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(workspace.router)
api_router.include_router(stories.router)
api_router.include_router(nodes.router)
api_router.include_router(canvas.router)
api_router.include_router(mutations.router)
api_router.include_router(search.router)
