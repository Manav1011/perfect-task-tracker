"""Commit 2 of Phase 3.3 — workspace_path flows from Settings to the
Filesystem substrate.

Previously `build_filesystem()` hardcoded `./data/workspace` and the
verify harness used a CWD-symlink trick to redirect it. Phase 3.3
threads the path through `Settings.workspace_path` and `WORKSPACE_PATH`
env var.
"""

from __future__ import annotations

from pathlib import Path

from backend.api.dependencies import build_filesystem
from backend.config.settings import Settings, get_settings


def _reset_settings_cache() -> None:
    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_settings_default_workspace_path() -> None:
    """The default is `./data/workspace` — back-compat with pre-Phase-3.3."""
    s = Settings()
    assert s.workspace_path == Path("./data/workspace")


def test_workspace_path_resolved_from_env(monkeypatch, tmp_path) -> None:
    """WORKSPACE_PATH env var overrides the default."""
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path / "my-ws"))
    s = Settings()
    assert s.workspace_path == Path(str(tmp_path / "my-ws"))


def test_build_filesystem_uses_settings_workspace_path(tmp_path) -> None:
    """build_filesystem() with no args reads Settings().workspace_path."""
    _reset_settings_cache()
    settings = Settings(workspace_path=tmp_path / "from-settings")
    fs = build_filesystem(settings=settings)
    # The filesystem's root must reflect the settings path.
    assert fs.root.path == (tmp_path / "from-settings").resolve()


def test_build_filesystem_explicit_path_overrides_settings(tmp_path) -> None:
    """Explicit `workspace_path` kwarg wins (used by tests with a tmpdir)."""
    settings = Settings(workspace_path=tmp_path / "ignored")
    fs = build_filesystem(workspace_path=tmp_path / "explicit", settings=settings)
    assert fs.root.path == (tmp_path / "explicit").resolve()


def test_build_filesystem_creates_workspace_root(tmp_path) -> None:
    """The root directory is created on first open (create=True by default)."""
    target = tmp_path / "auto-created"
    assert not target.exists()
    fs = build_filesystem(workspace_path=target)
    assert target.exists()
    # The .ptt/ marker should be present.
    assert (target / ".ptt").exists()
