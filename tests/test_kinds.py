from __future__ import annotations

import httpx
import pytest

from conftest import load
from helpers import ToolFailed, call
from heritage_research_mcp import kinds as K
from heritage_research_mcp.compat import FastMCP
from heritage_research_mcp.errors import HeritageError
from heritage_research_mcp.server import mcp
from heritage_research_mcp.sources import commons, dpla, nara
from heritage_research_mcp.sources import internet_archive as ia
from heritage_research_mcp.sources import smithsonian as si
from heritage_research_mcp.tools import general

IA = "https://archive.org/advancedsearch.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
DPLA = "https://api.dp.la/v2/items"
NARA = "https://catalog.archives.gov/api/v2/records/search"
SI = "https://api.si.edu/openaccess/api/v1.0/search"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (None, None), ("", None), ("  ", None), ("MAP", "map"), ("maps", "map"), ("photographs", "image"),
        ("books", "text"), ("documents", "text"), ("film", "video"), ("sound", "audio"), ("text", "text"),
    ],
)  # fmt: skip
def test_kind_names_and_aliases(given: str | None, expected: str | None) -> None:
    assert K.check(given) == expected


def test_unknown_kind_lists_the_valid_ones() -> None:
    with pytest.raises(HeritageError) as caught:
        K.check("spreadsheet")
    assert "map" in str(caught.value) and "audio" in str(caught.value)


@pytest.mark.parametrize(
    ("headings", "is_map"),
    [
        (["Maps"], True),
        (["Watersheds--Virginia--Fairfax County--Maps"], True),
        (["Cartographic material"], True),
        (["Historical maps"], True),
        (["Mapmakers", "Mapping the future"], False),
        (["Cavalry operations"], False),
        ([], False),
    ],
)
def test_map_headings(headings: list[str], is_map: bool) -> None:
    assert K.has_map_subject(headings) is is_map


async def test_internet_archive_translates_each_kind(api) -> None:
    route = api.get(IA).mock(return_value=httpx.Response(200, json=load("ia_advancedsearch.json")))
    expected = {
        "text": "mediatype:(texts)",
        "image": "mediatype:(image)",
        "audio": "mediatype:(audio)",
        "video": "mediatype:(movies)",
    }
    for kind, clause in expected.items():
        out = await ia.search("mosby", kind=kind)
        assert route.calls[-1].request.url.params["q"] == f"(mosby) AND {clause}"
        assert out["kind_applied"] == clause
    out = await ia.search("virginia", kind="map")
    assert "subject:(maps)" in route.calls[-1].request.url.params["q"]
    assert "david-rumsey-map-collection" in out["kind_applied"]


async def test_an_explicit_mediatype_is_not_duplicated_by_kind(api) -> None:
    route = api.get(IA).mock(return_value=httpx.Response(200, json=load("ia_advancedsearch.json")))
    out = await ia.search("mosby", mediatype="texts", kind="image")
    assert route.calls[0].request.url.params["q"] == "(mosby) AND mediatype:(texts)"
    assert "explicit mediatype" in out["kind_applied"]


async def test_no_kind_means_no_filter_and_no_note(api) -> None:
    route = api.get(IA).mock(return_value=httpx.Response(200, json=load("ia_advancedsearch.json")))
    out = await ia.search("mosby")
    assert route.calls[0].request.url.params["q"] == "(mosby)" and "kind_applied" not in out


async def test_commons_translates_each_kind(api) -> None:
    route = api.get(COMMONS).mock(return_value=httpx.Response(200, json=load("commons_search.json")))
    expected = {
        "text": "filetype:office",
        "image": "filetype:bitmap",
        "map": "intitle:map filetype:bitmap",
        "audio": "filetype:audio",
        "video": "filetype:video",
    }
    for kind, terms in expected.items():
        await commons.search("virginia", kind=kind)
        assert route.calls[-1].request.url.params["gsrsearch"] == f"virginia {terms}"
    await commons.search("virginia", kind="map", filetype="drawing")
    assert route.calls[-1].request.url.params["gsrsearch"] == "virginia intitle:map filetype:drawing"


async def test_dpla_translates_kind_into_type_and_subject(api, set_key) -> None:
    set_key("dpla")
    route = api.get(DPLA).mock(return_value=httpx.Response(200, json=load("dpla_items.json")))
    out = await dpla.search(q="virginia", kind="map")
    params = route.calls[0].request.url.params
    assert params["sourceResource.type"] == "image" and params["sourceResource.subject.name"] == "Maps"
    assert "Maps" in out["kind_applied"]
    await dpla.search(q="virginia", kind="audio")
    assert route.calls[1].request.url.params["sourceResource.type"] == "sound"
    await dpla.search(q="virginia", kind="video")
    assert route.calls[2].request.url.params["sourceResource.type"] == "moving image"


async def test_explicit_dpla_filters_win_over_kind(api, set_key) -> None:
    set_key("dpla")
    route = api.get(DPLA).mock(return_value=httpx.Response(200, json=load("dpla_items.json")))
    await dpla.search(q="virginia", kind="map", type="text", subject="Railroads")
    params = route.calls[0].request.url.params
    assert params["sourceResource.type"] == "text" and params["sourceResource.subject.name"] == "Railroads"


