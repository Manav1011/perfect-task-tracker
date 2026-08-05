"""JSON serialization for Node metadata — the single owner of disk JSON.

Per Architecture Requirement §8: JSON read/write is isolated in one
place. Nothing else in the codebase calls `json.load` or `json.dump`
on a Node payload. The schema is defined here as a typed mapping,
not duplicated in the filesystem layer.

Schema (node.json):

    {
        "id":          "<uuid-string>",     # NodeId
        "type":        "story"|"task"|"note",
        "title":       "<non-empty>",
        "parent_id":   "<uuid-string>" | null,
        "children_ids": ["<uuid-string>", ...],   # ordered
        "metadata":    { ... type-specific freeform ... }
    }

Notes:
    - `canvas` is NOT stored in node.json. The canvas lives in
      canvas.md and is read/written via filesystem canvas methods.
      Storing it in JSON would couple content to structure.
    - `children_ids` is ordered. Order is preserved across writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.domain.enums import NodeType
from backend.domain.metadata import NodeMetadata
from backend.domain.node import Node, NodeId

from backend.filesystem.exceptions import InvalidNodeJSONError, NodeMetadataMissingError

# Keys we expect to see in node.json. Extra keys are preserved (forward
# compatibility) but the required ones must all be present.
REQUIRED_KEYS = frozenset(
    {"id", "type", "title", "parent_id", "children_ids", "metadata"}
)


def node_to_dict(node: Node) -> dict[str, Any]:
    """Serialize a Node to the on-disk JSON shape.

    The inverse of `dict_to_node`. Stable field order is not promised.
    """
    return {
        "id": node.id,
        "type": node.type.value,
        "title": node.title,
        "parent_id": node.parent_id,
        "children_ids": list(node.children_ids),
        "metadata": node.metadata.as_dict(),
    }


def dict_to_node(data: dict[str, Any]) -> Node:
    """Parse a node.json payload into a domain Node.

    Raises:
        InvalidNodeJSONError: If a required key is missing or has the
                              wrong shape, or if `type` is unknown.
    """
    missing = REQUIRED_KEYS - data.keys()
    if missing:
        raise InvalidNodeJSONError(
            "<inline>", f"missing keys: {sorted(missing)}"
        )
    try:
        node_type = NodeType(data["type"])
    except ValueError as exc:
        raise InvalidNodeJSONError("<inline>", f"unknown type: {data['type']!r}") from exc
    try:
        return Node(
            id=NodeId(str(data["id"])),
            title=str(data["title"]),
            type=node_type,
            metadata=NodeMetadata.from_dict(data.get("metadata") or {}, node_type=node_type),
            parent_id=NodeId(str(data["parent_id"])) if data["parent_id"] else None,
            children_ids=[NodeId(str(c)) for c in data["children_ids"]],
            canvas=None,  # canvas lives in canvas.md, not JSON.
        )
    except (ValueError, TypeError) as exc:
        # Domain constructors raise ValueError on bad input; the
        # filesystem treats any such failure as corruption.
        raise InvalidNodeJSONError("<inline>", str(exc)) from exc


def write_node_json(path: Path, payload: dict[str, Any]) -> None:
    """Write `node.json` atomically (temp + rename).

    Uses `Path.replace` for POSIX-atomic semantics on the same
    filesystem. Indented with 2 spaces for human readability — the
    user can open any node.json in their editor.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        tmp.replace(path)
    except Exception:
        # If the rename never happened, the temp may still be on disk.
        # Best-effort cleanup; ignore failures (we're already in error).
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def read_node_json(path: Path) -> dict[str, Any]:
    """Read and parse a node.json payload.

    Raises:
        NodeMetadataMissingError: If the file does not exist.
        InvalidNodeJSONError: If the file exists but cannot be parsed.
    """
    if not path.exists():
        raise NodeMetadataMissingError(str(path.parent))
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise InvalidNodeJSONError(str(path.parent), f"JSON parse error: {exc.msg}") from exc