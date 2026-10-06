"""Smithsonian Open Access (EDAN) through api.data.gov."""

from __future__ import annotations

import re
from typing import Any

from .. import config
from .. import kinds as K
from .. import rights as R
from ..errors import HeritageError, NotFound
from ..http import http
from ..models import Kind, Media, MediaKind, Record, Rights
from ..util import as_int, as_str, as_year, clamp, listify, strs, truncate, uniq

NAME = "smithsonian"
BASE = "https://api.si.edu/openaccess/api/v1.0"
CATEGORIES = {"art_design", "history_culture", "science_technology"}
_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _params(**extra: Any) -> dict[str, Any]:
    return {"api_key": config.require_key(NAME), **{k: v for k, v in extra.items() if v is not None}}


_KIND_CLAUSE = {
    "map": 'object_type:"Maps"',
    "image": 'online_media_type:"Images"',
    "audio": 'online_media_type:"Sound recordings"',
    "video": 'online_media_type:"Video recordings"',
    "text": '(online_media_type:"Full text documents" OR online_media_type:"Scanned books" OR object_type:"Books" OR object_type:"Manuscripts")',
}
_CREATOR_LABELS = (
    "author", "artist", "maker", "created by", "editor", "photographer", "recording artist",
    "composer", "designer", "illustrator", "engraver", "attribution", "publisher",
)  # fmt: skip
_DATE_LABELS = {"date", "date made", "year"}
_SKIPPED_NOTES = {"location", "citation"}


def _entries(value: Any) -> list[tuple[str, str]]:
    """(lower-cased label, text) pairs from EDAN's [{label, content}] lists."""
    out: list[tuple[str, str]] = []
    for item in listify(value):
        if isinstance(item, dict):
            text = as_str(item.get("content"))
            label = (as_str(item.get("label")) or "").lower()
        else:
            text, label = as_str(item), ""
        if text:
            out.append((label, text))
    return out


def _is_creator(label: str) -> bool:
    return any(label.startswith(known) for known in _CREATOR_LABELS)


def _date(entries: list[tuple[str, str]]) -> str | None:
    preferred = [text for label, text in entries if label in _DATE_LABELS and as_year(text)]
    anywhere = [text for _, text in entries if as_year(text)]
    return (preferred or anywhere or [text for _, text in entries] or [None])[0]


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


def _resource_rank(label: str) -> int:
    low = label.lower()
    if "high-resolution" in low and "jpeg" in low:
        return 0
    if low.startswith("screen"):
        return 1
    if "high-resolution" in low:
        return 2
    return 3


def _media(descriptive: dict[str, Any]) -> list[Media]:
    block = descriptive.get("online_media") or {}
    out: list[Media] = []
    for item in listify(block.get("media")):
        if not isinstance(item, dict):
            continue
        usage = item.get("usage")
        licence = as_str(usage.get("access")) if isinstance(usage, dict) else None
        caption = as_str(item.get("caption")) or as_str(item.get("type")) or "media"
        thumbnail = as_str(item.get("thumbnail"))
        kind = _kind(as_str(item.get("type")))
        resources = [
            r for r in listify(item.get("resources")) if isinstance(r, dict) and as_str(r.get("url"))
        ]
        if kind == "image":
            resources = [r for r in resources if _resource_rank(as_str(r.get("label")) or "") < 3]
        resources.sort(key=lambda r: _resource_rank(as_str(r.get("label")) or ""))
        for resource in resources:
            label = as_str(resource.get("label")) or "file"
            out.append(
                Media(
                    kind=kind,
                    url=str(as_str(resource.get("url"))),
                    width=as_int(resource.get("width")),
                    height=as_int(resource.get("height")),
                    label=f"{truncate(caption, 80)}: {label}",
                    thumbnail_url=thumbnail,
                    license=licence,
                )
            )
        content = as_str(item.get("content"))
        if not resources and content:
            out.append(Media(kind=kind, url=content, label=caption, thumbnail_url=thumbnail, license=licence))
    return out


def _kind_of(row: dict[str, Any], object_types: list[str], media: list[Media]) -> Kind | None:
    if K.has_map_subject(object_types):
        return "map"
    for item in media:
        if item.kind in ("image", "audio", "video"):
            return item.kind
    if any(t.lower() in {"books", "manuscripts", "pamphlets", "periodicals"} for t in object_types):
        return "text"
    return "text" if as_str(row.get("unitCode")) == "SIL" and not media else None


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
    landing = (
        as_str(descriptive.get("record_link"))
        or as_str(descriptive.get("guid"))
        or (f"https://collections.si.edu/search/detail/{entry}" if entry else None)
    )
    names = _entries(free.get("name"))
    notes = [text for label, text in _entries(free.get("notes")) if label not in _SKIPPED_NOTES]
    kinds = [text for label, text in _entries(free.get("objectType")) if label != "other terms"]
    people = [text for label, text in names if not _is_creator(label)]
    topics = [text for _, text in _entries(free.get("topic"))]
    return Record(
        source=NAME,
        id=identifier,
        title=as_str(row.get("title")) or as_str(descriptive.get("title")),
        creators=uniq(text for label, text in names if _is_creator(label))[:8],
        date=_date(_entries(free.get("date"))),
        description=truncate(" ".join(notes), 1200),
        subjects=uniq(topics + people + strs(structured.get("topic")))[:15],
        places=uniq([text for _, text in _entries(free.get("place"))] + strs(structured.get("place")))[:10],
        type=(kinds or [as_str(row.get("type"))])[0],
        kind=_kind_of(row, kinds, media),
        holder=as_str(descriptive.get("data_source")) or as_str(row.get("unitCode")),
        landing_url=landing,
        rights=_rights(descriptive, media),
        media=media,
        extra={"unit": as_str(row.get("unitCode")), "edan_type": as_str(row.get("type")), "url_id": entry},
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
    kind: str | None = None,
) -> dict[str, Any]:
    if category and category not in CATEGORIES:
        raise HeritageError(f"category must be one of {sorted(CATEGORIES)}")
    wanted = K.check(kind)
    if wanted:
        q = f"({q}) AND {_KIND_CLAUSE[wanted]}"
    extra = {"q": q, "rows": clamp(rows, 1, 100), "start": max(0, start), "sort": _token("sort", sort)}
    if category:
        url = f"{BASE}/category/{category}/search"
    else:
        url = f"{BASE}/search"
        extra.update({"type": _token("type", type), "row_group": _token("row_group", row_group)})
    data = await http.get_json(NAME, url, params=_params(**extra))
    block = data.get("response") or {}
    records = [
        r.to_dict(brief=True)
        for r in (_record(r) for r in listify(block.get("rows")) if isinstance(r, dict))
        if r
    ]
    out: dict[str, Any] = {
        "total": block.get("rowCount"),
        "start": extra["start"],
        "rows": extra["rows"],
        "records": records,
    }
    if wanted:
        out["kind_applied"] = _KIND_CLAUSE[wanted]
    return out


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