async def test_nara_translates_kind_and_says_it_is_unverified(api, set_key) -> None:
    set_key("nara")
    route = api.get(NARA).mock(return_value=httpx.Response(200, json=load("nara_search.json")))
    out = await nara.search(q="herndon", kind="map")
    assert route.calls[0].request.url.params["typeOfMaterials"] == "Maps and Charts"
    assert "not yet verified" in out["kind_applied"]
    await nara.search(q="herndon", kind="map", type_of_materials="Architectural Drawings")
    assert route.calls[1].request.url.params["typeOfMaterials"] == "Architectural Drawings"


async def test_smithsonian_adds_the_kind_to_the_query(api, set_key) -> None:
    set_key("smithsonian")
    route = api.get(SI).mock(return_value=httpx.Response(200, json=load("si_search.json")))
    await si.search("virginia", kind="map")
    assert route.calls[0].request.url.params["q"] == '(virginia) AND object_type:"Maps"'
    await si.search("virginia", kind="image")
    assert route.calls[1].request.url.params["q"] == '(virginia) AND online_media_type:"Images"'
    assert "Scanned books" in (await si.search("virginia", kind="text"))["kind_applied"]


def test_records_are_labelled_with_their_kind() -> None:
    ia_doc = {"identifier": "x", "mediatype": "texts", "subject": ["Maps", "Virginia"]}
    assert ia._record(ia_doc).kind == "map"
    assert ia._record({"identifier": "y", "mediatype": "movies"}).kind == "video"
    assert ia._record({"identifier": "z", "mediatype": "texts"}).kind == "text"
    assert ia._record({"identifier": "m", "mediatype": "image", "collection": ["maps_usgs"]}).kind == "map"

    book = dpla._record({"id": "1", "sourceResource": {"type": "text", "title": "A book"}})
    atlas = dpla._record(
        {"id": "2", "sourceResource": {"type": "image", "format": ["Cartographic material"]}}
    )
    sound = dpla._record({"id": "3", "sourceResource": {"type": "sound"}})
    assert (book.kind, atlas.kind, sound.kind) == ("text", "map", "audio")  # type: ignore[union-attr]

    assert nara._kind_of({"generalRecordsTypes": ["Maps and Charts"]}, []) == "map"
    assert nara._kind_of({"generalRecordsTypes": ["Photographs and other Graphic Materials"]}, []) == "image"
    assert nara._kind_of({"generalRecordsTypes": ["Textual Records"]}, []) == "text"
    assert nara._kind_of({}, []) is None


async def test_commons_and_smithsonian_labels_from_real_responses(api, set_key) -> None:
    set_key("smithsonian")
    api.get(COMMONS).mock(return_value=httpx.Response(200, json=load("commons_search.json")))
    assert {r["kind"] for r in (await commons.search("mosby"))["records"]} == {"image"}
    api.get(SI).mock(return_value=httpx.Response(200, json=load("si_live_search.json")))
    assert (await si.search("custer"))["records"][0]["kind"] == "text"
    api.get(SI).mock(return_value=httpx.Response(200, json=load("si_live_media.json")))
    assert (await si.search("flag"))["records"][0]["kind"] == "image"


async def test_unified_search_passes_the_kind_to_every_source(api, set_key) -> None:
    set_key("dpla")
    ia_route = api.get(IA).mock(return_value=httpx.Response(200, json=load("ia_advancedsearch.json")))
    commons_route = api.get(COMMONS).mock(return_value=httpx.Response(200, json=load("commons_search.json")))
    dpla_route = api.get(DPLA).mock(return_value=httpx.Response(200, json=load("dpla_items.json")))
    out = await call(mcp, "search", {"query": "virginia", "kind": "maps"})
    assert "subject:(maps)" in ia_route.calls[0].request.url.params["q"]
    assert "intitle:map" in commons_route.calls[0].request.url.params["gsrsearch"]
    assert dpla_route.calls[0].request.url.params["sourceResource.subject.name"] == "Maps"
    assert set(out["results"]) == {"internet_archive", "commons", "dpla"}
    assert all("kind_applied" in block for block in out["results"].values())
    assert any("kind=map" in note for note in out["notes"])


async def test_an_invalid_kind_is_refused_before_any_request(api) -> None:
    route = api.route()
    with pytest.raises(ToolFailed) as caught:
        await call(mcp, "search", {"query": "x", "kind": "spreadsheet"})
    assert "kind must be one of" in str(caught.value)
    assert route.call_count == 0


async def test_downloads_can_be_switched_off_for_hosted_use(monkeypatch: pytest.MonkeyPatch) -> None:
    on = FastMCP("on")
    general.register(on)
    assert "download_media" in {t.name for t in await on.list_tools()}
    monkeypatch.setenv("HERITAGE_MCP_DISABLE_DOWNLOADS", "1")
    off = FastMCP("off")
    general.register(off)
    names = {t.name for t in await off.list_tools()}
    assert "download_media" not in names and "search" in names
