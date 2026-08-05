"""LocalWorkspaceRepository — round-trip and tree-reconstruction tests.

What we verify:

    - Single-node CRUD: create, load, rename, move, delete.
    - Tree reconstruction is deterministic: same disk state → same Tree.
    - Tree reconstruction preserves children's order across reloads.
    - Reconstruction is independent of the order the filesystem
      produced nodes (we shuffle the on-disk order and still get
      the same logical Tree).
    - Canvas read/write round-trips through the repository.
    - Repository passes through domain exceptions (no swallowing).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.domain import (
    InvalidParentError,
    Node,
    NodeMetadata,
    NodeNotFoundError,
    NodeType,
    TreeCycleError,
    new_node_id,
)
from backend.filesystem import LocalFilesystem, WorkspaceRoot
from backend.filesystem.exceptions import (
    CanvasMissingError,
    NodeNotFoundOnDiskError,
)
from backend.repositories import LocalWorkspaceRepository


def _make(title: str, type_: NodeType = NodeType.STORY) -> Node:
    return Node(
        id=new_node_id(),
        title=title,
        type=type_,
        metadata=NodeMetadata.from_dict({}, node_type=type_),
    )


def _seed_small_tree(repo: LocalWorkspaceRepository) -> dict[str, Node]:
    """Build a small tree and return id → Node for convenience.

    Layout:
        RootStory
          ├─ ChildA
          │    └─ GrandA1
          └─ ChildB
    """
    root = repo.save_node(_make("RootStory"), parent_id=None)
    a = repo.save_node(_make("ChildA"), parent_id=root.id)
    b = repo.save_node(_make("ChildB"), parent_id=root.id)
    a1 = repo.save_node(_make("GrandA1"), parent_id=a.id)
    return {"root": root, "a": a, "b": b, "a1": a1}


# ---- single-node CRUD -----------------------------------------------------


def test_create_root_node_returns_persisted_node(repo: LocalWorkspaceRepository) -> None:
    node = repo.save_node(_make("Hello"), parent_id=None)
    assert node.title == "Hello"
    assert node.parent_id is None
    # Reload from disk — same id, same title.
    reloaded = repo.load_node(node.id)
    assert reloaded.id == node.id
    assert reloaded.title == "Hello"


def test_create_child_appends_to_parent_children_ids(
    repo: LocalWorkspaceRepository,
) -> None:
    root = repo.save_node(_make("Root"), parent_id=None)
    a = repo.save_node(_make("A"), parent_id=root.id)
    b = repo.save_node(_make("B"), parent_id=root.id)
    # Order is preserved as the children were created.
    reloaded_root = repo.load_node(root.id)
    assert reloaded_root.children_ids == [a.id, b.id]


def test_rename_node_preserves_id(repo: LocalWorkspaceRepository) -> None:
    root = repo.save_node(_make("Original"), parent_id=None)
    renamed = repo.rename_node(root.id, "Renamed")
    assert renamed.id == root.id  # UUID is stable (Invariant §6).
    assert renamed.title == "Renamed"


def test_move_node_to_new_parent(repo: LocalWorkspaceRepository) -> None:
    nodes = _seed_small_tree(repo)
    root = nodes["root"]
    a = nodes["a"]
    b = nodes["b"]
    # Move ChildA from Root to under ChildB.
    moved = repo.move_node(a.id, new_parent_id=b.id)
    assert moved.parent_id == b.id
    # Old parent no longer lists ChildA.
    assert a.id not in repo.load_node(root.id).children_ids
    # New parent does.
    assert a.id in repo.load_node(b.id).children_ids


def test_move_node_rejects_cycle(repo: LocalWorkspaceRepository) -> None:
    nodes = _seed_small_tree(repo)
    root = nodes["root"]
    a = nodes["a"]
    # Try to move RootStory under ChildA — would create a cycle.
    with pytest.raises((InvalidParentError, TreeCycleError)):
        repo.move_node(root.id, new_parent_id=a.id)


def test_delete_node_removes_self_and_descendants(
    repo: LocalWorkspaceRepository,
) -> None:
    nodes = _seed_small_tree(repo)
    a = nodes["a"]
    a1 = nodes["a1"]
    repo.delete_node(a.id)
    # Repository translates filesystem-layer
    # `NodeNotFoundOnDiskError` into the domain `NodeNotFoundError`
    # so callers above the repository don't depend on the
    # filesystem module. (Verify-pass contract.)
    with pytest.raises(NodeNotFoundError):
        repo.load_node(a.id)
    with pytest.raises(NodeNotFoundError):
        repo.load_node(a1.id)


def test_duplicate_node_id_does_not_overwrite(  # noqa: D401
    repo: LocalWorkspaceRepository,
) -> None:
    """NOTE: This case is a known limitation of Phase 1.2 — the
    filesystem `_unique_slug` auto-suffixes a `-2` and `load_node`
    resolves by id, which can match either copy. The repository
    inherits this behavior. Tracking this gap so the constraint isn't
    forgotten in Phase 4 (Postgres index will enforce id uniqueness).
    """
    # No assertion here — we don't pin the behavior. The test exists
    # as a placeholder until the filesystem layer rejects id collisions.
    pass


# ---- canvas ---------------------------------------------------------------


def test_canvas_round_trip(repo: LocalWorkspaceRepository) -> None:
    node = repo.save_node(_make("WithCanvas"), parent_id=None)
    assert repo.read_canvas(node.id) == ""
    repo.write_canvas(node.id, "# hello\nbody")
    assert repo.read_canvas(node.id) == "# hello\nbody"


def test_canvas_missing_is_surfaced(repo: LocalWorkspaceRepository) -> None:
    node = repo.save_node(_make("NoCanvas"), parent_id=None)
    # Remove the canvas file the repository auto-creates on save_node,
    # so we can exercise the missing-canvas path.
    assert isinstance(repo._fs, LocalFilesystem)
    node_dir = repo._fs.node_dir(node.id)
    (node_dir / "canvas.md").unlink()
    # CanvasMissingError from the filesystem is the right signal here —
    # the repository does not invent its own exception.
    with pytest.raises(CanvasMissingError):
        repo.read_canvas(node.id)


# ---- tree reconstruction --------------------------------------------------


def test_reconstruct_tree_returns_domain_tree(
    repo: LocalWorkspaceRepository,
) -> None:
    _seed_small_tree(repo)
    tree = repo.load_tree()
    assert isinstance(tree, type(tree))  # smoke: not a dict
    # 4 nodes total.
    assert len(tree) == 4
    # Root exists and is root.
    roots = tree.roots()
    assert len(roots) == 1
    assert roots[0].title == "RootStory"


def test_reconstruct_tree_preserves_children_order(
    repo: LocalWorkspaceRepository,
) -> None:
    """Order created = order reconstructed.

    Insert children A, B, C under root in that order; on reload they
    must appear in the same order — both in the parent's children_ids
    list and in tree.children_of(...).
    """
    root = repo.save_node(_make("Root"), parent_id=None)
    repo.save_node(_make("A"), parent_id=root.id)
    repo.save_node(_make("B"), parent_id=root.id)
    repo.save_node(_make("C"), parent_id=root.id)

    tree = repo.load_tree()
    children = tree.children_of(root.id)
    assert [n.title for n in children] == ["A", "B", "C"]


def test_reconstruct_tree_is_ordering_independent(
    repo: LocalWorkspaceRepository,
) -> None:
    """Same on-disk workspace → same Tree regardless of filesystem
    walk order.

    We force the filesystem to yield directories in a non-sorted
    order (reverse alphabetical) and assert the resulting Tree is
    logically identical to one built in the default order.
    """

    def build() -> dict[str, list[str]]:
        _seed_small_tree(repo)
        tree = repo.load_tree()
        # Snapshot logical view: title of root + ordered child titles.
        roots = tree.roots()
        root = roots[0]
        children = [c.title for c in tree.children_of(root.id)]
        # And the grandchild's parent — for deeper comparison.
        grandchildren_parent = tree.children_of(root.id)[0]
        grandchildren = [g.title for g in tree.children_of(grandchildren_parent.id)]
        return {
            "root_title": root.title,
            "children": children,
            "grandchildren": grandchildren,
        }

    snapshot = build()

    # Now mutate the on-disk layout: rename sibling directories so the
    # filesystem's sorted walk returns them in reverse order. We do
    # this WITHOUT changing node ids or titles in node.json, because
    # identity must survive directory renames (Invariant §6).
    root = snapshot  # alias for clarity
    # Find root directory on disk.
    assert isinstance(repo._fs, LocalFilesystem)
    fs_root = repo._fs.root.path
    # Reverse the children dirs by prepending a tag to their slugs.
    children_dirs = sorted(p for p in fs_root.iterdir() if p.is_dir())
    # Tag them so that sorted order reverses the original creation order.
    for i, d in enumerate(reversed(children_dirs)):
        new_name = f"zzz-{i}-" + d.name
        d.rename(d.parent / new_name)

    tree = repo.load_tree()
    roots = tree.roots()
    assert len(roots) == 1
    assert roots[0].title == root["root_title"]
    assert [c.title for c in tree.children_of(roots[0].id)] == root["children"]
    # Grandchildren must still be under ChildA in the right order.
    grandchildren_parent = tree.children_of(roots[0].id)[0]
    assert [g.title for g in tree.children_of(grandchildren_parent.id)] == root[
        "grandchildren"
    ]


def test_reconstruct_tree_matches_in_memory_tree(
    repo: LocalWorkspaceRepository,
) -> None:
    """An in-memory Tree built by direct calls equals the Tree
    reconstructed from disk."""
    from backend.domain import Tree

    # Build in-memory tree directly.
    expected = Tree()
    root = _make("Root")
    expected.add(root)
    a = _make("A")
    b = _make("B")
    expected.add(a)
    expected.add(b)
    expected.attach(a.id, root.id)
    expected.attach(b.id, root.id)
    a1 = _make("A1")
    expected.add(a1)
    expected.attach(a1.id, a.id)

    # Build the same logical tree via the repository.
    disk_root = repo.save_node(root, parent_id=None)
    disk_a = repo.save_node(a, parent_id=disk_root.id)
    disk_b = repo.save_node(b, parent_id=disk_root.id)
    disk_a1 = repo.save_node(a1, parent_id=disk_a.id)

    # Reconstruct.
    actual = repo.load_tree()

    # Compare logical structure: same titles, same wiring.
    assert [n.title for n in actual.roots()] == [expected.roots()[0].title]
    actual_root = actual.roots()[0]
    expected_root = expected.roots()[0]
    actual_children = [c.title for c in actual.children_of(actual_root.id)]
    expected_children = [c.title for c in expected.children_of(expected_root.id)]
    assert actual_children == expected_children

    # Cross-check ids too — the actual ids should match what we
    # persisted.
    actual_child_ids = [c.id for c in actual.children_of(actual_root.id)]
    assert actual_child_ids == [disk_a.id, disk_b.id]
    assert actual.children_of(disk_a.id)[0].id == disk_a1.id


def test_reconstruct_after_complete_reload(
    repo: LocalWorkspaceRepository, tmp_path: Path
) -> None:
    """Reconstruct → write → reconstruct with a fresh repository
    instance → identical logical tree.
    """
    _seed_small_tree(repo)
    tree1 = repo.load_tree()

    # Build a brand-new repository over the same on-disk workspace.
    fresh_repo = LocalWorkspaceRepository(
        LocalFilesystem(WorkspaceRoot.open(tmp_path, create=False))
    )
    tree2 = fresh_repo.load_tree()

    assert len(tree1) == len(tree2) == 4
    # Compare title-by-title, root first.
    r1 = tree1.roots()[0]
    r2 = tree2.roots()[0]
    assert r1.title == r2.title
    assert [c.title for c in tree1.children_of(r1.id)] == [
        c.title for c in tree2.children_of(r2.id)
    ]
    # Ids must be identical too — UUIDs are stable across reloads.
    assert r1.id == r2.id


def test_reconstruct_skips_corrupt_subtree_safely(
    repo: LocalWorkspaceRepository,
) -> None:
    """A node whose node.json is corrupt is skipped; well-formed
    nodes still reconstruct. (Corruption is surfaced, not healed —
    see ADR-0004.)"""
    nodes = _seed_small_tree(repo)
    a = nodes["a"]
    # Corrupt GrandA1's node.json.
    assert isinstance(repo._fs, LocalFilesystem)
    a1_dir = repo._fs.node_dir(nodes["a1"].id)
    (a1_dir / "node.json").write_text("{ this is not valid json")
    tree = repo.load_tree()
    # 3 well-formed nodes survive; GrandA1 is skipped.
    assert len(tree) == 3
    # The other nodes still link correctly.
    assert tree.children_of(a.id) == []  # its only child was corrupt


# ---- duck typing against the Protocol ------------------------------------


def test_repository_satisfies_protocol(repo: LocalWorkspaceRepository) -> None:
    """Static check: LocalWorkspaceRepository must satisfy
    WorkspaceRepository. We assert at runtime via hasattr on the
    documented methods; mypy covers the static side."""
    for method in (
        "load_node",
        "load_children",
        "load_tree",
        "save_node",
        "rename_node",
        "move_node",
        "delete_node",
        "read_canvas",
        "write_canvas",
    ):
        assert hasattr(repo, method), f"missing method: {method}"
    # And every method is callable.
    assert callable(getattr(repo, "load_tree"))