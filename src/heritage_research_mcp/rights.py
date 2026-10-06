"""Turn the many ways archives state rights into one comparable `Rights`.

This is a convenience for filtering results, not legal advice. When a holder states nothing, the answer is
`unknown`, never `free`. The only inference we make is a publication-date rule for US texts, and it is labelled
as such.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Literal

from .models import Reuse, Rights

_CC_URL = re.compile(r"creativecommons\.org/(licenses|publicdomain)/([a-z\-]+)/?([0-9.]*)", re.I)
_RS_URL = re.compile(r"rightsstatements\.org/(?:vocab|page)/([A-Za-z\-]+)/?([0-9.]*)", re.I)
_CC_TEXT = re.compile(r"\bcc[ -]by((?:[ -](?:nc|sa|nd))*)[ -]?(\d\.\d)?\b", re.I)

_CC_LICENSES: dict[str, tuple[Reuse, str]] = {
    "by": ("attribution", "CC BY"),
    "by-sa": ("share_alike", "CC BY-SA"),
    "by-nc": ("non_commercial", "CC BY-NC"),
    "by-nc-sa": ("non_commercial", "CC BY-NC-SA"),
    "by-nd": ("no_derivatives", "CC BY-ND"),
    "by-nc-nd": ("restricted", "CC BY-NC-ND"),
}

_RIGHTS_STATEMENTS: dict[str, tuple[Reuse, str]] = {
    "noc-us": ("free", "No Copyright - United States"),
    "noc-nc": ("non_commercial", "No Copyright - Non-Commercial Use Only"),
    "noc-cr": ("restricted", "No Copyright - Contractual Restrictions"),
    "noc-oklr": ("restricted", "No Copyright - Other Known Legal Restrictions"),
    "inc": ("restricted", "In Copyright"),
    "inc-edu": ("restricted", "In Copyright - Educational Use Permitted"),
    "inc-nc": ("non_commercial", "In Copyright - Non-Commercial Use Permitted"),
    "inc-ruu": ("restricted", "In Copyright - Rights-holder(s) Unlocatable or Unidentifiable"),
    "inc-eu": ("restricted", "In Copyright - EU Orphan Work"),
    "nkc": ("unknown", "No Known Copyright"),
    "und": ("unknown", "Copyright Undetermined"),
    "cne": ("unknown", "Copyright Not Evaluated"),
}


def make(
    reuse: Reuse,
    label: str | None = None,
    *,
    statement: str | None = None,
    url: str | None = None,
    basis: Literal["holder", "date-heuristic", "none"] = "holder",
    note: str | None = None,
    attribution: str | None = None,
) -> Rights:
    return Rights(
        reuse=reuse,
        label=label,
        statement=statement,
        url=url,
        basis=basis,
        note=note,
        attribution=attribution,
    )


def unknown(note: str | None = None) -> Rights:
    return Rights(reuse="unknown", basis="none", note=note)


def public_domain_cutoff_year(today: dt.date | None = None) -> int:
    """US works first published this year or earlier are out of copyright (95 years, to the end of the year)."""
    return (today or dt.date.today()).year - 96


def from_year(year: int | None, today: dt.date | None = None) -> Rights | None:
    cutoff = public_domain_cutoff_year(today)
    if year is not None and year <= cutoff:
        return make(
            "free",
            f"Probably public domain (US, published before 1 Jan {cutoff + 1})",
            basis="date-heuristic",
            note="Inferred from the publication year only. Check the item for later editions, "
            "added material or foreign rules before relying on it.",
        )
    return None


def from_license_url(url: str | None) -> Rights | None:
    if not url:
        return None
    match = _CC_URL.search(url)
    if match:
        kind, slug, version = match.group(1).lower(), match.group(2).lower(), match.group(3)
        if kind == "publicdomain":
            if slug == "zero":
                return make("free", "CC0 1.0", url=url)
            if slug == "mark":
                return make("free", "Public Domain Mark 1.0", url=url)
            return None
        known = _CC_LICENSES.get(slug)
        if known:
            label = f"{known[1]} {version}".strip()
            return make(known[0], label, url=url)
        return None
    match = _RS_URL.search(url)
    if match:
        slug = match.group(1).lower()
        known = _RIGHTS_STATEMENTS.get(slug)
        if known:
            return make(known[0], known[1], url=url)
        if slug.startswith("inc"):
            return make("restricted", "In Copyright", url=url)
        return make("unknown", url=url)
    if "usa.gov/government-works" in url.lower():
        return make("free", "US Government work", url=url)
    return None


def from_text(text: str | None) -> Rights | None:
    """Classify a free-text rights label such as 'CC BY-SA 4.0', 'Public domain' or 'NOT_IN_COPYRIGHT'."""
    if not text:
        return None
    lowered = text.lower().strip()
    if re.search(r"\bcc0\b|cc zero|public domain dedication", lowered):
        return make("free", "CC0 1.0", statement=text)
    if lowered in {"not_in_copyright", "not in copyright"} or "no known copyright" in lowered:
        return make("free", "Not in copyright", statement=text)
    if lowered in {"in_copyright", "in copyright"}:
        return make("restricted", "In copyright", statement=text)
    if lowered == "unknown":
        return make("unknown", statement=text)
    cc = _CC_TEXT.search(text)
    if cc:
        slug = "by" + "".join("-" + p for p in re.findall(r"nc|sa|nd", cc.group(1).lower()))
        known = _CC_LICENSES.get(slug)
        if known:
            label = f"{known[1]} {cc.group(2) or ''}".strip()
            return make(known[0], label, statement=text)
    if "gfdl" in lowered or "gnu free documentation" in lowered:
        return make("share_alike", "GFDL", statement=text)
    if "usage conditions apply" in lowered or "all rights reserved" in lowered:
        return make("restricted", text.strip(), statement=text)
    if "public domain" in lowered or lowered.startswith("pd-") or lowered.startswith("pd "):
        return make("free", "Public domain", statement=text)
    return None
