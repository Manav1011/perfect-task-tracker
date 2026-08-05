"""Tree reconstruction benchmarks — baseline only.

We measure how long `LocalWorkspaceRepository.load_tree()` takes for
workspaces of 100 / 1000 / 5000 nodes. These are baseline numbers so
we can detect regressions as the persistence layer evolves; we are
NOT optimizing against them in Phase 1.3.

Phase 3.0 added a second benchmark target: `repo.load_node(id)`
after cache warmup. This is the production hot-path; the
baseline (no cache) is the disk walk. The cache-backed
measurement is what we expect to be sub-millisecond.

Run with:
    uv run pytest backend/tests/benchmarks/test_load_tree_benchmark.py --benchmark-disable-gc -s

The `-s` flag prints the timing summary pytest-benchmark produces.
Without pytest-benchmark installed, the tests still run and report a
wall-clock timing to stdout via `print`.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from backend.domain import Node, NodeMetadata, NodeType, new_node_id
from backend.filesystem import LocalFilesystem, WorkspaceRoot
from backend.repositories import LocalWorkspaceRepository
from backend.workspace import InMemoryWorkspaceCache

# Each node has a small canvas so we measure realistic disk pressure,
# not just JSON write speed.
_CANVAS_CHARS = 256


def _seed_flat(repo: LocalWorkspaceRepository, count: int) -> None:
    """Create `count` root-level nodes with the given count."""
    for i in range(count):
        node = Node(
            id=new_node_id(),
            title=f"Node-{i:05d}",
            type=NodeType.STORY,
            metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
        )
        repo.save_node(node, parent_id=None)
        repo.write_canvas(node.id, "x" * _CANVAS_CHARS)


def _seed_bushy(repo: LocalWorkspaceRepository, total: int, branching: int) -> None:
    """Create `total` nodes in a bushy tree of given branching factor."""
    import uuid as _uuid

    parent_stack: list[str] = []
    # First root.
    first = Node(
        id=new_node_id(),
        title="root-0",
        type=NodeType.STORY,
        metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
    )
    repo.save_node(first, parent_id=None)
    parent_stack.append(first.id)

    count = 1
    while count < total:
        parent_id = parent_stack[-1]
        for _ in range(branching):
            if count >= total:
                break
            node = Node(
                id=str(_uuid.uuid4()),
                title=f"n-{count:05d}",
                type=NodeType.STORY,
                metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
            )
            # type: ignore[arg-type] — NodeId is NewType[str]
            repo.save_node(node, parent_id=parent_id)
            parent_stack.append(node.id)  # type: ignore[arg-type]
            count += 1
        # Pop one level so we don't go infinitely deep.
        if len(parent_stack) > 1:
            parent_stack.pop()


@pytest.mark.parametrize("node_count", [100, 1000, 5000])
def test_load_tree_baseline_flat(tmp_path: Path, node_count: int) -> None:
    """Measure reconstruction time for a flat workspace (all root-level)."""
    if node_count > 1000 and os.environ.get("RUN_LARGE_BENCH") != "1":
        pytest.skip(
            f"Skipping {node_count}-node benchmark (set RUN_LARGE_BENCH=1 to enable)"
        )
    workspace = WorkspaceRoot.open(tmp_path, create=True)
    repo = LocalWorkspaceRepository(LocalFilesystem(workspace))
    _seed_flat(repo, node_count)

    # Force filesystem caches to be honest about cold-read time.
    # (Best-effort: drop_caches requires privileges; ignore failures.)
    try:
        with open("/proc/sys/vm/drop_caches", "w") as fh:
            fh.write("1\n")
    except (PermissionError, FileNotFoundError, OSError):
        pass

    start = time.perf_counter()
    tree = repo.load_tree()
    elapsed = time.perf_counter() - start

    assert len(tree) == node_count
    print(
        f"\n[bench] load_tree (flat, {node_count} nodes): "
        f"{elapsed * 1000:.1f} ms"
    )


@pytest.mark.parametrize("node_count", [100, 1000])
def test_load_tree_baseline_bushy(tmp_path: Path, node_count: int) -> None:
    """Measure reconstruction time for a bushy tree."""
    if node_count > 1000 and os.environ.get("RUN_LARGE_BENCH") != "1":
        pytest.skip(
            f"Skipping {node_count}-node benchmark (set RUN_LARGE_BENCH=1 to enable)"
        )
    workspace = WorkspaceRoot.open(tmp_path, create=True)
    repo = LocalWorkspaceRepository(LocalFilesystem(workspace))
    _seed_bushy(repo, node_count, branching=5)

    start = time.perf_counter()
    tree = repo.load_tree()
    elapsed = time.perf_counter() - start

    assert len(tree) == node_count
    print(
        f"\n[bench] load_tree (bushy-5, {node_count} nodes): "
        f"{elapsed * 1000:.1f} ms"
    )


# ---- Phase 3.0 cache-backed benchmark ----------------------------------


@pytest.mark.parametrize("node_count", [100, 1000, 5000])
def test_load_node_cached_after_warmup(
    tmp_path: Path, node_count: int
) -> None:
    """Phase 3.0 benchmark: `repo.load_node(id)` after cache
    warmup. This is the production hot-path; we expect
    sub-millisecond per call regardless of `node_count`.
    """
    if node_count > 1000 and os.environ.get("RUN_LARGE_BENCH") != "1":
        pytest.skip(
            f"Skipping {node_count}-node benchmark (set RUN_LARGE_BENCH=1 to enable)"
        )
    workspace = WorkspaceRoot.open(tmp_path, create=True)
    fs = LocalFilesystem(workspace)
    cache = InMemoryWorkspaceCache()
    repo = LocalWorkspaceRepository(fs, cache=cache)
    _seed_flat(repo, node_count)

    # Warmup the cache.
    cache.populate(repo.load_tree())

    # Pick a target id — first root.
    target_id = next(iter(repo.load_tree().all_nodes())).id

    # Measure N reads through the cache.
    iterations = 1000
    start = time.perf_counter()
    for _ in range(iterations):
        node = repo.load_node(target_id)
        assert node.id == target_id
    elapsed = time.perf_counter() - start

    per_call_us = (elapsed / iterations) * 1_000_000
    print(
        f"\n[bench] load_node cached ({node_count} nodes): "
        f"{per_call_us:.2f} µs/call over {iterations} iterations "
        f"({elapsed * 1000:.2f} ms total)"
    )
    # Sanity: cache hits should be sub-millisecond (the cache's
    # lock acquire + dict lookup + structlog no-op).
    # We don't enforce a hard threshold here — the goal is to
    # *see* the latency reduction vs the disk-walk baseline.
    # A flaky threshold would make this benchmark skip in CI;
    # the print is the diagnostic.
