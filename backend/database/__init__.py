"""Database wiring.

This package owns:
    - SQLAlchemy engine + session factory.
    - Alembic configuration for migrations.
    - Declarative base for ORM models.

It does NOT contain business logic. Models live in `backend.models`.
"""