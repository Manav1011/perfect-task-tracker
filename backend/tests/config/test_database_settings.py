"""Commit 3 of Phase 3.3 — DatabaseSettings POSTGRES_* synthesis.

Precedence rule (from `backend.config.database`):

  1. DATABASE_URL set explicitly   → use it (never look at POSTGRES_*).
  2. DATABASE_URL unset, all five
     POSTGRES_* set                → synthesise the DSN.
  3. DATABASE_URL unset, partial
     POSTGRES_*                    → ConfigError (ValueError at Pydantic
                                       construction; we surface as such).
  4. DATABASE_URL unset, no
     POSTGRES_*                    → default dev DSN
                                       (postgresql+psycopg://ptt:ptt@localhost:5433/ptt).
"""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from backend.config.database import DatabaseSettings


def _none_db() -> dict:
    """Return kwargs that set NO postgres_* fields — used to isolate
    each precedence case in isolation from the test environment."""
    return {
        "database_url": None,
        "postgres_host": None,
        "postgres_port": None,
        "postgres_db": None,
        "postgres_user": None,
        "postgres_password": None,
    }


# ---- Case 1: explicit DATABASE_URL wins --------------------------------


def test_explicit_database_url_wins_over_partial_postgres() -> None:
    """Even if some POSTGRES_* are set, DATABASE_URL is canonical."""
    s = DatabaseSettings(
        database_url="postgresql+psycopg://explicit:url@host:5432/db",
        postgres_host="ignored",  # partial POSTGRES_* — must be ignored
    )
    assert s.database_url == "postgresql+psycopg://explicit:url@host:5432/db"


def test_explicit_database_url_wins_over_full_postgres() -> None:
    """A complete POSTGRES_* set still loses to DATABASE_URL when both
    are present (operator intent is unambiguous)."""
    s = DatabaseSettings(
        database_url="postgresql+psycopg://e:e@h:1/d",
        postgres_host="h",
        postgres_port=2,
        postgres_db="d",
        postgres_user="u",
        postgres_password="p",
    )
    assert s.database_url == "postgresql+psycopg://e:e@h:1/d"


# ---- Case 2: synthesise from full POSTGRES_* ---------------------------


def test_full_postgres_synthesises_dsn() -> None:
    s = DatabaseSettings(
        database_url=None,
        postgres_host="db.example",
        postgres_port=5432,
        postgres_db="mydb",
        postgres_user="alice",
        postgres_password="secret",
    )
    assert (
        s.database_url
        == "postgresql+psycopg://alice:secret@db.example:5432/mydb"
    )


def test_full_postgres_with_int_port() -> None:
    """POSTGRES_PORT is an int — values come from env (string) and Pydantic
    coerces them. The synthesised DSN must not add quotes around the port."""
    s = DatabaseSettings(
        database_url=None,
        postgres_host="h",
        postgres_port=15432,
        postgres_db="d",
        postgres_user="u",
        postgres_password="p",
    )
    assert s.database_url.endswith(":15432/d")


# ---- Case 3: partial POSTGRES_* raises --------------------------------


@pytest.mark.parametrize(
    "missing",
    [
        ("postgres_host", None),
        ("postgres_port", None),
        ("postgres_db", None),
        ("postgres_user", None),
        ("postgres_password", None),
    ],
)
def test_partial_postgres_raises(missing) -> None:
    """Any single missing POSTGRES_* field ⇒ startup-time error."""
    kwargs = {
        "database_url": None,
        "postgres_host": "h",
        "postgres_port": 5432,
        "postgres_db": "d",
        "postgres_user": "u",
        "postgres_password": "p",
        missing[0]: missing[1],
    }
    with pytest.raises(ValidationError) as exc_info:
        DatabaseSettings(**kwargs)
    msg = str(exc_info.value)
    # The error message should mention which fields are missing, so an
    # operator reading startup logs can fix the configuration.
    assert "POSTGRES_" in msg or "DATABASE_URL" in msg


# ---- Case 4: nothing set → dev default --------------------------------


def test_nothing_set_falls_back_to_dev_default() -> None:
    """Untouched config keeps the pre-Phase-3.3 default DSN."""
    s = DatabaseSettings(**_none_db())
    assert s.database_url == "postgresql+psycopg://ptt:ptt@localhost:5433/ptt"


# ---- Cross-cutting: settings.database_url property ----------------------


def test_settings_database_url_property_propagates() -> None:
    """The `Settings.database_url` property reads through to the sub-model.
    Top-level `database_url` is not a kwarg — pass `database=...` with a
    populated `DatabaseSettings` instead."""
    from backend.config.settings import Settings

    s = Settings(
        database=DatabaseSettings(
            database_url="postgresql+psycopg://top:tp@top:1/top",
        )
    )
    assert s.database_url == "postgresql+psycopg://top:tp@top:1/top"


def test_settings_default_database_url_property() -> None:
    """Settings().database_url falls through to the dev default."""
    from backend.config.settings import Settings

    s = Settings()
    assert s.database_url == "postgresql+psycopg://ptt:ptt@localhost:5433/ptt"


# ---- Env-var resolution --------------------------------------------------


def test_database_url_resolved_from_env(monkeypatch) -> None:
    """DATABASE_URL env var resolves into the sub-model field."""
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://env:env@env:1234/envdb"
    )
    s = DatabaseSettings()
    assert s.database_url == "postgresql+psycopg://env:env@env:1234/envdb"
