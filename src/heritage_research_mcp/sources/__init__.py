"""Source registry: what each archive is good for, what it needs, and what its terms ask of us."""

from __future__ import annotations

from dataclasses import dataclass

from .. import config


@dataclass(frozen=True)
class SourceInfo:
    name: str
    label: str
    keyed: bool
    good_for: str
    limits: str
    terms: str
    setup: str | None = None


SOURCES: dict[str, SourceInfo] = {
    "internet_archive": SourceInfo(
        name="internet_archive",
        label="Internet Archive",
        keyed=False,
        good_for="Scanned books, memoirs, regimental histories, government reports, maps, full text inside books.",
        limits="No published quota. Requests are paced at about 5 per second.",
        terms="Rights vary per item and are often unstated. Lending-only items cannot be downloaded.",
    ),
    "commons": SourceInfo(
        name="commons",
        label="Wikimedia Commons",
        keyed=False,
        good_for="Freely licensed photographs, maps and prints, with a licence recorded per file.",
        limits="Requests are paced and use the maxlag convention. A descriptive User-Agent is sent.",
        terms="Each file carries its own licence; share-alike and attribution conditions are surfaced.",
    ),
    "dpla": SourceInfo(
        name="dpla",
        label="Digital Public Library of America",
        keyed=True,
        good_for="Finding items held by US libraries, archives and museums, including state and local collections.",
        limits="No routine rate limit, but access can be withdrawn for abusive use.",
        terms="Metadata is CC0. DPLA does not host content: rights belong to the contributing institution.",
    ),
    "nara": SourceInfo(
        name="nara",
        label="US National Archives Catalog",
        keyed=True,
        good_for="Federal records, military and census material, photographs, with OCR text for many scans.",
        limits="10,000 queries per month per key by default; this server counts and stops at that limit.",
        terms="No scraping or bulk download through the API. Attribution is required (included in results).",
    ),
    "smithsonian": SourceInfo(
        name="smithsonian",
        label="Smithsonian Open Access",
        keyed=True,
        good_for="Objects, photographs, archives and library items from the Smithsonian museums.",
        limits="Set by api.data.gov per key; remaining calls are reported when the server sees them.",
        terms="Records marked CC0 can be reused freely. Others say 'Usage conditions apply'.",
    ),
    "dispatch": SourceInfo(
        name="dispatch",
        label="Richmond Daily Dispatch (University of Richmond)",
        keyed=False,
        good_for="Full text of a Richmond newspaper, 1860 to 1865: war news, orders, notices, deaths, advertisements.",
        limits="Searched from a local index built from one 92 MB download, so it makes no per-search requests.",
        terms="The paper is out of copyright; the digital edition is (c) 2004 University of Richmond. Cite it.",
        setup="no local index; run dispatch_prepare",
    ),
    "chronam_local": SourceInfo(
        name="chronam_local",
        label="Chronicling America, local OCR index",
        keyed=False,
        good_for="Full-text search of newspaper pages for years you chose, built from the Library's bulk OCR archives.",
        limits="Built once per range with chronam_bulk_start (large downloads: Virginia 1863 is about 43 GB); "
        "searching makes no requests.",
        terms="Library of Congress OCR, raw and uncorrected. Pages up to 1930 are probably public domain.",
        setup="no local index; plan one with chronam_bulk_plan",
    ),
    "chronam": SourceInfo(
        name="chronam",
        label="Chronicling America (Library of Congress)",
        keyed=False,
        good_for="Digitised US newspaper pages, 1690 to the present, with raw OCR text: local news, notices, advertisements.",
        limits="The loc.gov API allows 20 requests a minute and blocks for about an hour beyond that; this server waits "
        "3 s between requests and stops for 10 minutes after a block or bot challenge.",
        terms="Public API, no key. Pages up to 1930 are probably public domain; later ones may be in copyright.",
    ),
}


def is_configured(name: str) -> bool:
    info = SOURCES[name]
    if name == "dispatch":
        from . import dispatch

        return dispatch.index_ready()
    if name == "chronam_local":
        from . import chronam_bulk

        return chronam_bulk.ready()
    return (not info.keyed) or config.get_key(name) is not None
