"""WorkspaceRoot — validates and normalizes the configured workspace path.

Owns the absolute, resolved path to the workspace root directory and
the path to its `.ptt/` config marker. Created via the classmethod
`open()`; rejected paths raise `WorkspaceRootError` with a clear
reason.

`WorkspaceRoot` is a value object — once constructed, it is
immutable. Constructing a new one for a different path is cheap.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.filesystem.exceptions import WorkspaceRootError


@dataclass(frozen=True, slots=True)
class WorkspaceRoot:
    """Absolute, validated path to a workspace root.

    A valid workspace root is:
        - an existing directory,
        - readable,
        - writable,
        - contains a `.ptt/` subdirectory (the workspace marker).
    """

    path: Path
    config_dir: Path

    @classmethod
    def open(cls, path: str | Path, *, create: bool = False) -> "WorkspaceRoot":
        """Validate and wrap a workspace root path.

        Args:
            path: Candidate root. Resolved to an absolute path.
            create: If True, create the root and its `.ptt/` marker if
                    they do not exist. If False (default), both must
                    already exist.

        Raises:
            WorkspaceRootError: If the path does not meet the validity
                                criteria described in the class docstring.
        """
        candidate = Path(path).expanduser().resolve()
        if not candidate.exists():
            if create:
                candidate.mkdir(parents=True, exist_ok=True)
            else:
                raise WorkspaceRootError(f"path does not exist: {candidate}")
        if not candidate.is_dir():
            raise WorkspaceRootError(f"path is not a directory: {candidate}")
        config_dir = candidate / ".ptt"
        if not config_dir.exists():
            if create:
                config_dir.mkdir(parents=True, exist_ok=True)
            else:
                raise WorkspaceRootError(
                    f"workspace marker missing: {config_dir} "
                    "(does this directory contain a .ptt/ folder?)"
                )
        # Read/write check — the cheapest probe that catches permission
        # issues before the user hits them on first save.
        if not os_access_check(candidate):
            raise WorkspaceRootError(f"path is not accessible: {candidate}")
        return cls(path=candidate, config_dir=config_dir)

    def child(self, *parts: str | Path) -> Path:
        """Return an absolute path under this root.

        Joins parts and resolves any `..` components. The result is
        guaranteed to live under `self.path`; otherwise raises.
        """
        joined = self.path.joinpath(*parts).resolve()
        try:
            joined.relative_to(self.path)
        except ValueError as exc:
            raise WorkspaceRootError(
                f"path escapes workspace root: {joined}"
            ) from exc
        return joined


def os_access_check(path: Path) -> bool:
    """Cheap read/write probe. Returns True if the path is usable.

    Implemented with `os.access` rather than actually writing a file
    so probes don't litter the workspace.
    """
    import os

    return os.access(path, os.R_OK | os.W_OK | os.X_OK)