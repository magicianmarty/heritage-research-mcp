from __future__ import annotations

import httpx
import pytest

from conftest import load
from heritage_research_mcp.errors import HeritageError, NotConfigured, NotFound, QuotaExceeded
from heritage_research_mcp.sources import nara

V2 = "https://catalog.archives.gov/api/v2"


async def test_needs_a_key(api) -> None:
    route = api.get(f"{V2}/records/search")
    with pytest.raises(NotConfigured) as caught:
        await nara.search(q="herndon")
    assert "NARA_API_KEY" in str(caught.value)
    assert route.call_count == 0


async def test_key_goes_in_the_x_api_key_header_not_the_url(api, set_key) -> None:
    key = set_key("nara", "nara-secret")
    route = api.get(f"{V2}/records/search").mock(
        return_value=httpx.Response(200, json=load("nara_search.json"))
    )
    await nara.search(q="herndon")
    request = route.calls[0].request
    assert request.headers["x-api-key"] == key
    assert key not in str(request.url)


async def test_search_parameters(api, set_key) -> None:
    set_key("nara")
    route = api.get(f"{V2}/records/search").mock(
        return_value=httpx.Response(200, json=load("nara_search.json"))
    )
    await nara.search(
        q="herndon station",
        start_date="1863",
        end_date="1863-03-31",
        available_online=True,
        type_of_materials="Textual Records",
        record_group="109",
        include_extracted_text=True,
        limit=9999,
        page=3,
    )
    params = route.calls[0].request.url.params
    assert (
        params["q"] == "herndon station"
        and params["startDate"] == "1863"
        and params["endDate"] == "1863-03-31"
    )
    assert params["availableOnline"] == "true" and params["includeExtractedText"] == "true"
    assert params["typeOfMaterials"] == "Textual Records" and params["recordGroupNumber"] == "109"
    assert (params["limit"], params["page"]) == ("100", "3")


async def test_records_are_normalised(api, set_key) -> None:
    set_key("nara")
    api.get(f"{V2}/records/search").mock(return_value=httpx.Response(200, json=load("nara_search.json")))
    out = await nara.search(q="herndon")
    assert out["total"] == 2 and "not endorsed or certified" in out["attribution"]
    report, rmap = out["records"]
    assert report["id"] == "12345" and report["date"] == "1863-03-18"
    assert report["landing_url"] == "https://catalog.archives.gov/id/12345"
    assert report["holder"] == "National Archives at Washington, DC"
    assert report["media"][0]["kind"] == "image" and report["media"][0]["bytes"] == 2400000
    assert report["extra"]["has_extracted_text"] is True
    assert report["extra"]["ancestors"][0]["level"] == "recordGroup"
    assert report["rights"]["reuse"] == "unknown" and report["rights"]["statement"] == "Undetermined"
    assert rmap["date"] == "1864-01-01" and rmap["media"][0]["kind"] == "pdf"
    assert rmap["rights"]["reuse"] == "free" and "unrestricted" in rmap["rights"]["label"].lower()


async def test_restricted_records_say_so(api, set_key) -> None:
    set_key("nara")
    data = load("nara_search.json")
    data["body"]["hits"]["hits"][0]["_source"]["record"]["useRestriction"] = {"status": "Restricted - Fully"}
    api.get(f"{V2}/records/search").mock(return_value=httpx.Response(200, json=data))
    assert (await nara.search(q="x"))["records"][0]["rights"]["reuse"] == "restricted"


async def test_unexpected_shapes_give_empty_results_not_crashes(api, set_key) -> None:
    set_key("nara")
    api.get(f"{V2}/records/search").mock(return_value=httpx.Response(200, json={"surprise": True}))
    out = await nara.search(q="x")
    assert out["records"] == [] and out["total"] is None


async def test_version_three_is_opt_in(api, set_key, monkeypatch: pytest.MonkeyPatch) -> None:
    set_key("nara")
    monkeypatch.setenv("NARA_API_VERSION", "v3")
    route = api.get("https://catalog.archives.gov/api/v3/records/search").mock(
        return_value=httpx.Response(200, json=load("nara_search.json"))
    )
    await nara.search(q="x")
    assert route.call_count == 1


async def test_input_validation(set_key) -> None:
    set_key("nara")
    with pytest.raises(HeritageError):
        await nara.search()
    with pytest.raises(HeritageError):
        await nara.search(q="x", start_date="March 1863")


async def test_get_record_by_na_id(api, set_key) -> None:
    set_key("nara")
    route = api.get(f"{V2}/records/search").mock(
        return_value=httpx.Response(200, json=load("nara_search.json"))
    )
    out = await nara.get_record(12345)
    assert out["record"]["id"] == "12345"
    assert route.calls[0].request.url.params["naId_is"] == "12345"


async def test_get_record_not_found(api, set_key) -> None:
    set_key("nara")
    api.get(f"{V2}/records/search").mock(
        return_value=httpx.Response(200, json={"body": {"hits": {"hits": []}}})
    )
    with pytest.raises(NotFound):
        await nara.get_record(1)


async def test_children(api, set_key) -> None:
    set_key("nara")
    route = api.get(f"{V2}/records/parentNaId/777").mock(
        return_value=httpx.Response(200, json=load("nara_search.json"))
    )
    out = await nara.children(777, limit=5)
    assert out["parent"] == 777 and len(out["records"]) == 2
    assert route.calls[0].request.url.params["limit"] == "5"


async def test_extracted_text_is_passed_through_but_shortened(api, set_key) -> None:
    set_key("nara")
    long_text = "word " * 5000
    api.get(f"{V2}/extractedText/12345").mock(
        return_value=httpx.Response(200, json={"pages": [{"text": long_text}]})
    )
    out = await nara.extracted_text(12345, object_id=777)
    text = out["data"]["pages"][0]["text"]
    assert len(text) < len(long_text) and "more characters" in text
    assert "not endorsed" in out["attribution"]


async def test_the_monthly_quota_is_enforced(api, set_key, monkeypatch: pytest.MonkeyPatch) -> None:
    set_key("nara")
    monkeypatch.setenv("NARA_MONTHLY_LIMIT", "1")
    api.get(f"{V2}/records/search").mock(return_value=httpx.Response(200, json=load("nara_search.json")))
    await nara.search(q="x")
    with pytest.raises(QuotaExceeded):
        await nara.search(q="x")
