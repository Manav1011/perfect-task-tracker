"""Structural isolation test for the configuration boundary.

Per TECH_SPEC §13g and ADR-0021 (Phase 3.3):

    - `backend.config` is the ONLY module that may read environment
      variables directly (via Pydantic BaseSettings).
    - All other production modules must consume configuration through
      `app.state` or via an injected dependency.
    - Direct `os.environ` / `os.getenv` reads outside `backend.config/`
      are a layering violation — this test fails the build when one
      slips in.

The configuration boundary this enforces:

    Pydantic settings → BackendConfig package (configurable) →
        lifespan → app.state → request handlers

    The right side of the arrow is the only place env vars are read.
    Everything else gets configuration via DI / app.state.

Allowlist:
    - `backend/config/` — the configuration layer itself.
    - `backend/database/session.py` and `backend/alembic/env.py` —
      documented carve-outs (these run before app.state exists:
      session.py is a lazy singleton, alembic is a standalone tool).

Test-only reads:
    - `backend/tests/benchmarks/` reads `RUN_LARGE_BENCH` to gate
      expensive benchmarks. Allowed because tests are not part of
      the production config boundary — they drive benchmarks, not
      application behavior.

AST-walking is the same approach used by every other isolation test
in this project (see `tests/test_api_isolation.py`,
`tests/test_services_isolation.py`, `tests/workspace/test_isolation.py`,
`tests/index/test_isolation.py`, `tests/search/test_isolation.py`).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


# Modules where os.environ / os.getenv IS allowed. Anything not in this
# allowlist that turns up in a subsequent change gets a build fail.
ALLOWED_CONFIG_READERS: frozenset[str] = frozenset(
    {
        # The configuration boundary itself — Pydantic BaseSettings does
        # the env reads internally; this module wraps that with
        # validation, defaults, and the precedence rule.
        "backend/config/settings.py",
        "backend/config/database.py",
        # Lazy engine construction runs before app.state exists.
        # Per the carve-out documented in CONFIGURATION_INVENTORY.md and
        # the plan's "Startup-time vs runtime-read classification".
        "backend/database/session.py",
        # Alembic migration tool — runs in its own process, never as
        # part of a request.
        "backend/alembic/env.py",
    }
)


def _all_backend_modules() -> list[Path]:
    """Walk `backend/` and return every .py file that isn't a test,
    isn't `__init__.py`, and isn't under a test directory."""
    backend_dir = Path(__file__).resolve().parent.parent
    out: list[Path] = []
    for path in sorted(backend_dir.glob("**/*.py")):
        rel = path.relative_to(backend_dir)
        parts = rel.parts
        if parts[0] == "__pycache__":
            continue
        if any(p == "tests" for p in parts):
            # Production isolation — test files use os.environ freely.
            continue
        if path.name == "__init__.py":
            continue
        out.append(path)
    return out


def _find_env_reads(module_path: Path) -> list[tuple[int, str]]:
    """Return `[(line, code-snippet), ...]` for every os.environ /
    os.getenv / os.getenvb / etc. reference in this module.

    We accept either `import os` followed by `os.environ` / `os.getenv`,
    or a `from os import environ` / `from os import getenv`. Both patterns
    appear in real code and both bypass the configuration layer.
    """
    src = module_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    hits: list[tuple[int, str]] = []

    def _is_env_attr(attr: str) -> bool:
        return attr in {"environ", "getenv", "getenvb"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            # os.environ, os.getenv, os.getenvb
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and _is_env_attr(node.attr)
            ):
                hits.append((node.lineno, f"os.{node.attr}"))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "os":
                for alias in node.names:
                    if alias.name in {"environ", "getenv", "getenvb"}:
                        hits.append((node.lineno, f"from os import {alias.name}"))
    return hits


def _rel(p: Path) -> str:
    """Return path with the `backend/` prefix normalised.

    `_all_backend_modules()` walks `backend/`, so the relative path is
    already `config/settings.py`-shaped. The allowlist is repo-relative
    (`backend/config/settings.py`). Strip the prefix before comparing.
    """
    return str(p).removeprefix(str(Path(__file__).resolve().parent.parent) + "/")


def test_configuration_isolation_no_direct_env_reads_outside_config() -> None:
    """All production modules other than the allowlist must NOT touch
    os.environ / os.getenv directly. The configuration boundary is
    the single point of contact with the environment."""
    violations: list[str] = []
    for path in _all_backend_modules():
        if _rel(path) in ALLOWED_CONFIG_READERS:
            continue
        for line_no, snippet in _find_env_reads(path):
            violations.append(f"{_rel(path)}:{line_no}: {snippet}")
    assert not violations, (
        "Direct os.environ / os.getenv reads must go through "
        "backend.config only. Violations:\n  "
        + "\n  ".join(violations)
        + "\n\nAdd the module path to ALLOWED_CONFIG_READERS (only "
        "with a documented carve-out) or refactor to read config via "
        "app.state / a Settings dependency."
    )


@pytest.mark.parametrize(
    "allowed_path",
    sorted(ALLOWED_CONFIG_READERS),
)
def test_allowlisted_module_is_actually_present(allowed_path: str) -> None:
    """Sanity: an entry in ALLOWED_CONFIG_READERS must exist on disk.
    Catches typos in the allowlist (a renamed/moved module would
    silently become policy without enforcement)."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    assert (repo_root / allowed_path).exists(), (
        f"ALLOWED_CONFIG_READERS contains {allowed_path!r}, but that "
        f"file does not exist. Remove the stale entry."
    )


def test_env_read_detector_recognises_violations(tmp_path) -> None:
    """Belt-and-suspenders: the AST detector MUST flag obvious
    environment-variable reads in non-allowlisted code.

    Without this, the structural test above could silently miss every
    real violation (a broken AST walker would still pass the main
    test because no violations exist today — but the boundary is
    exactly what we're trying to enforce going forward).

    We synthesise a fake module that reads `os.environ` and assert
    the detector flags it.
    """
    bad = tmp_path / "rogue.py"
    bad.write_text(
        "import os\n"
        "x = os.environ['FOO']\n"
        "y = os.getenv('BAR', 'baz')\n"
    )
    reads = _find_env_reads(bad)
    snippets = [s for _, s in reads]
    assert "os.environ" in snippets
    assert "os.getenv" in snippets


def test_env_read_detector_recognises_from_os_import() -> None:
    """AST walker also catches `from os import environ / getenv` — both
    are seen in real codebases. Both bypass the configuration layer
    even though they avoid the `os.` attribute-access shape."""
    import tempfile

    with tempfile.NamedTemporaryFile(
        suffix=".py", delete=False, mode="w"
    ) as f:
        f.write("from os import environ, getenv\nx = environ['FOO']\n")
        path = Path(f.name)
    try:
        reads = _find_env_reads(path)
        snippets = [s for _, s in reads]
        # Both patterns detected.
        assert any("environ" in s for s in snippets)
        assert any("getenv" in s for s in snippets)
    finally:
        path.unlink(missing_ok=True)
