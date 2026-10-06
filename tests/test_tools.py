from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from conftest import load
from helpers import ToolFailed, call
from heritage_research_mcp import download
from heritage_research_mcp.server import mcp

EXPECTED_TOOLS = {
    "list_sources", "usage_report", "search", "get_record", "download_media",
    "ia_search", "ia_fulltext_search", "ia_get_item", "ia_read_text", "ia_grep_text",
    "commons_search", "commons_file_info", "commons_category_members",
    "dpla_search", "dpla_get_item",
    "nara_search", "nara_get_record", "nara_children", "nara_extracted_text",
    "si_search", "si_get_content", "si_terms", "si_stats",
}  # fmt: skip

IA_SEARCH = "https://archive.org/advancedsearch.php"
IA_FTS = "https://archive.org/services/search/beta/page_production/"
COMMONS = "https://commons.wikimedia.org/w/api.php"
DPLA = "https://api.dp.la/v2/items"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(_host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(download, "resolve", resolve)


async def test_every_tool_is_registered_and_described() -> None:
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert all((t.description or "").strip() for t in tools)


async def test_list_sources_reflects_which_keys_exist(set_key) -> None:
    before = (await call(mcp, "list_sources"))["sources"]
    assert before["internet_archive"]["configured"] is True and before["commons"]["configured"] is True
    for name in ("dpla", "nara", "smithsonian"):
        assert before[name]["configured"] is False and "get_a_key" in before[name]
    assert before["nara"]["env_var"] == "NARA_API_KEY"
    set_key("dpla")
    after = (await call(mcp, "list_sources"))["sources"]
    assert after["dpla"]["configured"] is True and after["dpla"]["key_from"] == "env"
    assert "test-key-123" not in str(after)


async def test_search_queries_every_ready_source_and_skips_the_rest(api) -> None:
    api.get(IA_SEARCH).mock(return_value=httpx.Response(200, json=load("ia_advancedsearch.json")))
    api.get(COMMONS).mock(return_value=httpx.Response(200, json=load("commons_search.json")))
    out = await call(mcp, "search", {"query": "mosby", "limit": 3})
    assert set(out["results"]) == {"internet_archive", "commons"}
    assert len(out["results"]["internet_archive"]["records"]) == 3
    assert out["errors"] == {} and out["skipped"] == {}


async def test_asking_for_an_unconfigured_source_is_reported_not_fatal(api) -> None:
    api.get(IA_SEARCH).mock(return_value=httpx.Response(200, json=load("ia_advancedsearch.json")))
    out = await call(mcp, "search", {"query": "mosby", "sources": ["internet_archive", "dpla"]})
    assert "internet_archive" in out["results"]
    assert "no API key" in out["skipped"]["dpla"]


async def test_one_failing_source_does_not_sink_the_others(api) -> None:
    api.get(IA_SEARCH).mock(return_value=httpx.Response(500, text="archive is down"))
    api.get(COMMONS).mock(return_value=httpx.Response(200, json=load("commons_search.json")))
    out = await call(mcp, "search", {"query": "mosby"})
    assert "HTTP 500" in out["errors"]["internet_archive"]
    assert len(out["results"]["commons"]["records"]) == 2


async def test_search_can_include_text_inside_books(api) -> None:
    api.get(IA_SEARCH).mock(return_value=httpx.Response(200, json=load("ia_advancedsearch.json")))
    api.get(COMMONS).mock(return_value=httpx.Response(200, json=load("commons_search.json")))
    api.get(IA_FTS).mock(return_value=httpx.Response(200, json=load("ia_fts.json")))
    out = await call(mcp, "search", {"query": "Herndon Station", "fulltext": True, "date_from": 1860})
    assert out["fulltext"]["returned"] == 2
    assert "Date filters apply" in out["notes"][0]


async def test_keyed_sources_join_the_default_search_once_configured(api, set_key) -> None:
    set_key("dpla")
    api.get(IA_SEARCH).mock(return_value=httpx.Response(200, json=load("ia_advancedsearch.json")))
    api.get(COMMONS).mock(return_value=httpx.Response(200, json=load("commons_search.json")))
    route = api.get(DPLA).mock(return_value=httpx.Response(200, json=load("dpla_items.json")))
    out = await call(mcp, "search", {"query": "herndon", "date_from": 1863, "date_to": 1864})
    assert "dpla" in out["results"]
    params = route.calls[0].request.url.params
    assert params["sourceResource.date.after"] == "1863" and params["sourceResource.date.before"] == "1864"


async def test_unknown_sources_are_rejected_with_the_valid_names() -> None:
    with pytest.raises(ToolFailed) as caught:
        await call(mcp, "search", {"query": "x", "sources": ["google"]})
    assert "unknown source" in str(caught.value) and "internet_archive" in str(caught.value)


async def test_missing_key_guidance_reaches_the_model() -> None:
    with pytest.raises(ToolFailed) as caught:
        await call(mcp, "dpla_search", {"q": "herndon"})
    message = str(caught.value)
    assert "DPLA_API_KEY" in message and "https://pro.dp.la/developers/policies#get-a-key" in message


async def test_get_record_dispatches_by_source(api) -> None:
    api.get(COMMONS).mock(return_value=httpx.Response(200, json=load("commons_search.json")))
    out = await call(mcp, "get_record", {"source": "commons", "record_id": "File:x.jpg"})
    assert out["record"]["source"] == "commons"
    with pytest.raises(ToolFailed):
        await call(mcp, "get_record", {"source": "nara", "record_id": "not-a-number"})


async def test_download_media_end_to_end(api) -> None:
    pages = load("commons_search.json")
    record_id = pages["query"]["pages"][0]["title"]
    api.get(COMMONS).mock(return_value=httpx.Response(200, json=pages))
    api.route(host="upload.wikimedia.org").mock(return_value=httpx.Response(200, content=b"JPEGDATA" * 50))
    out = await call(mcp, "download_media", {"source": "commons", "record_id": record_id})
    assert Path(out["path"]).read_bytes() == b"JPEGDATA" * 50
    assert out["rights"]["reuse"] == "free" and "rights_warning" not in out
    assert Path(out["path"] + ".provenance.json").exists()


async def test_downloading_something_not_marked_free_carries_a_warning(api, set_key) -> None:
    set_key("dpla")
    doc = {
        "id": "zzz",
        "isShownAt": "https://example.org/zzz",
        "object": "https://files.example.org/zzz.jpg",
        "rights": "http://rightsstatements.org/vocab/InC/1.0/",
        "sourceResource": {"title": "A protected photograph"},
    }
    api.get(f"{DPLA}/zzz").mock(return_value=httpx.Response(200, json={"count": 1, "docs": [doc]}))
    api.route(host="files.example.org").mock(return_value=httpx.Response(200, content=b"PIC"))
    out = await call(mcp, "download_media", {"source": "dpla", "record_id": "zzz"})
    assert "reference" in out["rights_warning"]


async def test_download_media_validates_what_to_fetch(api, set_key) -> None:
    set_key("dpla")
    no_media = {"id": "m", "sourceResource": {"title": "Nothing to fetch"}}
    api.get(f"{DPLA}/m").mock(return_value=httpx.Response(200, json={"docs": [no_media]}))
    with pytest.raises(ToolFailed) as caught:
        await call(mcp, "download_media", {"source": "dpla", "record_id": "m"})
    assert "no downloadable media" in str(caught.value)
    with_media = {**no_media, "id": "w", "object": "https://files.example.org/w.jpg"}
    api.get(f"{DPLA}/w").mock(return_value=httpx.Response(200, json={"docs": [with_media]}))
    with pytest.raises(ToolFailed) as bad_index:
        await call(mcp, "download_media", {"source": "dpla", "record_id": "w", "media_index": 5})
    assert "between 0 and 0" in str(bad_index.value)
    with pytest.raises(ToolFailed) as bad_kind:
        await call(mcp, "download_media", {"source": "dpla", "record_id": "w", "media_kind": "audio"})
    assert "kinds present" in str(bad_kind.value)


async def test_usage_report_counts_requests(api) -> None:
    api.get(COMMONS).mock(return_value=httpx.Response(200, json=load("commons_search.json")))
    await call(mcp, "commons_search", {"query": "mosby"})
    report = await call(mcp, "usage_report")
    assert report["this_session"]["commons"] == 1 and report["this_month"]["commons"] == 1
