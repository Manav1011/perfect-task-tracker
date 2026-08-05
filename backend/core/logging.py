"""Structured logging via structlog.

Configures a single JSON-renderer pipeline suitable for container output.
Call `configure_logging()` once at application startup with the validated
`settings.log_level` (a `LogLevel` Literal, see `backend.config.settings`).
"""

from __future__ import annotations

import logging
import sys

import structlog

from backend.config.settings import LogLevel


def configure_logging(level: LogLevel) -> None:
    """Configure structlog + stdlib logging.

    `level` MUST be a validated `LogLevel` value. Construction-time
    validation at `Settings` layer rejects unknown levels — this
    function trusts its input. No silent coercion to INFO
    (Phase 3.3 cleanup: previously `LOG_LEVEL=DEBG` silently became INFO).

    Output is JSON to stdout. Each log record carries: timestamp, level,
    logger name, event, and any kw args passed to the logger.
    """
    # Direct int mapping; level is pre-validated by Pydantic Literal.
    _LEVEL_MAP = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    log_level = _LEVEL_MAP[level]  # type: ignore[index]

    # Route stdlib logs through structlog's renderer so output is uniform.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Module-level logger accessor."""
    return structlog.get_logger(name)
