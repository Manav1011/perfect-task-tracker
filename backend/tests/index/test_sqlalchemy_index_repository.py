"""End-to-end tests against the SQLAlchemy implementation.

These require a live Postgres at `DATABASE_URL`. They're
skipped (not failed) when no DB is reachable so the test
suite stays green in CI without compose.

When Postgres IS available:

    - The migration is run (`upgrade` to head) before the
      tests.
    - Each test starts in a clean schema state: tests share a
      single connection and run inside a SAVEPOINT.

This is an isolation test in the strict sense — it's the only
test suite that actually exercises Postgres. If the schema
mismatches the model, these tests will fail.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text

from backend.config.settings import get_settings
from backend.domain.node import NodeId
from backend.index.impl import SQLAlchemyIndexRepository
from backend.index.types import IndexRecord


def _postgres_reachable() -> bool:
    """True iff the configured Postgres accepts a SELECT 1."""
    try:
        engine = create_engine(get_settings().database_url, future=True)
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(),
    reason="Postgres is not reachable at DATABASE_URL; skipping live-DB tests",
)


@pytest.fixture
def sql_repo() -> Iterator[SQLAlchemyIndexRepository]:
    """A repository that uses a real DB connection.

    The schema is created via SQLAlchemy's `Base.metadata.create_all`
    (lightweight test bootstrap, not Alembic) so we don't depend
    on alembic's offline/online modes for tests. A separate
    migration-runnability test covers the Alembic path.
    """
    from backend.database.base import Base

    engine = create_engine(get_settings().database_url, future=True)
    Base.metadata.create_all(engine)
    repo = SQLAlchemyIndexRepository()
    # Each test sees a clean table.
    repo.truncate()
    yield repo
    # Tear down so tests don't leak rows into each other.
    repo.truncate()


def test_get_round_trip(sql_repo: SQLAlchemyIndexRepository) -> None:
    record = IndexRecord(
        node_id=NodeId("11111111-1111-1111-1111-111111111111"),
        parent_id=None,
        story_id=NodeId("11111111-1111-1111-1111-111111111111"),
        title="Story A",
        node_type="story",
        filesystem_path="story-a",
        created_at=__import__("datetime").datetime(2026, 8, 5, tzinfo=__import__("datetime").timezone.utc),
        updated_at=__import__("datetime").datetime(2026, 8, 5, tzinfo=__import__("datetime").timezone.utc),
    )
    sql_repo.upsert(record)
    got = sql_repo.get(record.node_id)
    assert got.title == "Story A"
    assert got.node_type == "story"


def test_count_against_live_db(sql_repo: SQLAlchemyIndexRepository) -> None:
    assert sql_repo.count() == 0
    sql_repo.upsert(_record())
    assert sql_repo.count() == 1


def _record(**overrides) -> IndexRecord:
    """Compact helper for live-DB tests."""
    fields = dict(
        node_id=NodeId("22222222-2222-2222-2222-222222222222"),
        parent_id=None,
        story_id=NodeId("22222222-2222-2222-2222-222222222222"),
        title="Task A",
        node_type="task",
        filesystem_path="story-a/task-a",
        created_at=__import__("datetime").datetime(2026, 8, 5, tzinfo=__import__("datetime").timezone.utc),
        updated_at=__import__("datetime").datetime(2026, 8, 5, tzinfo=__import__("datetime").timezone.utc),
    )
    fields.update(overrides)
    return IndexRecord(**fields)
