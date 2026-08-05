"""Commit 1 of Phase 3.3 — log_level Literal validator.

Settings.log_level is constrained to the `LogLevel` Literal:
{DEBUG, INFO, WARNING, ERROR, CRITICAL}. Pydantic rejects anything else
at construction time. Previously `configure_logging()` silently coerced
unknown values to INFO; that fallback is removed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.config.settings import Settings


@pytest.mark.parametrize(
    "level",
    ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
)
def test_settings_accepts_each_log_level(level: str) -> None:
    """Every documented LogLevel must be accepted."""
    s = Settings(log_level=level)  # type: ignore[arg-type]
    assert s.log_level == level


@pytest.mark.parametrize(
    "level",
    [
        "",                       # empty
        "INFO ",                  # trailing whitespace
        "info",                   # lowercase
        "DEBG",                   # typo
        "VERBOSE",                # unsupported
        "10",                     # numeric string
        "NOPE",                   # random
    ],
)
def test_settings_rejects_unknown_log_level(level: str) -> None:
    """Unknown levels are rejected at construction time. Previously
    `configure_logging()` did `getattr(logging, level.upper(), INFO)`
    and silently degraded to INFO — that failure mode is now a hard
    422-equivalent at startup.
    """
    with pytest.raises(ValidationError):
        Settings(log_level=level)  # type: ignore[arg-type]


def test_default_log_level_is_info() -> None:
    """The default must remain INFO (back-compat with the verify harness)."""
    s = Settings()
    assert s.log_level == "INFO"
