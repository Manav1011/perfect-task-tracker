"""Index layer value types — wire-shape between the domain and the index.

These are dataclasses, not Pydantic models, because nothing here
crosses an HTTP boundary. The API layer reaches the index only
through future read endpoints; when those land, they map
`IndexRecord` → a Pydantic DTO in `backend.schemas`.

Per ADR-0011, the index is a derived projection of the domain, not
a second source of truth. The fields here are exactly what's
queryable in SQL — nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.domain.node import NodeId


@dataclass(slots=True, frozen=True)
class IndexRecord:
    """A single row's worth of queryable metadata for one Node.

    Every field is stable per-Node: timestamps move forward with
    updates, but `node_id` never changes (Invariant §6). The
    record is `frozen` so callers can't accidentally mutate
    values that originate from the database.

    Field semantics:

        - `node_id`     — stable UUID; primary key in the index.
        - `story_id`    — the root Story's UUID, or `None` for
                          orphan nodes (only briefly valid during
                          a move). Used to scope searches by
                          workspace region.
        - `parent_id`   — current parent or `None` for roots.
        - `title`       — current display title.
        - `node_type`   — discriminator string ("story" / "task" /
                          "note"). Stored as text rather than a SQL
                          enum so adding a NodeType in the future
                          doesn't require a migration.
        - `filesystem_path` — relative path of the Node's directory
                          on disk (e.g. `story-a/child-of-a`).
                          Stored so an out-of-band rebuild can
                          re-locate the Node without re-walking
                          the tree. Indexes store this; they do
                          not use it for queries.
        - `created_at`  — first write timestamp; never updated.
        - `updated_at`  — last write timestamp; updated on every
                          successful index write.
        - `search_text` — reserved column for full-text search
                          (Phase 4+). Phase 2.0 leaves it empty.
    """

    node_id: NodeId
    parent_id: NodeId | None
    story_id: NodeId | None
    title: str
    node_type: str
    filesystem_path: str
    created_at: datetime
    updated_at: datetime
    search_text: str = ""

    def __post_init__(self) -> None:
        # Title invariant mirrors the domain Node: empty title is a
        # bug in whatever populated the index.
        if not self.title or not self.title.strip():
            raise ValueError(
                f"IndexRecord.title must be non-empty (node_id={self.node_id})"
            )
