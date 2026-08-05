"""Shared pytest fixtures for the index layer.

The index has two reachable implementations (in-memory and
Postgres-backed) but only one Protocol. Tests cover both with
the same set of contract assertions via parametrization — see
`test_index_repository_contract.py`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from backend.index.impl import InMemoryIndexRepository
from backend.index.protocol import IndexRepository


@pytest.fixture
def in_memory_repo() -> InMemoryIndexRepository:
    """A fresh dict-backed repo, no I/O."""
    return InMemoryIndexRepository()


@pytest.fixture(params=["in_memory"])
def index_repo(request: pytest.FixtureRequest) -> Iterator[IndexRepository]:
    """Parametrize over every concrete IndexRepository.

    Phase 2.0 ships only the in-memory fake as a parametrized
    fixture; the SQLAlchemy path runs against a real Postgres
    in `tests/index/test_sqlalchemy_index_repository.py`. That
    keeps the contract tests fast and DB-free.
    """
    if request.param == "in_memory":
        yield InMemoryIndexRepository()
    else:  # pragma: no cover - guarded by parameter set
        raise AssertionError(f"unknown repo kind: {request.param}")
