"""Commit 1 of Phase 3.3 — Settings immutability.

Settings is `frozen=True`; any assignment after construction must raise.
Tests build fresh instances to vary values (not mutate one in place) —
that's the contract the structural-test layer relies on too.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.config.settings import Settings


def test_settings_can_be_constructed() -> None:
    """Sanity check: default construction still works after the refactor."""
    s = Settings()
    assert s.app_name == "perfect-task-tracker"
    assert s.log_level == "INFO"
    assert s.app_env == "development"


def test_settings_host_and_port_fields_are_removed() -> None:
    """Phase 3.3 split: HOST/PORT are now owned by the process supervisor
    (uvicorn CLI args), not by the application Settings. The fields must
    not exist anymore — this pins the boundary that the rationale
    documents.
    """
    s = Settings()
    assert not hasattr(s, "host"), "Settings.host must be removed in Phase 3.3"
    assert not hasattr(s, "port"), "Settings.port must be removed in Phase 3.3"


def test_settings_is_frozen() -> None:
    """A frozen Pydantic model must reject attribute assignment."""
    s = Settings()
    with pytest.raises(ValidationError):
        s.app_name = "some-other-name"  # type: ignore[misc]


def test_settings_is_frozen_even_for_log_level() -> None:
    """Mutating the LogLevel-validated field must also raise."""
    s = Settings()
    with pytest.raises(ValidationError):
        s.log_level = "DEBUG"  # type: ignore[misc]


def test_settings_can_be_reconstructed_with_new_values() -> None:
    """Frozen ≠ immutable-once-ever — fresh construction still works.
    Tests can vary config without mutating a shared instance.
    """
    s = Settings(app_name="other-app", log_level="DEBUG", app_env="prod")
    assert s.app_name == "other-app"
    assert s.log_level == "DEBUG"
    assert s.app_env == "prod"
