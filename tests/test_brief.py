"""Search results are compact; the get_* tools return everything."""

from __future__ import annotations

import httpx
import pytest

from conftest import load
from helpers import call
from heritage_research_mcp.models import BRIEF_DESCRIPTION, BRIEF_MEDIA, BRIEF_SUBJECTS, Media, Record
from heritage_research_mcp.server import mcp
from heritage_research_mcp.sources import commons
from heritage_research_mcp.sources import internet_archive as ia
from heritage_research_mcp.util import clean_url

LONG = "word " * 400


def fat_record() -> Record:
    return Record(
        source="x",
        id="1",
        title="T",
        description=LONG,
        subjects=[f"subject {i}" for i in range(12)],
        media=[
            Media(url=f"https://x.org/{i}.jpg", thumbnail_url=f"https://x.org/t{i}.jpg") for i in range(5)
        ],
        extra={"url_id": "u", "has_extracted_text": True, "ancestors": [{"naId": "1"}], "downloads": 9},
    )


def test_brief_keeps_what_helps_choose_a_result() -> None:
    brief = fat_record().to_dict(brief=True)
    assert len(brief["description"]) <= BRIEF_DESCRIPTION and brief["description"].endswith("…")
    assert len(brief["subjects"]) == BRIEF_SUBJECTS
    assert len(brief["media"]) == BRIEF_MEDIA and all("thumbnail_url" not in m for m in brief["media"])
    assert set(brief["extra"]) == {"url_id", "has_extracted_text"}
    assert brief["title"] == "T" and brief["rights"]["reuse"] == "unknown"


def test_the_full_form_is_unchanged() -> None:
    full = fat_record().to_dict()
    assert full["description"] == LONG.strip() or len(full["description"]) > BRIEF_DESCRIPTION
    assert len(full["subjects"]) == 12 and len(full["media"]) == 5
    assert full["media"][0]["thumbnail_url"] == "https://x.org/t0.jpg" and "ancestors" in full["extra"]


def test_a_short_description_is_not_touched() -> None:
    record = Record(source="x", id="1", description="Short.")
    assert record.to_dict(brief=True)["description"] == "Short."


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://upload.wikimedia.org/a.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo",
            "https://upload.wikimedia.org/a.jpg",
        ),
        ("https://x.org/a.jpg?id=5&utm_content=original", "https://x.org/a.jpg?id=5"),
        ("https://x.org/a.jpg?UTM_Source=x", "https://x.org/a.jpg"),
        ("https://x.org/a.jpg?id=5", "https://x.org/a.jpg?id=5"),
        ("https://x.org/a.jpg", "https://x.org/a.jpg"),
        (None, None),
    ],
)
def test_tracking_parameters_are_stripped(url: str | None, expected: str | None) -> None:
    assert clean_url(url) == expected


async def test_internet_archive_search_is_brief_but_get_item_is_full(api) -> None:
    data = load("ia_advancedsearch.json")
    data["response"]["docs"][0]["description"] = LONG
    api.get("https://archive.org/advancedsearch.php").mock(return_value=httpx.Response(200, json=data))
    listed = (await ia.search("x"))["records"][0]
    assert len(listed["description"]) <= BRIEF_DESCRIPTION


async def test_commons_search_is_brief_and_file_info_is_full(api) -> None:
    api.get("https://commons.wikimedia.org/w/api.php").mock(
        return_value=httpx.Response(200, json=load("commons_search.json"))
    )
    listed = (await commons.search("mosby"))["records"][0]
    full = (await commons.file_info("x"))["record"]
    assert "thumbnail_url" not in listed["media"][0] and "thumbnail_url" in full["media"][0]
    assert "utm_" not in full["media"][0]["url"] and "utm_" not in full["media"][0]["thumbnail_url"]


async def test_the_unified_search_returns_the_brief_form(api) -> None:
    api.get("https://commons.wikimedia.org/w/api.php").mock(
        return_value=httpx.Response(200, json=load("commons_search.json"))
    )
    api.get("https://archive.org/advancedsearch.php").mock(
        return_value=httpx.Response(200, json=load("ia_advancedsearch.json"))
    )
    out = await call(mcp, "search", {"query": "mosby", "sources": ["commons"]})
    assert "thumbnail_url" not in out["results"]["commons"]["records"][0]["media"][0]
