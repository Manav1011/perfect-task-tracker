"""Purity check: domain layer must not import forbidden frameworks.

This is a structural test. If a future change drags FastAPI, SQLAlchemy,
or pydantic into the domain layer, this test fails before review.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Generator

import backend.domain as domain_pkg

FORBIDDEN_SUBSTRINGS = (
    "fastapi",
    "sqlalchemy",
    "starlette",
    "pydantic",
    "alembic",
)


def _walk(module) -> Generator[str, None, None]:
    """Yield the full dotted path of every submodule under `module`."""
    yield module.__name__
    for _finder, name, _is_pkg in pkgutil.walk_packages(
        module.__path__, prefix=module.__name__ + "."
    ):
        yield name


def test_domain_has_no_forbidden_imports() -> None:
    """Walk every submodule and inspect its loaded modules for forbidden deps."""
    leaked: list[str] = []
    for name in _walk(domain_pkg):
        mod = importlib.import_module(name)
        for loaded in mod.__dict__.values():
            mod_name = getattr(loaded, "__module__", None) or getattr(loaded, "__name__", None)
            if not isinstance(mod_name, str):
                continue
            for bad in FORBIDDEN_SUBSTRINGS:
                if mod_name == bad or mod_name.startswith(bad + "."):
                    leaked.append(f"{name} -> {mod_name} ({bad})")
                    break
    assert not leaked, f"domain layer leaks forbidden deps: {leaked}"