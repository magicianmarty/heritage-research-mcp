"""US National Archives Catalog (API v2, with an opt-in v3 search).

The API's terms forbid scraping or downloading everything through it, so every call here is a single page
capped at 100 records, and the monthly quota (10,000 per key by default) is counted and enforced locally.

Field names, vocabularies and quirks below were checked against the live service with a real key
(2026-10-07); `tests/fixtures/nara_live_*.json` are trimmed captures.
"""

from __future__ import annotations

import calendar
import datetime as dt
import mimetypes
import re
from typing import Any
from urllib.parse import urlsplit

from .. import config
from .. import kinds as K
from .. import rights as R
from ..errors import HeritageError, NotFound
from ..http import Resp, http
from ..models import Kind, Media, MediaKind, Record, Rights
from ..util import as_int, as_str, clamp, listify, strs, truncate, uniq

NAME = "nara"
ROOT = "https://catalog.archives.gov/api"
ATTRIBUTION = "This product uses the National Archives Catalog API but is not endorsed or certified by the National Archives."
MEDIA_LIMIT = 25
_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
_OPEN_START, _OPEN_END = "1000-01-01", "2100-12-31"
_SIZE_PLACEHOLDERS = {1234, 12345, 123456, 1234567, 5242880}  # seen on files whose real size was 5 to 8 MB
_NOTE = (
    "US federal records are often public domain, but donated or third-party material can carry restrictions. "
    "Read the record's own use restriction."
)

MATERIALS = (
    "Architectural and Engineering Drawings", "Artifacts", "Data Files", "Maps and Charts", "Moving Images",
    "Photographs and other Graphic Materials", "Sound Recordings", "Textual Records", "Web Pages",
)  # fmt: skip
LEVELS = ("recordGroup", "collection", "series", "fileUnit", "item")
_KIND_MATERIALS = {
    "text": "Textual Records",
    "image": "Photographs and other Graphic Materials",
    "map": "Maps and Charts",
    "audio": "Sound Recordings",
    "video": "Moving Images",
}
_MATERIAL_ALIASES = {
    "map": "Maps and Charts", "maps": "Maps and Charts", "charts": "Maps and Charts",
    "photo": "Photographs and other Graphic Materials", "photos": "Photographs and other Graphic Materials",
    "photograph": "Photographs and other Graphic Materials", "photographs": "Photographs and other Graphic Materials",
    "image": "Photographs and other Graphic Materials", "images": "Photographs and other Graphic Materials",
    "text": "Textual Records", "textual": "Textual Records", "documents": "Textual Records",
    "records": "Textual Records", "audio": "Sound Recordings", "sound": "Sound Recordings",
    "film": "Moving Images", "video": "Moving Images", "drawings": "Architectural and Engineering Drawings",
    "data": "Data Files", "web": "Web Pages",
}  # fmt: skip

_USE: dict[str, tuple[R.Reuse, str]] = {
    "unrestricted": ("free", "Use unrestricted (per NARA)"),
    "restricted - fully": ("restricted", "Use restricted (per NARA)"),
    "restricted - partly": ("restricted", "Use partly restricted (per NARA)"),
    "restricted - possibly": ("unknown", "Use possibly restricted (per NARA)"),
    "undetermined": ("unknown", "Use restrictions undetermined (per NARA)"),
}  # fmt: skip

_EXT_KIND: dict[str, MediaKind] = {
    "jpg": "image", "jpeg": "image", "gif": "image", "png": "image", "tif": "image", "tiff": "image",
    "jp2": "image", "pdf": "pdf", "txt": "text", "mp3": "audio", "wav": "audio", "mp4": "video",
    "mov": "video", "mpg": "video", "zip": "archive",
}  # fmt: skip
_LABEL_KIND: tuple[tuple[str, MediaKind], ...] = (
    ("pdf", "pdf"), ("image", "image"), ("audio", "audio"), ("video", "video"), ("zip", "archive"),
    ("text", "text"),
)  # fmt: skip


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


async def _get(url: str, params: dict[str, Any]) -> Any:
    resp: Resp = await http.request(NAME, url, params=params, headers=_headers())
    if "json" not in resp.headers.get("content-type", "").lower():
        query = str(params.get("q") or "")
        if "(" in query or ")" in query:
            cause = (
                "Its firewall refuses many queries that put parentheses next to AND or OR (the key is fine): "
                "rewrite without parentheses, for example `mosby AND rangers`, or run two searches."
            )
        else:
            cause = (
                "That is what it does for a missing or wrong API key: check NARA_API_KEY, or run "
                "`heritage-research-mcp doctor --live`."
            )
        raise HeritageError(
            f"nara answered with its website instead of data. {cause} The request still counted against the monthly quota."
        )
    return resp.json()


