"""The one shape every source is normalised into."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Reuse = Literal[
    "free", "attribution", "share_alike", "non_commercial", "no_derivatives", "restricted", "unknown"
]
MediaKind = Literal["image", "text", "pdf", "audio", "video", "archive", "data", "other"]
Kind = Literal["text", "image", "map", "audio", "video"]


class Rights(BaseModel):
    """What the holder says about reuse, reduced to one comparable value.

    `reuse` is a convenience for filtering, not legal advice. `basis` says where it came from:
    `holder` (the source stated it), `date-heuristic` (we inferred it from a publication year) or
    `none` (nothing was stated).
    """

    reuse: Reuse = "unknown"
    label: str | None = None
    statement: str | None = None
    url: str | None = None
    basis: Literal["holder", "date-heuristic", "none"] = "none"
    note: str | None = None
    attribution: str | None = None


class Media(BaseModel):
    kind: MediaKind = "other"
    url: str
    mime: str | None = None
    bytes: int | None = None
    width: int | None = None
    height: int | None = None
    label: str | None = None
    thumbnail_url: str | None = None
    license: str | None = None


class Record(BaseModel):
    source: str
    id: str
    title: str | None = None
    creators: list[str] = Field(default_factory=list)
    date: str | None = None
    description: str | None = None
    subjects: list[str] = Field(default_factory=list)
    places: list[str] = Field(default_factory=list)
    type: str | None = None
    kind: Kind | None = None
    holder: str | None = None
    landing_url: str | None = None
    rights: Rights = Field(default_factory=Rights)
    media: list[Media] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self, brief: bool = False) -> dict[str, Any]:
        """The full record, or the compact form used in search results.

        Brief keeps what is needed to choose a result (title, date, rights, one or two files) and drops what
        costs tokens without helping the choice: long descriptions, thumbnails, trailing subjects. The get_*
        tools return the full record.
        """
        data = self.model_dump(mode="json")
        if brief:
            data["description"] = _clip(data.get("description"), BRIEF_DESCRIPTION)
            data["subjects"] = data["subjects"][:BRIEF_SUBJECTS]
            data["media"] = [
                {k: v for k, v in m.items() if k != "thumbnail_url"} for m in data["media"][:BRIEF_MEDIA]
            ]
            data["extra"] = {k: v for k, v in data["extra"].items() if k in BRIEF_EXTRA}
        return compact(data)


BRIEF_DESCRIPTION = 300
BRIEF_SUBJECTS = 6
BRIEF_MEDIA = 2
BRIEF_EXTRA = {"url_id", "has_extracted_text"}


def _clip(text: str | None, limit: int) -> str | None:
    if not text or len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def compact(value: Any) -> Any:
    """Drop None, empty strings and empty containers so results stay small."""
    if isinstance(value, dict):
        out = {k: compact(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value
