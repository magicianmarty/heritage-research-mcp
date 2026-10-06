from __future__ import annotations

import httpx
import pytest

from conftest import load
from heritage_research_mcp.errors import HeritageError, NotConfigured, NotFound
from heritage_research_mcp.sources import dpla

ITEMS = "https://api.dp.la/v2/items"


async def test_needs_a_key_and_makes_no_request_without_one(api) -> None:
    route = api.get(ITEMS)
    with pytest.raises(NotConfigured) as caught:
        await dpla.search(q="herndon")
    assert "DPLA_API_KEY" in str(caught.value)
    assert route.call_count == 0


async def test_needs_something_to_search_for(set_key) -> None:
    set_key("dpla")
    with pytest.raises(HeritageError):
        await dpla.search()


async def test_search_sends_the_documented_parameters(api, set_key) -> None:
    key = set_key("dpla", "k-123")
    route = api.get(ITEMS).mock(return_value=httpx.Response(200, json=load("dpla_items.json")))
    await dpla.search(
        q="herndon",
        place_state="Virginia",
        date_after="1863",
        date_before="1864",
        type="text",
        data_provider="Library of Virginia",
        page=2,
        page_size=500,
        sort_by="sourceResource.date.begin",
    )
    params = route.calls[0].request.url.params
    assert params["api_key"] == key and params["q"] == "herndon"
    assert params["sourceResource.spatial.state"] == "Virginia"
    assert params["sourceResource.date.after"] == "1863" and params["sourceResource.date.before"] == "1864"
    assert params["sourceResource.type"] == "text" and params["dataProvider"] == "Library of Virginia"
    assert (params["page"], params["page_size"]) == ("2", "100")


async def test_documented_shape_is_normalised(api, set_key) -> None:
    set_key("dpla")
    api.get(ITEMS).mock(return_value=httpx.Response(200, json=load("dpla_items.json")))
    out = await dpla.search(q="herndon")
    assert out["total"] == 2
    letter, photo = out["records"]
    assert letter["title"] == "Letter from a Ranger, March 1863" and letter["date"] == "1863-03-20"
    assert letter["places"] == ["Herndon (Va.)"] and "Mosby's Rangers" in letter["subjects"]
    assert (
        letter["holder"] == "Library of Virginia"
        and letter["landing_url"] == "https://example.org/items/abc123"
    )
    assert letter["rights"]["reuse"] == "free" and letter["rights"]["label"] == "No Copyright - United States"
    assert letter["media"][0]["label"].startswith("thumbnail")
    assert photo["holder"] == "Virginia Museum of History & Culture" and photo["date"] == "ca. 1864"
    assert photo["rights"]["reuse"] == "restricted" and "media" not in photo


async def test_real_responses_are_normalised(api, set_key) -> None:
    set_key("dpla")
    api.get(ITEMS).mock(return_value=httpx.Response(200, json=load("dpla_live.json")))
    out = await dpla.search(q="mosby")
    unspecified, by_licence, _second = out["records"]
    assert unspecified["rights"]["reuse"] == "restricted"
    assert "copyright laws protect" in unspecified["rights"]["statement"]
    assert unspecified["holder"] == "Nashville Public Library"
    assert by_licence["rights"]["reuse"] == "attribution" and by_licence["rights"]["label"] == "CC BY 4.0"
    assert by_licence["media"][0]["label"] == "master file from the holder"
    assert "Unlimited Re-Use" in by_licence["rights"]["note"]


async def test_dpla_categories_fill_in_when_nothing_else_is_stated() -> None:
    def rights(category: str | None, **doc: object) -> dict:
        return dpla._rights({"rightsCategory": category, "sourceResource": {}, **doc}).model_dump()

    assert rights("Unlimited Re-Use")["reuse"] == "free"
    assert rights("Re-use, No Modification")["reuse"] == "no_derivatives"
    assert rights("Permission or Fair Use")["reuse"] == "restricted"
    assert rights("Unspecified Rights Status")["reuse"] == "unknown"
    assert rights(None)["reuse"] == "unknown"
    assert (
        rights("Unlimited Re-Use", rights="http://rightsstatements.org/vocab/InC/1.0/")["reuse"]
        == "restricted"
    )


async def test_get_item(api, set_key) -> None:
    set_key("dpla")
    api.get(f"{ITEMS}/abc123").mock(return_value=httpx.Response(200, json=load("dpla_items.json")))
    assert (await dpla.get_item("abc123"))["record"]["id"] == "abc123"


async def test_unknown_item_is_not_found(api, set_key) -> None:
    set_key("dpla")
    api.get(f"{ITEMS}/zzz").mock(return_value=httpx.Response(200, json={"count": 0, "docs": []}))
    with pytest.raises(NotFound):
        await dpla.get_item("zzz")


async def test_the_key_never_appears_in_results(api, set_key) -> None:
    key = set_key("dpla", "very-secret-key")
    api.get(ITEMS).mock(return_value=httpx.Response(200, json=load("dpla_items.json")))
    assert key not in str(await dpla.search(q="herndon"))
