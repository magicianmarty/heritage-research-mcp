from __future__ import annotations

import httpx
import pytest

from conftest import load
from heritage_research_mcp.errors import HeritageError, NotFound
from heritage_research_mcp.sources import commons

API = "https://commons.wikimedia.org/w/api.php"


def page(**ext: str) -> dict:
    meta = {k: {"value": v} for k, v in ext.items()}
    return {
        "pageid": 1,
        "title": "File:Example_photo.jpg",
        "index": 1,
        "imageinfo": [
            {
                "url": "https://upload.wikimedia.org/x/Example_photo.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Example_photo.jpg",
                "mime": "image/jpeg",
                "size": 1234,
                "width": 800,
                "height": 600,
                "extmetadata": meta,
            }
        ],
    }


async def test_search_normalises_real_results(api) -> None:
    route = api.get(API).mock(return_value=httpx.Response(200, json=load("commons_search.json")))
    out = await commons.search("Mosby's Rangers", limit=2, filetype="bitmap")
    params = route.calls[0].request.url.params
    assert params["generator"] == "search" and params["gsrnamespace"] == "6" and params["maxlag"] == "5"
    assert params["gsrsearch"] == "Mosby's Rangers filetype:bitmap"
    assert out["returned"] == 2 and out["more"] is True
    first = out["records"][0]
    assert first["id"].startswith("File:Col. John Singleton Mosby")
    assert first["rights"]["reuse"] == "free" and first["rights"]["label"] == "Public domain"
    assert first["media"][0]["width"] == 8153 and first["media"][0]["bytes"] == 3680164
    assert "thumbnail_url" not in first["media"][0]
    assert "utm_" not in first["media"][0]["url"]
    assert first["landing_url"].startswith("https://commons.wikimedia.org/wiki/File:")


async def test_share_alike_files_carry_attribution_text(api) -> None:
    data = {
        "query": {
            "pages": [
                page(
                    LicenseShortName="CC BY-SA 4.0",
                    LicenseUrl="https://creativecommons.org/licenses/by-sa/4.0",
                    Artist='<a href="//x">Jane Doe</a>',
                    AttributionRequired="true",
                    ImageDescription="<p>A <b>barn</b></p>",
                    Categories="Barns|Virginia",
                )
            ]
        }
    }
    api.get(API).mock(return_value=httpx.Response(200, json=data))
    record = (await commons.file_info("Example photo.jpg"))["record"]
    assert record["rights"]["reuse"] == "share_alike"
    assert "Jane Doe" in record["rights"]["attribution"]
    assert "CC BY-SA 4.0" in record["rights"]["attribution"]
    assert record["description"] == "A barn"
    assert record["subjects"] == ["Barns", "Virginia"]
    assert record["creators"] == ["Jane Doe"]


async def test_unstated_licence_is_unknown_not_free(api) -> None:
    api.get(API).mock(return_value=httpx.Response(200, json={"query": {"pages": [page(Artist="Someone")]}}))
    record = (await commons.file_info("File:Example_photo.jpg"))["record"]
    assert record["rights"]["reuse"] == "unknown"


async def test_copyrighted_false_means_public_domain(api) -> None:
    api.get(API).mock(
        return_value=httpx.Response(200, json={"query": {"pages": [page(Copyrighted="False")]}})
    )
    assert (await commons.file_info("x"))["record"]["rights"]["reuse"] == "free"


async def test_restrictions_are_surfaced(api) -> None:
    data = {"query": {"pages": [page(LicenseShortName="Public domain", Restrictions="personality")]}}
    api.get(API).mock(return_value=httpx.Response(200, json=data))
    note = (await commons.file_info("x"))["record"]["rights"]["note"]
    assert "personality" in note


async def test_missing_file_is_not_found(api) -> None:
    api.get(API).mock(
        return_value=httpx.Response(200, json={"query": {"pages": [{"title": "File:X", "missing": True}]}})
    )
    with pytest.raises(NotFound):
        await commons.file_info("X")


async def test_file_prefix_is_added_once(api) -> None:
    route = api.get(API).mock(
        return_value=httpx.Response(200, json={"query": {"pages": [page(Copyrighted="False")]}})
    )
    await commons.file_info("Example.jpg")
    await commons.file_info("file:Example.jpg")
    assert [c.request.url.params["titles"] for c in route.calls] == ["File:Example.jpg", "file:Example.jpg"]


async def test_category_members(api) -> None:
    route = api.get(API).mock(return_value=httpx.Response(200, json=load("commons_search.json")))
    out = await commons.category_members("Mosby's Rangers", limit=5, filetype="bitmap")
    params = route.calls[0].request.url.params
    assert params["gcmtitle"] == "Category:Mosby's Rangers" and params["gcmtype"] == "file"
    assert out["returned"] == 2


async def test_bad_filetype_is_refused() -> None:
    with pytest.raises(HeritageError):
        await commons.search("x", filetype="exe")


async def test_commons_housekeeping_categories_are_not_subjects(api) -> None:
    categories = "Old maps of Fairfax County, Virginia|Images uploaded by Fæ|PD-old-100-expired|Template Unknown (author)|CC-PD-Mark|Maps in the Library of Congress"
    api.get(API).mock(
        return_value=httpx.Response(200, json={"query": {"pages": [page(Categories=categories)]}})
    )
    record = (await commons.file_info("x"))["record"]
    assert record["subjects"] == ["Old maps of Fairfax County, Virginia", "Maps in the Library of Congress"]
