"""FastAPI application entrypoint.

Wires:
    - the v1 router (currently just /health)
    - the lifespan (logging + future startup hooks)
    - the OpenAPI metadata
    - the API exception handlers (Phase 1.5)

Run with:
    uv run uvicorn backend.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

from backend import __version__
from backend.api.exception_handlers import register_exception_handlers
from backend.api.v1.router import api_router
from backend.config.settings import get_settings
from backend.core.lifespan import lifespan


def create_app() -> FastAPI:
    """Application factory.

    Returning a factory (not a module-level `app = FastAPI(...)`) keeps
    tests clean — each test can build its own app with overridden deps.
    """
    # Settings is read here so future startup hooks can use it; the
    # import also primes the cached settings accessor.
    get_settings()
    app = FastAPI(
        title="PerfectTaskTracker API",
        version=__version__,
        lifespan=lifespan,
    )
    app.include_router(api_router, prefix="/api/v1")
    register_exception_handlers(app)
    return app


app: FastAPI = create_app()