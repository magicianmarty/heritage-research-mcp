"""The five kinds of material a researcher asks for, and how to recognise them in an archive's records."""

from __future__ import annotations

import re
from typing import Any

from .errors import HeritageError

KINDS = ("text", "image", "map", "audio", "video")

_ALIASES = {
    "document": "text", "documents": "text", "book": "text", "books": "text", "texts": "text",
    "photo": "image", "photos": "image", "photograph": "image", "photographs": "image",
    "picture": "image", "images": "image", "maps": "map", "sound": "audio", "film": "video",
    "movie": "video", "movies": "video",
}  # fmt: skip
_MAP_SUBJECT = re.compile(
    r"^(maps?|atlas(es)?|cartography|maps and charts|historical maps|topographic maps|cartographic materials?|cartographic resources?)$",
    re.I,
)
_MAP_TITLE = re.compile(r"\b(maps?|atlas(es)?)\b", re.I)


def check(kind: str | None) -> str | None:
    """A validated kind, or None when no filter was asked for."""
    if kind is None or not kind.strip():
        return None
    wanted = kind.strip().lower()
    wanted = _ALIASES.get(wanted, wanted)
    if wanted not in KINDS:
        raise HeritageError(f"kind must be one of {list(KINDS)}")
    return wanted


def has_map_subject(subjects: list[str]) -> bool:
    """True if any heading is, or has a segment that is, a map term. Library headings read "Watersheds--Virginia--Maps"."""
    return any(_MAP_SUBJECT.match(part.strip()) for s in subjects for part in s.split("--"))


def title_says_map(title: Any) -> bool:
    return isinstance(title, str) and bool(_MAP_TITLE.search(title))