def _fmt_date(value: Any) -> str | None:
    """'1860' for a year-only date, never the padded logicalDate; keeps NARA's 'ca.' qualifier."""
    if not isinstance(value, dict):
        return as_str(value)
    year = as_int(value.get("year"))
    if year is None:
        logical = as_str(value.get("logicalDate"))
        return logical[:10] if logical else None
    text = f"{year:04d}"
    month, day = as_int(value.get("month")), as_int(value.get("day"))
    if month:
        text += f"-{month:02d}"
        if day:
            text += f"-{day:02d}"
    qualifier = as_str(value.get("dateQualifier"))
    return f"{qualifier} {text}" if qualifier else text


def _date(rec: dict[str, Any]) -> str | None:
    for key in ("productionDates", "broadcastDates", "releaseDates"):
        items = listify(rec.get(key))
        if items:
            found = _fmt_date(items[0])
            if found:
                return found
    for start_key, end_key in (
        ("inclusiveStartDate", "inclusiveEndDate"),
        ("coverageStartDate", "coverageEndDate"),
    ):
        start, end = _fmt_date(rec.get(start_key)), _fmt_date(rec.get(end_key))
        if start and end and start != end:
            return f"{start} to {end}"
        if start or end:
            return start or end
    return None


def _expand(name: str, value: str, *, end: bool) -> str:
    if not _DATE.match(value):
        raise HeritageError(f"{name} must be YYYY, YYYY-MM or YYYY-MM-DD")
    parts = [int(p) for p in value.split("-")]
    year = parts[0]
    month = parts[1] if len(parts) > 1 else (12 if end else 1)
    day = parts[2] if len(parts) > 2 else (calendar.monthrange(year, month)[1] if end else 1)
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError as exc:
        raise HeritageError(f"{name} {value!r} is not a real date") from exc


def date_bounds(start_date: str | None, end_date: str | None) -> dict[str, str]:
    """NARA applies a date filter only when both bounds are sent, in the same format; full dates always work."""
    if not start_date and not end_date:
        return {}
    start = _expand("start_date", start_date, end=False) if start_date else _OPEN_START
    end = _expand("end_date", end_date, end=True) if end_date else _OPEN_END
    return {"startDate": start, "endDate": end}


def _squash(text: str) -> str:
    return re.sub(r"[\s_-]+", "", text).lower()


