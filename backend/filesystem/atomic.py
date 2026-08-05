"""Atomic file write — temp + rename on the same filesystem.

`Path.replace` is atomic on POSIX and Windows when source and target
are on the same filesystem, which we guarantee by putting the temp
file next to the target. Crash before the rename leaves the original
file untouched; crash after leaves the new file in place.

Per Architecture Requirement §7.
"""

from __future__ import annotations

from pathlib import Path


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write `content` to `path` atomically.

    The temp file lives next to the target so the final rename stays
    on the same filesystem.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding=encoding) as fh:
            fh.write(content)
        tmp.replace(path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Byte-string variant of `atomic_write_text`."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("wb") as fh:
            fh.write(content)
        tmp.replace(path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise