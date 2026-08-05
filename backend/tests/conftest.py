"""Test fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app


@pytest.fixture
def client() -> TestClient:
    """Synchronous test client against the app factory.

    Phase 1.0 has no DB or filesystem dependencies, so the default app
    boots cleanly under TestClient without overrides.
    """
    return TestClient(create_app())