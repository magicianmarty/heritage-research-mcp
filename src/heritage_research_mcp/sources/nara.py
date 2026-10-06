"""US National Archives Catalog (API v2, with an opt-in v3 search).

The API's terms forbid scraping or downloading everything through it, so every call here is a single page
capped at 100 records, and the monthly quota (10,000 per key by default) is counted and enforced locally.
"""

from __future__ import annotations

import re
from typing import Any

from .. import config
from .. import rights as R
from ..errors import HeritageError, NotFound
from ..http import http
from ..models import Media, MediaKind, Record, Rights
from ..util import as_int, as_str, clamp, listify, strs, truncate, uniq

NAME = "nara"
ROOT = "https://catalog.archives.gov/api"
ATTRIBUTION = "This product uses the National Archives Catalog API but is not endorsed or certified by the National Archives."
_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
_NOTE = (
    "US federal records are often public domain, but donated or third-party material can carry restrictions. "
    "Read the record's own use restriction."
)


def _headers() -> dict[str, str]:
    return {"x-api-key": config.require_key(NAME)}


def _dig(data: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _hits(data: Any) -> list[Any]:
    for path in (("body", "hits", "hits"), ("hits", "hits")):
        found = _dig(data, *path)
        if isinstance(found, list):
            return found
    return []


def _total(data: Any) -> int | None:
    for path in (("body", "hits", "total", "value"), ("hits", "total", "value"), ("body", "hits", "total")):
        found = _dig(data, *path)
        if isinstance(found, int):
            return found
    return None


def _date_of(value: Any) -> str | None:
    if isinstance(value, dict):
        logical = as_str(value.get("logicalDate"))
        if logical:
            return logical[:10]
        year = as_str(value.get("year"))
        if year:
            parts = [year, as_str(value.get("month")), as_str(value.get("day"))]
            return "-".join(p.zfill(2) if i else p for i, p in enumerate(p for p in parts if p))
    return as_str(value)


def _date(rec: dict[str, Any]) -> str | None:
    for key in ("inclusiveStartDate", "coverageStartDate"):
        found = _date_of(rec.get(key))
        if found:
            return found
    for key in ("productionDates", "broadcastDates", "releaseDates"):
        items = listify(rec.get(key))
        if items:
            found = _date_of(items[0])
            if found:
                return found
    return None


def _kind(name: str, label: str) -> MediaKind:
    probe = f"{name} {label}".lower()
    if "pdf" in probe:
        return "pdf"
    if any(x in probe for x in ("jpg", "jpeg", "png", "tif", "gif", "image")):
        return "image"
    if any(x in probe for x in ("mp3", "wav", "audio")):
        return "audio"
    if any(x in probe for x in ("mp4", "mov", "video")):
        return "video"
    if any(x in probe for x in ("txt", "text")):
        return "text"
    return "other"


def _rights(rec: dict[str, Any]) -> Rights:
    use = rec.get("useRestriction")
    status = as_str(use.get("status")) if isinstance(use, dict) else as_str(use)
    low = (status or "").lower()
    if low.startswith("unrestricted"):
        return R.make("free", "Use unrestricted (per NARA)", statement=status, note=_NOTE)
    if low.startswith("restricted"):
        return R.make("restricted", status, statement=status, note=_NOTE)
    return R.make("unknown", statement=status, basis="holder" if status else "none", note=_NOTE)


def _record(hit: Any) -> Record | None:
    if not isinstance(hit, dict):
        return None
    source = hit.get("_source")
    if not isinstance(source, dict):
        source = hit
    rec = source.get("record")
    if not isinstance(rec, dict):
        rec = source
    na_id = as_str(rec.get("naId"))
    if not na_id:
        return None
    media: list[Media] = []
    has_text = False
    for obj in listify(rec.get("digitalObjects")):
        if not isinstance(obj, dict):
            continue
        url = as_str(obj.get("objectUrl"))
        if not url:
            continue
        name = as_str(obj.get("objectFilename")) or url
        label = as_str(obj.get("objectType")) or ""
        has_text = has_text or bool(obj.get("extractedText"))
        media.append(
            Media(
                kind=_kind(name, label), url=url, label=label or name, bytes=as_int(obj.get("objectFileSize"))
            )
        )
    occurrences = [o for o in listify(rec.get("physicalOccurrences")) if isinstance(o, dict)]
    holder = as_str(occurrences[0].get("referenceUnits")) if occurrences else None
    ancestors = [
        {
            "naId": as_str(a.get("naId")),
            "title": as_str(a.get("title")),
            "level": as_str(a.get("levelOfDescription")),
        }
        for a in listify(rec.get("ancestors"))
        if isinstance(a, dict)
    ]
    return Record(
        source=NAME,
        id=na_id,
        title=as_str(rec.get("title")),
        creators=uniq(strs(rec.get("creators")))[:8],
        date=_date(rec),
        description=truncate(as_str(rec.get("scopeAndContentNote")), 1200),
        subjects=uniq(strs(rec.get("subjects")))[:15],
        type=as_str(rec.get("levelOfDescription")) or as_str(listify(rec.get("generalRecordsTypes"))),
        holder=holder or "US National Archives",
        landing_url=f"https://catalog.archives.gov/id/{na_id}",
        rights=_rights(rec),
        media=media,
        extra={
            "ancestors": ancestors[:6],
            "has_extracted_text": has_text,
            "record_group": as_str(rec.get("recordGroupNumber")),
        },
    )


def _check_date(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not _DATE.match(value):
        raise HeritageError(f"{name} must be YYYY, YYYY-MM or YYYY-MM-DD")
    return value


async def search(
    *,
    q: str | None = None,
    title: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    available_online: bool | None = None,
    type_of_materials: str | None = None,
    level: str | None = None,
    record_group: str | None = None,
    ancestor_na_id: int | None = None,
    geographic: str | None = None,
    creators: str | None = None,
    include_extracted_text: bool = False,
    limit: int = 10,
    page: int = 1,
) -> dict[str, Any]:
    given = {
        "title": title, "typeOfMaterials": type_of_materials, "levelOfDescription": level,
        "recordGroupNumber": record_group, "geographicReference": geographic, "creators": creators,
        "ancestorNaId": ancestor_na_id,
    }  # fmt: skip
    if not q and not any(v not in (None, "") for v in given.values()):
        raise HeritageError("give NARA a search term or at least one filter")
    params: dict[str, Any] = {"limit": clamp(limit, 1, 100), "page": max(1, page)}
    if q:
        params["q"] = q
    params.update({k: v for k, v in given.items() if v not in (None, "")})
    start, end = _check_date("start_date", start_date), _check_date("end_date", end_date)
    if start:
        params["startDate"] = start
    if end:
        params["endDate"] = end
    if available_online is not None:
        params["availableOnline"] = str(available_online).lower()
    if include_extracted_text:
        params["includeExtractedText"] = "true"
    data = await http.get_json(
        NAME, f"{ROOT}/{config.nara_api_version()}/records/search", params=params, headers=_headers()
    )
    records = [r.to_dict() for r in (_record(h) for h in _hits(data)) if r]
    return {
        "total": _total(data),
        "page": params["page"],
        "limit": params["limit"],
        "records": records,
        "attribution": ATTRIBUTION,
    }


async def get_record(na_id: int) -> dict[str, Any]:
    params = {"naId_is": na_id, "limit": 1, "includeExtractedText": "true"}
    data = await http.get_json(NAME, f"{ROOT}/v2/records/search", params=params, headers=_headers())
    records = [r for r in (_record(h) for h in _hits(data)) if r]
    if not records:
        raise NotFound(f"nara has no record with naId {na_id}")
    return {"record": records[0].to_dict(), "attribution": ATTRIBUTION}


async def children(parent_na_id: int, *, limit: int = 20, page: int = 1) -> dict[str, Any]:
    params = {"limit": clamp(limit, 1, 100), "page": max(1, page)}
    data = await http.get_json(
        NAME, f"{ROOT}/v2/records/parentNaId/{int(parent_na_id)}", params=params, headers=_headers()
    )
    records = [r.to_dict() for r in (_record(h) for h in _hits(data)) if r]
    return {"parent": parent_na_id, "total": _total(data), "records": records, "attribution": ATTRIBUTION}


def _shrink(value: Any, limit: int = 4000) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"… [{len(value) - limit} more characters]"
    if isinstance(value, list):
        return [_shrink(v, limit) for v in value[:50]]
    if isinstance(value, dict):
        return {k: _shrink(v, limit) for k, v in value.items()}
    return value


async def extracted_text(
    na_id: int, *, object_id: int | None = None, limit: int = 5, page: int = 1
) -> dict[str, Any]:
    """OCR text for a record's scans. The response shape is passed through, with long strings shortened."""
    params: dict[str, Any] = {"limit": clamp(limit, 1, 50), "page": max(1, page)}
    if object_id is not None:
        params["objectId"] = object_id
    data = await http.get_json(
        NAME, f"{ROOT}/v2/extractedText/{int(na_id)}", params=params, headers=_headers()
    )
    return {"naId": na_id, "data": _shrink(data), "attribution": ATTRIBUTION}
