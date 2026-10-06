"""Internet Archive: catalogue search, full-text search inside books, item files and OCR text."""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any
from urllib.parse import quote

from .. import kinds as K
from .. import rights as R
from ..errors import HeritageError, NotFound
from ..http import http
from ..models import Kind, Media, MediaKind, Record, Rights
from ..util import as_int, as_str, as_year, clamp, strs, truncate, uniq

NAME = "internet_archive"
BASE = "https://archive.org"
FTS_URL = f"{BASE}/services/search/beta/page_production/"
MAX_TEXT_BYTES = 40 * 1024 * 1024

_FIELDS = [
    "identifier", "title", "creator", "date", "year", "mediatype", "collection", "description",
    "subject", "downloads", "licenseurl", "rights", "possible-copyright-status",
]  # fmt: skip
_SORT = re.compile(r"^[a-z_]+ (asc|desc)$")
_SKIP_FILE = re.compile(
    r"(_meta\.(xml|sqlite)|_files\.xml|__ia_thumb\.jpg|_archive\.torrent|_marc\.xml|_scandata\.xml|"
    r"_chocr\.html\.gz|_hocr\.html|_hocr_pageindex\.json\.gz|_hocr_searchtext\.txt\.gz|"
    r"_page_numbers\.json|_djvu\.xml|_reviews\.json|_itemimage\.jpg)$"
)
_KIND_CLAUSE = {
    "text": "mediatype:(texts)",
    "image": "mediatype:(image)",
    "audio": "mediatype:(audio)",
    "video": "mediatype:(movies)",
    "map": "(subject:(maps) OR collection:(maps*) OR collection:(david-rumsey-map-collection))",
}
_MEDIATYPE_KIND: dict[str, Kind] = {"texts": "text", "image": "image", "audio": "audio", "movies": "video"}
_text_cache: OrderedDict[str, tuple[str, str]] = OrderedDict()


def _details_url(identifier: str) -> str:
    return f"{BASE}/details/{quote(identifier, safe='')}"


def _rights_for(meta: dict[str, Any], year: int | None, mediatype: str | None) -> Rights:
    url = as_str(meta.get("licenseurl"))
    if url:
        hit = R.from_license_url(url)
        if hit:
            return hit
    status = as_str(meta.get("possible-copyright-status"))
    if status:
        hit = R.from_text(status)
        if hit and hit.reuse != "unknown":
            return hit
    text = as_str(meta.get("rights"))
    if text:
        hit = R.from_text(text)
        if hit:
            return hit.model_copy(update={"statement": text})
        return R.make("unknown", statement=text)
    if mediatype == "texts":
        hit = R.from_year(year)
        if hit:
            return hit
    return R.unknown("The Internet Archive states no rights for this item.")


def _kind_of(mediatype: str | None, subjects: list[str], collections: list[str]) -> Kind | None:
    if K.has_map_subject(subjects) or any(
        c == "david-rumsey-map-collection" or c.startswith("maps") for c in collections
    ):
        return "map"
    return _MEDIATYPE_KIND.get(mediatype or "")


def _kind(name: str, fmt: str | None) -> MediaKind:
    lowered = name.lower()
    if lowered.endswith(".pdf"):
        return "pdf"
    if lowered.endswith((".txt", ".epub", ".mobi")) or (fmt or "").lower().startswith("djvutxt"):
        return "text"
    if lowered.endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif")):
        return "image"
    if lowered.endswith((".mp3", ".ogg", ".flac", ".wav")):
        return "audio"
    if lowered.endswith((".mp4", ".ogv", ".mpeg", ".avi", ".mkv")):
        return "video"
    if lowered.endswith((".zip", ".tar", ".gz", ".7z")):
        return "archive"
    return "other"


