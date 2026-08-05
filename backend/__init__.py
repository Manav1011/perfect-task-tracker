"""PerfectTaskTracker backend.

Layering (see docs/TECH_SPEC.md §6):
    api/         — HTTP routes; request validation, serialization. No business logic.
    services/    — Business logic and mutation orchestration. All writes route through here.
    repositories/— DB access for the Postgres index. Pure SQL.
    models/      — SQLAlchemy ORM models (Postgres side of the world).
    schemas/     — Pydantic request/response models for the API layer.
    database/    — Engine, session factory, Alembic wiring.
    filesystem/  — Disk I/O for the source of truth. Empty in Phase 1.0.
    workspace/   — In-memory tree mirroring the workspace. Empty in Phase 1.0.
    graph/       — Graph projection built from the tree. Empty in Phase 1.0.
    search/      — Full-text search over the Postgres index. Empty in Phase 1.0.
    events/      — In-process pub/sub for layer-to-layer signals. Empty in Phase 1.0.
    core/        — Cross-cutting primitives (logging, errors, IDs). No business logic.
    config/      — Pydantic Settings; reads .env.
    utils/       — Pure helpers. No I/O, no business logic.
    tests/       — pytest suite.
"""

__version__ = "0.1.0"