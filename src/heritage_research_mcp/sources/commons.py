"""Wikimedia Commons: freely licensed images, with the licence read from each file's metadata."""

from __future__ import annotations

import re
from typing import Any

from .. import kinds as K
from .. import rights as R
from ..errors import HeritageError, NotFound
from ..http import http
from ..models import Kind, Media, MediaKind, Record, Rights
from ..util import as_int, as_str, clamp, strip_html, truncate, uniq

NAME = "commons"
API = "https://commons.wikimedia.org/w/api.php"
FILETYPES = {"bitmap", "drawing", "audio", "video", "office", "multimedia"}

_KIND_TERMS = {
    "text": "filetype:office",
    "image": "filetype:bitmap",
    "map": "intitle:map filetype:bitmap",
    "audio": "filetype:audio",
    "video": "filetype:video",
}
_PARAMS = {
    "action": "query",
    "format": "json",
    "formatversion": "2",
    "maxlag": "5",
    "prop": "imageinfo|info",
    "inprop": "url",
    "iiprop": "url|size|mime|extmetadata",
    "iiurlwidth": "640",
}


def _ext(ext: dict[str, Any], key: str) -> str | None:
    return strip_html((ext.get(key) or {}).get("value"))


def _kind(mime: str | None) -> MediaKind:
    if not mime:
        return "other"
    if mime == "application/pdf":
        return "pdf"
    major = mime.split("/")[0]
    return major if major in ("image", "audio", "video") else "other"  # type: ignore[return-value]


_HOUSEKEEPING = re.compile(
    r"(?i)(uploaded by|^pd-|^cc-|\btemplate\b|^files? (from|with|needing)|^media (needing|with)|^pages? (with|using)"
    r"|^self-published|\blicen[cs]e\b|^(images|photos|pictures) (from|by|with|needing)|\bwikidata\b|\bbot\b)"
)


def _kind_of(mime: str | None, title: str, categories: list[str]) -> Kind | None:
    if K.title_says_map(title) or K.has_map_subject(categories):
        return "map"
    major = (mime or "").split("/")[0]
    if major in ("image", "audio", "video"):
        return major  # type: ignore[return-value]
    if mime in ("application/pdf", "image/vnd.djvu"):
        return "text"
    return None


def _rights(ext: dict[str, Any], page_url: str | None) -> Rights:
    url = _ext(ext, "LicenseUrl")
    short = _ext(ext, "LicenseShortName")
    terms = _ext(ext, "UsageTerms")
    copyrighted = (_ext(ext, "Copyrighted") or "").lower()
    restrictions = _ext(ext, "Restrictions")
    found = R.from_license_url(url) or R.from_text(short) or R.from_text(terms)
    if found is None and copyrighted == "false":
        found = R.make("free", "Public domain")
    if found is None:
        found = R.unknown("Commons did not state a recognisable licence.")
    update: dict[str, Any] = {"statement": terms or short}
    if url and not found.url:
        update["url"] = url
    if restrictions:
        update["note"] = f"Commons flags additional restrictions: {restrictions}."
    if (
        found.reuse in ("attribution", "share_alike")
        or (_ext(ext, "AttributionRequired") or "").lower() == "true"
    ):
        who = _ext(ext, "Artist") or _ext(ext, "Credit") or "unknown author"
        update["attribution"] = (
            f"{truncate(who, 160)}, {found.label or short or 'licence on file page'}, via Wikimedia Commons ({page_url})"
        )
    return found.model_copy(update=update)


def _record(page: dict[str, Any]) -> Record | None:
    infos = page.get("imageinfo") or []
    if not infos:
        return None
    info = infos[0]
    ext = info.get("extmetadata") or {}
    title = as_str(page.get("title")) or ""
    stem = title.removeprefix("File:").rsplit(".", 1)[0]
    page_url = as_str(info.get("descriptionurl")) or as_str(page.get("canonicalurl"))
    categories = [c for c in (_ext(ext, "Categories") or "").split("|") if c and not _HOUSEKEEPING.search(c)]
    mime = as_str(info.get("mime"))
    original = as_str(info.get("url"))
    media = (
        [
            Media(
                kind=_kind(mime),
                url=original,
                mime=mime,
                bytes=as_int(info.get("size")),
                width=as_int(info.get("width")),
                height=as_int(info.get("height")),
                label="original",
                thumbnail_url=as_str(info.get("thumburl")),
            )
        ]
        if original
        else []
    )
    return Record(
        source=NAME,
        id=title,
        title=stem.replace("_", " "),
        creators=[c for c in [_ext(ext, "Artist")] if c],
        date=_ext(ext, "DateTimeOriginal") or _ext(ext, "DateTime"),
        description=truncate(_ext(ext, "ImageDescription"), 1200),
        subjects=uniq(categories)[:15],
        type=mime,
        kind=_kind_of(mime, stem, categories),
        holder=truncate(_ext(ext, "Credit"), 200),
        landing_url=page_url,
        rights=_rights(ext, page_url),
        media=media,
    )


def _records(data: dict[str, Any]) -> list[dict[str, Any]]:
    pages = (data.get("query") or {}).get("pages") or []
    pages = sorted((p for p in pages if isinstance(p, dict)), key=lambda p: p.get("index", 0))
    return [r.to_dict() for r in (_record(p) for p in pages) if r]


async def search(
    query: str, *, limit: int = 10, filetype: str | None = None, kind: str | None = None
) -> dict[str, Any]:
    if filetype and filetype not in FILETYPES:
        raise HeritageError(f"filetype must be one of {sorted(FILETYPES)}")
    wanted = K.check(kind)
    extra = ""
    if wanted:
        extra = _KIND_TERMS[wanted] if not filetype else ("intitle:map" if wanted == "map" else "")
    term = " ".join(part for part in (query, extra, f"filetype:{filetype}" if filetype else "") if part)
    params = {
        **_PARAMS,
        "generator": "search",
        "gsrsearch": term,
        "gsrnamespace": "6",
        "gsrlimit": str(clamp(limit, 1, 50)),
    }
    data = await http.get_json(NAME, API, params=params)
    records = _records(data)
    out: dict[str, Any] = {"returned": len(records), "records": records, "more": "continue" in data}
    if wanted:
        out["kind_applied"] = extra or "(the explicit filetype decides)"
    return out


async def file_info(title: str) -> dict[str, Any]:
    name = title if title.lower().startswith("file:") else f"File:{title}"
    data = await http.get_json(NAME, API, params={**_PARAMS, "titles": name})
    records = _records(data)
    if not records:
        raise NotFound(f"commons has no file called {name!r}")
    return {"record": records[0]}


async def category_members(category: str, *, limit: int = 20, filetype: str | None = None) -> dict[str, Any]:
    name = category if category.lower().startswith("category:") else f"Category:{category}"
    params = {
        **_PARAMS,
        "generator": "categorymembers",
        "gcmtitle": name,
        "gcmtype": "file",
        "gcmlimit": str(clamp(limit, 1, 50)),
    }
    data = await http.get_json(NAME, API, params=params)
    records = _records(data)
    if filetype:
        wanted = {"bitmap": "image", "drawing": "image", "audio": "audio", "video": "video"}.get(filetype)
        if wanted:
            records = [r for r in records if str(r.get("type", "")).startswith(wanted)]
    return {"category": name, "returned": len(records), "records": records, "more": "continue" in data}
