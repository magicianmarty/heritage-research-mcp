from __future__ import annotations

import httpx
import pytest

from conftest import load
from heritage_research_mcp.errors import HeritageError, NotFound
from heritage_research_mcp.sources import internet_archive as ia

SEARCH = "https://archive.org/advancedsearch.php"
FTS = "https://archive.org/services/search/beta/page_production/"
BOOK = "mosbyswarreimini00mosb"
META = f"https://archive.org/metadata/{BOOK}"
TEXT = f"https://archive.org/download/{BOOK}/{BOOK}_djvu.txt"

BOOK_TEXT = (
    "CONTENTS. Capture of a Federal Picket at Herndon Station. The Dash and Excitement.\n"
    "On the 17th of March the reserve picket at Herndon Station, twenty-five men, was surprised. " + "x" * 400 +
    " Later the Rangers left Herndon Station behind them."
)  # fmt: skip


async def test_search_builds_the_query_and_normalises_records(api) -> None:
    route = api.get(SEARCH).mock(return_value=httpx.Response(200, json=load("ia_advancedsearch.json")))
    out = await ia.search(
        "mosby",
        mediatype="texts",
        year_from=1860,
        year_to=1930,
        collection="americana",
        rows=3,
        sort="downloads desc",
    )
    params = route.calls[0].request.url.params
    assert params["q"] == "(mosby) AND mediatype:(texts) AND year:[1860 TO 1930] AND collection:(americana)"
    assert "identifier" in params.get_list("fl[]") and "licenseurl" in params.get_list("fl[]")
    assert (params["rows"], params["sort[]"], params["output"]) == ("3", "downloads desc", "json")
    assert out["total"] == 575 and len(out["records"]) == 3
    government = out["records"][1]
    assert government["rights"]["reuse"] == "free" and government["rights"]["label"] == "US Government work"
    stated_nothing = out["records"][0]
    assert stated_nothing["rights"]["reuse"] == "unknown" and stated_nothing["rights"]["basis"] == "none"
    assert stated_nothing["landing_url"].startswith("https://archive.org/details/")


async def test_open_ended_year_range(api) -> None:
    route = api.get(SEARCH).mock(return_value=httpx.Response(200, json=load("ia_advancedsearch.json")))
    await ia.search("mosby", year_from=1860)
    assert route.calls[0].request.url.params["q"] == "(mosby) AND year:[1860 TO *]"


async def test_rows_are_clamped_and_bad_sorts_refused(api) -> None:
    route = api.get(SEARCH).mock(return_value=httpx.Response(200, json=load("ia_advancedsearch.json")))
    await ia.search("mosby", rows=5000)
    assert route.calls[0].request.url.params["rows"] == "100"
    with pytest.raises(HeritageError):
        await ia.search("mosby", sort="downloads; drop table")


async def test_fulltext_search_returns_clean_snippets_and_pages(api) -> None:
    route = api.get(FTS).mock(return_value=httpx.Response(200, json=load("ia_fts.json")))
    out = await ia.fulltext_search('"Herndon Station" Mosby', hits=2)
    params = route.calls[0].request.url.params
    assert (params["service_backend"], params["hits_per_page"]) == ("fts", "2")
    assert out["total"] == 339 and out["returned"] == len(out["hits"]) == 2
    hit = out["hits"][1]
    assert hit["id"] == BOOK and hit["year"] == 1887
    assert any("Herndon Station" in s for s in hit["snippets"])
    assert all("{{{" not in s and "}}}" not in s for h in out["hits"] for s in h["snippets"])
    assert any("**" in s for s in hit["snippets"])
    assert hit["landing_url"].startswith("https://archive.org/details/")


async def test_get_item_lists_files_and_dates_rights_from_the_year(api) -> None:
    api.get(META).mock(return_value=httpx.Response(200, json=load("ia_metadata.json")))
    out = await ia.get_item(BOOK)
    record = out["record"]
    assert record["title"].startswith("Mosby's war")
    assert record["rights"]["reuse"] == "free" and record["rights"]["basis"] == "date-heuristic"
    kinds = [m["kind"] for m in record["media"]]
    assert kinds[0] == "pdf" and "text" in kinds
    assert record["media"][0]["url"] == f"https://archive.org/download/{BOOK}/{BOOK}.pdf"
    assert record["media"][0]["bytes"] == 16845243
    names = " ".join(m["url"] for m in record["media"])
    assert "_meta.xml" not in names and "__ia_thumb.jpg" not in names and "_files.xml" not in names
    assert out["notes"] == []


