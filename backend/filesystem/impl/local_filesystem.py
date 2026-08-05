"""LocalFilesystem — real-disk implementation of the Filesystem protocol.

This is the only filesystem implementation we ship in Phase 1.2. It
owns:
    - directory creation/rename/deletion,
    - atomic node.json and canvas.md writes,
    - the JSON serialization calls (via `serialization.py`),
    - slug generation and de-duplication.

It does NOT own:
    - business logic (validation beyond on-disk shape),
    - the in-memory tree (workspace/ package, future),
    - Postgres indexing,
    - the API layer.

All paths are `pathlib.Path`. All writes are atomic (temp + rename).
All JSON goes through `serialization.py`.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from backend.domain.node import Node, NodeId

from backend.filesystem.atomic import atomic_write_text
from backend.filesystem.exceptions import (
    CanvasMissingError,
    DuplicateNodeIdError,
    InvalidNodeJSONError,
    NodeDirectoryMissingError,
    NodeMetadataMissingError,
    NodeNotFoundOnDiskError,
    SiblingNameCollisionError,
)
# InvalidParentError lives in the domain; the filesystem re-raises it
# as a structural error (it observed the disk refusing a parent).
from backend.domain.exceptions import InvalidParentError
from backend.filesystem.protocol import Filesystem
from backend.filesystem.serialization import (
    dict_to_node,
    node_to_dict,
    read_node_json,
    write_node_json,
)
from backend.filesystem.slug import slugify
from backend.filesystem.workspace_root import WorkspaceRoot

# Filenames used inside a Node's directory. Per the on-disk layout
# (Architecture Requirement §4) each Node has exactly these two files
# plus zero or more child directories.
NODE_JSON = "node.json"
CANVAS_MD = "canvas.md"


class LocalFilesystem:
    """Concrete Filesystem backed by the local disk."""

    def __init__(self, root: WorkspaceRoot) -> None:
        self._root = root

    @property
    def root(self) -> WorkspaceRoot:
        return self._root

    # ---- path resolution --------------------------------------------

    def node_dir(self, node_id: NodeId) -> Path:
        """Absolute path to the directory holding the Node with this id.

        Walks the workspace tree. Raises NodeNotFoundOnDiskError if
        the id is not present anywhere on disk. A directory whose
        `node.json` is missing or unreadable is treated as
        "unidentifiable" — the directory exists on disk but the Node
        cannot be loaded, which `load_node` reports as the underlying
        corruption error rather than "not found".
        """
        # First pass: look for an id match, tolerating corrupt dirs.
        for path in self._iter_node_dirs():
            try:
                node = self._load_from_dir(path)
            except (
                NodeDirectoryMissingError,
                NodeMetadataMissingError,
                InvalidNodeJSONError,
            ):
                continue
            if node.id == node_id:
                return path
        # Second pass: maybe the id would have matched a corrupt dir?
        # We can't know — node.json is what carries the id. So if we
        # didn't find a clean match, the Node is genuinely absent.
        raise NodeNotFoundOnDiskError(node_id)

    # ---- Node CRUD --------------------------------------------------

    def load_node(self, node_id: NodeId) -> Node:
        """Load a Node by id."""
        return self._load_from_dir(self.node_dir(node_id))

    def create_node(self, node: Node, parent_id: NodeId | None) -> Node:
        """Create a Node directory under `parent_id` (None for roots)."""
        if parent_id is None:
            parent_dir = self._root.path
        else:
            parent_dir = self.node_dir(parent_id)

        slug = self._unique_slug(parent_dir, slugify(node.title))
        node_dir = parent_dir / slug
        node_dir.mkdir(parents=False)

        # Persist node.json. We rewrite the entity with the correct
        # children_ids and the title that became the slug-friendly form.
        on_disk = node.with_title(node.title).with_children([])
        on_disk = on_disk.with_parent(parent_id) if parent_id is not None else on_disk
        write_node_json(node_dir / NODE_JSON, node_to_dict(on_disk))

        # Persist canvas.md (initially empty).
        atomic_write_text(node_dir / CANVAS_MD, "")

        # Update parent.children_ids.
        if parent_id is not None:
            self._append_child_to_parent(parent_id, node.id)

        return self._load_from_dir(node_dir)

    def rename_node(self, node_id: NodeId, new_title: str) -> Node:
        """Rename the Node on disk. Preserves UUID (Invariant §6)."""
        old_dir = self.node_dir(node_id)
        old_node = self._load_from_dir(old_dir)

        parent_dir = old_dir.parent
        new_slug = self._unique_slug(parent_dir, slugify(new_title), exclude=old_dir.name)
        new_dir = parent_dir / new_slug
        old_dir.rename(new_dir)

        updated = old_node.with_title(new_title)
        write_node_json(new_dir / NODE_JSON, node_to_dict(updated))
        return self._load_from_dir(new_dir)

    def write_node(self, node: Node) -> Node:
        """Rewrite node.json in place.

        Used for partial updates (e.g. metadata) that don't change
        the directory name. The directory is NOT renamed; the
        caller MUST pass a Node whose `title` matches the
        current directory slug.

        This is the in-place sibling of `rename_node` — same
        write-atomicity guarantees, no directory move.
        """
        node_dir = self.node_dir(node.id)
        write_node_json(node_dir / NODE_JSON, node_to_dict(node))
        return self._load_from_dir(node_dir)

    def move_node(
        self,
        node_id: NodeId,
        new_parent_id: NodeId | None,
        position: int | None = None,
    ) -> Node:
        """Move the Node to a new parent directory.

        Refuses if `new_parent_id` is `node_id` or one of its
        descendants (cycle prevention — the filesystem is the last
        line of defense for this invariant).
        """
        if new_parent_id is not None and new_parent_id == node_id:
            raise InvalidParentError("cannot move a node into itself")
        old_dir = self.node_dir(node_id)
        node = self._load_from_dir(old_dir)

        # Cycle check: walk descendants of node_id and refuse if
        # new_parent_id appears.
        if new_parent_id is not None:
            descendants = self._collect_descendant_ids(node_id)
            if new_parent_id in descendants:
                raise InvalidParentError("cannot move a node into its own descendant")

        # Determine destination directory.
        if new_parent_id is None:
            new_parent_dir = self._root.path
        else:
            new_parent_dir = self.node_dir(new_parent_id)
            if new_parent_dir == old_dir:
                raise InvalidParentError("destination is the same directory")

        # Detach from old parent's children list.
        if node.parent_id is not None:
            self._remove_child_from_parent(node.parent_id, node_id)

        # Slug the destination.
        new_slug = self._unique_slug(new_parent_dir, slugify(node.title))
        new_dir = new_parent_dir / new_slug
        old_dir.rename(new_dir)

        # Update node.json: parent_id.
        updated = node.with_parent(new_parent_id)
        write_node_json(new_dir / NODE_JSON, node_to_dict(updated))

        # Append to new parent's children list.
        if new_parent_id is not None:
            self._insert_child_to_parent(
                new_parent_id, node_id, position=position
            )

        return self._load_from_dir(new_dir)

    def delete_node(self, node_id: NodeId) -> None:
        """Recursively delete the Node and its descendants."""
        directory = self.node_dir(node_id)
        # Collect descendants before we destroy the tree.
        descendants = list(self._iter_descendant_dirs(directory))
        # Detach from parent first.
        try:
            node = self._load_from_dir(directory)
        except (NodeDirectoryMissingError,):
            node = None
        if node is not None and node.parent_id is not None:
            self._remove_child_from_parent(node.parent_id, node_id)
        # Delete deepest first.
        for d in reversed(descendants + [directory]):
            if d.exists():
                # rm of a non-empty directory; rely on shutil for safety.
                import shutil

                shutil.rmtree(d)

    # ---- Reads -------------------------------------------------------

    def list_children(self, node_id: NodeId) -> list[Node]:
        """Return the ordered children of `node_id`."""
        parent_dir = self.node_dir(node_id)
        try:
            parent = self._load_from_dir(parent_dir)
        except NodeDirectoryMissingError as exc:
            raise NodeNotFoundOnDiskError(node_id) from exc
        children: list[Node] = []
        for cid in parent.children_ids:
            try:
                children.append(self.load_node(cid))
            except NodeNotFoundOnDiskError:
                # The children list references an id that's not on
                # disk. Skip — the caller decides how to surface this.
                continue
        return children

    def walk(self) -> list[Node]:
        """Return every Node in the workspace."""
        nodes: list[Node] = []
        for d in self._iter_node_dirs():
            try:
                nodes.append(self._load_from_dir(d))
            except (NodeDirectoryMissingError,):
                # A directory without node.json is a structural anomaly.
                # We could raise here, but the spec asks us to keep
                # corruption recoverable; skip and let the caller
                # decide.
                continue
        # Detect duplicate ids across the workspace.
        seen: dict[str, Path] = {}
        for n in nodes:
            existing = seen.get(n.id)
            if existing is not None:
                # We can't import node_dir without a recursive call;
                # the duplicate paths are: the directory we found n in.
                # Recover by walking again with dir lookup.
                dup_path = self._find_dir_for_id(n.id, exclude=existing)
                raise DuplicateNodeIdError(
                    n.id, paths=[str(existing), str(dup_path)]
                )
            # The directory that holds n.id is whatever we just walked.
            # We don't track it here directly; use a second pass below.
            seen[n.id] = self.node_dir(n.id)
        return nodes

    # ---- Canvas ------------------------------------------------------

    def read_canvas(self, node_id: NodeId) -> str:
        """Read canvas.md. Raises CanvasMissingError if absent."""
        directory = self.node_dir(node_id)
        canvas = directory / CANVAS_MD
        if not canvas.exists():
            raise CanvasMissingError(str(directory))
        return canvas.read_text(encoding="utf-8")

    def write_canvas(self, node_id: NodeId, content: str) -> None:
        """Atomically overwrite canvas.md."""
        directory = self.node_dir(node_id)
        canvas = directory / CANVAS_MD
        atomic_write_text(canvas, content)

    # ---- Internals ---------------------------------------------------

    def _load_from_dir(self, directory: Path) -> Node:
        """Read node.json from `directory` and return a domain Node."""
        if not directory.exists() or not directory.is_dir():
            raise NodeDirectoryMissingError(str(directory))
        payload = read_node_json(directory / NODE_JSON)
        try:
            return dict_to_node(payload)
        except Exception as exc:
            # Wrap with the directory path so callers can locate it.
            from backend.filesystem.exceptions import InvalidNodeJSONError
            raise InvalidNodeJSONError(str(directory), str(exc)) from exc

    def _iter_node_dirs(self) -> Iterable[Path]:
        """Yield every directory under the root that contains node.json."""
        # Pre-order traversal — root directories come first.
        for entry in sorted(self._root.path.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                # Skip workspace marker (.ptt) and any hidden dirs.
                continue
            yield from self._walk_node_dirs(entry)

    def _walk_node_dirs(self, base: Path) -> Iterable[Path]:
        """Recursive helper for `_iter_node_dirs`."""
        if (base / NODE_JSON).exists():
            yield base
        for child in sorted(base.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                yield from self._walk_node_dirs(child)

    def _iter_descendant_dirs(self, base: Path) -> Iterable[Path]:
        """Yield all descendant directories (deepest first would need post-order)."""
        descendants: list[Path] = []

        def recurse(d: Path) -> None:
            for child in sorted(d.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    descendants.append(child)
                    recurse(child)

        recurse(base)
        return descendants

    def _collect_descendant_ids(self, node_id: NodeId) -> set[NodeId]:
        """Return the set of all descendant NodeIds (not including self)."""
        ids: set[NodeId] = set()
        for d in self._iter_descendant_dirs(self.node_dir(node_id)):
            try:
                ids.add(self._load_from_dir(d).id)
            except Exception:
                continue
        return ids

    def _unique_slug(
        self, parent: Path, base_slug: str, *, exclude: str | None = None
    ) -> str:
        """Pick a non-colliding directory name under `parent`.

        If `base_slug` already exists (and isn't the entry we're
        renaming away from), append `-2`, `-3`, ... until a free name
        is found.
        """
        candidate = base_slug
        n = 2
        while (parent / candidate).exists() and candidate != exclude:
            candidate = f"{base_slug}-{n}"
            n += 1
        if (parent / candidate).exists() and candidate == exclude:
            return candidate
        return candidate

    def _find_dir_for_id(self, node_id: NodeId, *, exclude: Path) -> Path:
        """Find the directory holding `node_id`, skipping `exclude`."""
        for path in self._iter_node_dirs():
            if path == exclude:
                continue
            try:
                if self._load_from_dir(path).id == node_id:
                    return path
            except Exception:
                continue
        raise NodeNotFoundOnDiskError(node_id)

    def _append_child_to_parent(self, parent_id: NodeId, child_id: NodeId) -> None:
        """Append `child_id` to `parent_id.children_ids` on disk."""
        parent_dir = self.node_dir(parent_id)
        node = self._load_from_dir(parent_dir)
        if child_id in node.children_ids:
            return
        updated = node.with_children([*node.children_ids, child_id])
        write_node_json(parent_dir / NODE_JSON, node_to_dict(updated))

    def _remove_child_from_parent(self, parent_id: NodeId, child_id: NodeId) -> None:
        """Remove `child_id` from `parent_id.children_ids` on disk."""
        parent_dir = self.node_dir(parent_id)
        node = self._load_from_dir(parent_dir)
        if child_id not in node.children_ids:
            return
        updated = node.with_children(
            [c for c in node.children_ids if c != child_id]
        )
        write_node_json(parent_dir / NODE_JSON, node_to_dict(updated))

    def _insert_child_to_parent(
        self, parent_id: NodeId, child_id: NodeId, *, position: int | None
    ) -> None:
        """Insert `child_id` at `position` (None = end)."""
        parent_dir = self.node_dir(parent_id)
        node = self._load_from_dir(parent_dir)
        children = [c for c in node.children_ids if c != child_id]
        if position is None or position >= len(children):
            children.append(child_id)
        else:
            position = max(0, position)
            children.insert(position, child_id)
        updated = node.with_children(children)
        write_node_json(parent_dir / NODE_JSON, node_to_dict(updated))