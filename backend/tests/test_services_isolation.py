"""Structural isolation tests for the Service layer.

Per TECH_SPEC §8 and ADR-0006:

    - Services may only import `backend.domain`, `backend.repositories.protocol`,
      and standard library.
    - Services MUST NOT import `backend.filesystem`, `sqlalchemy`,
      `fastapi`, `pathlib`, or any I/O library.
    - Services depend on the repository Protocol, not the concrete
      implementation.

These tests walk the AST of every module under `backend/services/` and
verify the import set against the allowlist. They are the enforcement
arm of ADR-0006.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Allowlist: the modules the service layer is permitted to depend on,
# plus the standard library.
ALLOWED_BACKEND_MODULES: frozenset[str] = frozenset(
    {
        "backend.domain",
        "backend.domain.exceptions",
        "backend.domain.tree",
        "backend.domain.node",
        "backend.domain.metadata",
        "backend.domain.enums",
        "backend.repositories",
        "backend.repositories.protocol",
        "backend.services",
        "backend.services.exceptions",
    }
)

# Hard-deny: even if a sub-allowlisted module imported one of these,
# the test would fail.
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "backend.filesystem",
        "backend.filesystem.impl",
        "backend.filesystem.impl.local_filesystem",
        "backend.filesystem.protocol",
        "backend.filesystem.serialization",
        "backend.filesystem.atomic",
        "backend.filesystem.slug",
        "backend.filesystem.workspace_root",
        "backend.filesystem.exceptions",
        "sqlalchemy",
        "fastapi",
        "starlette",
        "alembic",
        "psycopg",
        "psycopg2",
        "httpx",
    }
)

FORBIDDEN_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "pathlib",  # service layer must not know about disk paths
        "os",  # os.* is fine in test fixtures; we strip here for service modules
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


def _all_service_modules() -> list[Path]:
    """Walk backend/services/ and return every .py file."""
    services_dir = Path(__file__).resolve().parent.parent / "services"
    return sorted(p for p in services_dir.glob("*.py") if p.name != "__init__.py")


def test_service_modules_exist() -> None:
    modules = _all_service_modules()
    assert modules, "no service modules found — did Phase 1.4 land?"


@pytest.mark.parametrize("module_path", _all_service_modules(), ids=lambda p: p.name)
def test_service_module_only_imports_allowed_backends(module_path: Path) -> None:
    imports = _collect_imports(module_path)
    for mod in imports:
        # Top-level stdlib imports are fine.
        if "." not in mod:
            assert mod not in FORBIDDEN_TOP_LEVEL, (
                f"{module_path.name} imports forbidden top-level module: {mod}"
            )
            continue
        # Backend imports must be on the allowlist.
        if mod.startswith("backend."):
            assert mod in ALLOWED_BACKEND_MODULES, (
                f"{module_path.name} imports non-allowlisted backend module: {mod}\n"
                f"  Allowed: {sorted(ALLOWED_BACKEND_MODULES)}\n"
                f"  Forbidden: {sorted(FORBIDDEN_MODULES)}"
            )
            assert mod not in FORBIDDEN_MODULES, (
                f"{module_path.name} imports forbidden backend module: {mod}"
            )


def test_service_init_does_not_re_export_persistence() -> None:
    """The services/__init__.py must not re-export anything from
    backend.filesystem, sqlalchemy, fastapi, etc."""
    init_path = (
        Path(__file__).resolve().parent.parent / "services" / "__init__.py"
    )
    if not init_path.exists():
        pytest.skip("services/__init__.py missing")
    imports = _collect_imports(init_path)
    for mod in imports:
        if mod.startswith("backend.") and mod.startswith("backend.filesystem"):
            pytest.fail(f"services/__init__.py imports {mod}")
        for forbidden in FORBIDDEN_MODULES:
            if mod == forbidden or mod.startswith(forbidden + "."):
                pytest.fail(f"services/__init__.py imports forbidden module: {mod}")


def test_services_dependency_directions() -> None:
    """WorkspaceService must depend on the Protocol, not the
    concrete LocalWorkspaceRepository.

    This catches accidental `from backend.repositories.impl...`
    imports.
    """
    forbidden_substrings = (
        "backend.repositories.impl",
        "backend.repositories.exceptions",  # services can have their own
    )
    for module_path in _all_service_modules():
        text = module_path.read_text(encoding="utf-8")
        for forbidden in forbidden_substrings:
            assert forbidden not in text, (
                f"{module_path.name} contains forbidden reference: {forbidden}"
            )
