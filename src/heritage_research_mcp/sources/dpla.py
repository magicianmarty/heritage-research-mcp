"""Digital Public Library of America: item metadata from US libraries, archives and museums."""

from __future__ import annotations

import re
from typing import Any

from .. import config
from .. import kinds as K
from .. import rights as R
from ..errors import HeritageError, NotFound
from ..http import http
from ..models import Kind, Media, MediaKind, Record, Rights
from ..util import as_str, clamp, listify, strs, truncate, uniq

NAME = "dpla"
BASE = "https://api.dp.la/v2"

_FILTERS = {
    "title": "sourceResource.title",
    "creator": "sourceResource.creator",
    "subject": "sourceResource.subject.name",
    "place_state": "sourceResource.spatial.state",
    "date_after": "sourceResource.date.after",
    "date_before": "sourceResource.date.before",
    "type": "sourceResource.type",
    "provider": "provider.name",
    "data_provider": "dataProvider",
}


_KIND_TYPE = {"text": "text", "image": "image", "map": "image", "audio": "sound", "video": "moving image"}
_TYPE_KIND: dict[str, Kind] = {"text": "text", "image": "image", "sound": "audio", "moving image": "video"}
_NOTICE = re.compile(
    r"copyright laws? protect|protected by (?:u\.s\. )?copyright|all rights reserved|may not be (?:downloaded|reproduced|copied)",
    re.I,
)
_MASTER_KIND: dict[str, MediaKind] = {
    "pdf": "pdf",
    "txt": "text",
    "mp3": "audio",
    "wav": "audio",
    "mp4": "video",
    "mov": "video",
}


def _from_category(category: str | None) -> Rights | None:
    """DPLA's own summary, used only when the record has no rights URL or recognisable text."""
    low = (category or "").lower()
    if low.startswith("unlimited"):
        return R.make("free", "Unlimited re-use (per DPLA)", statement=category)
    if low.startswith("re-use, no modification"):
        return R.make("no_derivatives", "Re-use, no modification (per DPLA)", statement=category)
    if low.startswith("permission"):
        return R.make("restricted", "Permission or fair use (per DPLA)", statement=category)
    return None


def _rights(doc: dict[str, Any]) -> Rights:
    source = doc.get("sourceResource") or {}
    uri = as_str(doc.get("rights"))
    text = "; ".join(strs(source.get("rights"))) or None
    category = as_str(doc.get("rightsCategory"))
    found = R.from_license_url(uri) or R.from_text(text) or _from_category(category)
    if found is None and text and _NOTICE.search(text):
        found = R.make("restricted", "Copyright notice from the holder", statement=truncate(text, 400))
    if found is None:
        found = R.unknown("The contributing institution did not state a recognisable rights label.")
    update: dict[str, Any] = {}
    if text and not found.statement:
        update["statement"] = truncate(text, 400)
    if uri and not found.url:
        update["url"] = uri
    base = "DPLA metadata is CC0. Reuse of the item is set by the contributing institution."
    update["note"] = f"{base} DPLA rights category: {category}." if category else base
    return found.model_copy(update=update)


def _kind_of(source: dict[str, Any], subjects: list[str]) -> Kind | None:
    formats = strs(source.get("format"))
    if K.has_map_subject(subjects) or K.has_map_subject(formats):
        return "map"
    return _TYPE_KIND.get((as_str(source.get("type")) or "").lower())


def _media(doc: dict[str, Any]) -> list[Media]:
    media: list[Media] = []
    for url in strs(doc.get("mediaMaster")):
        extension = url.rsplit(".", 1)[-1].lower().split("?")[0]
        media.append(
            Media(kind=_MASTER_KIND.get(extension, "image"), url=url, label="master file from the holder")
        )
    thumbnail = as_str(doc.get("object"))
    if thumbnail and thumbnail not in {m.url for m in media}:
        media.append(Media(kind="image", url=thumbnail, label="thumbnail supplied by the provider"))
    return media


def _record(doc: dict[str, Any]) -> Record | None:
    identifier = as_str(doc.get("id"))
    if not identifier:
        return None
    source = doc.get("sourceResource") or {}
    media = _media(doc)
    holder = as_str(doc.get("dataProvider"))
    subjects = uniq(strs(source.get("subject")))[:15]
    return Record(
        source=NAME,
        id=identifier,
        title=as_str(source.get("title")),
        creators=strs(source.get("creator")),
        date=as_str(source.get("date")),
        description=truncate(" ".join(strs(source.get("description"))), 1200),
        subjects=subjects,
        places=uniq(strs(source.get("spatial")))[:10],
        type=as_str(source.get("type")),
        kind=_kind_of(source, subjects),
        holder=holder,
        landing_url=as_str(doc.get("isShownAt")),
        rights=_rights(doc),
        media=media,
        extra={"provider": as_str(doc.get("provider")), "languages": strs(source.get("language"))[:3]},
    )


async def search(
    *,
    q: str | None = None,
    title: str | None = None,
    creator: str | None = None,
    subject: str | None = None,
    place_state: str | None = None,
    date_after: str | None = None,
    date_before: str | None = None,
    type: str | None = None,  # noqa: A002 - mirrors the DPLA field name
    provider: str | None = None,
    data_provider: str | None = None,
    page: int = 1,
    page_size: int = 10,
    sort_by: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    wanted = K.check(kind)
    applied: list[str] = []
    if wanted:
        if not type:
            type = _KIND_TYPE[wanted]
            applied.append(f"sourceResource.type={type}")
        if wanted == "map" and not subject:
            subject = "Maps"
            applied.append("sourceResource.subject.name=Maps")
    given = {
        "title": title, "creator": creator, "subject": subject, "place_state": place_state,
        "date_after": date_after, "date_before": date_before, "type": type,
        "provider": provider, "data_provider": data_provider,
    }  # fmt: skip
    if not q and not any(given.values()):
        raise HeritageError("give DPLA at least a search term or one filter")
    params: dict[str, Any] = {
        "api_key": config.require_key(NAME),
        "page": max(1, page),
        "page_size": clamp(page_size, 1, 100),
    }
    if q:
        params["q"] = q
    for key, value in given.items():
        if value:
            params[_FILTERS[key]] = value
    if sort_by:
        params["sort_by"] = sort_by
    data = await http.get_json(NAME, f"{BASE}/items", params=params)
    docs = [d for d in listify(data.get("docs")) if isinstance(d, dict)]
    records = [r.to_dict() for r in (_record(d) for d in docs) if r]
    out: dict[str, Any] = {
        "total": data.get("count"),
        "page": params["page"],
        "page_size": params["page_size"],
        "records": records,
    }
    if wanted:
        out["kind_applied"] = " and ".join(applied) or "(explicit filters decide)"
    return out


async def get_item(item_id: str) -> dict[str, Any]:
    data = await http.get_json(NAME, f"{BASE}/items/{item_id}", params={"api_key": config.require_key(NAME)})
    docs = [d for d in listify(data.get("docs")) if isinstance(d, dict)]
    record = _record(docs[0]) if docs else None
    if record is None:
        raise NotFound(f"dpla has no item with id {item_id!r}")
    return {"record": record.to_dict()}
