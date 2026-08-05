"""API exception → HTTP exception translators.

These handlers live in the API layer because they're the API's
contract with the outside world. The Service layer raises typed
ServiceError subclasses; the API turns them into HTTP responses
with stable shape.

The mapping is one-way: Service → HTTP. Domain and Repository
exceptions must never reach the API layer (the Service wraps them
— see ADR-0007).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.services.exceptions import (
    CycleInMoveServiceError,
    NodeNotFoundServiceError,
    ParentNotFoundServiceError,
    StoryNotFoundServiceError,
    WorkspaceEmptyServiceError,
)
from backend.search.exceptions import InvalidSearchQueryError


def _error_response(status: int, code: str, message: str, **extra: object) -> JSONResponse:
    """Build a uniform JSON error body.

    Stable shape so the frontend can switch on `code` without parsing
    free-form messages.
    """
    body: dict[str, object] = {"code": code, "message": message}
    body.update(extra)
    return JSONResponse(status_code=status, content=body)


async def _service_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Generic ServiceError → 500 fallback (defense in depth)."""
    return _error_response(500, "service_error", str(exc))


async def _node_not_found(request: Request, exc: NodeNotFoundServiceError) -> JSONResponse:
    return _error_response(404, "node_not_found", str(exc), node_id=exc.node_id)


async def _story_not_found(request: Request, exc: StoryNotFoundServiceError) -> JSONResponse:
    return _error_response(404, "story_not_found", str(exc), story_id=exc.story_id)


async def _workspace_empty(request: Request, exc: WorkspaceEmptyServiceError) -> JSONResponse:
    # 200 with an empty workspace is technically valid; we return 404
    # because the operation the caller asked for has no answer.
    return _error_response(404, "workspace_empty", str(exc))


async def _parent_not_found(request: Request, exc: ParentNotFoundServiceError) -> JSONResponse:
    return _error_response(404, "parent_not_found", str(exc), parent_id=exc.parent_id)


async def _cycle_in_move(request: Request, exc: CycleInMoveServiceError) -> JSONResponse:
    return _error_response(409, "cycle_in_move", str(exc), node_id=exc.node_id)


async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Translate service-layer ValueError (programmer/usage error)
    to a 422 with the same envelope as other errors. Without this
    handler, FastAPI returns 500 by default for unhandled exceptions."""
    return _error_response(422, "validation_error", str(exc))


async def _invalid_search_query(
    request: Request, exc: InvalidSearchQueryError
) -> JSONResponse:
    """Translate search-layer InvalidSearchQueryError to a 422.

    The `field` attribute (when set) is included in the
    response so the frontend can highlight the offending
    form field. The error code is `invalid_search_query`
    to distinguish search validation from generic
    `validation_error`.
    """
    extra: dict[str, object] = {}
    if exc.field is not None:
        extra["field"] = exc.field
    return _error_response(422, "invalid_search_query", str(exc), **extra)


async def _request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Translate Pydantic/FastAPI request validation to the
    stable service error envelope.

    Without this, FastAPI returns 422s with the shape
    ``{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}``,
    which is a different shape from the service error envelope
    used elsewhere (``{"code": ..., "message": ..., ...extra}``).
    Frontends would have to special-case 422 from request
    validation vs 422 from the service layer.

    The error code is ``validation_error`` (same as the
    service-layer `ValueError` handler) so the frontend can
    handle both with one branch. We include the first field
    name in `loc` as `field` so the UI can highlight the
    offending form field, when there is one.
    """
    errors = exc.errors()
    extra: dict[str, object] = {"errors": errors}
    if errors:
        first = errors[0]
        loc = first.get("loc") or ()
        # Pydantic's `loc` is the path to the offending value, e.g.
        # ("body", "title") for a request-body field, or
        # ("query", "page_size") for a query parameter, or just
        # ("path", "node_id") for a path parameter. We want the
        # LAST non-path element as `field` so the frontend can
        # highlight the offending form field. Skip the leading
        # "body" / "query" / "path" / "header" / "cookie" routing
        # segments.
        field: str | None = None
        for segment in reversed(loc):
            if isinstance(segment, str) and segment not in (
                "body", "query", "path", "header", "cookie"
            ):
                field = segment
                break
        if field is not None:
            extra["field"] = field
    # The "message" summarises the first error so a generic
    # "invalid request" toast can show up without parsing
    # `errors[]`. The full breakdown is in `errors`.
    message = (
        errors[0].get("msg", "invalid request")
        if errors
        else "invalid request"
    )
    return _error_response(422, "validation_error", message, **extra)


def register_exception_handlers(app: FastAPI) -> None:
    """Wire the typed exception handlers onto the app."""
    app.add_exception_handler(NodeNotFoundServiceError, _node_not_found)
    app.add_exception_handler(StoryNotFoundServiceError, _story_not_found)
    app.add_exception_handler(WorkspaceEmptyServiceError, _workspace_empty)
    app.add_exception_handler(ParentNotFoundServiceError, _parent_not_found)
    app.add_exception_handler(CycleInMoveServiceError, _cycle_in_move)
    app.add_exception_handler(ValueError, _value_error_handler)
    app.add_exception_handler(InvalidSearchQueryError, _invalid_search_query)
    app.add_exception_handler(
        RequestValidationError, _request_validation_error
    )
