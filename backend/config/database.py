"""Database URL synthesis — Phase 3.3.

Precedence rule:

    1. DATABASE_URL set explicitly   → use it.
    2. DATABASE_URL unset, all five  → synthesise DATABASE_URL from
       POSTGRES_* family set            POSTGRES_*.
                                        This is the new convenience path
                                        for setups that already export the
                                        POSTGRES_* family (e.g. docker
                                        compose, k8s ConfigMaps).
    3. DATABASE_URL unset, POSTGRES_* → ConfigError at construction:
       partial                       the operator must commit to either a
                                       full POSTGRES_* set or an explicit
                                       DSN. Partial-and-borrow leads to
                                       silent "best-effort" DSNs that
                                       surprise users later.
    4. DATABASE_URL unset, no         → use the default `database_url`
       POSTGRES_*                       (localhost:5433 / ptt — dev-only).

DEFAULT_DEV_URL documents the dev-only fallback. Production deploys
MUST set either an explicit DATABASE_URL or the full POSTGRES_* set.
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Used by the validator when synthesising a DSN. The driver prefix has
# to include `+psycopg` so SQLAlchemy picks the psycopg3 dialect; plain
# `postgresql://...` would default to psycopg2.
_DRIVER_PREFIX = "postgresql+psycopg"


class DatabaseSettings(BaseSettings):
    """Database connection — explicit DSN or POSTGRES_* family.

    Either DATABASE_URL OR all five POSTGRES_* fields must resolve to
    a connectable DSN. The validator enforces that contract.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    # ---- Explicit DSN (canonical — wins when present) -----
    database_url: Optional[str] = Field(default=None, alias="DATABASE_URL")

    # ---- POSTGRES_* family -----
    postgres_host: Optional[str] = Field(default=None, alias="POSTGRES_HOST")
    postgres_port: Optional[int] = Field(default=None, alias="POSTGRES_PORT")
    postgres_db: Optional[str] = Field(default=None, alias="POSTGRES_DB")
    postgres_user: Optional[str] = Field(default=None, alias="POSTGRES_USER")
    postgres_password: Optional[str] = Field(
        default=None, alias="POSTGRES_PASSWORD"
    )

    @model_validator(mode="after")
    def _resolve_database_url(self) -> "DatabaseSettings":
        """Apply the precedence rule documented at the top of this module."""
        if self.database_url:
            # Case 1: explicit DSN wins. Do not look at POSTGRES_*.
            return self

        pg_fields = (
            self.postgres_host,
            self.postgres_port,
            self.postgres_db,
            self.postgres_user,
            self.postgres_password,
        )
        pg_set = [v for v in pg_fields if v is not None]

        if not pg_set:
            # Case 4: nothing set — fall back to the dev default.
            # Match the pre-Phase-3.3 default so behaviour is unchanged
            # for any dev that doesn't set DATABASE_URL or POSTGRES_*.
            object.__setattr__(
                self,
                "database_url",
                f"{_DRIVER_PREFIX}://ptt:ptt@localhost:5433/ptt",
            )
            return self

        if len(pg_set) != len(pg_fields):
            # Case 3: partial POSTGRES_* set — refuse to guess.
            missing = [
                name
                for name, v in zip(
                    (
                        "POSTGRES_HOST",
                        "POSTGRES_PORT",
                        "POSTGRES_DB",
                        "POSTGRES_USER",
                        "POSTGRES_PASSWORD",
                    ),
                    pg_fields,
                )
                if v is None
            ]
            raise ValueError(
                "DATABASE_URL is not set and POSTGRES_* is partial. "
                f"Missing: {', '.join(missing)}. "
                "Either set DATABASE_URL explicitly, or set all five "
                "POSTGRES_* fields."
            )

        # Case 2: full POSTGRES_* set — synthesise the DSN.
        object.__setattr__(
            self,
            "database_url",
            (
                f"{_DRIVER_PREFIX}://{self.postgres_user}:"
                f"{self.postgres_password}@{self.postgres_host}:"
                f"{self.postgres_port}/{self.postgres_db}"
            ),
        )
        return self
