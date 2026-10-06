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
}


def is_configured(name: str) -> bool:
    info = SOURCES[name]
    return (not info.keyed) or config.get_key(name) is not None
