from __future__ import annotations

import copy

import httpx
import pytest

from conftest import load
from helpers import ToolFailed, call
from heritage_research_mcp import doctor
from heritage_research_mcp import http as http_module
from heritage_research_mcp.errors import Blocked, HeritageError, NotFound
from heritage_research_mcp.server import mcp
from heritage_research_mcp.sources import chronicling_america as chronam

SEARCH = "https://www.loc.gov/collections/chronicling-america/"
PAGE = "https://www.loc.gov/resource/sn92070146/1924-11-20/ed-1/"
BASE = "https://tile.loc.gov/storage-services/service/ndnp/curiv/batch_curiv_ocotillo_ver01/data/sn92070146/00414189696/1924112001/1270"
CHALLENGE = {"cf-mitigated": "challenge"}


def challenged() -> httpx.Response:
    return httpx.Response(403, text="<title>Just a moment...</title>", headers=CHALLENGE)


def resource_page(**item: object) -> dict:
    return {
        "item": {
            "newspaper_title": ["Imperial Valley press (El Centro, Calif.)"],
            "date": "1924-11-20",
            "location_city": ["el centro"],
            "location_state": ["california"],
            "number_lccn": ["sn92070146"],
            "contributor_names": ["University of California, Riverside"],
            "batch": ["curiv_ocotillo_ver01"],
            **item,
        },
        "resource": {"pdf": f"{BASE}.pdf"},
        "pagination": {"current": 6},
    }


async def test_search_reads_the_recorded_loc_result(api) -> None:
    api.get(SEARCH).mock(return_value=httpx.Response(200, json=load("chronam_search.json")))
    out = await chronam.search("clara bow", rows=5)
    assert out["total"] == 2 and out["returned"] == 1 and out["more"] is False
    record = out["records"][0]
    assert record["id"] == "sn92070146/1924-11-20/ed-1/6"
    assert record["title"].startswith("Image 6 of Imperial Valley press")
    assert record["date"] == "1924-11-20" and record["places"] == ["El Centro, California"]
    assert record["landing_url"] == "https://www.loc.gov/resource/sn92070146/1924-11-20/ed-1/?sp=6"
    assert "**Clara**" in record["description"] and "**Bow**" in record["description"]
    assert "[[tag]]" not in record["description"]
    assert record["kind"] == "text" and record["type"] == "newspaper page"
    assert [m["kind"] for m in record["media"]] == ["pdf", "text"]
    assert record["media"][0]["url"] == f"{BASE}.pdf" and record["media"][1]["url"] == f"{BASE}.txt"
    assert record["rights"]["reuse"] == "free" and record["rights"]["basis"] == "date-heuristic"
    assert "Chronicling America" in record["rights"]["attribution"]


async def test_search_sends_the_query_shape_loc_documents(api) -> None:
    route = api.get(SEARCH).mock(return_value=httpx.Response(200, json=load("chronam_search.json")))
    await chronam.search(
        "clara bow", date_from="1924-10", date_to="1924", state="California", phrase=True, rows=500, page=2
    )
    params = route.calls[0].request.url.params
    assert params["fo"] == "json" and params["at"] == "results,pagination" and params["dl"] == "page"
    assert params["qs"] == "clara bow" and params["ops"] == "PHRASE" and params["searchType"] == "advanced"
    assert params["start_date"] == "1924-10-01" and params["end_date"] == "1924-12-31"
    assert params["location_state"] == "california" and params["c"] == "100" and params["sp"] == "2"


async def test_an_open_date_end_is_filled_in_and_no_dates_sends_none(api) -> None:
    route = api.get(SEARCH).mock(return_value=httpx.Response(200, json=load("chronam_search.json")))
    await chronam.search("x", date_from="1863")
    assert route.calls[0].request.url.params["start_date"] == "1863-01-01"
    assert route.calls[0].request.url.params["end_date"].endswith("-12-31")
    await chronam.search("x")
    assert (
        "start_date" not in route.calls[1].request.url.params
        and "ops" not in route.calls[1].request.url.params
    )


async def test_pages_after_1930_are_not_called_free(api) -> None:
    data = copy.deepcopy(load("chronam_search.json"))
    data["results"][0]["id"] = "http://www.loc.gov/resource/sn92070146/1951-11-20/ed-1/?sp=6"
    api.get(SEARCH).mock(return_value=httpx.Response(200, json=data))
    record = (await chronam.search("x"))["records"][0]
    assert record["rights"]["reuse"] == "unknown"


async def test_restricted_pages_are_marked_restricted(api) -> None:
    data = copy.deepcopy(load("chronam_search.json"))
    data["results"][0]["access_restricted"] = True
    api.get(SEARCH).mock(return_value=httpx.Response(200, json=data))
    assert (await chronam.search("x"))["records"][0]["rights"]["reuse"] == "restricted"


async def test_a_storage_path_that_escapes_the_service_tree_yields_no_file_links(api) -> None:
    data = copy.deepcopy(load("chronam_search.json"))
    result = data["results"][0]
    result["word_coordinates_url"] = (
        "https://tile.loc.gov/text-services/word-coordinates-service?segment=/service/../../etc/passwd.xml"
    )
    result["image_url"] = []
    api.get(SEARCH).mock(return_value=httpx.Response(200, json=data))
    assert "media" not in (await chronam.search("x"))["records"][0]


