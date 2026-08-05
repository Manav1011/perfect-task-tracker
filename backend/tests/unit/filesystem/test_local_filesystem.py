"""LocalFilesystem integration tests using temporary directories."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.domain import Node, NodeMetadata, NodeType, new_node_id
from backend.filesystem import (
    CanvasMissingError,
    DuplicateNodeIdError,
    InvalidParentError,
    LocalFilesystem,
    NodeNotFoundOnDiskError,
)


def _root_story(fs: LocalFilesystem, title: str = "Root") -> Node:
    node = Node(
        id=new_node_id(),
        title=title,
        type=NodeType.STORY,
        metadata=NodeMetadata({}, NodeType.STORY),
    )
    return fs.create_node(node, parent_id=None)


def _task(fs: LocalFilesystem, title: str, parent_id) -> Node:
    node = Node(
        id=new_node_id(),
        title=title,
        type=NodeType.TASK,
        metadata=NodeMetadata({"status": "todo"}, NodeType.TASK),
    )
    return fs.create_node(node, parent_id=parent_id)


# ---- create / load ---------------------------------------------------


def test_create_root_writes_files(fs: LocalFilesystem, tmp_path: Path) -> None:
    n = _root_story(fs)
    directory = tmp_path / "root"
    assert directory.is_dir()
    assert (directory / "node.json").exists()
    assert (directory / "canvas.md").exists()
    assert fs.load_node(n.id).title == "Root"


def test_create_task_under_root(fs: LocalFilesystem, tmp_path: Path) -> None:
    p = _root_story(fs, title="Project")
    t = _task(fs, "First Task", parent_id=p.id)
    assert t.parent_id == p.id
    # Children list updated on the parent.
    p_loaded = fs.load_node(p.id)
    assert p_loaded.children_ids == [t.id]


def test_create_collides_with_sibling(fs: LocalFilesystem, tmp_path: Path) -> None:
    """A sibling collision is auto-resolved by appending `-2`, `-3`, ....

    Two Nodes can share the same title as long as their directory
    names differ. The filesystem de-duplicates transparently.
    """
    p = _root_story(fs, title="Project")
    t1 = _task(fs, "Same Title", parent_id=p.id)
    t2 = _task(fs, "Same Title", parent_id=p.id)
    assert t1.id != t2.id
    # Both nodes exist under their parent and have distinct dirs.
    children = fs.list_children(p.id)
    assert {t1.id, t2.id} == {c.id for c in children}
    child_dirs = [d for d in (tmp_path / "project").iterdir() if d.is_dir()]
    assert len(child_dirs) == 2
    assert sorted(d.name for d in child_dirs) == ["same-title", "same-title-2"]


def test_create_rejects_unknown_parent(fs: LocalFilesystem) -> None:
    with pytest.raises(NodeNotFoundOnDiskError):
        _task(fs, "Orphan", parent_id=new_node_id())


# ---- rename ----------------------------------------------------------


def test_rename_updates_title_and_dir(fs: LocalFilesystem, tmp_path: Path) -> None:
    p = _root_story(fs, title="Old")
    old_dir = tmp_path / "old"
    assert old_dir.exists()
    renamed = fs.rename_node(p.id, "New Title")
    assert renamed.title == "New Title"
    assert (tmp_path / "new-title").exists()
    assert not old_dir.exists()
    # Id preserved.
    assert renamed.id == p.id


def test_rename_preserves_uuid_under_collision(fs: LocalFilesystem, tmp_path: Path) -> None:
    a = _root_story(fs, title="Alpha")
    fs.rename_node(a.id, "Beta")
    # Beta now exists; renaming a sibling's name would collide but
    # renaming the same node is fine.
    reloaded = fs.rename_node(a.id, "Beta")
    assert reloaded.id == a.id


# ---- move ------------------------------------------------------------


def test_move_between_parents(fs: LocalFilesystem) -> None:
    p1 = _root_story(fs, title="P1")
    p2 = _root_story(fs, title="P2")
    t = _task(fs, "Moveable", parent_id=p1.id)
    moved = fs.move_node(t.id, new_parent_id=p2.id)
    assert moved.parent_id == p2.id
    assert t.id not in fs.load_node(p1.id).children_ids
    assert t.id in fs.load_node(p2.id).children_ids


def test_move_into_descendant_rejected(fs: LocalFilesystem) -> None:
    p = _root_story(fs, title="Parent")
    t = _task(fs, "Task", parent_id=p.id)
    nested = _task(fs, "Nested", parent_id=t.id)
    with pytest.raises(InvalidParentError):
        fs.move_node(p.id, new_parent_id=nested.id)


def test_move_into_self_rejected(fs: LocalFilesystem) -> None:
    p = _root_story(fs, title="Self")
    with pytest.raises(InvalidParentError):
        fs.move_node(p.id, new_parent_id=p.id)


# ---- delete ----------------------------------------------------------


def test_delete_removes_subtree(fs: LocalFilesystem, tmp_path: Path) -> None:
    p = _root_story(fs, title="Project")
    t = _task(fs, "Task", parent_id=p.id)
    grandchild = _task(fs, "Grand", parent_id=t.id)
    fs.delete_node(t.id)
    assert not (tmp_path / "project" / "task").exists()
    with pytest.raises(NodeNotFoundOnDiskError):
        fs.load_node(grandchild.id)


# ---- canvas ----------------------------------------------------------


def test_canvas_round_trip(fs: LocalFilesystem) -> None:
    p = _root_story(fs)
    fs.write_canvas(p.id, "# Hello\n\nWorld")
    assert fs.read_canvas(p.id) == "# Hello\n\nWorld"


def test_canvas_missing_raises(fs: LocalFilesystem, tmp_path: Path) -> None:
    p = _root_story(fs)
    (tmp_path / "root" / "canvas.md").unlink()
    with pytest.raises(CanvasMissingError):
        fs.read_canvas(p.id)


# ---- corruption ------------------------------------------------------


def test_missing_node_json_raises(fs: LocalFilesystem, tmp_path: Path) -> None:
    """A Node whose node.json was deleted cannot be loaded.

    The filesystem reports this as `NodeNotFoundOnDiskError` because
    the id is no longer locatable — the JSON was the only carrier of
    identity.
    """
    p = _root_story(fs)
    (tmp_path / "root" / "node.json").unlink()
    with pytest.raises(NodeNotFoundOnDiskError):
        fs.load_node(p.id)
    # But the directory still exists, and trying to read its canvas
    # surfaces the structural anomaly.
    assert (tmp_path / "root" / "canvas.md").exists()


def test_invalid_json_raises(fs: LocalFilesystem, tmp_path: Path) -> None:
    """A Node whose node.json is corrupt is reported as not-on-disk.

    The walk tolerates corrupt directories by skipping them, since the
    id is no longer recoverable. The directory still exists; callers
    that need to surface the corruption can call `walk()` (which
    catches the same errors silently today) or scan the tree directly.
    """
    p = _root_story(fs)
    (tmp_path / "root" / "node.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(NodeNotFoundOnDiskError):
        fs.load_node(p.id)


def test_duplicate_ids_detected_on_walk(fs: LocalFilesystem) -> None:
    p = _root_story(fs)
    same_id = p.id
    sibling = _root_story(fs, title="Other")
    # Forge a duplicate by overwriting the second node's id.
    import json

    f = fs.node_dir(sibling.id) / "node.json"
    payload = json.loads(f.read_text())
    payload["id"] = same_id
    f.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DuplicateNodeIdError):
        fs.walk()


# ---- atomic write ----------------------------------------------------


def test_atomic_write_replaces_target(fs: LocalFilesystem, tmp_path: Path) -> None:
    """A successful atomic write replaces the target file."""
    p = _root_story(fs)
    target = fs.node_dir(p.id) / "canvas.md"
    target.write_text("OLD", encoding="utf-8")
    fs.write_canvas(p.id, "NEW")
    assert target.read_text(encoding="utf-8") == "NEW"
    # No temp file left behind.
    assert not list(target.parent.glob("*.tmp"))


# ---- list_children / walk -------------------------------------------


def test_list_children_returns_ordered(fs: LocalFilesystem) -> None:
    p = _root_story(fs)
    a = _task(fs, "A", parent_id=p.id)
    b = _task(fs, "B", parent_id=p.id)
    children = fs.list_children(p.id)
    assert [c.id for c in children] == [a.id, b.id]


def test_walk_finds_all(fs: LocalFilesystem) -> None:
    p = _root_story(fs)
    t = _task(fs, "T", parent_id=p.id)
    all_nodes = fs.walk()
    ids = {n.id for n in all_nodes}
    assert {p.id, t.id} <= ids


def test_load_missing_id_raises(fs: LocalFilesystem) -> None:
    with pytest.raises(NodeNotFoundOnDiskError):
        fs.load_node(new_node_id())