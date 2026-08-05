"""Index layer — concrete IndexRepository implementations.

The Protocol lives in `backend.index.protocol`; this package
holds the implementations. Two exist today:

    - `SQLAlchemyIndexRepository` — production, Postgres-backed.
    - `InMemoryIndexRepository`   — for tests; no I/O.

DI happens in `backend.api.dependencies` (Phase 3+ when the
index is wired into the application factory). For Phase 2.0
the index is constructed directly by tests and future scripts.
"""

from backend.index.impl.in_memory_index_repository import InMemoryIndexRepository
from backend.index.impl.sqlalchemy_index_repository import SQLAlchemyIndexRepository

__all__ = ["InMemoryIndexRepository", "SQLAlchemyIndexRepository"]
