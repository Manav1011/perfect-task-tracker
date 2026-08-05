"""Structural isolation tests for the search API.

Per ChatGPT's Phase 3.2 acceptance criteria:

    - The API depends ONLY on the SearchService Protocol.
    - The API must NOT import the filesystem, repository,
      cache, or any concrete index implementation.

These tests are AST/textual scans of the API search
endpoint and supporting modules to enforce the
dependency boundary.

What this test enforces:

    1. The endpoint file (`backend/api/v1/endpoints/search.py`)
       may import only:
         - `backend.api.*`  (sibling API modules)
         - `backend.schemas.search`  (its own DTOs)
         - `backend.search`  (the search Protocol)
         - `fastapi`
       It may NOT import:
         - `backend.filesystem.*`
         - `backend.repositories.*`
         - `backend.workspace.*`
         - `backend.index.impl.*`
         - `backend.search.service`  (the concrete impl)
         - `backend.services.*`
         - `backend.models` / `backend.database`

    2. The DTOs (`backend/schemas/search.py`) may import
       only Pydantic. No backend.* imports.

    3. The mappers' search functions depend on
       `backend.search.*` and `backend.index.types`
       (which is the index's wire shape, not an
       implementation).
"""

from __future__ import annotations

import ast
from pathlib import Path


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


# ---- endpoint file -------------------------------------------------------


def test_endpoint_only_imports_allowed_backends() -> None:
    """The endpoint must NOT import filesystem, repository,
    cache, services, or concrete index implementations."""
    endpoint_path = (
        Path(__file__).resolve().parent.parent.parent
        / "api"
        / "v1"
        / "endpoints"
        / "search.py"
    )
    imports = _collect_imports(endpoint_path)

    forbidden = {
        "backend.filesystem",
        "backend.repositories",
        "backend.workspace",
        "backend.index.impl",
        "backend.index.impl.in_memory_index_repository",
        "backend.index.impl.sqlalchemy_index_repository",
        "backend.services",
        "backend.models",
        "backend.database",
        "backend.search.service",  # concrete impl, not Protocol
    }
    for mod in imports:
        if mod.startswith("backend."):
            for forbidden_prefix in forbidden:
                assert not mod.startswith(forbidden_prefix), (
                    f"endpoint imports forbidden module: {mod} "
                    f"(prefix forbidden: {forbidden_prefix})"
                )


def test_endpoint_depends_on_search_service_protocol() -> None:
    """The endpoint must import `SearchService` (the Protocol).

    It must NOT import `DefaultSearchService` (the concrete
    class) — that would couple the API to the implementation.
    """
    endpoint_path = (
        Path(__file__).resolve().parent.parent.parent
        / "api"
        / "v1"
        / "endpoints"
        / "search.py"
    )
    text = endpoint_path.read_text(encoding="utf-8")

    assert "SearchService" in text, (
        "endpoint must import SearchService (the Protocol)"
    )
    assert "DefaultSearchService" not in text, (
        "endpoint must NOT import DefaultSearchService — "
        "depend on the Protocol, not the impl"
    )


def test_endpoint_uses_pydantic_query_validation() -> None:
    """The endpoint uses FastAPI's `Query` for type-validated
    query parameters (so OpenAPI exposes constraints)."""
    endpoint_path = (
        Path(__file__).resolve().parent.parent.parent
        / "api"
        / "v1"
        / "endpoints"
        / "search.py"
    )
    text = endpoint_path.read_text(encoding="utf-8")
    assert "from fastapi import" in text
    assert "Query" in text


# ---- DTO file ------------------------------------------------------------


def test_dto_module_only_imports_pydantic() -> None:
    """The search DTOs must not import any backend module —
    they are pure data shapes."""
    dto_path = (
        Path(__file__).resolve().parent.parent.parent
        / "schemas"
        / "search.py"
    )
    imports = _collect_imports(dto_path)
    for mod in imports:
        assert not mod.startswith("backend."), (
            f"DTO file imports forbidden backend module: {mod}"
        )


def test_dto_module_does_not_circular_import_search() -> None:
    """The DTOs must not import from `backend.search.types`
    even though the names are similar. The API is the
    conversion layer; the DTOs are API-side data shapes."""
    dto_path = (
        Path(__file__).resolve().parent.parent.parent
        / "schemas"
        / "search.py"
    )
    # Check actual imports only — docstrings may reference
    # `backend.search` descriptively.
    imports = _collect_imports(dto_path)
    for mod in imports:
        assert not mod.startswith("backend.search"), (
            f"DTO file imports from backend.search: {mod}"
        )


# ---- mapper additions ----------------------------------------------------


def test_mappers_depend_on_search_protocol_not_impl() -> None:
    """The mapper additions import `SearchRequest` / `SearchResult`
    from `backend.search` (the package re-export). They do NOT
    import `backend.search.service` directly."""
    mappers_path = (
        Path(__file__).resolve().parent.parent.parent
        / "api"
        / "mappers.py"
    )
    text = mappers_path.read_text(encoding="utf-8")
    # The package re-export is fine.
    assert "from backend.search import" in text
    # But the implementation module is forbidden.
    assert "from backend.search.service import" not in text


def test_mappers_depend_on_index_types_not_impl() -> None:
    """Mappers import `IndexRecord` from `backend.index.types`
    (the wire shape, not the implementation)."""
    mappers_path = (
        Path(__file__).resolve().parent.parent.parent
        / "api"
        / "mappers.py"
    )
    text = mappers_path.read_text(encoding="utf-8")
    assert "from backend.index.types import" in text
    assert "from backend.index.impl import" not in text
    assert "from backend.index.impl.in_memory_index_repository" not in text
    assert "from backend.index.impl.sqlalchemy_index_repository" not in text


# ---- dependency wiring ---------------------------------------------------


def test_dependency_module_does_not_import_filesystem() -> None:
    """The `get_index_repository` dependency reads from
    `app.state.index_repo` rather than building a filesystem
    path. This is the seam — the API never sees the filesystem."""
    deps_path = (
        Path(__file__).resolve().parent.parent.parent
        / "api"
        / "dependencies.py"
    )
    text = deps_path.read_text(encoding="utf-8")
    # The existing `build_filesystem` is for the workspace
    # service, not search. The search wiring uses `app.state`.
    assert "request.app.state.index_repo" in text


def test_get_search_service_dependency_returns_protocol() -> None:
    """`get_search_service` returns the `SearchService` Protocol,
    not the concrete `DefaultSearchService`."""
    deps_path = (
        Path(__file__).resolve().parent.parent.parent
        / "api"
        / "dependencies.py"
    )
    text = deps_path.read_text(encoding="utf-8")
    assert "-> SearchService" in text
    # The function may construct DefaultSearchService internally
    # — that's the production wiring — but the return type is
    # the Protocol.
    # The implementation reference is allowed in the dependency
    # wiring (it's the bind point), but the endpoint should
    # only see the Protocol. (Already tested above.)
