"""Small helpers for tolerant parsing of messy archive metadata."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from typing import Any

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def listify(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list | tuple) else [value]


def as_str(value: Any) -> str | None:
    """A readable string from the odd shapes archives return (str, number, or a dict with a label)."""
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, dict):
        for key in ("displayDate", "name", "content", "value", "heading", "title", "label"):
            found = as_str(value.get(key))
            if found:
                return found
    if isinstance(value, list | tuple):
        for item in value:
            found = as_str(item)
            if found:
                return found
    return None


def strs(value: Any) -> list[str]:
    out = [as_str(v) for v in listify(value)]
    return [s for s in out if s]


def as_int(value: Any) -> int | None:
    """An integer from an int or a string that is only digits (sizes, counts, pixel dimensions)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = as_str(value)
    if text and re.fullmatch(r"-?\d+", text):
        return int(text)
    return None


_YEAR = re.compile(r"(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)")


def as_year(value: Any) -> int | None:
    """The first plausible four-digit year in a date-like value ('1887-01-01T00:00:00Z', '[1887?]', 1887)."""
    text = as_str(value)
    if not text:
        return None
    match = _YEAR.search(text)
    return int(match.group(1)) if match else None


def strip_html(value: Any) -> str | None:
    text = as_str(value)
    if not text:
        return None
    text = html.unescape(_TAG.sub(" ", text))
    return _SPACE.sub(" ", text).strip() or None


def truncate(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    text = _SPACE.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def uniq(items: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)
