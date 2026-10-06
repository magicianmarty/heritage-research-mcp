from __future__ import annotations

import datetime as dt

import pytest

from heritage_research_mcp import rights as R


@pytest.mark.parametrize(
    ("url", "reuse", "label"),
    [
        ("https://creativecommons.org/publicdomain/zero/1.0/", "free", "CC0 1.0"),
        ("https://creativecommons.org/publicdomain/mark/1.0/", "free", "Public Domain Mark 1.0"),
        ("https://creativecommons.org/licenses/by/4.0/", "attribution", "CC BY 4.0"),
        ("https://creativecommons.org/licenses/by-sa/3.0/deed.en", "share_alike", "CC BY-SA 3.0"),
        ("https://creativecommons.org/licenses/by-nc/4.0/", "non_commercial", "CC BY-NC 4.0"),
        ("https://creativecommons.org/licenses/by-nc-sa/4.0/", "non_commercial", "CC BY-NC-SA 4.0"),
        ("https://creativecommons.org/licenses/by-nd/4.0/", "no_derivatives", "CC BY-ND 4.0"),
        ("https://creativecommons.org/licenses/by-nc-nd/4.0/", "restricted", "CC BY-NC-ND 4.0"),
        ("http://rightsstatements.org/vocab/NoC-US/1.0/", "free", "No Copyright - United States"),
        (
            "http://rightsstatements.org/vocab/NoC-NC/1.0/",
            "non_commercial",
            "No Copyright - Non-Commercial Use Only",
        ),
        ("http://rightsstatements.org/vocab/InC/1.0/", "restricted", "In Copyright"),
        (
            "http://rightsstatements.org/vocab/InC-EDU/1.0/",
            "restricted",
            "In Copyright - Educational Use Permitted",
        ),
        ("http://rightsstatements.org/vocab/NKC/1.0/", "unknown", "No Known Copyright"),
        ("http://rightsstatements.org/vocab/CNE/1.0/", "unknown", "Copyright Not Evaluated"),
        ("https://www.usa.gov/government-works", "free", "US Government work"),
    ],
)
def test_license_urls(url: str, reuse: str, label: str) -> None:
    found = R.from_license_url(url)
    assert found is not None
    assert (found.reuse, found.label, found.url) == (reuse, label, url)


@pytest.mark.parametrize(
    "url", [None, "", "https://example.org/terms", "https://creativecommons.org/licenses/zzz/4.0/"]
)
def test_unrecognised_urls_give_nothing(url: str | None) -> None:
    assert R.from_license_url(url) is None


@pytest.mark.parametrize(
    ("text", "reuse", "label"),
    [
        ("CC BY-SA 4.0", "share_alike", "CC BY-SA 4.0"),
        ("CC BY 4.0", "attribution", "CC BY 4.0"),
        ("cc by-nc-nd 3.0", "restricted", "CC BY-NC-ND 3.0"),
        ("CC0", "free", "CC0 1.0"),
        ("Public domain", "free", "Public domain"),
        ("PD-US-expired", "free", "Public domain"),
        ("NOT_IN_COPYRIGHT", "free", "Not in copyright"),
        ("IN_COPYRIGHT", "restricted", "In copyright"),
        ("UNKNOWN", "unknown", None),
        ("Usage conditions apply", "restricted", "Usage conditions apply"),
        ("All rights reserved", "restricted", "All rights reserved"),
        ("GFDL", "share_alike", "GFDL"),
    ],
)
def test_free_text(text: str, reuse: str, label: str | None) -> None:
    found = R.from_text(text)
    assert found is not None
    assert (found.reuse, found.label) == (reuse, label)


@pytest.mark.parametrize("text", [None, "", "something unrelated"])
def test_unrecognised_text_gives_nothing(text: str | None) -> None:
    assert R.from_text(text) is None


def test_year_rule_follows_the_calendar() -> None:
    assert R.public_domain_cutoff_year(dt.date(2026, 10, 6)) == 1930
    assert R.public_domain_cutoff_year(dt.date(2027, 1, 1)) == 1931
    today = dt.date(2026, 10, 6)
    inside = R.from_year(1930, today)
    assert inside is not None and inside.reuse == "free" and inside.basis == "date-heuristic"
    assert R.from_year(1931, today) is None
    assert R.from_year(None, today) is None


def test_unknown_is_never_free() -> None:
    found = R.unknown("nothing stated")
    assert (found.reuse, found.basis, found.note) == ("unknown", "none", "nothing stated")
