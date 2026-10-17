from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from conftest import FIXTURES
from helpers import ToolFailed, call
from heritage_research_mcp import download
from heritage_research_mcp.errors import HeritageError, NotFound, NotPrepared
from heritage_research_mcp.server import mcp
from heritage_research_mcp.sources import dispatch

XML = (FIXTURES / "dispatch_issue.xml").read_text(encoding="utf-8")
FILE = "D_025_053.pers.ie.xml"


def archive_bytes(**extra: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(FILE, XML)
        for name, text in extra.items():
            bundle.writestr(name, text)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(_host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(download, "resolve", resolve)


@pytest.fixture
def built(tmp_path: Path) -> dict:
    archive = tmp_path / "DispatchTEI.zip"
    archive.write_bytes(archive_bytes())
    return dispatch.build_index(archive, dispatch.index_path())


def test_parse_numbers_articles_like_the_site_and_skips_ad_blank_placeholders() -> None:
    issue = dispatch.parse_issue(XML, FILE)
    assert issue is not None
    assert issue["file"] == "D_025_053" and issue["date"] == "1863-09-01"
    assert issue["title"] == "The Daily Dispatch: September 1, 1863."
    shape = [(a["no"], a["page"], a["type"]) for a in issue["articles"]]
    assert shape == [(1, 1, "article"), (2, 1, "article"), (3, 1, "article"), (4, 2, "death")]


def test_parse_cleans_markup_without_splitting_words_or_repeating_the_headline() -> None:
    issue = dispatch.parse_issue(XML, FILE)
    assert issue is not None
    mosby = issue["articles"][2]
    assert mosby["title"] == "Mosby wounded."
    assert mosby["body"].startswith("--Col. Mosby, the daring and brilliant cavalry chieftain")
    assert "<" not in mosby["body"] and "&" not in mosby["body"]


def test_parse_ignores_a_file_that_states_no_date() -> None:
    assert dispatch.parse_issue("<TEI.2><text></text></TEI.2>", "x.xml") is None


def test_parse_does_not_resolve_the_external_dtd_or_entities() -> None:
    hostile = XML.replace("<TEI.2", '<!ENTITY x SYSTEM "file:///etc/passwd">\n<TEI.2', 1)
    issue = dispatch.parse_issue(hostile, FILE)
    assert issue is not None and "root:" not in json.dumps(issue)


async def test_search_before_the_index_exists_says_how_to_build_it() -> None:
    with pytest.raises(NotPrepared) as caught:
        await dispatch.search("mosby")
    assert "dispatch_prepare" in str(caught.value) and "dispatch-index" in str(caught.value)


async def test_search_finds_and_ranks_with_highlights_rights_and_links(built) -> None:
    assert built["issues"] == 1 and built["articles"] == 4
    out = await dispatch.search("Mosby")
    assert out["total"] == 1 and out["returned"] == 1
    record = out["records"][0]
    assert record["id"] == "D_025_053/1/3"
    assert record["title"] == "Mosby wounded." and record["date"] == "1863-09-01"
    assert "**Mosby**" in record["description"]
    assert record["kind"] == "text"
    assert record["landing_url"] == "https://dispatch.richmond.edu/1863/9/1/1/3"
    assert (
        record["media"][0]["url"] == "https://dispatch.richmond.edu/static/DispatchXML/D_025_053.pers.ie.xml"
    )
    rights = record["rights"]
    assert rights["reuse"] == "free" and rights["basis"] == "date-heuristic"
    assert (
        "University of Richmond" in rights["statement"] and "University of Richmond" in rights["attribution"]
    )


async def test_search_matches_word_endings_and_phrases_and_alternatives(built) -> None:
    assert (await dispatch.search("wound"))["total"] == 1
    assert (await dispatch.search('"cavalry chieftain"'))["total"] == 1
    assert (await dispatch.search('"chieftain cavalry"'))["total"] == 0
    assert (await dispatch.search("Moseby OR Mosby"))["total"] == 1
    assert (await dispatch.search("Mosby Hartman"))["total"] == 0


async def test_odd_punctuation_never_reaches_the_query_parser(built) -> None:
    for query in ['AND ( " NEAR* -', "OR", "mosby'; DROP TABLE articles; --", "col:mosby", "mosby*"]:
        try:
            await dispatch.search(query)
        except HeritageError as exc:
            assert "at least one word" in str(exc)
    assert (await dispatch.search("Mosby"))["total"] == 1


async def test_empty_query_is_an_error(built) -> None:
    with pytest.raises(HeritageError, match="at least one word"):
        await dispatch.search("  ")


async def test_date_and_section_filters(built) -> None:
    assert (await dispatch.search("Mosby", date_from="1863-09", date_to="1863-09-01"))["total"] == 1
    assert (await dispatch.search("Mosby", date_from="1863-09-02"))["total"] == 0
    assert (await dispatch.search("Mosby", date_to="1862"))["total"] == 0
    assert (await dispatch.search("Steel", section="death"))["total"] == 1
    assert (await dispatch.search("Steel", section="article"))["total"] == 0
    with pytest.raises(HeritageError, match="YYYY"):
        await dispatch.search("Mosby", date_from="September 1863")


async def test_a_kind_other_than_text_returns_nothing_and_says_why(built) -> None:
    out = await dispatch.search("Mosby", kind="image")
    assert out["records"] == [] and "newspaper text only" in out["kind_applied"]
    assert (await dispatch.search("Mosby", kind="text"))["kind_applied"] == "text"


async def test_get_article_returns_the_whole_text_and_the_printed_issue_title(built) -> None:
    record = (await dispatch.get_article("D_025_053/1/3"))["record"]
    assert record["extra"]["text"].startswith("Mosby wounded.\n\n--Col. Mosby")
    assert record["extra"]["issue_title"] == "The Daily Dispatch: September 1, 1863."
    assert record["extra"]["tei_file"] == FILE and record["extra"]["page"] == 1
    with pytest.raises(NotFound):
        await dispatch.get_article("D_025_053/1/99")
    with pytest.raises(HeritageError, match="looks like"):
        await dispatch.get_article("../../etc/passwd")


async def test_issue_lists_headlines_by_date_or_file(built) -> None:
    by_date = await dispatch.issue("1863-09-01")
    articles = by_date["issues"][0]["articles"]
    assert [a["title"] for a in articles] == [
        "From the army of Northern Virginia.",
        "From Tennessee.",
        "Mosby wounded.",
        "Died.",
    ]
    assert articles[2]["id"] == "D_025_053/1/3" and articles[3]["page"] == 2
    assert (await dispatch.issue("D_025_053"))["issues"][0]["date"] == "1863-09-01"
    with pytest.raises(NotFound, match="1860-11-01 to 1865-12-30"):
        await dispatch.issue("1861-01-01")
    with pytest.raises(HeritageError, match="issue date"):
        await dispatch.issue("../x")


async def test_prepare_downloads_once_indexes_and_records_provenance(api) -> None:
    route = api.get(dispatch.ARCHIVE_URL).mock(
        return_value=httpx.Response(200, content=archive_bytes(), headers={"content-type": "application/zip"})
    )
    first = await dispatch.prepare()
    assert first["built"] is True and first["articles"] == 4 and first["ready"] is True
    sidecar = Path(first["archive"] + ".provenance.json")
    assert json.loads(sidecar.read_text(encoding="utf-8"))["url"] == dispatch.ARCHIVE_URL
    second = await dispatch.prepare()
    assert second["built"] is False and route.call_count == 1
    third = await dispatch.prepare(refresh=True)
    assert third["built"] is True and route.call_count == 2


async def test_prepare_discards_a_download_that_is_not_a_zip(api) -> None:
    api.get(dispatch.ARCHIVE_URL).mock(
        return_value=httpx.Response(
            200, text="<!doctype html><html></html>", headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(HeritageError, match="not a zip"):
        await dispatch.prepare()
    assert not dispatch.index_ready()
    assert not list((dispatch.index_path().parent).rglob("*.html*"))


def test_build_ignores_entries_that_are_not_plain_issue_files(tmp_path: Path) -> None:
    archive = tmp_path / "bundle.zip"
    archive.write_bytes(archive_bytes(**{"../evil.xml": XML, "notes.txt": "x", "sub/dir.xml": XML}))
    stats = dispatch.build_index(archive, dispatch.index_path())
    assert stats["issues"] == 1


def test_a_failed_build_leaves_no_half_written_index(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    archive.write_bytes(archive_bytes())
    dest = dispatch.index_path()
    dispatch.build_index(archive, dest)
    before = dest.read_bytes()
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip")
    with pytest.raises(HeritageError):
        dispatch.build_index(broken, dest)
    assert dest.read_bytes() == before and not dest.with_name(dest.name + ".building").exists()


async def test_index_from_an_older_schema_is_treated_as_missing(built) -> None:
    import sqlite3

    con = sqlite3.connect(dispatch.index_path())
    con.execute(f"PRAGMA user_version = {dispatch.SCHEMA - 1}")
    con.commit()
    con.close()
    assert not dispatch.index_ready()
    with pytest.raises(NotPrepared):
        await dispatch.search("Mosby")


async def test_tools_and_cross_source_search_use_the_index(built) -> None:
    sources = (await call(mcp, "list_sources"))["sources"]
    assert sources["dispatch"]["configured"] is True and sources["dispatch"]["needs_index"] is True
    found = await call(mcp, "dispatch_search", {"query": "Mosby"})
    assert found["records"][0]["id"] == "D_025_053/1/3"
    both = await call(
        mcp, "search", {"query": "Mosby", "sources": ["dispatch"], "date_from": 1863, "date_to": 1863}
    )
    assert both["results"]["dispatch"]["total"] == 1 and both["errors"] == {}
    record = (await call(mcp, "get_record", {"source": "dispatch", "record_id": "D_025_053/1/3"}))["record"]
    assert record["extra"]["text"].startswith("Mosby wounded.")
    assert (await call(mcp, "dispatch_get_article", {"article_id": "D_025_053/2/4"}))["record"][
        "title"
    ] == "Died."
    listing = await call(mcp, "dispatch_issue", {"issue": "1863-09-01"})
    assert listing["issues"][0]["id_prefix"] == "D_025_053" and len(listing["issues"][0]["articles"]) == 4


async def test_without_an_index_search_leaves_the_dispatch_out_and_tools_explain() -> None:
    sources = (await call(mcp, "list_sources"))["sources"]
    assert sources["dispatch"]["configured"] is False
    plain = await call(mcp, "search", {"query": "x", "sources": ["dispatch"]})
    assert "dispatch_prepare" in plain["skipped"]["dispatch"]
    with pytest.raises(ToolFailed, match="dispatch_prepare"):
        await call(mcp, "dispatch_search", {"query": "Mosby"})
