"""Domain enums.

Small, closed sets of values that the system understands. Keeping them
in one place makes the type vocabulary greppable.
"""

from __future__ import annotations

from enum import Enum


class NodeType(str, Enum):
    """Discriminator for what *kind* of Node something is.

    Inherits from `str` so values serialize cleanly to JSON and round-
    trip through the API layer without a custom encoder. New types
    should be added by extending this enum — no schema migration needed
    (Invariant §6: UUIDs are stable; types are flexible).
    """

    STORY = "story"
    TASK = "task"
    NOTE = "note"