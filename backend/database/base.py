"""SQLAlchemy declarative base for ORM models.

Models declare `__tablename__` and inherit from `Base`. Kept separate
from session wiring so Alembic's autogenerate can import models
without booting the engine.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Project-wide declarative base."""