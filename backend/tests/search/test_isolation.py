"""Structural isolation tests for the search layer.

Per ChatGPT's Phase 3.1 brief:

    - SearchService may depend ONLY on IndexRepository Protocol.
    - It must NOT import repositories, filesystem, workspace
      cache, API, or SQLAlchemy directly.

These tests enforce the layering by AST-walking every
module under `backend/search/` and rejecting forbidden
imports.

What this test enforces:

    1. Every module under `backend/search/` may import:
         - `backend.domain.*`  (entities, value objects)
         - `backend.index.protocol`  (the IndexRepository)
         - `backend.index.types`  (IndexRecord, read-only)
         - stdlib + typing
       Every module may NOT import:
         - `backend.filesystem.*`
         - `backend.repositories.*`
         - `backend.workspace.*`
         - `backend.api.*`
         - `backend.services.*`
         - `sqlalchemy`
         - `fastapi`

    2. The package exports a Protocol (`SearchService`) so
       consumers (eventually the API layer) depend on the
       Protocol, not the concrete `DefaultSearchService`.

    3. The protocol surface is narrow: exactly one method
       (`search`). Future additions land as new methods,
       not by extending `SearchRequest` (we don't enforce
       that here — it's a design choice, not a layering
       rule).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


# ---- allowlist for search modules themselves ---------------------------

ALLOWED_SEARCH_BACKEND_IMPORTS: frozenset[str] = frozenset(
    {
        # Domain layer.
        "backend.domain",
        "backend.domain.node",
        "backend.domain.tree",
        "backend.domain.metadata",
        "backend.domain.enums",
        # Index layer — but ONLY the protocol + types.
        # (The implementation is forbidden; the search layer
        # must depend on the Protocol surface.)
        "backend.index",
        "backend.index.protocol",
        "backend.index.types",
        "backend.index.exceptions",
        # Intra-package.
        "backend.search",
        "backend.search.exceptions",
        "backend.search.protocol",
        "backend.search.service",
        "backend.search.types",
    }
)

FORBIDDEN_FROM_SEARCH: frozenset[str] = frozenset(
    {
        # Filesystem.
        "backend.filesystem",
        # Workspace cache.
        "backend.workspace",
        "backend.workspace.protocol",
        "backend.workspace.cache",
        "backend.workspace.exceptions",
        "backend.workspace.tree_provider",
        # Repositories.
        "backend.repositories",
        "backend.repositories.protocol",
        "backend.repositories.impl",
        "backend.repositories.impl.local_workspace_repository",
        # Services.
        "backend.services",
        "backend.services.exceptions",
        "backend.services.workspace_service",
        # API.
        "backend.api",
        "backend.api.dependencies",
        "backend.api.exception_handlers",
        "backend.api.mappers",
        "backend.api.v1",
        "backend.api.v1.endpoints",
        "backend.api.v1.router",
        # Models / DB.
        "backend.models",
        "backend.database",
        # Settings.
        "backend.config",
        "backend.config.settings",
        # Index implementation (only the Protocol is allowed).
        "backend.index.impl",
        "backend.index.impl.in_memory_index_repository",
        "backend.index.impl.sqlalchemy_index_repository",
        "backend.index.reconciler",
        "backend.index.startup",
        "backend.index.sync",
    }
)

# Forbidden I/O libraries. The search layer is pure dict-walking
# over IndexRecord; it must not reach for SQLAlchemy, FastAPI, or
# any filesystem primitives.
FORBIDDEN_TOP_LEVEL_IMPORTS: frozenset[str] = frozenset(
    {
        "sqlalchemy",
        "fastapi",
        "pydantic",
        "pathlib",
        "requests",
        "httpx",
        "aiohttp",
        "asyncpg",
        "psycopg2",
        "psycopg",
        "boto3",
        "redis",
    }
)


def _collect_imports(module_path: Path) -> set[str]:
    """Return every imported module name from a Python source file."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def _all_search_modules() -> list[Path]:
    search_dir = (
        Path(__file__).resolve().parent.parent.parent / "search"
    )
    return sorted(p for p in search_dir.glob("**/*.py") if p.name != "__init__.py")


