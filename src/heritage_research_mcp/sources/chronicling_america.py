"""Chronicling America (Library of Congress): digitised US newspaper pages, 1690 to the present, with OCR text.

Served by the public loc.gov JSON API (no key). Its limits page asks for at most 20 requests a minute and blocks
clients for an hour beyond that, and may show a bot challenge even under the stated rate, so requests are paced
at one every three seconds and a challenge stops this source for a while (see http.py) instead of being retried.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .. import kinds as K
from .. import rights as R
from ..errors import HeritageError, NotFound
from ..http import http
from ..models import Media, MediaKind, Record
from ..util import as_year, clamp, date_range, listify, strs, truncate
from . import chronam_bulk

NAME = "chronam"
SEARCH = "https://www.loc.gov/collections/chronicling-america/"
RESOURCE = "https://www.loc.gov/resource"
STORAGE = "https://tile.loc.gov/storage-services"
TEXT_LIMIT = 100_000
TEXT_MAX_BYTES = 2 * 1024 * 1024

_ID = re.compile(r"([A-Za-z0-9]+)/(\d{4}-\d{2}-\d{2})/ed-(\d+)/(\d+)")
_RESOURCE_URL = re.compile(r"/resource/([A-Za-z0-9]+)/(\d{4}-\d{2}-\d{2})/ed-(\d+)/\?sp=(\d+)")
_STORAGE_PATH = re.compile(r"/service/[A-Za-z0-9_/.-]+")
_TAG = re.compile(r"\[\[/?tag\]\]")
_SIZE = re.compile(r"[#&]h=(\d+)&w=(\d+)")
_MEDIA: dict[str, tuple[MediaKind, str, str]] = {
    "pdf": ("pdf", "application/pdf", "page PDF"),
    "txt": ("text", "text/plain", "OCR text (raw, uncorrected)"),
    "xml": ("data", "text/xml", "OCR with word coordinates (ALTO XML)"),
    "jp2": ("image", "image/jp2", "page image (JPEG 2000)"),
}


def _base(path: str) -> str | None:
    """The storage path of a page without its extension, from a /storage-services or segment path."""
    path = path.removeprefix("/storage-services")
    if ".." in path or not _STORAGE_PATH.fullmatch(path):
        return None
    stem, dot, _ = path.rpartition(".")
    return stem if dot else None


def _storage_base(result: dict[str, Any]) -> str | None:
    """The OCR file location the result names in its word-coordinates link: ?segment=/service/ndnp/.../1270.xml."""
    links = [result.get("word_coordinates_url"), *strs(result.get("image_url"))]
    for link in links:
        if isinstance(link, str) and "word-coordinates-service" in link:
            segment = parse_qs(urlsplit(link).query).get("segment", [""])[0]
            return _base(segment)
    return None


def _media_for(base: str | None) -> list[Media]:
    if not base:
        return []
    out = []
    for ext in ("pdf", "txt", "xml", "jp2"):
        kind, mime, label = _MEDIA[ext]
        out.append(Media(kind=kind, url=f"{STORAGE}{base}.{ext}", mime=mime, label=label))
    return out


def _previews(result: dict[str, Any]) -> list[Media]:
    out = []
    for link in strs(result.get("image_url")):
        if "/iiif/" not in link or not link.lower().split("#")[0].endswith(".jpg"):
            continue
        size = _SIZE.search(link)
        out.append(
            Media(
                kind="image",
                url=link.split("#")[0],
                mime="image/jpeg",
                height=int(size.group(1)) if size else None,
                width=int(size.group(2)) if size else None,
                label="preview",
            )
        )
    return out[-1:]


def _snippet(result: dict[str, Any]) -> str | None:
    found = (result.get("page_coordinate_data") or {}).get("relevant_snippet")
    if isinstance(found, str) and found.strip():
        return truncate(_TAG.sub("**", found), 400)
    return truncate(" ".join(strs(result.get("description"))), 300)


def _places(city: str | None, state: str | None) -> list[str]:
    parts = [p.title() for p in (city, state) if p]
    return [", ".join(parts)] if parts else []


def _record_from_result(result: dict[str, Any]) -> Record | None:
    link = result.get("id")
    match = _RESOURCE_URL.search(link) if isinstance(link, str) else None
    if not match:
        return None
    lccn, date, edition, sequence = match.groups()
    landing = f"https://www.loc.gov/resource/{lccn}/{date}/ed-{edition}/?sp={sequence}"
    title = truncate((result.get("title") or None), 200)
    city, state = (
        (strs(result.get("location_city")) or [None])[0],
        (strs(result.get("location_state")) or [None])[0],
    )
    paper = (strs(result.get("partof_title")) or [None])[0]
    return Record(
        source=NAME,
        id=f"{lccn}/{date}/ed-{edition}/{sequence}",
        title=title,
        date=date,
        description=_snippet(result),
        places=_places(city, state),
        type="newspaper page",
        kind="text",
        holder=(strs(result.get("contributor")) or [None])[0],
        landing_url=landing,
        rights=R.newspaper_page(as_year(date), result.get("access_restricted") is True, title, landing),
        media=[*_media_for(_storage_base(result)), *_previews(result)],
        extra={
            "newspaper": paper,
            "lccn": lccn,
            "edition": int(edition),
            "page": int(sequence),
            "batch": (strs(result.get("batch")) or [None])[0],
            "url_id": result.get("page_id"),
        },
    )


def _parse_id(record_id: str) -> tuple[str, str, int, int]:
    found = _ID.fullmatch(record_id.strip())
    if not found:
        raise HeritageError(
            "a Chronicling America id looks like sn83030214/1863-06-11/ed-1/2 (LCCN, date, edition, page)"
        )
    return found.group(1), found.group(2), int(found.group(3)), int(found.group(4))


async def search(
    query: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    state: str | None = None,
    phrase: bool = False,
    rows: int = 10,
    page: int = 1,
    kind: str | None = None,
) -> dict[str, Any]:
    if kind is not None and kind.strip().lower() not in {"", "text"}:
        return {"total": 0, "records": [], "kind_applied": "none: Chronicling America is newspaper text only"}
    K.check(kind)
    if not query.strip():
        raise HeritageError("give at least one word to search for")
    params: dict[str, Any] = {
        "fo": "json",
        "at": "results,pagination",
        "dl": "page",
        "searchType": "advanced",
        "qs": query.strip(),
        "c": clamp(rows, 1, 100),
        "sp": max(1, page),
    }
    if phrase:
        params["ops"] = "PHRASE"
    if date_from or date_to:
        low, high = date_range(date_from, date_to)
        params["start_date"] = low if date_from else "1690-01-01"
        params["end_date"] = high if date_to else f"{dt.date.today().year}-12-31"
    if state:
        params["location_state"] = state.strip().lower()
    data = await http.get_json(NAME, SEARCH, params=params)
    results = [r for r in listify(data.get("results")) if isinstance(r, dict)]
    records = [rec.to_dict(brief=True) for rec in (_record_from_result(r) for r in results) if rec]
    paging = data.get("pagination") or {}
    out: dict[str, Any] = {
        "total": paging.get("of"),
        "returned": len(records),
        "page": paging.get("current"),
        "more": bool(paging.get("next")),
        "records": records,
        "attribution": "Chronicling America, Library of Congress. OCR text is raw and uncorrected.",
    }
    if kind:
        out["kind_applied"] = "text"
    return out


def _record_from_resource(data: dict[str, Any], lccn: str, date: str, edition: int, sequence: int) -> Record:
    item = data.get("item") or {}
    paper = (strs(item.get("newspaper_title")) or [None])[0]
    city, state = (
        (strs(item.get("location_city")) or [None])[0],
        (strs(item.get("location_state")) or [None])[0],
    )
    landing = f"https://www.loc.gov/resource/{lccn}/{date}/ed-{edition}/?sp={sequence}"
    title = f"{paper}, {date}, p. {sequence}" if paper else f"{lccn}, {date}, p. {sequence}"
    pdf = (data.get("resource") or {}).get("pdf")
    base = (
        _base(urlsplit(pdf).path)
        if isinstance(pdf, str) and urlsplit(pdf).hostname == "tile.loc.gov"
        else None
    )
    return Record(
        source=NAME,
        id=f"{lccn}/{date}/ed-{edition}/{sequence}",
        title=title,
        date=strs(item.get("date"))[0] if strs(item.get("date")) else date,
        places=_places(city, state),
        type="newspaper page",
        kind="text",
        holder=", ".join(strs(item.get("contributor_names"))) or None,
        landing_url=landing,
        rights=R.newspaper_page(as_year(date), False, title, landing),
        media=_media_for(base),
        extra={
            "newspaper": paper,
            "lccn": lccn,
            "edition": edition,
            "page": sequence,
            "batch": (strs(item.get("batch")) or [None])[0],
        },
    )


async def get_page(record_id: str) -> dict[str, Any]:
    lccn, date, edition, sequence = _parse_id(record_id)
    local = chronam_bulk.local_page(record_id)
    if local:
        return {"record": local}
    url = f"{RESOURCE}/{lccn}/{date}/ed-{edition}/"
    data = await http.get_json(NAME, url, params={"sp": sequence, "fo": "json"})
    if not isinstance(data, dict) or not data.get("item"):
        raise NotFound(f"{NAME} has no page {record_id!r}")
    return {"record": _record_from_resource(data, lccn, date, edition, sequence).to_dict()}


async def page_text(record_id: str) -> dict[str, Any]:
    _parse_id(record_id)
    local = chronam_bulk.local_page(record_id)
    if local:
        text = local["extra"]["text"]
        return {
            "id": local["id"],
            "title": local.get("title"),
            "url": local.get("landing_url"),
            "text": text[:TEXT_LIMIT],
            "truncated": len(text) > TEXT_LIMIT,
            "from": "local index (no request made)",
            "note": "Raw OCR from the Library of Congress: uncorrected, with errors in names and numbers.",
            "rights": local.get("rights"),
        }
    record = (await get_page(record_id))["record"]
    link = next((m["url"] for m in record.get("media", []) if m.get("kind") == "text"), None)
    if not link:
        raise NotFound(f"{NAME} lists no OCR text file for {record_id!r}")
    text = await http.get_text(NAME, link, max_bytes=TEXT_MAX_BYTES)
    return {
        "id": record["id"],
        "title": record.get("title"),
        "url": link,
        "text": text[:TEXT_LIMIT],
        "truncated": len(text) > TEXT_LIMIT,
        "note": "Raw OCR from the Library of Congress: uncorrected, with errors in names and numbers.",
        "rights": record.get("rights"),
    }
