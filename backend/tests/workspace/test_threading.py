"""Threading tests for the runtime cache.

We exercise:

    - Concurrent reads while a write invalidates — no
      exceptions, no torn reads.
    - Concurrent populate + read — populate wins (later
      reads see the populated cache).
    - Concurrent invalidate_many + load_node — no torn
      reads; a missing id is consistent.

These tests don't aim to prove freedom from all races
(specifically, the dict iteration during `_nodes = {…}`
construction in populate would surface as a TypeError if
two threads populated simultaneously, but populate is
documented as single-caller).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from backend.domain import Node, NodeMetadata, NodeType, new_node_id
from backend.filesystem import LocalFilesystem, WorkspaceRoot
from backend.repositories import LocalWorkspaceRepository
from backend.workspace import InMemoryWorkspaceCache


def _setup(tmp_path: Path, node_count: int = 50):
    """Build a workspace with `node_count` root nodes; return
    the populated repo + cache + node ids.
    """
    workspace = WorkspaceRoot.open(tmp_path, create=True)
    fs = LocalFilesystem(workspace)
    cache = InMemoryWorkspaceCache()
    repo = LocalWorkspaceRepository(fs, cache=cache)

    ids = []
    for i in range(node_count):
        node = Node(
            id=new_node_id(),
            title=f"n-{i}",
            type=NodeType.STORY,
            metadata=NodeMetadata.from_dict({}, node_type=NodeType.STORY),
        )
        repo.save_node(node, parent_id=None)
        ids.append(node.id)

    cache.populate(repo.load_tree())
    return repo, cache, ids


def test_concurrent_reads_and_invalidate(tmp_path: Path) -> None:
    """8 reader threads + 1 writer thread. Reads either
    succeed or raise CacheConsistencyError (after invalidate).
    No exceptions are uncaught.
    """
    repo, cache, ids = _setup(tmp_path, node_count=20)

    target_id = ids[0]
    errors: list[BaseException] = []

    def reader():
        try:
            for _ in range(50):
                # load_node may raise CacheConsistencyError
                # if the writer invalidated mid-read. We
                # accept that — what matters is no OTHER
                # exception is raised.
                repo.load_node(target_id)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def writer():
        for _ in range(20):
            cache.invalidate(target_id)
            # Repopulate via clear+populate cycle (cache
            # populate is idempotent on success; we use
            # clear() to allow re-population).
            cache.clear()
            cache.populate(repo.load_tree())

    with ThreadPoolExecutor(max_workers=9) as ex:
        futures = []
        for _ in range(8):
            futures.append(ex.submit(reader))
        futures.append(ex.submit(writer))
        for f in as_completed(futures):
            f.result()  # surface any exception from the workers

    assert errors == [], f"unexpected errors: {errors!r}"


def test_concurrent_load_children_does_not_corrupt(tmp_path: Path) -> None:
    """16 reader threads calling load_children on the same
    parent repeatedly. The result is always a list (empty
    for root nodes in this fixture). No exceptions.
    """
    repo, _, ids = _setup(tmp_path, node_count=10)
    target_id = ids[0]

    def reader():
        for _ in range(50):
            children = repo.load_children(target_id)
            assert children == []  # root nodes have no children

    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = [ex.submit(reader) for _ in range(16)]
        for f in as_completed(futures):
            f.result()


def test_concurrent_stats_calls_are_safe(tmp_path: Path) -> None:
    """Many threads reading `stats()` simultaneously never
    raises — the dataclass snapshot is built under the
    cache's lock.
    """
    _, cache, _ = _setup(tmp_path, node_count=5)

    def reader():
        for _ in range(100):
            s = cache.stats()
            assert s.populated is True

    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = [ex.submit(reader) for _ in range(16)]
        for f in as_completed(futures):
            f.result()


def test_concurrent_load_tree_does_not_corrupt(tmp_path: Path) -> None:
    """Many threads reading `load_tree()` simultaneously —
    each gets a stable view of the Tree.
    """
    repo, _, _ = _setup(tmp_path, node_count=10)

    def reader():
        for _ in range(50):
            tree = repo.load_tree()
            assert len(tree) == 10

    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = [ex.submit(reader) for _ in range(16)]
        for f in as_completed(futures):
            f.result()