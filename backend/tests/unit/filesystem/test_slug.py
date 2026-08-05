"""Slug generation tests."""

from __future__ import annotations

from backend.filesystem.slug import slugify


def test_simple_title() -> None:
    assert slugify("Hello World") == "hello-world"


def test_strips_punctuation() -> None:
    assert slugify("Foo!! Bar??") == "foo-bar"


def test_collapses_dashes() -> None:
    assert slugify("a---b") == "a-b"


def test_trims_dashes() -> None:
    assert slugify("  Hello  ") == "hello"


def test_strips_diacritics() -> None:
    assert slugify("Café Olé") == "cafe-ole"


def test_fallback_for_empty() -> None:
    assert slugify("") == "node"
    assert slugify("!!!") == "node"