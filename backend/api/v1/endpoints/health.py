"""Health endpoints — Phase 5.0 C2.

Three endpoints under `/api/v1/health`:

| Path               | Purpose            | Status mapping                             |
| ------------------ | ------------------ | ------------------------------------------ |
| `/health/live`     | Liveness probe     | always 200 (process is up)                 |
| `/health/ready`    | Readiness probe    | 200 if `app.state.startup_report` healthy; |
|                    |                    | 503 if degraded or failed.                 |
| `/health`          | Back-compat alias  | thin wrapper over `/health/ready`          |

Liveness is intentionally trivial: it answers only "is the process
alive and the router stack responding?" — no I/O, no
`app.state` reads, no settings touches beyond the same
identifier pair the legacy endpoint already returned.

Readiness is the *only* probe that consults
`app.state.startup_report`. It does NOT probe the database,
filesystem, index, cache, or any downstream dependency. Those
checks belong to a future phase (5.1 Observability, plus the
pending dependency-health milestone). The contract is pinned
in ADR-0022 and TECH_SPEC §13h.

Why an alias for `/health` instead of a hard rename:
the verify harness and any external smoke-check caller built
across Phases 2–3 already hit `/api/v1/health`. Breaking that
path would break the existing Phase 3.2 verify evidence
captured at `data/verify_evidence/`. The alias preserves the
backward contract for one release; the canonical names
(`/health/live`, `/health/ready`) are the new contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel

from backend.config.settings import Settings, get_settings
from backend.index.startup import StartupOutcome, StartupReport

router = APIRouter()


# ---- response shapes ------------------------------------------------------


class HealthResponse(BaseModel):
    """Liveness payload (unchanged from Phase 3.2).

    Static payload: identifies the process and its environment.
    No `app.state` reads, no I/O. The intent is to give a
    Kubernetes-style liveness probe something cheap to poll.
    """

    status: str
    app: str
    env: str


class ReadinessResponse(BaseModel):
    """Readiness payload.

    `status` mirrors the HTTP status code as a string for
    human-readable logs and dashboards. `outcome` is the raw
    `StartupReport.outcome` value so an operator can
    distinguish HEALTHY from RECOVERING without parsing prose.
    `checks` is a flat summary of the `StartupReport` fields
    that drove the decision (kept narrow on purpose — the
    full stage list belongs in logs, not in the response body).
    """

    status: str
    app: str
    env: str
    outcome: str
    checks: dict[str, bool]


# ---- startup-report dependency -------------------------------------------
#
# A small DI seam so tests can override the report without
# rewriting app.state directly. Production reads the real
# lifespan-installed report; tests inject a synthetic one.


def get_startup_report(request: Request) -> StartupReport:
    """Return the StartupReport stashed by the lifespan.

    The dependency is intentionally small — it reads
    `app.state.startup_report` and returns it. Tests override
    this with `app.dependency_overrides[get_startup_report]`
    to inject a synthetic `StartupReport` (e.g. a DEGRADED
    fixture) without re-booting the lifespan.

    If `app.state.startup_report` is missing (e.g. when
    running outside the lifespan, or during a partial
    startup), the endpoint is treated as FAILED — readiness
    is "no" until startup actually finished installing the
    report. This is the safest default for a probe.
    """
    report: StartupReport | None = getattr(request.app.state, "startup_report", None)
    if report is None:
        # All-zero FAILED report — readiness must explicitly
        # opt-in to success. Mirrors the case where lifespan
        # is still running.
        return StartupReport(
            outcome=StartupOutcome.FAILED,
            rebuild_attempted=False,
            rebuild_skipped_reason="startup_report_missing",
            rebuild_report=None,
            elapsed_seconds=0.0,
            index_unavailable=True,
            filesystem_unavailable=True,
        )
    return report


# ---- endpoints ------------------------------------------------------------


@router.get("/health/live", response_model=HealthResponse)
def health_live(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Liveness probe — always 200 if the process is alive.

    Intentionally cheap. No I/O, no `app.state` reads. The
    payload identifies the process (app, env) so an operator
    can verify which instance answered.
    """
    return HealthResponse(status="ok", app=settings.app_name, env=settings.app_env)


@router.get("/health/ready", response_model=ReadinessResponse)
def health_ready(
    response: Response,
    settings: Settings = Depends(get_settings),
    report: StartupReport = Depends(get_startup_report),
) -> ReadinessResponse:
    """Readiness probe — reflects the StartupReport stored on app.state.

    Mapping:

        HEALTHY    → 200, status="ok"
        RECOVERING → 200, status="ok" (rebuild succeeded; serving)
        DEGRADED   → 503, status="degraded"
        FAILED     → 503, status="failed"

    The split between HEALTHY and RECOVERING is intentional:
    a healthy startup that had to rebuild is still *ready to
    serve* requests. Only DEGRADED / FAILED report "not ready".
    The distinction is preserved in the `outcome` field so an
    operator can tell the difference without re-reading the
    raw report.

    Dependency probes (database, filesystem, index, cache) are
    deliberately NOT performed here. The StartupReport already
    encodes those results at startup time; re-probing them
    per-request would be the dependency-health milestone, not
    a readiness probe. See ADR-0022.

    Implementation note: the readiness response body is
    always a `ReadinessResponse` (FastAPI's `response_model`).
    The HTTP status code is set on the injected `Response`
    instance so the model's declarative shape is preserved
    for OpenAPI; the alternative — returning a `JSONResponse`
    directly — would defeat the response_model contract.
    """
    ready = _is_ready(report.outcome)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ok" if ready else report.outcome.value,
        app=settings.app_name,
        env=settings.app_env,
        outcome=report.outcome.value,
        checks={
            "index_unavailable": report.index_unavailable,
            "filesystem_unavailable": report.filesystem_unavailable,
        },
    )


@router.get("/health", response_model=HealthResponse)
def health_alias(
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    """Back-compat alias for the legacy `/health` payload.

    Phase 3.2 health-check payload: `{status, app, env}`.
    The readiness state is *not* included here — the alias
    is for callers that already know the answer is "ok" and
    only want to confirm the process is up. Callers that need
    the readiness signal should switch to `/health/ready`.
    """
    return HealthResponse(status="ok", app=settings.app_name, env=settings.app_env)


# ---- internal helpers ----------------------------------------------------


def _is_ready(outcome: StartupOutcome) -> bool:
    """True when the outcome means the app is serving requests.

    HEALTHY and RECOVERING both return True. DEGRADED and
    FAILED return False. The split is intentional: a
    rebuild-after-startup that succeeded is still ready.
    """
    return outcome in (StartupOutcome.HEALTHY, StartupOutcome.RECOVERING)