def check_materials(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    wanted = value.strip()
    for name in MATERIALS:
        if _squash(name) == _squash(wanted):
            return name
    alias = _MATERIAL_ALIASES.get(wanted.lower())
    if alias:
        return alias
    raise HeritageError(
        f"type_of_materials must be one of {list(MATERIALS)} (or text, image, map, audio, video)"
    )


def check_level(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    for name in LEVELS:
        if _squash(name) == _squash(value):
            return name
    raise HeritageError(f"level must be one of {list(LEVELS)}")


def _kind_of(rec: dict[str, Any], media: list[Media]) -> Kind | None:
    probe = " ".join(strs(rec.get("generalRecordsTypes")) + strs(rec.get("typeOfMaterials"))).lower()
    for needle, found in (
        ("map", "map"), ("photograph", "image"), ("graphic", "image"), ("textual", "text"),
        ("moving image", "video"), ("sound", "audio"),
    ):  # fmt: skip
        if needle in probe:
            return found  # type: ignore[return-value]
    for item in media:
        if item.kind in ("image", "audio", "video"):
            return item.kind
        if item.kind in ("pdf", "text"):
            return "text"
    return None


def _rights(rec: dict[str, Any]) -> Rights:
    use = rec.get("useRestriction")
    status = as_str(use.get("status")) if isinstance(use, dict) else as_str(use)
    note = as_str(use.get("note")) if isinstance(use, dict) else None
    specific = strs(use.get("specificUseRestrictions")) if isinstance(use, dict) else []
    reuse, label = _USE.get((status or "").lower(), ("unknown", None))
    if status and label is None:
        label = f"{status} (per NARA)"
    if specific and reuse != "free":
        label = f"{label}: {', '.join(specific)}"
    return R.make(
        reuse,
        label,
        statement=note or status,
        basis="holder" if status else "none",
        note=None if reuse == "free" else _NOTE,
    )


def _media_kind(filename: str, label: str) -> MediaKind:
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension in _EXT_KIND:
        return _EXT_KIND[extension]
    low = label.lower()
    for needle, kind in _LABEL_KIND:
        if needle in low:
            return kind
    return "other"


def _media(obj: Any, *, excerpts: bool) -> Media | None:
    if not isinstance(obj, dict):
        return None
    url = as_str(obj.get("objectUrl"))
    if not url:
        return None
    filename = as_str(obj.get("objectFilename")) or urlsplit(url).path.rsplit("/", 1)[-1]
    label = as_str(obj.get("objectType")) or ""
    size = as_int(obj.get("objectFileSize"))
    return Media(
        kind=_media_kind(filename, label),
        url=url,
        mime=mimetypes.guess_type(filename)[0],
        bytes=None if size in _SIZE_PLACEHOLDERS else size,
        label=truncate(as_str(obj.get("objectDescription")), 160) or label or filename,
        id=as_str(obj.get("objectId")),
        text_excerpt=truncate(as_str(obj.get("extractedText")), 300) if excerpts else None,
    )


def _heading(entry: Any, role_key: str | None = None) -> str | None:
    if not isinstance(entry, dict):
        return as_str(entry)
    heading = as_str(entry.get("heading"))
    role = as_str(entry.get(role_key)) if role_key else None
    return f"{heading} [{role}]" if heading and role else heading


def _ancestor(ancestors: list[dict[str, Any]], level: str) -> dict[str, Any] | None:
    return next((a for a in ancestors if a.get("levelOfDescription") == level), None)


def _created_by(ancestors: list[dict[str, Any]]) -> list[str]:
    for ancestor in reversed(ancestors):
        creators = [c for c in listify(ancestor.get("creators")) if isinstance(c, dict)]
        if creators:
            creators.sort(key=lambda c: c.get("creatorType") != "Most Recent")
            return uniq(h for h in (_heading(c) for c in creators) if h)[:4]
    return []


def _citation(rec: dict[str, Any], ancestors: list[dict[str, Any]], date: str | None) -> str:
    group, series = _ancestor(ancestors, "recordGroup"), _ancestor(ancestors, "series")
    file_unit = _ancestor(ancestors, "fileUnit")
    parts = [as_str(rec.get("title")) or f"NAID {rec.get('naId')}"]
    if date:
        parts[0] += f", {date}"
    for ancestor in (file_unit, series):
        if ancestor and as_str(ancestor.get("title")):
            parts.append(as_str(ancestor.get("title")) or "")
    if group:
        parts.append(f"RG {as_str(group.get('recordGroupNumber'))}, {as_str(group.get('title'))}")
    parts.append("National Archives and Records Administration")
    parts.append(f"NAID {rec.get('naId')}")
    local = as_str(rec.get("localIdentifier"))
    if local:
        parts.append(f"local ID {local}")
    return "; ".join(parts) + "."


def _record(hit: Any, *, excerpts: bool = False, detail: bool = False) -> Record | None:
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
    objects = [o for o in listify(rec.get("digitalObjects")) if isinstance(o, dict)]
    media = [m for m in (_media(o, excerpts=excerpts) for o in objects) if m]
    ocr_asked = any("extractedText" in o for o in objects)
    occurrences = [o for o in listify(rec.get("physicalOccurrences")) if isinstance(o, dict)]
    holder = as_str(occurrences[0].get("referenceUnits")) if occurrences else None
    ancestors = [a for a in listify(rec.get("ancestors")) if isinstance(a, dict)]
    group, series = _ancestor(ancestors, "recordGroup"), _ancestor(ancestors, "series")
    subjects = [s for s in listify(rec.get("subjects")) if isinstance(s, dict)]
    places = [s for s in subjects if s.get("authorityType") == "geographicPlaceName"]
    date = _date(rec)
    access = rec.get("accessRestriction")
    access_status = as_str(access.get("status")) if isinstance(access, dict) else as_str(access)

    extra: dict[str, Any] = {
        "record_group": (
            f"RG {as_str(group.get('recordGroupNumber'))}: {as_str(group.get('title'))}" if group else None
        ),
        "series": as_str(series.get("title")) if series else None,
        "has_extracted_text": any(o.get("extractedText") for o in objects) if ocr_asked else None,
        "access": access_status if access_status and access_status.lower() != "unrestricted" else None,
    }
    if detail:
        extra.update(
            {
                "citation": _citation(rec, ancestors, date),
                "created_by": _created_by(ancestors),
                "local_id": as_str(rec.get("localIdentifier")),
                "access_note": as_str(access.get("note")) if isinstance(access, dict) else None,
                "microfilm": [t for t in (as_str(m) for m in listify(rec.get("microformPublications"))) if t][
                    :5
                ],
                "related_links": [
                    {"description": as_str(r.get("description")), "url": as_str(r.get("url"))}
                    for r in listify(rec.get("onlineResources"))
                    if isinstance(r, dict) and as_str(r.get("url"))
                ][:5],
                "ancestors": [
                    {
                        "naId": as_str(a.get("naId")),
                        "title": as_str(a.get("title")),
                        "level": as_str(a.get("levelOfDescription")),
                    }
                    for a in ancestors
                ][:6],
            }
        )
    return Record(
        source=NAME,
        id=na_id,
        title=as_str(rec.get("title")),
        creators=uniq(
            h
            for h in [
                *(_heading(c) for c in listify(rec.get("creators"))),
                *(_heading(c, "contributorType") for c in listify(rec.get("contributors"))),
            ]
            if h
        )[:8],
        date=date,
        description=truncate(as_str(rec.get("scopeAndContentNote")), 1200),
        subjects=uniq(h for h in (_heading(s) for s in subjects if s not in places) if h)[:15],
        places=uniq(h for h in (_heading(s) for s in places) if h)[:8],
        type=as_str(rec.get("levelOfDescription")) or as_str(listify(rec.get("generalRecordsTypes"))),
        kind=_kind_of(rec, media),
        holder=holder or "US National Archives",
        landing_url=f"https://catalog.archives.gov/id/{na_id}",
        rights=_rights(rec),
        media=media,
        extra=extra,
    )


def _buckets(node: Any) -> list[dict[str, Any]]:
    found = _dig(node, "buckets")
    return [b for b in found if isinstance(b, dict)] if isinstance(found, list) else []


def _facets(data: Any) -> dict[str, Any]:
    """Where the hits sit, so a researcher can narrow by record group or type of material."""
    agg = _dig(data, "body", "aggregations")
    if not isinstance(agg, dict):
        return {}
    types = {str(b.get("key")): b.get("doc_count") for b in _buckets(agg.get("typeOfMaterials"))}
    groups = _buckets(_dig(agg, "recordGroupNumber", "filter_recordGroups", "recordGroups"))
    out = {
        "types": types,
        "record_groups": [f"{b.get('key')} ({b.get('doc_count')})" for b in groups[:6]],
    }
    return {k: v for k, v in out.items() if v}


def _hint(used: list[str]) -> str:
    return (
        f"No record matched every filter ({', '.join(used)}). geographic and creators match NARA's subject and "
        "creator headings, which many records (maps and military files especially) lack: put the place or "
        "name in q instead. Dropping one filter at a time finds the one that excludes everything."
    )


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
    kind: str | None = None,
) -> dict[str, Any]:
    wanted = K.check(kind)
    materials = check_materials(type_of_materials)
    applied = None
    if wanted and not materials:
        materials = _KIND_MATERIALS[wanted]
        applied = f"typeOfMaterials={materials!r}"
    given = {
        "title": title, "typeOfMaterials": materials, "levelOfDescription": check_level(level),
        "recordGroupNumber": str(record_group).strip().removeprefix("RG").strip() if record_group else None,
        "geographicReference": geographic, "creators": creators, "ancestorNaId": ancestor_na_id,
    }  # fmt: skip
    if not q and not any(v not in (None, "") for v in given.values()):
        raise HeritageError("give NARA a search term or at least one filter")
    params: dict[str, Any] = {"limit": clamp(limit, 1, 100), "page": max(1, page)}
    if q:
        params["q"] = q
    params.update({k: v for k, v in given.items() if v not in (None, "")})
    params.update(date_bounds(start_date, end_date))
    if available_online is not None:
        params["availableOnline"] = str(available_online).lower()
    if include_extracted_text:
        params["includeExtractedText"] = "true"
    data = await _get(f"{ROOT}/{config.nara_api_version()}/records/search", params)
    records = [
        r.to_dict(brief=True) for r in (_record(h, excerpts=include_extracted_text) for h in _hits(data)) if r
    ]
    out: dict[str, Any] = {
        "total": _total(data),
        "page": params["page"],
        "limit": params["limit"],
        "records": records,
        "attribution": ATTRIBUTION,
    }
    if params["page"] == 1:
        out["facets"] = _facets(data)
    if "startDate" in params:
        out["date_note"] = (
            "NARA's date filter also matches records whose parent series or file spans the range, so some hits "
            "are undated or fall outside it: check each record's own `date`."
        )
    tool_names = {
        "title": "title", "levelOfDescription": "level", "recordGroupNumber": "record_group",
        "geographicReference": "geographic", "creators": "creators", "ancestorNaId": "ancestor_na_id",
    }  # fmt: skip
    used = [
        tool_names[name] for name, value in given.items() if name in tool_names and value not in (None, "")
    ]
    if "startDate" in params:
        used.append("dates")
    if not records and used:
        out["hint"] = _hint(used)
    if wanted:
        out["kind_applied"] = applied or "(the explicit type_of_materials decides)"
    return out


async def get_record(na_id: int, *, media_limit: int | None = MEDIA_LIMIT) -> dict[str, Any]:
    params = {"naId_is": na_id, "limit": 1, "includeExtractedText": "true"}
    data = await _get(f"{ROOT}/v2/records/search", params)
    records = [r for r in (_record(h, detail=True) for h in _hits(data)) if r]
    if not records:
        raise NotFound(
            f"nara has no description with naId {na_id} (authority records for people and bodies are not returned)"
        )
    record = records[0].to_dict()
    media = record.get("media") or []
    if media_limit is not None and len(media) > media_limit:
        record["media"] = media[:media_limit]
        record.setdefault("extra", {}).update(
            {
                "media_total": len(media),
                "media_note": (
                    f"Showing the first {media_limit} of {len(media)} files. nara_extracted_text reads OCR for any "
                    "of them, and download_media reaches every file by media_index."
                ),
            }
        )
    return {"record": record, "attribution": ATTRIBUTION}


async def children(parent_na_id: int, *, limit: int = 20, page: int = 1) -> dict[str, Any]:
    params = {"limit": clamp(limit, 1, 100), "page": max(1, page)}
    data = await _get(f"{ROOT}/v2/records/parentNaId/{int(parent_na_id)}", params)
    records = [r.to_dict(brief=True) for r in (_record(h) for h in _hits(data)) if r]
    return {
        "parent": parent_na_id,
        "total": _total(data),
        "page": params["page"],
        "records": records,
        "attribution": ATTRIBUTION,
    }


def _shrink(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} more characters]"


