"""Structural isolation tests for the index layer.

Per ADR-0011 + TECH_SPEC §13, the index is a *passive cache*.
Neither the API layer nor the service layer may import from
the index, and the index must never import the API or service
modules. We enforce both directions via AST walking.

Per the Phase 2.1 brief, the Reconciler is the *explicit*
exception: it depends on both Protocols (IndexRepository and
WorkspaceRepository) by design — it's the orchestrator that
knows about both repositories. The Reconciler still must NOT
import any concrete repository implementation, the Filesystem
layer, or SQLAlchemy models. Two tests cover that distinction:

    - `test_index_module_only_imports_allowed_backends` —
      forbids everything except the curated allowlist.
    - `test_reconciler_does_not_reach_concrete_repositories` —
      a stronger guarantee: even the Reconciler cannot import
      LocalWorkspaceRepository or anything from
      `backend.repositories.impl`.

Why this matters now:

    - Phase 2.0 establishes the boundary. Future phases will
      try (with reason) to wire the index into write paths;
      this test makes that an explicit decision, not an
      accident.
    - If a refactor ever lets a service module reach into
      SQLAlchemyIndexRepository directly, we want the diff to
      fail this test so the choice lands in code review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Index modules — the ONLY places allowed to import each other.
#
# Phase 2.1: the `reconciler` is on the index-side allowlist and
# pulls `backend.repositories.protocol` (the WorkspaceRepository
# Protocol, not the concrete class). That Protocol is *the*
# dependency-inversion seam — the index reads domain objects
# through it and never sees the filesystem.
#
# Phase 2.2: the `sync` module joins the allowlist. Like the
# Reconciler, it depends on the WorkspaceRepository Protocol
# (for Tree access via the `WorkspaceTreeProvider` Protocol).
# It does NOT depend on the concrete repository, the Filesystem,
# FastAPI, or SQLAlchemy models.
ALLOWED_INDEX_MODULES: frozenset[str] = frozenset(
    {
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
        # Index depends on the ORM model — that's allowed.
        "backend.models",
        "backend.models.node_index",
        # Index touches the database engine/session.
        "backend.database",
        "backend.database.base",
        "backend.database.session",
        # Index depends on the domain.
        "backend.domain",
        "backend.domain.node",
        "backend.domain.tree",
        "backend.domain.metadata",
        "backend.domain.enums",
        # The Reconciler reads the WorkspaceRepository Protocol.
        # This is the index's *legitimate* outward dependency:
        # the Protocol IS the dependency-inversion seam.
        "backend.repositories.protocol",
        # Index reads Settings.
        "backend.config.settings",
    }
)

# Modules the index must never reach. These represent the layers
# that conceptually sit "above" the index — importing any of them
# is architectural debt.
#
# Note: `backend.repositories.protocol` is intentionally NOT in
# this set (the Reconciler needs it). `backend.repositories.impl`
# and `backend.repositories` package init are still forbidden —
# those would couple the index to a concrete filesystem tree.
FORBIDDEN_FROM_INDEX: frozenset[str] = frozenset(
    {
        "backend.api",
        "backend.api.dependencies",
        "backend.api.exception_handlers",
        "backend.api.mappers",
        "backend.api.v1",
        "backend.api.v1.endpoints",
        "backend.api.v1.router",
        # Services coordinate domain behavior; reaching here from
        # the index would invert the dependency direction.
        "backend.services",
        "backend.services.exceptions",
        "backend.services.workspace_service",
        # The other repository's package init pulls in concrete
        # classes; the Reconciler must depend only on the
        # Protocol.
        "backend.repositories",
        "backend.repositories.impl",
        "backend.repositories.impl.local_workspace_repository",
        # Filesystem layer is conceptually the LocalWorkspace-
        # Repository's I/O substrate; never import it.
        "backend.filesystem",
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


def _all_index_modules() -> list[Path]:
    """Walk backend/index/ and return every .py file."""
    index_dir = (
        Path(__file__).resolve().parent.parent.parent / "index"
    )
    return sorted(p for p in index_dir.glob("**/*.py") if p.name != "__init__.py")


def test_index_modules_exist() -> None:
    assert _all_index_modules(), "no index modules found"


@pytest.mark.parametrize(
    "module_path",
    _all_index_modules(),
    ids=lambda p: str(p.relative_to(Path(__file__).resolve().parent.parent.parent)),
)
def test_index_module_only_imports_allowed_backends(module_path: Path) -> None:
    """Every import in an index module must be on the index-side allowlist."""
    imports = _collect_imports(module_path)
    for mod in imports:
        if mod.startswith("backend."):
            assert mod in ALLOWED_INDEX_MODULES, (
                f"{module_path.name} imports non-allowlisted backend module: {mod}\n"
                f"  Allowed: {sorted(ALLOWED_INDEX_MODULES)}"
            )
            assert mod not in FORBIDDEN_FROM_INDEX, (
                f"{module_path.name} imports forbidden module "
                f"(forbidden = services/api/repositories-impl): {mod}"
            )


def test_reconciler_does_not_reach_concrete_repositories() -> None:
    """The Reconciler may depend on Protocols, but never on
    concrete classes (LocalWorkspaceRepository, etc.).

    This is the test that proves the Reconciler stays a leaf
    layer — even though it *is* allowed to depend on the
    WorkspaceRepository Protocol, it must not reach for the
    concrete implementation.
    """
    forbidden_strings = (
        "LocalWorkspaceRepository",
        "LocalFilesystem",
        "WorkspaceRoot",
        "backend.filesystem",
        "backend.repositories.impl",
        "backend.services",
        "backend.api",
        "SQLAlchemy",  # explicit: no SQLAlchemy ORM imports either
    )
    reconciler_path = (
        Path(__file__).resolve().parent.parent.parent / "index" / "reconciler.py"
    )
    text = reconciler_path.read_text(encoding="utf-8")
    for forbidden in forbidden_strings:
        assert forbidden not in text, (
            f"reconciler.py references forbidden symbol: {forbidden}"
        )


def test_sync_does_not_reach_concrete_repositories() -> None:
    """Phase 2.2 mirror of the Reconciler isolation test.

    The IncrementalIndexSynchronizer is allowed to depend on
    Protocols (`WorkspaceRepository`, `IndexRepository`) but
    MUST NOT reach for the concrete `LocalWorkspaceRepository`,
    the Filesystem layer, FastAPI, services, or SQLAlchemy
    models. Same guarantee, applied to a different file.
    """
    forbidden_strings = (
        "LocalWorkspaceRepository",
        "LocalFilesystem",
        "WorkspaceRoot",
        "backend.filesystem",
        "backend.repositories.impl",
        "backend.services",
        "backend.api",
        "SQLAlchemy",  # explicit: no SQLAlchemy ORM imports either
    )
    sync_path = (
        Path(__file__).resolve().parent.parent.parent / "index" / "sync.py"
    )
    text = sync_path.read_text(encoding="utf-8")
    for forbidden in forbidden_strings:
        assert forbidden not in text, (
            f"sync.py references forbidden symbol: {forbidden}"
        )


def test_api_and_service_layers_do_not_reach_index() -> None:
    """Belt-and-suspenders: the index is unreachable from above.

    Phase 2.0/2.1 hadn't wired the index into the API or service
    layers, so a passing test today should mean a passing test
    after every future phase *unless* an explicit decision was
    made. That's exactly when this test should fail.

    Phase 2.2 introduces ONE allowed exception: the concrete
    `LocalWorkspaceRepository` is permitted to reference the
    synchroniser Protocol (`IncrementalIndexSynchronizer`).
    The repository is the dependency-inversion seam between
    the filesystem and the index (per Phase 2.2 brief). The
    rest of the API/service tree still cannot reach the index
    at all.
    """
    forbidden_strings = (
        "IndexRepository",
        "IndexRecord",
        "SQLAlchemyIndexRepository",
        "InMemoryIndexRepository",
        "IndexReconciler",
        "ReconcileReport",
        "backend.index",
    )
    forbidden_paths = (
        Path(__file__).resolve().parent.parent.parent.parent / "api",
        Path(__file__).resolve().parent.parent.parent.parent / "services",
        # The repositories package also must not depend on the
        # index — same decoupling rationale.
        Path(__file__).resolve().parent.parent.parent.parent / "repositories",
    )
    # Phase 2.2 — repositories.impl.local_workspace_repository
    # is the *only* place that may reach the index. It does so
    # by referencing the synchroniser Protocol structurally,
    # not by importing a concrete class.
    repository_index_seam = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "repositories"
        / "impl"
        / "local_workspace_repository.py"
    )
    for root in forbidden_paths:
        if not root.exists():
            continue
        for module_path in sorted(root.glob("**/*.py")):
            if module_path.name == "__init__.py":
                continue
            # The repository → index seam is the explicit
            # exception. We still forbid *every other* module
            # in the repositories package from referencing
            # index symbols (the Protocol lives in the
            # repositories package itself; nothing else should
            # touch it).
            if module_path == repository_index_seam:
                continue
            text = module_path.read_text(encoding="utf-8")
            for forbidden in forbidden_strings:
                assert forbidden not in text, (
                    f"{module_path.relative_to(root.parent)} references "
                    f"the forbidden index symbol: {forbidden}"
                )
