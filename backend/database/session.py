"""SQLAlchemy 2.x engine and session factory.

The Postgres connection is to the *index* (rebuildable from disk), per
TECH_SPEC §7. The engine is lazy: it's only created on first request for
a session, so the app can boot even if Postgres is temporarily
unreachable.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config.settings import get_settings

_engine: Optional[Engine] = None
_SessionFactory: Optional[sessionmaker[Session]] = None


def get_engine() -> Engine:
    """Lazy singleton engine.

    `pool_pre_ping=True` survives DB restarts (relevant for compose dev).
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Lazy singleton session factory."""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return _SessionFactory


def session_scope() -> Iterator[Session]:
    """Context-managed session for ad-hoc scripts and Alembic.

    Yields a session and commits on success / rolls back on exception.
    API code should use FastAPI's dependency-injected session instead.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()