def _record(meta: dict[str, Any], media: list[Media] | None = None) -> Record:
    identifier = str(meta.get("identifier"))
    year = as_year(meta.get("year")) or as_year(meta.get("date"))
    mediatype = as_str(meta.get("mediatype"))
    date = as_str(meta.get("date"))
    description = " ".join(strs(meta.get("description")))
    subjects = uniq(strs(meta.get("subject")))[:15]
    collections = strs(meta.get("collection"))
    return Record(
        source=NAME,
        id=identifier,
        title=as_str(meta.get("title")),
        creators=strs(meta.get("creator")),
        date=date[:10] if date else (str(year) if year else None),
        description=truncate(re.sub(r"<[^>]+>", " ", description), 1200),
        subjects=subjects,
        type=mediatype,
        kind=_kind_of(mediatype, subjects, collections),
        holder="Internet Archive",
        landing_url=_details_url(identifier),
        rights=_rights_for(meta, year, mediatype),
        media=media or [],
        extra={"collections": strs(meta.get("collection"))[:5], "downloads": meta.get("downloads")},
    )


async def search(
    query: str,
    *,
    mediatype: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    collection: str | None = None,
    rows: int = 10,
    page: int = 1,
    sort: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Search item metadata (title, creator, subject, description). Not the text inside books."""
    wanted = K.check(kind)
    clauses = [f"({query})"]
    if mediatype:
        clauses.append(f"mediatype:({mediatype})")
    if wanted and (wanted == "map" or not mediatype):
        clauses.append(_KIND_CLAUSE[wanted])
    if year_from is not None or year_to is not None:
        clauses.append(
            f"year:[{year_from if year_from is not None else '*'} TO {year_to if year_to is not None else '*'}]"
        )
    if collection:
        clauses.append(f"collection:({collection})")
    rows = clamp(rows, 1, 100)
    params: list[tuple[str, str]] = [("q", " AND ".join(clauses))]
    params += [("fl[]", f) for f in _FIELDS]
    params += [("rows", str(rows)), ("page", str(max(1, page))), ("output", "json")]
    if sort:
        if not _SORT.match(sort):
            raise HeritageError("sort must look like 'downloads desc' or 'date asc'")
        params.append(("sort[]", sort))
    data = await http.get_json(NAME, f"{BASE}/advancedsearch.php", params=params)
    block = data.get("response") or {}
    docs = [d for d in block.get("docs", []) if isinstance(d, dict) and d.get("identifier")]
    out: dict[str, Any] = {
        "total": block.get("numFound"),
        "page": page,
        "rows": rows,
        "records": [_record(d).to_dict(brief=True) for d in docs],
    }
    if wanted:
        out["kind_applied"] = _KIND_CLAUSE[wanted] + (
            " (an explicit mediatype was also given)" if mediatype and wanted != "map" else ""
        )
    return out


def _clean_snippet(text: str) -> str:
    return " ".join(text.replace("{{{", "**").replace("}}}", "**").split())


async def fulltext_search(query: str, *, hits: int = 10, page: int = 1) -> dict[str, Any]:
    """Search the OCR text inside books (an experimental Internet Archive endpoint that may change)."""
    params: dict[str, Any] = {
        "service_backend": "fts",
        "user_query": query,
        "hits_per_page": clamp(hits, 1, 50),
    }
    if page > 1:
        params["page"] = page
    data = await http.get_json(NAME, FTS_URL, params=params)
    block = ((data.get("response") or {}).get("body") or {}).get("hits") or {}
    out: list[dict[str, Any]] = []
    for hit in block.get("hits", []):
        fields = hit.get("fields") or {}
        identifier = as_str(fields.get("identifier"))
        if not identifier:
            continue
        href = as_str(fields.get("__href__"))
        snippets = [
            _clean_snippet(s) for s in (hit.get("highlight") or {}).get("text", []) if isinstance(s, str)
        ]
        entry = {
            "id": identifier,
            "title": as_str(fields.get("title")),
            "year": as_year(fields.get("year")),
            "creators": strs(fields.get("creator")),
            "page_num": fields.get("page_num"),
            "snippets": snippets[:5],
            "landing_url": f"{BASE}{href}" if href else _details_url(identifier),
            "collections": strs(fields.get("collection"))[:3],
        }
        out.append({k: v for k, v in entry.items() if v not in (None, "", [])})
    return {
        "total": block.get("total"),
        "returned": len(out),
        "hits": out,
        "note": "Full-text search is an experimental Internet Archive endpoint. Check rights on the item itself.",
    }


async def _metadata(identifier: str) -> dict[str, Any]:
    data = await http.get_json(NAME, f"{BASE}/metadata/{quote(identifier, safe='')}")
    if not isinstance(data, dict) or not data.get("metadata"):
        raise NotFound(f"internet_archive has no item called {identifier!r}")
    return data


def _media_from_files(identifier: str, files: list[dict[str, Any]]) -> list[Media]:
    media: list[Media] = []
    for entry in files:
        name = as_str(entry.get("name"))
        if not name or _SKIP_FILE.search(name):
            continue
        size = as_int(entry.get("size"))
        fmt = as_str(entry.get("format"))
        media.append(
            Media(
                kind=_kind(name, fmt),
                url=f"{BASE}/download/{quote(identifier, safe='')}/{quote(name)}",
                bytes=size,
                label=fmt or name,
            )
        )
    priority = {"pdf": 0, "text": 1, "image": 2, "archive": 3}
    media.sort(key=lambda m: (priority.get(m.kind, 4), -(m.bytes or 0)))
    return media[:40]


async def get_item(identifier: str) -> dict[str, Any]:
    data = await _metadata(identifier)
    meta = {**data["metadata"], "identifier": identifier}
    record = _record(meta, _media_from_files(identifier, data.get("files") or []))
    notes: list[str] = []
    if str(meta.get("access-restricted-item", "")).lower() == "true":
        notes.append(
            "This is a lending-only item: the files listed cannot be downloaded without borrowing it."
        )
    if data.get("is_dark"):
        notes.append("This item is dark (not publicly available).")
    return {"record": record.to_dict(), "file_count": len(data.get("files") or []), "notes": notes}


async def _load_text(identifier: str) -> tuple[str, str]:
    cached = _text_cache.get(identifier)
    if cached:
        _text_cache.move_to_end(identifier)
        return cached
    data = await _metadata(identifier)
    files = [f for f in data.get("files") or [] if isinstance(f, dict)]
    pick = next((f for f in files if str(f.get("name", "")).endswith("_djvu.txt")), None)
    pick = pick or next((f for f in files if str(f.get("format", "")).lower().startswith("djvutxt")), None)
    if not pick:
        raise NotFound(f"{identifier} has no OCR text file")
    name = str(pick["name"])
    text = await http.get_text(
        NAME, f"{BASE}/download/{quote(identifier, safe='')}/{quote(name)}", max_bytes=MAX_TEXT_BYTES
    )
    _text_cache[identifier] = (text, name)
    while len(_text_cache) > 6:
        _text_cache.popitem(last=False)
    return text, name


async def read_text(identifier: str, *, start: int = 0, length: int = 4000) -> dict[str, Any]:
    text, name = await _load_text(identifier)
    start = clamp(start, 0, max(0, len(text) - 1))
    length = clamp(length, 1, 20_000)
    return {
        "id": identifier,
        "file": name,
        "start": start,
        "length": min(length, len(text) - start),
        "total_chars": len(text),
        "text": text[start : start + length],
    }


_NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*][^)]*\)[+*{]")


async def grep_text(
    identifier: str,
    pattern: str,
    *,
    regex: bool = False,
    ignore_case: bool = True,
    context: int = 200,
    max_matches: int = 20,
) -> dict[str, Any]:
    if len(pattern) > 200:
        raise HeritageError("pattern is longer than 200 characters")
    if regex and _NESTED_QUANTIFIER.search(pattern):
        raise HeritageError("that regular expression has a nested quantifier, which can hang; simplify it")
    flags = re.IGNORECASE if ignore_case else 0
    try:
        compiled = re.compile(pattern if regex else re.escape(pattern), flags)
    except re.error as exc:
        raise HeritageError(f"invalid regular expression: {exc}") from exc
    text, name = await _load_text(identifier)
    context = clamp(context, 0, 1000)
    max_matches = clamp(max_matches, 1, 100)
    matches: list[dict[str, Any]] = []
    total = 0
    for found in compiled.finditer(text):
        total += 1
        if len(matches) < max_matches:
            before = " ".join(text[max(0, found.start() - context) : found.start()].split())
            after = " ".join(text[found.end() : found.end() + context].split())
            matches.append(
                {"offset": found.start(), "text": f"{before} **{found.group(0)}** {after}".strip()}
            )
    return {
        "id": identifier,
        "file": name,
        "total_matches": total,
        "returned": len(matches),
        "matches": matches,
    }
