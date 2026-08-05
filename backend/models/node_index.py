"""SQLAlchemy ORM model — `node_index` table.

This is the ONLY table in the index schema for Phase 2.0. Every
column is a queryable field requested in the Phase 2.0 brief.

Per ADR-0011:

    - The Postgres index holds *indexable* metadata, not the
      authoritative data. The filesystem is the source of truth;
      this table can be dropped and rebuilt at any time.
    - No business logic lives in the model. No relationships, no
      cascade rules, no constraints beyond what the index needs
      to do its one job (look up data by `node_id`, scope by
      `story_id`, ORDER BY relevance later).
    - NodeType is stored as a free-form string (with a CHECK
      constraint at the DB level) — adding a new type should
      not require a schema migration.

What this model explicitly does NOT have:

    - No `ForeignKey` to anything else. There are no other tables
      in Phase 2.0; even when more arrive, the source of truth is
      the filesystem, not the database. Foreign keys would imply
      referential integrity we don't actually own.
    - No `children` relationship / back-population. Tree
      structure lives in the filesystem.
    - No triggers, no views, no server-side defaults besides
      timestamps.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base


class NodeIndexRow(Base):
    """One row per Node, holding only the metadata needed to query.

    The `node_id` column is the primary key; `story_id` and
    `parent_id` are nullable because roots have no parent and
    briefly-orphan nodes have no story during a move.
    """

    __tablename__ = "node_index"

    node_id: Mapped[str] = mapped_column(
        String(36),  # UUID4-as-string; 36 chars with hyphens.
        primary_key=True,
        nullable=False,
    )
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    story_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    filesystem_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    # Reserved for full-text search (Phase 4+). Empty in Phase 2.0.
    search_text: Mapped[str] = mapped_column(
        String, nullable=False, server_default="", default=""
    )

    __table_args__ = (
        # Discriminator stays within the closed set the domain
        # currently knows. Adding a NodeType means adding a string
        # here — no schema change required, just data.
        CheckConstraint(
            "node_type IN ('story', 'task', 'note')",
            name="ck_node_index_node_type",
        ),
        # The most common scoped query in the future is
        # "everything under story X" — index it now so the
        # search phase doesn't need a migration later.
        Index("ix_node_index_story_id", "story_id"),
        # Secondary access pattern: parent's children.
        Index("ix_node_index_parent_id", "parent_id"),
        # Reserved FTS column — btree is fine until we add a
        # tsvector index; the column is empty in Phase 2.0.
        Index("ix_node_index_search_text", "search_text"),
    )
