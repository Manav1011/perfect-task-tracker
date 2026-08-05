"""Search benchmarks — baseline numbers.

Per ChatGPT's Phase 3.1 acceptance criteria: include
benchmark numbers for exact lookup, prefix lookup, and
paginated listing on representative datasets in the
completion report.

We measure `DefaultSearchService.search(...)` against
the in-memory index. The numbers are the *ceiling* —
the SQL-backed index will be at-or-below these for
small datasets and significantly below for large ones
(once Phase 3.2 adds per-query repo methods).

Run with:
    uv run pytest backend/tests/benchmarks/test_search_benchmark.py --benchmark-disable-gc -s

The `-s` flag prints the timing summary pytest-benchmark
produces. Without pytest-benchmark installed, the tests
still run and report a wall-clock timing to stdout via
`print`.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.domain.node import NodeId
from backend.index.impl import InMemoryIndexRepository
from backend.index.types import IndexRecord
from backend.search import DefaultSearchService, SearchRequest, SearchSort


def _ts(offset_seconds: float = 0.0) -> datetime:
    base = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=offset_seconds)


def _seed_index(count: int) -> InMemoryIndexRepository:
    """Seed an index with `count` records. Titles are
    `Node-{i:05d}` so prefix queries can match a known
    fraction.
    """
    index = InMemoryIndexRepository()
    for i in range(count):
        rec = IndexRecord(
            node_id=NodeId(str(uuid.uuid4())),
            parent_id=None,
            story_id=None,
            title=f"Node-{i:05d}",
            node_type="story",
            filesystem_path=f"node-{i:05d}",
            created_at=_ts(i),
            updated_at=_ts(i),
            search_text="",
        )
        index.upsert(rec)
    return index


@pytest.mark.parametrize("node_count", [100, 1000, 5000])
def test_bench_exact_title_lookup(node_count: int) -> None:
    """Exact title lookup: search for a single known title
    in a workspace of `node_count` records.

    Phase 3.1 cost is O(n) (full scan + filter); Phase 3.2
    will route through a dedicated index method.
    """
    if node_count > 1000 and os.environ.get("RUN_LARGE_BENCH") != "1":
        pytest.skip(
            f"Skipping {node_count}-node benchmark "
            f"(set RUN_LARGE_BENCH=1 to enable)"
        )
    index = _seed_index(node_count)
    svc = DefaultSearchService(index)

    target_title = f"Node-{node_count // 2:05d}"
    iterations = 100

    start = time.perf_counter()
    for _ in range(iterations):
        result = svc.search(SearchRequest(title=target_title))
        assert result.total == 1
    elapsed = time.perf_counter() - start

    per_call_us = (elapsed / iterations) * 1_000_000
    print(
        f"\n[bench] search exact title ({node_count} nodes): "
        f"{per_call_us:.2f} µs/call over {iterations} iterations "
        f"({elapsed * 1000:.2f} ms total)"
    )


@pytest.mark.parametrize("node_count", [100, 1000, 5000])
def test_bench_prefix_lookup(node_count: int) -> None:
    """Prefix lookup: search for `Node-0001` (matches
    Node-0001, Node-00010, Node-00011, ..., Node-00019
    — 11 records in a 100-node workspace; 11 in 1000;
    11 in 5000).

    Phase 3.1 cost is O(n) (full scan + filter); the
    SQL implementation will use a `LIKE 'prefix%'` query.
    """
    if node_count > 1000 and os.environ.get("RUN_LARGE_BENCH") != "1":
        pytest.skip(
            f"Skipping {node_count}-node benchmark "
            f"(set RUN_LARGE_BENCH=1 to enable)"
        )
    index = _seed_index(node_count)
    svc = DefaultSearchService(index)

    iterations = 100
    start = time.perf_counter()
    last_result = None
    for _ in range(iterations):
        last_result = svc.search(SearchRequest(prefix="Node-0001"))
        assert last_result.total >= 1
    elapsed = time.perf_counter() - start

    per_call_us = (elapsed / iterations) * 1_000_000
    print(
        f"\n[bench] search prefix 'Node-0001' ({node_count} nodes): "
        f"{per_call_us:.2f} µs/call over {iterations} iterations "
        f"({elapsed * 1000:.2f} ms total, "
        f"matches={last_result.total if last_result else 0})"
    )


@pytest.mark.parametrize("node_count", [100, 1000, 5000])
def test_bench_paginated_list(node_count: int) -> None:
    """Paginated listing: page through all records 50
    at a time, sorted by `updated_at DESC`. Measures
    the full filter + sort + slice path.
    """
    if node_count > 1000 and os.environ.get("RUN_LARGE_BENCH") != "1":
        pytest.skip(
            f"Skipping {node_count}-node benchmark "
            f"(set RUN_LARGE_BENCH=1 to enable)"
        )
    index = _seed_index(node_count)
    svc = DefaultSearchService(index)

    page_size = 50
    iterations = 100
    start = time.perf_counter()
    for _ in range(iterations):
        # Single page query (not the full walk) — measures
        # one request, not the loop.
        result = svc.search(
            SearchRequest(
                sort=SearchSort.UPDATED_AT_DESC,
                page=0,
                page_size=page_size,
            )
        )
        assert result.total == node_count
        assert len(result.hits) == page_size
    elapsed = time.perf_counter() - start

    per_call_us = (elapsed / iterations) * 1_000_000
    print(
        f"\n[bench] search paginated ({node_count} nodes, "
        f"page_size={page_size}): "
        f"{per_call_us:.2f} µs/call over {iterations} iterations "
        f"({elapsed * 1000:.2f} ms total)"
    )


def test_bench_filtered_list() -> None:
    """Filtered list: 1000 nodes, 50% task, 50% story.
    Measure the filter cost added to a full scan.
    """
    index = InMemoryIndexRepository()
    for i in range(1000):
        rec = IndexRecord(
            node_id=NodeId(str(uuid.uuid4())),
            parent_id=None,
            story_id=None,
            title=f"Node-{i:05d}",
            node_type="task" if i % 2 == 0 else "story",
            filesystem_path=f"node-{i:05d}",
            created_at=_ts(i),
            updated_at=_ts(i),
            search_text="",
        )
        index.upsert(rec)
    svc = DefaultSearchService(index)

    iterations = 100
    start = time.perf_counter()
    for _ in range(iterations):
        result = svc.search(SearchRequest(node_type="task"))
        assert result.total == 500
    elapsed = time.perf_counter() - start

    per_call_us = (elapsed / iterations) * 1_000_000
    print(
        f"\n[bench] search filter by node_type (1000 nodes, "
        f"500 tasks): "
        f"{per_call_us:.2f} µs/call over {iterations} iterations "
        f"({elapsed * 1000:.2f} ms total)"
    )
