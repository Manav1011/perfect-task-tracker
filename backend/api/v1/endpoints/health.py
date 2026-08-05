"""Health endpoint.

Liveness only: confirms the app process is up and the router stack
responds. Does not probe Postgres, the filesystem, or any business
state — those belong in a separate readiness check (added in Phase 3+).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.config.settings import get_settings

router = APIRouter()


class HealthResponse(BaseModel):
    """Minimal liveness payload."""

    status: str
    app: str
    env: str


@router.get("/health", response_model=HealthResponse)
def healthcheck() -> HealthResponse:
    """Return a static liveness response.

    Intentionally cheap. No I/O. See module docstring.
    """
    settings = get_settings()
    return HealthResponse(status="ok", app=settings.app_name, env=settings.app_env)