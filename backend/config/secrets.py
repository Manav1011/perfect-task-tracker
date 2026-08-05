"""Secrets sub-model — Phase 5.0 C1.

Construction rule (env-gated):

    app_env == "development"  →  secrets may be unset (None or empty
                                  string). No failure.
    app_env != "development"  →  both SESSION_SECRET and CSRF_SECRET
                                  must resolve to non-empty values,
                                  or ValueError raises at Settings()
                                  construction.

The seam exists before any consumer wires it (auth is Phase 5.4
future). The rule enforces in vivo at construction time so the
behaviour is testable, not just promised.

Read pattern note: this module is the SOLE reader of
`SESSION_SECRET` / `CSRF_SECRET` env vars. Pydantic BaseSettings
reads them via the env bridge — `os.environ` is touched exactly
once via `os.getenv("APP_ENV", ...)` to enforce the gating rule.
`tests/test_config_isolation.py` AST-fails any direct env-var read
outside this module's allowlist entry.
"""

from __future__ import annotations

import os
from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pydantic import SecretStr


class Secrets(BaseSettings):
    """Auth-time secrets — typed, frozen, env-gated.

    `Session_secret` and `csrf_secret` use Pydantic `SecretStr` so the
    unwrapped value is not accidentally logged or echoed in tracebacks.

    Construction rules:

    - In `app_env == "development"` (the default), both fields may be
      `None` or empty-string — no failure.
    - In any other `app_env`, both must be set to non-empty values, or
      `ValueError` raises from the `@model_validator`.

    No consumers wire `Secrets` today; the seam exists so the rules
    are enforced and the next milestone (Phase 5.4 Auth) can build
    against a frozen, validated boundary.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    session_secret: Optional[SecretStr] = Field(
        default=None, alias="SESSION_SECRET"
    )
    csrf_secret: Optional[SecretStr] = Field(
        default=None, alias="CSRF_SECRET"
    )

    @model_validator(mode="after")
    def _enforce_secrets_in_non_development(self) -> "Secrets":
        """Enforce the env-gating rule documented at the top of this
        module.

        `APP_ENV` is the only env var this module reads directly,
        and only here. Pydantic BaseSettings handles the secrets
        aliases via the env bridge; we read `APP_ENV` here to apply
        the gating rule.

        Empty string is treated as missing (defensive — a blank
        `.env` line in a non-dev env must not silently pass).
        """
        # `os.getenv` is the one explicit env-var read in this module
        # and is the docstring-prescribed exception. The isolation test
        # (`tests/test_config_isolation.py`) allowlists this file.
        app_env = os.getenv("APP_ENV", "development")
        if app_env == "development":
            return self

        missing: list[str] = []
        if self.session_secret is None or not self.session_secret.get_secret_value():
            missing.append("SESSION_SECRET")
        if self.csrf_secret is None or not self.csrf_secret.get_secret_value():
            missing.append("CSRF_SECRET")

        if missing:
            raise ValueError(
                "Settings.secrets invariant: in app_env="
                f"{app_env!r}, the following must be set to non-empty "
                f"values: {', '.join(missing)}. Update the runtime "
                "environment (or your .env file) and retry construction."
            )
        return self