async def test_results_that_are_not_newspaper_pages_are_dropped(api) -> None:
    data = copy.deepcopy(load("chronam_search.json"))
    data["results"].append({"id": "https://www.loc.gov/item/2009581123/", "title": "A map"})
    api.get(SEARCH).mock(return_value=httpx.Response(200, json=data))
    assert (await chronam.search("x"))["returned"] == 1


async def test_empty_query_and_other_kinds(api) -> None:
    route = api.get(SEARCH)
    with pytest.raises(HeritageError, match="at least one word"):
        await chronam.search("   ")
    out = await chronam.search("x", kind="image")
    assert out["records"] == [] and "newspaper text only" in out["kind_applied"]
    assert route.call_count == 0


async def test_get_page_builds_file_links_from_the_resource_pdf(api) -> None:
    route = api.get(PAGE).mock(return_value=httpx.Response(200, json=resource_page()))
    record = (await chronam.get_page("sn92070146/1924-11-20/ed-1/6"))["record"]
    params = route.calls[0].request.url.params
    assert params["fo"] == "json" and params["sp"] == "6"
    assert record["title"] == "Imperial Valley press (El Centro, Calif.), 1924-11-20, p. 6"
    assert record["holder"] == "University of California, Riverside"
    assert [m["url"] for m in record["media"]] == [f"{BASE}.{e}" for e in ("pdf", "txt", "xml", "jp2")]
    assert record["extra"]["lccn"] == "sn92070146" and record["extra"]["page"] == 6


async def test_a_pdf_link_on_another_host_is_not_trusted(api) -> None:
    data = resource_page()
    data["resource"]["pdf"] = "https://evil.example/storage-services/service/ndnp/x/0001.pdf"
    api.get(PAGE).mock(return_value=httpx.Response(200, json=data))
    assert "media" not in (await chronam.get_page("sn92070146/1924-11-20/ed-1/6"))["record"]


async def test_get_page_rejects_bad_ids_and_reports_missing_pages(api) -> None:
    for bad in ("../../etc/passwd", "sn1/1924-11-20", "sn92070146/1924-11-20/ed-1/x"):
        with pytest.raises(HeritageError, match="looks like"):
            await chronam.get_page(bad)
    api.get(PAGE).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(NotFound):
        await chronam.get_page("sn92070146/1924-11-20/ed-1/6")


async def test_page_text_fetches_the_ocr_file_and_caps_it(api) -> None:
    api.get(PAGE).mock(return_value=httpx.Response(200, json=resource_page()))
    text = api.get(f"{BASE}.txt").mock(return_value=httpx.Response(200, text="Clara Bow " * 20_000))
    out = await chronam.page_text("sn92070146/1924-11-20/ed-1/6")
    assert text.call_count == 1 and out["truncated"] is True and len(out["text"]) == chronam.TEXT_LIMIT
    assert "uncorrected" in out["note"] and out["rights"]["reuse"] == "free"


async def test_requests_are_paced_inside_the_published_limit() -> None:
    assert http_module.MIN_INTERVAL["chronam"] * 20 >= 60


async def test_a_bot_challenge_is_reported_once_and_then_the_source_is_left_alone(api) -> None:
    route = api.get(SEARCH).mock(return_value=challenged())
    with pytest.raises(Blocked, match="bot challenge"):
        await chronam.search("mosby")
    assert route.call_count == 1
    with pytest.raises(Blocked, match="not retrying for another"):
        await chronam.search("mosby")
    assert route.call_count == 1


async def test_a_block_on_one_source_does_not_stop_the_others(api) -> None:
    api.get(SEARCH).mock(return_value=challenged())
    api.get("https://archive.org/advancedsearch.php").mock(
        return_value=httpx.Response(200, json=load("ia_advancedsearch.json"))
    )
    out = await call(mcp, "search", {"query": "mosby", "sources": ["chronam", "internet_archive"]})
    assert "bot challenge" in out["errors"]["chronam"]
    assert len(out["results"]["internet_archive"]["records"]) > 0


async def test_a_429_that_outlasts_the_retries_blocks_the_source(api) -> None:
    route = api.get(SEARCH).mock(return_value=httpx.Response(429, text="slow down"))
    with pytest.raises(Blocked, match="rate limited"):
        await chronam.search("mosby")
    calls = route.call_count
    with pytest.raises(Blocked):
        await chronam.search("mosby")
    assert route.call_count == calls


async def test_the_cooldown_ends(api, monkeypatch: pytest.MonkeyPatch) -> None:
    route = api.get(SEARCH).mock(return_value=challenged())
    with pytest.raises(Blocked):
        await chronam.search("mosby")
    http_module.http._blocked["chronam"] = 0.0
    route.mock(return_value=httpx.Response(200, json=load("chronam_search.json")))
    assert (await chronam.search("mosby"))["returned"] == 1


async def test_the_tools_surface_a_block_as_an_error_the_model_can_read(api) -> None:
    api.get(SEARCH).mock(return_value=challenged())
    with pytest.raises(ToolFailed, match="wait"):
        await call(mcp, "chronam_search", {"query": "mosby"})


def test_doctor_labels_a_challenge_blocked(api) -> None:
    import asyncio

    api.get(SEARCH).mock(return_value=challenged())
    assert asyncio.run(doctor._probe("chronam")).startswith("BLOCKED")
