"""The one shape every source is normalised into."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Reuse = Literal[
    "free", "attribution", "share_alike", "non_commercial", "no_derivatives", "restricted", "unknown"
]
MediaKind = Literal["image", "text", "pdf", "audio", "video", "archive", "data", "other"]


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
    holder: str | None = None
    landing_url: str | None = None
    rights: Rights = Field(default_factory=Rights)
    media: list[Media] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return compact(self.model_dump(mode="json"))


def compact(value: Any) -> Any:
    """Drop None, empty strings and empty containers so results stay small."""
    if isinstance(value, dict):
        out = {k: compact(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value
