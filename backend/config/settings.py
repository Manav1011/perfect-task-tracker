"""Pydantic Settings — single source of configuration for the backend.

Reads from environment variables and the local `.env` file. Anything that
needs to vary between dev, test, and prod lives here.

Hot-reload policy (V1, Phase 3.3): every setting is RESTART ONLY.
Construction-time validation is the bound on misuse. See TECH_SPEC §13g
and ADR-0021 for the rationale.

Process boundary: this module owns APPLICATION configuration only. The
process supervisor (uvicorn CLI) owns host/port. Settings that the
supervisor consumes (HOST, PORT) are NOT exported here. See
`scripts/verify_backend.sh` and `.env.example` for the supervisor-side
contract.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.config.database import DatabaseSettings


# Allowed log levels — single source of truth for the validator below
# AND for the structlog/stdlib mapping in `backend.core.logging`.
# Changing this list is a deliberate API change.
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Application settings — frozen, validated.

    Field names map to env vars (case-insensitive). Values in `.env` are
    loaded only when `env_file` is found relative to the working dir.

    The model is `frozen=True`: once constructed at startup, no field
    can be mutated. Tests can still build a fresh `Settings(...)`
    instance with different values.

    `log_level` is constrained to the `LogLevel` Literal — unknown
    levels are rejected at Pydantic construction time. Previously
    `configure_logging()` silently coerced unknown values to INFO;
    that fallback is removed (Phase 3.3, Bug-fix replacement).

    Database: `database: DatabaseSettings` is the sub-model that owns
    the POSTGRES_* / DATABASE_URL precedence contract. See
    `backend.config.database`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        populate_by_name=True,  # allow field_name kwargs (e.g. log_level=...) alongside env-alias kwargs
    )

    # ---- Application identity (startup; health endpoint surface) -----
    app_env: str = Field(default="development", alias="APP_ENV")
    app_name: str = Field(default="perfect-task-tracker", alias="APP_NAME")
    log_level: LogLevel = Field(default="INFO", alias="LOG_LEVEL")

    # ---- Filesystem substrate (startup; WorkspaceRoot.open reads this) -----
    # Default `./data/workspace` matches the pre-Phase-3.3 hardcoded value in
    # `backend/api/dependencies.py` for back-compat. The verify harness
    # overrides this with `WORKSPACE_PATH=data/verify_workspace`.
    workspace_path: Path = Field(
        default=Path("./data/workspace"), alias="WORKSPACE_PATH"
    )

    # ---- Database (sub-model with explicit-or-synthesised DSN) -----
    # Nested model gives the precedence rule its own home (see the
    # module docstring at backend.config.database). The lifetime of the
    # constructed DatabaseSettings matches the lifetime of Settings
    # (frozen at startup).
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)

    @property
    def database_url(self) -> str:
        """Convenience accessor for the resolved DSN.

        `backend.database.session` and `alembic/env.py` historically
        read `settings.database_url` directly. Rather than rewrite
        every consumer, we expose the resolved URL via this property.
        Sub-model field holds the synthesis; this property is the
        public read API.
        """
        url = self.database.database_url
        assert url is not None, (
            "DatabaseSettings invariant: _resolve_database_url must "
            "always populate database_url (default or explicit)."
        )
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor.

    Cached so we don't re-parse .env on every import. Tests that need
    to vary config should clear the cache or instantiate Settings directly.
    """
    return Settings()
