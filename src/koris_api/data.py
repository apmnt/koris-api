"""Small helpers for reading untrusted source records without dynamic typing."""

from collections.abc import Mapping
from typing import cast


def fields(value: object) -> Mapping[str, object]:
    """Read a source object; absent optional objects have no fields."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("Expected a source object")
    items = cast(Mapping[object, object], value)
    if any(not isinstance(key, str) for key in items):
        raise ValueError("Source object keys must be strings")
    return cast(Mapping[str, object], items)


def records(value: object) -> list[Mapping[str, object]]:
    """Read an optional array of source objects, preserving source order."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("Expected a source array")
    return [fields(row) for row in cast(list[object], value)]


def text(value: object) -> str | None:
    """Preserve optional source text, rejecting non-text values."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("Expected source text")
    return value
