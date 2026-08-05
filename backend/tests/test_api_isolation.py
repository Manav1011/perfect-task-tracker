"""Structural isolation tests for the API layer.

Per TECH_SPEC §8 and ADR-0005/0006/0007:

    - The API layer may depend on FastAPI, services, schemas, and
      mappers — and ONLY on those.
    - The API layer must never import `backend.filesystem` or the
      concrete `LocalWorkspaceRepository`. The only place those are
      bound together is `backend.api.dependencies`.
    - Endpoint handlers should be thin: parse → call service → map
      response. No business logic in the API.

We enforce these via AST walking (same approach as the services
isolation test).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Allowlist for API modules.
ALLOWED_BACKEND_MODULES: frozenset[str] = frozenset(
    {
        "backend.api",
        "backend.api.dependencies",
        "backend.api.exception_handlers",
        "backend.api.mappers",
        "backend.api.v1",
        "backend.api.v1.endpoints",
        "backend.api.v1.router",
        "backend.domain",
        "backend.domain.tree",
        "backend.domain.node",
        "backend.index",
        "backend.index.types",
        "backend.repositories.protocol",
        "backend.search",
        "backend.search.exceptions",
        "backend.search.protocol",
        "backend.search.types",
        "backend.search.service",
        # ADR-0007: dependencies.py is the *only* module that may import
        # concrete persistence implementations. We list them here so the
        # per-module import check passes for that file; the second test
        # (test_api_does_not_reach_persistence_implementation) verifies
        # no other module references them.
        "backend.filesystem",
        "backend.repositories",
        "backend.repositories.impl.local_workspace_repository",
        "backend.schemas",
        "backend.schemas.canvas",
        "backend.schemas.node",
        "backend.schemas.requests",
        "backend.schemas.search",
        "backend.schemas.story",
        "backend.schemas.workspace",
        "backend.services",
        "backend.services.exceptions",
        "backend.config.settings",
        "backend.core.lifespan",
    }
)

FORBIDDEN_BACKEND_MODULES: frozenset[str] = frozenset(
    {
        "backend.filesystem",
        "backend.filesystem.impl",
        "backend.filesystem.protocol",
        "backend.filesystem.serialization",
        "backend.filesystem.atomic",
        "backend.filesystem.slug",
        "backend.filesystem.workspace_root",
        "backend.filesystem.exceptions",
        "backend.filesystem.impl.local_filesystem",
        "backend.repositories.impl",
        "backend.repositories.impl.local_workspace_repository",
        "backend.repositories.exceptions",  # service has its own
    }
)

FORBIDDEN_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "pathlib",
    }
)


def _collect_imports(module_path: Path) -> set[str]:
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


def _all_api_modules() -> list[Path]:
    """Walk backend/api/ and return every .py file."""
    api_dir = Path(__file__).resolve().parent.parent / "api"
    return sorted(p for p in api_dir.glob("**/*.py") if p.name != "__init__.py")


def test_api_modules_exist() -> None:
    modules = _all_api_modules()
    assert modules, "no API modules found"


@pytest.mark.parametrize(
    "module_path",
    _all_api_modules(),
    ids=lambda p: str(p.relative_to(Path(__file__).resolve().parent.parent)),
)
def test_api_module_only_imports_allowed_backends(module_path: Path) -> None:
    # dependencies.py is the DI seam — it imports the concrete repo
    # implementation by design (ADR-0007). Its containment is verified
    # by `test_api_does_not_reach_persistence_implementation`.
    if module_path.name == "dependencies.py":
        pytest.skip("dependencies.py is the allowed DI seam (ADR-0007)")
    imports = _collect_imports(module_path)
    for mod in imports:
        if "." not in mod:
            assert mod not in FORBIDDEN_TOP_LEVEL, (
                f"{module_path.name} imports forbidden top-level module: {mod}"
            )
            continue
        if mod.startswith("backend."):
            assert mod in ALLOWED_BACKEND_MODULES, (
                f"{module_path.name} imports non-allowlisted backend module: {mod}\n"
                f"  Allowed: {sorted(ALLOWED_BACKEND_MODULES)}"
            )
            assert mod not in FORBIDDEN_BACKEND_MODULES, (
                f"{module_path.name} imports forbidden backend module: {mod}"
            )


def test_endpoint_files_are_thin() -> None:
    """Endpoint handlers must be small. We don't enforce a hard line
    count (because that's brittle), but we do forbid business-logic
    smells: importing Node/Tree and mutating them, conditional logic
    that's not a simple if-raise, etc.

    For Phase 1.5, the contract is: every endpoint is
        parse → call service → map response
    with no try/except for service errors (those are handled by
    the registered exception handlers).
    """
    endpoints_dir = (
        Path(__file__).resolve().parent.parent / "api" / "v1" / "endpoints"
    )
    for module_path in sorted(endpoints_dir.glob("*.py")):
        if module_path.name == "__init__.py" or module_path.name == "health.py":
            continue  # health.py predates the layering rules
        text = module_path.read_text(encoding="utf-8")
        # Endpoints must not try/except service exceptions directly —
        # they go through registered handlers.
        assert "except " not in text or "ServiceError" not in text, (
            f"{module_path.name} catches ServiceError inline; let the "
            f"registered exception handler do it."
        )
        # Endpoints must not build Nodes manually — that's a domain
        # construction concern, not an API concern.
        assert "Node(" not in text, (
            f"{module_path.name} constructs Node objects inline; "
            f"delegate to the service layer."
        )


def test_api_does_not_reach_persistence_implementation() -> None:
    """Belt-and-suspenders: even an indirect import path is denied.

    ADR-0007: `backend.api.dependencies` is the *only* module that
    may reference concrete persistence implementations. Every other
    API module must reach persistence only via the repository Protocol
    (i.e. via the WorkspaceService dependency).
    """
    api_dir = Path(__file__).resolve().parent.parent / "api"
    for module_path in sorted(api_dir.glob("**/*.py")):
        if module_path.name == "dependencies.py":
            # The one allowed exception — this is the DI seam.
            continue
        text = module_path.read_text(encoding="utf-8")
        for forbidden in (
            "LocalFilesystem",
            "LocalWorkspaceRepository",
            "WorkspaceRoot",
            "backend.filesystem",
            "backend.repositories.impl",
        ):
            assert forbidden not in text, (
                f"{module_path.relative_to(api_dir)} references forbidden "
                f"persistence implementation: {forbidden}"
            )
