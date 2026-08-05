"""Index layer exceptions.

These map to HTTP 5xx (or 404 if `node_id` is missing) once the
API surface lands. The IndexRepository raises these directly;
no separate service-layer mapping is needed because the index
isn't reachable from the API in Phase 2.0.
"""

from __future__ import annotations


class IndexError(Exception):
    """Base class for index layer failures."""


class IndexRecordNotFoundError(IndexError):
    """Raised when `get(node_id)` finds no row.

    Distinct from a generic IndexError because "not in the
    index" is not the same as "the index is broken" — the
    former is normal during a partial rebuild.
    """

    def __init__(self, node_id: str) -> None:
        super().__init__(f"no index record for node_id={node_id!r}")
        self.node_id = node_id
