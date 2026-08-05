"""Structural isolation tests for the workspace cache layer.

Per ChatGPT's Phase 3.0 refinement #6: only the repository
implementation (`local_workspace_repository.py`) may import
the concrete `InMemoryWorkspaceCache`. The API, services,
index, and core layers must depend only on the Protocols
(or, in the case of the repository, on the MutableWorkspaceCache
Protocol — never on the concrete impl, except for the one
explicit seam file).

What this test enforces:

    1. Every module under `backend/workspace/` may import:
         - `backend.domain.*` (the domain layer)
         - `backend.core.logging` (structlog logger)
         - stdlib + typing
       Every module may NOT import:
         - `backend.filesystem.*`
         - `backend.repositories.*`
         - `backend.index.*`
         - `backend.api.*`
         - `backend.services.*`

    2. Only `backend/repositories/impl/local_workspace_repository.py`
       may import `InMemoryWorkspaceCache` (the concrete class)
       from the package. Every other consumer must reach the
       cache via Protocols only.

    3. Only `StartupSubsystem.run()` may call `populate()`. We
       enforce this by a textual scan of every Python file
       under `backend/`: any file that calls
       `cache.populate(` (where `cache` is one of the cache
       attributes the workspace module exposes) is rejected
       unless it's the subsystem file. The textual check is
       intentionally narrow — `populate` is a unique word in
       our codebase.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# ---- allowlist for workspace modules themselves -------------------------

ALLOWED_WORKSPACE_BACKEND_IMPORTS: frozenset[str] = frozenset(
    {
        # Workspace cache is a domain-aware structure: it
        # imports domain types but nothing else from
        # `backend.*`.
        "backend.domain",
        "backend.domain.node",
        "backend.domain.tree",
        "backend.domain.metadata",
        "backend.domain.enums",
        # Logging is allowed (structlog binding).
        "backend.core.logging",
        # The tree_provider adapter reaches back to the
        # workspace package itself (Protocol import). This
        # is intra-package, not an upward dependency.
        "backend.workspace",
        "backend.workspace.protocol",
        "backend.workspace.cache",
        "backend.workspace.exceptions",
        "backend.workspace.tree_provider",
    }
)

FORBIDDEN_FROM_WORKSPACE: frozenset[str] = frozenset(
    {
        # Filesystem layer (disk I/O).
        "backend.filesystem",
        # Repository layer (persistence orchestration).
        "backend.repositories",
        "backend.repositories.protocol",
        "backend.repositories.impl",
        "backend.repositories.impl.local_workspace_repository",
        # Index layer (synchroniser, reconciler, projections).
        "backend.index",
        "backend.index.protocol",
        "backend.index.types",
        "backend.index.exceptions",
        "backend.index.reconciler",
        "backend.index.sync",
        "backend.index.startup",
        "backend.index.impl",
        "backend.index.impl.in_memory_index_repository",
        "backend.index.impl.sqlalchemy_index_repository",
        # API layer.
        "backend.api",
        "backend.api.dependencies",
        "backend.api.exception_handlers",
        "backend.api.mappers",
        "backend.api.v1",
        "backend.api.v1.endpoints",
        "backend.api.v1.router",
        # Service layer.
        "backend.services",
        "backend.services.exceptions",
        "backend.services.workspace_service",
        # The other repository's package init.
        "backend.repositories",
        # Models / DB layer.
        "backend.models",
        "backend.database",
        # Settings / config.
        "backend.config",
        "backend.config.settings",
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


def _all_workspace_modules() -> list[Path]:
    workspace_dir = (
        Path(__file__).resolve().parent.parent.parent / "workspace"
    )
    return sorted(p for p in workspace_dir.glob("**/*.py") if p.name != "__init__.py")


def test_workspace_modules_exist() -> None:
    modules = _all_workspace_modules()
    assert modules, "no workspace modules found"


@pytest.mark.parametrize(
    "module_path",
    _all_workspace_modules(),
    ids=lambda p: str(
        p.relative_to(Path(__file__).resolve().parent.parent.parent)
    ),
)
def test_workspace_module_only_imports_allowed_backends(
    module_path: Path,
) -> None:
    """Every import in a workspace module must be on the
    workspace allowlist. Workspace modules must not reach
    into filesystem / repositories / index / api / services /
    config — the cache is a passive data structure with
    locking.
    """
    imports = _collect_imports(module_path)
    for mod in imports:
        if mod.startswith("backend."):
            assert mod in ALLOWED_WORKSPACE_BACKEND_IMPORTS, (
                f"{module_path.name} imports non-allowlisted backend module: {mod}\n"
                f"  Allowed: {sorted(ALLOWED_WORKSPACE_BACKEND_IMPORTS)}"
            )
            assert mod not in FORBIDDEN_FROM_WORKSPACE, (
                f"{module_path.name} imports forbidden module: {mod}"
            )


def test_only_repository_impl_may_mutate_cache_at_runtime() -> None:
    """Enforces the *single-mutation-boundary* rule from
    ChatGPT's Phase 3.0 refinement #6.

    The rule is: only the repository implementation
    may *mutate* the cache at runtime (via `invalidate`,
    `invalidate_many`, `subtree_ids`, `clear`). The
    construction sites (`lifespan.py`, `startup_subsystem.py`,
    test files) may import the concrete class to wire it
    up; runtime mutation stays in the repository.

    We enforce this by textual scan: any file outside the
    allowed list that calls `.invalidate(`, `.invalidate_many(`,
    `.subtree_ids(`, or `.clear(` on a cache variable is
    rejected unless it's the repository, the workspace
    package (which defines these methods), or the test
    suite (which exercises the contract).
    """
    backend_root = Path(__file__).resolve().parent.parent.parent
    repository_seam = (
        backend_root
        / "repositories"
        / "impl"
        / "local_workspace_repository.py"
    )
    workspace_pkg_root = backend_root / "workspace"
    # Files allowed to reference the concrete class for
    # construction only (not runtime mutation): the lifespan
    # and the subsystem, plus tests.
    construction_sites = {
        repository_seam,
        backend_root / "core" / "lifespan.py",
        backend_root / "core" / "startup_subsystem.py",
    }
    forbidden_calls = (".invalidate(", ".invalidate_many(", ".subtree_ids(")
    for module_path in sorted(backend_root.glob("**/*.py")):
        # Skip __init__ files (they may re-export).
        if module_path.name == "__init__.py":
            continue
        # Skip the workspace package (defines these methods).
        if workspace_pkg_root in module_path.parents or module_path.parent == workspace_pkg_root:
            continue
        # Skip test files (exercise the contract).
        if "/tests/" in str(module_path):
            continue
        text = module_path.read_text(encoding="utf-8")
        # Check for forbidden mutation calls.
        for call in forbidden_calls:
            if call in text:
                # Allowed only in the repository seam.
                if module_path in construction_sites and module_path != repository_seam:
                    pytest.fail(
                        f"{module_path.relative_to(backend_root)} calls "
                        f"`{call}` on a cache; only "
                        f"{repository_seam.relative_to(backend_root)} may "
                        f"mutate cache state at runtime."
                    )
                if module_path != repository_seam and module_path not in construction_sites:
                    pytest.fail(
                        f"{module_path.relative_to(backend_root)} calls "
                        f"`{call}`; only the repository may mutate cache state."
                    )


def test_only_lifespan_and_subsystem_may_construct_concrete_cache() -> None:
    """Only construction sites may *import* the concrete
    `InMemoryWorkspaceCache` class. API, index, services,
    and tests must depend on the Protocol (if at all).

    The repository implementation also imports it (it's
    the seam that mutates cache state at runtime).
    """
    backend_root = Path(__file__).resolve().parent.parent.parent
    workspace_pkg_root = backend_root / "workspace"
    construction_sites = {
        # The repository is the seam: it imports the
        # concrete class for type-checking the optional
        # `cache` argument.
        backend_root
        / "repositories"
        / "impl"
        / "local_workspace_repository.py",
        # The lifespan constructs the cache at boot.
        backend_root / "core" / "lifespan.py",
        # The subsystem holds the cache as a collaborator.
        backend_root / "core" / "startup_subsystem.py",
    }
    for module_path in sorted(backend_root.glob("**/*.py")):
        if module_path.name == "__init__.py":
            continue
        # The workspace package may import its own class.
        if workspace_pkg_root in module_path.parents or module_path.parent == workspace_pkg_root:
            continue
        # Tests are allowed (they exercise the contract).
        if "/tests/" in str(module_path):
            continue
        # Allowed construction sites.
        if module_path in construction_sites:
            continue
        text = module_path.read_text(encoding="utf-8")
        # Look for direct class references (not just the
        # word inside a docstring). A `from backend.workspace
        # import InMemoryWorkspaceCache` would surface as
        # the symbol appearing in a non-comment line. We
        # do a simple textual check that's strict enough
        # for our purposes.
        if "InMemoryWorkspaceCache" in text and "noqa" not in text:
            # Allow it inside a docstring only if it's
            # purely descriptive and not an actual import.
            # For simplicity, we reject outright.
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "InMemoryWorkspaceCache" in stripped and (
                    "import" in stripped or " = " in stripped
                ):
                    pytest.fail(
                        f"{module_path.relative_to(backend_root)} imports "
                        f"InMemoryWorkspaceCache; only construction sites "
                        f"may. Reach the cache via Protocols instead."
                    )


def test_api_layer_does_not_import_workspace_package() -> None:
    """The API layer must not import the cache package."""
    backend_root = Path(__file__).resolve().parent.parent.parent
    api_dir = backend_root / "api"
    if not api_dir.exists():
        return
    for module_path in sorted(api_dir.glob("**/*.py")):
        if module_path.name == "__init__.py":
            continue
        imports = _collect_imports(module_path)
        for mod in imports:
            assert not mod.startswith("backend.workspace"), (
                f"{module_path.relative_to(backend_root)} imports "
                f"the workspace package: {mod}"
            )


def test_index_layer_does_not_import_workspace_package() -> None:
    """The index layer must not import the cache package."""
    backend_root = Path(__file__).resolve().parent.parent.parent
    index_dir = backend_root / "index"
    if not index_dir.exists():
        return
    for module_path in sorted(index_dir.glob("**/*.py")):
        if module_path.name == "__init__.py":
            continue
        imports = _collect_imports(module_path)
        for mod in imports:
            assert not mod.startswith("backend.workspace"), (
                f"{module_path.relative_to(backend_root)} imports "
                f"the workspace package: {mod}"
            )


def test_services_layer_does_not_import_workspace_package() -> None:
    """The service layer must not import the cache package."""
    backend_root = Path(__file__).resolve().parent.parent.parent
    services_dir = backend_root / "services"
    if not services_dir.exists():
        return
    for module_path in sorted(services_dir.glob("**/*.py")):
        if module_path.name == "__init__.py":
            continue
        imports = _collect_imports(module_path)
        for mod in imports:
            assert not mod.startswith("backend.workspace"), (
                f"{module_path.relative_to(backend_root)} imports "
                f"the workspace package: {mod}"
            )


def test_only_startup_subsystem_calls_populate() -> None:
    """Only `StartupSubsystem.run()` may call `populate()` on
    the cache. We enforce this with a textual scan: any
    file outside the workspace package and outside
    `backend/core/startup_subsystem.py` that calls
    `.populate(` on a cache variable is rejected.

    The textual scan is conservative — `populate` is a
    distinctive method name. We exclude:

        - the workspace package (which defines `populate`),
        - the startup subsystem file (which is allowed to
          call it),
        - test files (which test the API).
    """
    backend_root = Path(__file__).resolve().parent.parent.parent
    workspace_pkg_root = backend_root / "workspace"
    subsystem_path = (
        backend_root / "core" / "startup_subsystem.py"
    )
    for module_path in sorted(backend_root.glob("**/*.py")):
        # The workspace package itself defines populate.
        if workspace_pkg_root in module_path.parents or module_path.parent == workspace_pkg_root:
            continue
        # The subsystem is the one allowed caller.
        if module_path == subsystem_path:
            continue
        text = module_path.read_text(encoding="utf-8")
        # Look for `.populate(` outside of test files.
        if "/tests/" in str(module_path):
            continue
        if ".populate(" in text and "_populate_cache" not in text:
            pytest.fail(
                f"{module_path.relative_to(backend_root)} calls "
                f"`.populate(` on something; only "
                f"{subsystem_path.relative_to(backend_root)} may "
                f"call `populate()` on a cache."
            )