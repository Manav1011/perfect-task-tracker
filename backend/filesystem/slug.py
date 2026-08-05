"""Slug generation — derive filesystem-safe folder names from titles.

Pure functions; no I/O. Lives in the filesystem package because the
slug contract is part of the disk-layout spec, not the domain.
"""

from __future__ import annotations

import re
import unicodedata

_NON_SLUG = re.compile(r"[^a-z0-9-]+")
_DASHES = re.compile(r"-+")
_LEADING_TRAILING_DASH = re.compile(r"^-+|-+$")


def slugify(title: str) -> str:
    """Return a filesystem-safe slug for `title`.

    Algorithm:
        1. NFKD-normalize and strip combining marks.
        2. Lowercase.
        3. Replace any non `[a-z0-9-]` run with a single `-`.
        4. Collapse repeated dashes.
        5. Trim leading/trailing dashes.
        6. Fallback to "node" for empty results.

    The slug is not unique. The filesystem layer de-duplicates when
    the slug collides with a sibling (by appending `-2`, `-3`, …).
    """
    text = unicodedata.normalize("NFKD", title)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = _NON_SLUG.sub("-", text)
    text = _DASHES.sub("-", text)
    text = _LEADING_TRAILING_DASH.sub("", text)
    return text or "node"