async def test_lending_only_items_are_flagged(api) -> None:
    data = load("ia_metadata.json")
    data["metadata"]["access-restricted-item"] = "true"
    api.get(META).mock(return_value=httpx.Response(200, json=data))
    out = await ia.get_item(BOOK)
    assert "lending-only" in out["notes"][0]


async def test_unknown_item_is_not_found(api) -> None:
    api.get("https://archive.org/metadata/nope").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(NotFound):
        await ia.get_item("nope")


async def test_explicit_licence_beats_the_date_rule(api) -> None:
    data = load("ia_metadata.json")
    data["metadata"]["licenseurl"] = "https://creativecommons.org/licenses/by-nc/4.0/"
    api.get(META).mock(return_value=httpx.Response(200, json=data))
    rights = (await ia.get_item(BOOK))["record"]["rights"]
    assert (rights["reuse"], rights["basis"]) == ("non_commercial", "holder")


async def test_grep_finds_every_match_and_caches_the_text(api) -> None:
    api.get(META).mock(return_value=httpx.Response(200, json=load("ia_metadata.json")))
    text = api.get(TEXT).mock(return_value=httpx.Response(200, text=BOOK_TEXT))
    out = await ia.grep_text(BOOK, "herndon station", context=30)
    assert out["total_matches"] == 3 and out["returned"] == 3
    assert "**Herndon Station**" in out["matches"][0]["text"]
    assert out["matches"][0]["offset"] == BOOK_TEXT.index("Herndon Station")
    again = await ia.grep_text(BOOK, "Rangers")
    assert again["total_matches"] == 1
    assert text.call_count == 1


async def test_grep_limits_matches_but_reports_the_total(api) -> None:
    api.get(META).mock(return_value=httpx.Response(200, json=load("ia_metadata.json")))
    api.get(TEXT).mock(return_value=httpx.Response(200, text=BOOK_TEXT))
    out = await ia.grep_text(BOOK, "Herndon Station", max_matches=1)
    assert (out["total_matches"], out["returned"]) == (3, 1)


async def test_grep_refuses_dangerous_or_broken_patterns(api) -> None:
    with pytest.raises(HeritageError):
        await ia.grep_text(BOOK, "(a+)+$", regex=True)
    with pytest.raises(HeritageError):
        await ia.grep_text(BOOK, "(unclosed", regex=True)
    with pytest.raises(HeritageError):
        await ia.grep_text(BOOK, "x" * 201)


async def test_literal_patterns_are_not_treated_as_regular_expressions(api) -> None:
    api.get(META).mock(return_value=httpx.Response(200, json=load("ia_metadata.json")))
    api.get(TEXT).mock(return_value=httpx.Response(200, text="price (a+)+ and more"))
    out = await ia.grep_text(BOOK, "(a+)+")
    assert out["total_matches"] == 1


async def test_read_text_returns_a_slice_with_the_total(api) -> None:
    api.get(META).mock(return_value=httpx.Response(200, json=load("ia_metadata.json")))
    api.get(TEXT).mock(return_value=httpx.Response(200, text=BOOK_TEXT))
    out = await ia.read_text(BOOK, start=10, length=20)
    assert out["text"] == BOOK_TEXT[10:30]
    assert out["total_chars"] == len(BOOK_TEXT) and out["file"].endswith("_djvu.txt")


async def test_item_without_ocr_text_is_not_found(api) -> None:
    data = load("ia_metadata.json")
    data["files"] = [f for f in data["files"] if "djvu" not in f["name"].lower()]
    api.get(META).mock(return_value=httpx.Response(200, json=data))
    with pytest.raises(NotFound):
        await ia.read_text(BOOK)