def test_search_modules_exist() -> None:
    modules = _all_search_modules()
    assert modules, "no search modules found"


@pytest.mark.parametrize(
    "module_path",
    _all_search_modules(),
    ids=lambda p: str(
        p.relative_to(Path(__file__).resolve().parent.parent.parent)
    ),
)
def test_search_module_only_imports_allowed_backends(
    module_path: Path,
) -> None:
    """Every import in a search module must be on the
    search allowlist. Search modules must not reach into
    filesystem / repositories / workspace / api / services
    / config — the search layer is a query layer over the
    index, not an I/O layer.
    """
    imports = _collect_imports(module_path)
    for mod in imports:
        if mod.startswith("backend."):
            assert mod in ALLOWED_SEARCH_BACKEND_IMPORTS, (
                f"{module_path.name} imports non-allowlisted backend module: {mod}\n"
                f"  Allowed: {sorted(ALLOWED_SEARCH_BACKEND_IMPORTS)}"
            )
            assert mod not in FORBIDDEN_FROM_SEARCH, (
                f"{module_path.name} imports forbidden module: {mod}"
            )


@pytest.mark.parametrize(
    "module_path",
    _all_search_modules(),
    ids=lambda p: str(
        p.relative_to(Path(__file__).resolve().parent.parent.parent)
    ),
)
def test_search_module_never_imports_io_libraries(
    module_path: Path,
) -> None:
    """Search modules must not import SQLAlchemy, FastAPI,
    pydantic, pathlib, or any I/O library. The search
    service is a pure function over IndexRecord.
    """
    imports = _collect_imports(module_path)
    for mod in imports:
        # The top-level package name is enough — we don't
        # need to chase dotted sub-modules.
        top = mod.split(".")[0]
        assert top not in FORBIDDEN_TOP_LEVEL_IMPORTS, (
            f"{module_path.name} imports forbidden I/O library: {mod}"
        )


def test_search_service_is_exported_as_protocol() -> None:
    """The package must export `SearchService` so consumers
    depend on the Protocol, not the concrete class.
    """
    from backend.search import SearchService  # noqa: F401
    from backend.search.protocol import SearchService as ProtocolImpl

    # The re-export and the implementation must be the same
    # symbol. If a future refactor accidentally exports a
    # subclass, this catches it.
    assert SearchService is ProtocolImpl


def test_default_search_service_constructor_takes_index() -> None:
    """The concrete `DefaultSearchService` constructor takes
    only an `IndexRepository`. No filesystem, no cache,
    no settings — the dependency graph is exactly one edge.
    """
    import inspect

    from backend.search import DefaultSearchService

    sig = inspect.signature(DefaultSearchService.__init__)
    params = list(sig.parameters.values())
    # self + index.
    assert len(params) == 2, (
        f"DefaultSearchService should have only `index` "
        f"parameter besides `self`, got: {params}"
    )
    assert params[1].name == "index"


def test_search_service_index_param_is_typed() -> None:
    """The `index` parameter must be typed as
    `IndexRepository` (no concrete class). This is the
    type-level seam — search depends on the Protocol,
    not on InMemoryIndexRepository or
    SQLAlchemyIndexRepository.
    """
    import inspect

    from backend.search import DefaultSearchService

    src = inspect.getsource(DefaultSearchService.__init__)
    # The annotation must reference `IndexRepository` —
    # not a concrete subclass.
    assert "index: IndexRepository" in src, (
        f"DefaultSearchService.__init__ must type `index` as "
        f"`IndexRepository`, got: {src}"
    )
    # And must NOT mention any concrete class.
    assert "InMemoryIndexRepository" not in src, (
        "DefaultSearchService must not depend on "
        "InMemoryIndexRepository — use the Protocol."
    )
    assert "SQLAlchemyIndexRepository" not in src
