"""Phrase search must survive what OCR does to a scanned page."""

from __future__ import annotations

import httpx
import pytest

from conftest import load
from heritage_research_mcp.errors import HeritageError
from heritage_research_mcp.sources import internet_archive as ia

BOOK = "mosbyswarreimini00mosb"
META = f"https://archive.org/metadata/{BOOK}"
TEXT = f"https://archive.org/download/{BOOK}/{BOOK}_djvu.txt"

# Spacing taken from a real scan of Mosby's memoirs.
SCAN = (
    "Herndon Station, completely i88 COLONEL JOHN S. MOSBY routing them. I brought  off  twenty-five  prisoners \u2014 a major,\n"
    "one captain, two lieutenants, and twenty- one men, all their arms. With us the picket\npost  of  twenty-one men. "
    "Capture of a Fed- eral Picket."
)


@pytest.fixture
def book(api):
    api.get(META).mock(return_value=httpx.Response(200, json=load("ia_metadata.json")))
    api.get(TEXT).mock(return_value=httpx.Response(200, text=SCAN))


@pytest.mark.parametrize(
    "phrase",
    [
        "twenty-five prisoners",
        "brought off twenty-five prisoners",
        "twenty-one men",
        "picket post of twenty-one men",
        "TWENTY-FIVE PRISONERS",
    ],
)
async def test_a_plain_phrase_matches_through_ocr_spacing_and_split_hyphens(book, phrase: str) -> None:
    assert (await ia.grep_text(BOOK, phrase))["total_matches"] >= 1


async def test_the_match_is_reported_where_the_text_really_is(book) -> None:
    out = await ia.grep_text(BOOK, "twenty-five prisoners", context=20)
    assert out["matches"][0]["offset"] == SCAN.index("twenty-five")
    assert "**twenty-five  prisoners**" in out["matches"][0]["text"]


async def test_a_phrase_still_has_to_be_the_whole_phrase(book) -> None:
    assert (await ia.grep_text(BOOK, "twenty-five horses"))["total_matches"] == 0
    assert (await ia.grep_text(BOOK, "twenty five"))["total_matches"] == 0


async def test_regex_metacharacters_in_a_plain_phrase_stay_literal(api) -> None:
    api.get(META).mock(return_value=httpx.Response(200, json=load("ia_metadata.json")))
    api.get(TEXT).mock(return_value=httpx.Response(200, text="cost (a+)+ and a.b"))
    assert (await ia.grep_text(BOOK, "(a+)+"))["total_matches"] == 1
    assert (await ia.grep_text(BOOK, "a.b"))["total_matches"] == 1
    assert (await ia.grep_text(BOOK, "a-b"))["total_matches"] == 0


async def test_an_empty_phrase_is_refused(book) -> None:
    with pytest.raises(HeritageError):
        await ia.grep_text(BOOK, "   ")