def _contribution(item: Any, limit: int) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    text = as_str(item.get("contribution"))
    if not text:
        return None
    who = item.get("contributor")
    who = who if isinstance(who, dict) else {}
    return {
        "text": _shrink(text, limit),
        "chars": len(text),
        "by": as_str(who.get("partnerFullName")) or as_str(who.get("fullName")),
        "machine_generated": str(item.get("aiMachineGenerated", "")).lower() in {"1", "true"} or None,
        "date": (as_str(item.get("createdAt")) or "")[:10] or None,
    }


async def extracted_text(
    na_id: int, *, object_id: int | None = None, limit: int = 5, page: int = 1, max_chars: int = 4000
) -> dict[str, Any]:
    """The text NARA holds for a record's scans: machine OCR and partner or volunteer transcriptions."""
    params: dict[str, Any] = {"limit": clamp(limit, 1, 50), "page": max(1, page)}
    if object_id is not None:
        params["objectId"] = object_id
    data = await _get(f"{ROOT}/v2/extractedText/{int(na_id)}", params)
    limit_chars = clamp(max_chars, 200, 50_000)
    objects: list[dict[str, Any]] = []
    for obj in listify(_dig(data, "digitalObjects")):
        if not isinstance(obj, dict):
            continue
        ocr = as_str(obj.get("extractedText"))
        entry = {
            "object_id": as_str(obj.get("objectId")),
            "ocr": _shrink(ocr, limit_chars) if ocr else None,
            "ocr_chars": len(ocr) if ocr else None,
            "transcriptions": [
                c
                for c in (_contribution(i, limit_chars) for i in listify(obj.get("otherExtractedText")))
                if c
            ],
        }
        objects.append(
            {k: v for k, v in entry.items() if v not in (None, [])} | {"object_id": entry["object_id"]}
        )
    return {
        "naId": na_id,
        "total_objects": _dig(data, "total"),
        "page": params["page"],
        "objects": objects,
        "note": (
            "`ocr` is NARA's machine text. `transcriptions` come from NARA partners or volunteers (see `by`), and "
            "`machine_generated` marks AI output. Either can be wrong on handwriting: use them to find and "
            "check names, and read the scan before quoting. A file with neither has no text."
        ),
        "attribution": ATTRIBUTION,
    }
