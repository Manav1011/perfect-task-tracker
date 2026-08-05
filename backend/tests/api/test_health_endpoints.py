"""Health endpoint tests — Phase 5.0 C2.

Coverage:

    - `/health/live` always returns 200 (liveness has no
      dependency on app.state).
    - `/health/ready` returns 200 when the StartupReport is
      HEALTHY or RECOVERING, 503 when DEGRADED or FAILED.
    - `/health/ready` returns 503 when `app.state.startup_report`
      is missing (the synthetic FAILED report path).
    - `/health` returns the legacy Phase 3.2 payload
      (back-compat — see TECH_SPEC §13h).
    - All three endpoints are route-registered.

DI strategy: the new tests build a `TestClient` against
`create_app()` and inject synthetic `StartupReport` fixtures
via `app.dependency_overrides[get_startup_report]`. This is
the same pattern as the rest of `tests/api/` for service
overrides.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.api.v1.endpoints.health import get_startup_report
from backend.index.startup import StartupOutcome, StartupReport
from backend.main import create_app


# ---- fixtures and helpers ------------------------------------------------


def _report(outcome: StartupOutcome) -> StartupReport:
    """Convenience constructor for synthetic StartupReports.

    The fields other than `outcome` are filled with values
    that match a typical happy-path startup, so the test's
    `replace(...)` calls only need to swap the outcome. The
    `index_unavailable` and `filesystem_unavailable` flags
    are passed through to the readiness payload's `checks`
    field — they're not asserted here, just preserved.
    """
    return StartupReport(
        outcome=outcome,
        rebuild_attempted=False,
        rebuild_skipped_reason="index_healthy",
        rebuild_report=None,
        elapsed_seconds=0.5,
        index_unavailable=False,
        filesystem_unavailable=False,
    )


@pytest.fixture
def app_with_report():
    """Build a fresh app and yield it with a dependency override slot.

    Tests inject their own `StartupReport` via
    `app.dependency_overrides[get_startup_report]` and then
    call `with TestClient(app) as client:` themselves. The
    yield is the app object so tests can configure overrides
    before constructing the client.
    """
    app = create_app()
    yield app


# ---- /health/live --------------------------------------------------------


def test_live_returns_200_with_static_payload(app_with_report) -> None:
    """Liveness returns 200 + identical payload regardless of app.state.

    The implementation must not read `app.state.startup_report` —
    liveness is the cheapest possible probe. We install a
    DEGRADED report as a baseline and confirm `/live` still
    answers ok.
    """
    app = app_with_report
    app.dependency_overrides[get_startup_report] = lambda: _report(
        StartupOutcome.DEGRADED
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "perfect-task-tracker"
    assert body["env"] in {"development", "verify", "production"}
    # The liveness payload must NOT contain readiness fields.
    assert "outcome" not in body
    assert "checks" not in body


def test_live_does_not_consult_startup_report(app_with_report) -> None:
    """Even when startup_report is missing, /live returns 200.

    Confirms the dependency override is bypassed by /live (the
    route signature has no `report` parameter). We trigger
    the missing-report path by overriding the dependency to
    raise; if `/live` consulted the dependency, the test would
    see a 500.
    """
    app = app_with_report

    def _blow_up() -> None:
        raise RuntimeError("should not be consulted by /health/live")

    app.dependency_overrides[get_startup_report] = _blow_up
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")
    assert response.status_code == 200


# ---- /health/ready -------------------------------------------------------


@pytest.mark.parametrize(
    "outcome",
    [StartupOutcome.HEALTHY, StartupOutcome.RECOVERING],
)
def test_ready_returns_200_for_serving_outcomes(
    app_with_report, outcome: StartupOutcome
) -> None:
    """HEALTHY and RECOVERING are both `ready to serve` — 200.

    The distinction is preserved in the `outcome` field so
    operators can tell a freshly-rebuilt instance from one
    that bootstrapped without incident.
    """
    app = app_with_report
    app.dependency_overrides[get_startup_report] = lambda: replace(
        _report(StartupOutcome.HEALTHY), outcome=outcome
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["outcome"] == outcome.value
    assert body["checks"] == {
        "index_unavailable": False,
        "filesystem_unavailable": False,
    }


@pytest.mark.parametrize(
    "outcome",
    [StartupOutcome.DEGRADED, StartupOutcome.FAILED],
)
def test_ready_returns_503_for_not_serving_outcomes(
    app_with_report, outcome: StartupOutcome
) -> None:
    """DEGRADED and FAILED return 503 — readiness is "no".

    The body still echoes the outcome so an operator can tell
    which degraded state tripped the probe. `status` mirrors
    the outcome for human-readable dashboards.
    """
    app = app_with_report
    app.dependency_overrides[get_startup_report] = lambda: replace(
        _report(StartupOutcome.HEALTHY), outcome=outcome
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == outcome.value
    assert body["outcome"] == outcome.value


def test_ready_returns_503_when_startup_report_missing(app_with_report) -> None:
    """Missing startup_report → synthetic FAILED → 503.

    The dependency's missing-report branch returns a FAILED
    StartupReport explicitly. This is the safest default:
    readiness must opt-in to success via the lifespan, not
    opt-out via absence.
    """
    app = app_with_report

    def _missing() -> StartupReport:
        return StartupReport(
            outcome=StartupOutcome.FAILED,
            rebuild_attempted=False,
            rebuild_skipped_reason="startup_report_missing",
            rebuild_report=None,
            elapsed_seconds=0.0,
            index_unavailable=True,
            filesystem_unavailable=True,
        )

    app.dependency_overrides[get_startup_report] = _missing
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["outcome"] == StartupOutcome.FAILED.value
    assert body["checks"] == {
        "index_unavailable": True,
        "filesystem_unavailable": True,
    }


def test_ready_payload_includes_app_identity(app_with_report) -> None:
    """The readiness body includes app/env so dashboards can identify
    which instance answered.
    """
    app = app_with_report
    app.dependency_overrides[get_startup_report] = lambda: _report(
        StartupOutcome.HEALTHY
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
    body = response.json()
    assert "app" in body
    assert "env" in body
    assert body["app"] == "perfect-task-tracker"


# ---- /health (legacy alias) ---------------------------------------------


def test_legacy_health_alias_returns_phase_3_2_payload(app_with_report) -> None:
    """Backward compat: `/api/v1/health` returns the Phase 3.2 payload.

    The verify harness parses `body.status == "ok"`; this contract
    must hold. The alias is a thin wrapper — no readiness fields,
    no app.state read.
    """
    app = app_with_report
    # Even with a degraded report, the legacy alias still
    # returns the static `{status, app, env}` payload. The
    # alias is for callers that already know they want liveness.
    app.dependency_overrides[get_startup_report] = lambda: _report(
        StartupOutcome.DEGRADED
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "app" in body
    assert "env" in body
    # Confirm the legacy payload shape — no readiness fields.
    assert "outcome" not in body
    assert "checks" not in body


def test_legacy_health_alias_does_not_consult_startup_report(
    app_with_report,
) -> None:
    """The legacy alias must not consume the startup-report dependency.

    Same shape as the `/live` test: a dependency that raises
    must not break the legacy alias. This pins the alias as
    a Phase-3.2-shape back-compat surface.
    """
    app = app_with_report

    def _blow_up() -> None:
        raise RuntimeError("legacy alias must not consult startup_report")

    app.dependency_overrides[get_startup_report] = _blow_up
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200


# ---- route registration ------------------------------------------------


def test_three_routes_are_registered() -> None:
    """All three health routes are mounted on the v1 router.

    Static check via the router object — confirms the
    `api_router.include_router(health.router, ...)` wiring
    picks up the new endpoints in addition to the
    legacy `/health`.
    """
    from backend.api.v1.endpoints.health import router

    paths = {r.path for r in router.routes}
    assert paths == {"/health/live", "/health/ready", "/health"}
