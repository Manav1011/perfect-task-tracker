"""SQLAlchemy ORM models for the Postgres index.

Phase 2.0: `NodeIndexRow` — one row per Node, holding only
the metadata the index needs to serve queries. No business
logic, no relationships, no foreign keys (TECH_SPEC §7, ADR-0011).

Alembic autogenerate imports from this package via env.py.
Add new tables here; migrations pick them up automatically.
"""

from backend.models.node_index import NodeIndexRow

__all__ = ["NodeIndexRow"]
