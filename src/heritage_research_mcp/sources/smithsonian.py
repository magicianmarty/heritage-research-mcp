"""Smithsonian Open Access (EDAN) through api.data.gov."""

from __future__ import annotations

import re
from typing import Any

from .. import config
from .. import rights as R
from ..errors import HeritageError, NotFound
from ..http import http
from ..models import Media, MediaKind, Record, Rights
from ..util import as_str, clamp, listify, strs, truncate, uniq

NAME = "smithsonian"
BASE = "https://api.si.edu/openaccess/api/v1.0"
CATEGORIES = {"art_design", "history_culture", "science_technology"}
_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _params(**extra: Any) -> dict[str, Any]:
    return {"api_key": config.require_key(NAME), **{k: v for k, v in extra.items() if v is not None}}


def _contents(value: Any) -> list[str]:
    """Strings from EDAN's [{label, content}] lists."""
    return uniq(s for s in (as_str(v) for v in listify(value)) if s)


def _kind(kind: str | None) -> MediaKind:
    low = (kind or "").lower()
    if low.startswith("image"):
        return "image"
    if low.startswith("audio"):
        return "audio"
    if low.startswith("video"):
        return "video"
    if "pdf" in low:
        return "pdf"
    return "other"


def _media(descriptive: dict[str, Any]) -> list[Media]:
    block = descriptive.get("online_media") or {}
    out: list[Media] = []
    for item in listify(block.get("media")):
        if not isinstance(item, dict):
            continue
        url = as_str(item.get("content"))
        if not url:
            continue
        usage = item.get("usage") or {}
        out.append(
            Media(
                kind=_kind(as_str(item.get("type"))),
                url=url,
                label=as_str(item.get("caption")) or as_str(item.get("type")),
                thumbnail_url=as_str(item.get("thumbnail")),
                license=as_str(usage.get("access")) if isinstance(usage, dict) else None,
            )
        )
    return out


def _rights(descriptive: dict[str, Any], media: list[Media]) -> Rights:
    metadata = as_str((descriptive.get("metadata_usage") or {}).get("access"))
    licences = [m.license for m in media if m.license]
    if licences and all(lic == "CC0" for lic in licences):
        return R.make(
            "free",
            "CC0 (media and metadata)",
            statement="CC0",
            url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
    if licences:
        return R.make(
            "restricted",
            "Usage conditions apply",
            statement=", ".join(sorted(set(licences))),
            note="At least one image or media item is not marked CC0, even if the metadata is.",
        )
    if metadata == "CC0":
        return R.make(
            "free", "CC0 (metadata only)", statement="CC0", note="No media is attached to this record."
        )
    return R.unknown("The record does not state a usage licence.")


def _record(row: dict[str, Any]) -> Record | None:
    identifier = as_str(row.get("id"))
    if not identifier:
        return None
    content = row.get("content") or {}
    descriptive = content.get("descriptiveNonRepeating") or {}
    free = content.get("freetext") or {}
    structured = content.get("indexedStructured") or {}
    media = _media(descriptive)
    entry = as_str(row.get("url"))
    landing = as_str(descriptive.get("record_link")) or (
        f"https://collections.si.edu/search/detail/{entry}" if entry else None
    )
    return Record(
        source=NAME,
        id=identifier,
        title=as_str(row.get("title")) or as_str(descriptive.get("title")),
        creators=_contents(free.get("name"))[:8],
        date=(_contents(free.get("date")) or [None])[0],
        description=truncate(" ".join(_contents(free.get("notes"))), 1200),
        subjects=uniq(_contents(free.get("topic")) + strs(structured.get("topic")))[:15],
        places=uniq(_contents(free.get("place")) + strs(structured.get("place")))[:10],
        type=(_contents(free.get("objectType")) or [as_str(row.get("type"))])[0],
        holder=as_str(descriptive.get("data_source")) or as_str(row.get("unitCode")),
        landing_url=landing,
        rights=_rights(descriptive, media),
        media=media,
        extra={"unit": as_str(row.get("unitCode")), "edan_type": as_str(row.get("type"))},
    )


def _token(name: str, value: str | None) -> str | None:
    if value is not None and not _TOKEN.match(value):
        raise HeritageError(f"{name} contains characters that are not allowed")
    return value


async def search(
    q: str,
    *,
    rows: int = 10,
    start: int = 0,
    sort: str | None = None,
    type: str | None = None,  # noqa: A002 - mirrors the EDAN parameter
    row_group: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    if category and category not in CATEGORIES:
        raise HeritageError(f"category must be one of {sorted(CATEGORIES)}")
    extra = {"q": q, "rows": clamp(rows, 1, 100), "start": max(0, start), "sort": _token("sort", sort)}
    if category:
        url = f"{BASE}/category/{category}/search"
    else:
        url = f"{BASE}/search"
        extra.update({"type": _token("type", type), "row_group": _token("row_group", row_group)})
    data = await http.get_json(NAME, url, params=_params(**extra))
    block = data.get("response") or {}
    records = [
        r.to_dict() for r in (_record(r) for r in listify(block.get("rows")) if isinstance(r, dict)) if r
    ]
    return {
        "total": block.get("rowCount"),
        "start": extra["start"],
        "rows": extra["rows"],
        "records": records,
    }


async def get_content(content_id: str) -> dict[str, Any]:
    safe = _token("id", content_id) or ""
    data = await http.get_json(NAME, f"{BASE}/content/{safe}", params=_params())
    row = data.get("response")
    record = _record(row) if isinstance(row, dict) else None
    if record is None:
        raise NotFound(f"smithsonian has no record {content_id!r}")
    return {"record": record.to_dict()}


async def terms(category: str, *, starts_with: str | None = None, limit: int = 200) -> dict[str, Any]:
    safe = _token("category", category) or ""
    params = _params(starts_with=starts_with)
    data = await http.get_json(NAME, f"{BASE}/terms/{safe}", params=params)
    items = [t for t in listify((data.get("response") or {}).get("terms")) if isinstance(t, str)]
    return {"category": category, "count": len(items), "terms": items[: clamp(limit, 1, 1000)]}


async def stats() -> dict[str, Any]:
    data = await http.get_json(NAME, f"{BASE}/stats", params=_params())
    return {"stats": data.get("response")}
