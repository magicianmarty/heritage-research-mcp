from __future__ import annotations

import httpx
import pytest

from conftest import load
from heritage_research_mcp.errors import HeritageError, NotConfigured, NotFound
from heritage_research_mcp.sources import smithsonian as si

BASE = "https://api.si.edu/openaccess/api/v1.0"


async def test_needs_a_key(api) -> None:
    route = api.get(f"{BASE}/search")
    with pytest.raises(NotConfigured) as caught:
        await si.search("saber")
    assert "https://api.data.gov/signup/" in str(caught.value)
    assert route.call_count == 0


async def test_search_parameters(api, set_key) -> None:
    key = set_key("smithsonian", "si-key")
    route = api.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json=load("si_search.json")))
    await si.search(
        "cavalry AND saber", rows=9999, start=20, sort="newest", type="edanmdm", row_group="objects"
    )
    params = route.calls[0].request.url.params
    assert params["api_key"] == key and params["q"] == "cavalry AND saber"
    assert (params["rows"], params["start"], params["sort"]) == ("100", "20", "newest")
    assert (params["type"], params["row_group"]) == ("edanmdm", "objects")


async def test_records_are_normalised(api, set_key) -> None:
    set_key("smithsonian")
    api.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json=load("si_search.json")))
    out = await si.search("saber")
    assert out["total"] == 2
    saber, portrait = out["records"]
    assert saber["title"] == "Cavalry Saber" and saber["creators"] == ["Ames Manufacturing Company"]
    assert saber["date"] == "1860" and saber["type"] == "Sabers"
    assert saber["subjects"].count("Cavalry") == 1 and "Weapons" in saber["subjects"]
    assert saber["places"] == ["Chicopee, Massachusetts"]
    assert saber["landing_url"] == "https://americanhistory.si.edu/collections/nmah_1"
    assert saber["media"][0]["kind"] == "image" and saber["media"][0]["license"] == "CC0"
    assert saber["rights"]["reuse"] == "free" and saber["rights"]["label"] == "CC0 (media and metadata)"
    assert portrait["rights"]["reuse"] == "restricted"
    assert portrait["landing_url"] == "https://collections.si.edu/search/detail/edanmdm:saam_2"


async def test_cc0_metadata_alone_is_free_only_for_the_metadata() -> None:
    rights = si._rights({"metadata_usage": {"access": "CC0"}}, [])
    assert rights.reuse == "free" and "metadata only" in (rights.label or "")
    assert si._rights({}, []).reuse == "unknown"


async def test_category_search_uses_its_own_path(api, set_key) -> None:
    set_key("smithsonian")
    route = api.get(f"{BASE}/category/history_culture/search").mock(
        return_value=httpx.Response(200, json=load("si_search.json"))
    )
    await si.search("saber", category="history_culture", type="edanmdm")
    assert "type" not in route.calls[0].request.url.params


async def test_input_validation(set_key) -> None:
    set_key("smithsonian")
    with pytest.raises(HeritageError):
        await si.search("x", category="weapons")
    with pytest.raises(HeritageError):
        await si.search("x", sort="newest; drop")
    with pytest.raises(HeritageError):
        await si.terms("topic/../../admin")


async def test_get_content(api, set_key) -> None:
    set_key("smithsonian")
    row = load("si_search.json")["response"]["rows"][0]
    api.get(f"{BASE}/content/edanmdm-nmah_1").mock(
        return_value=httpx.Response(200, json={"status": 200, "response": row})
    )
    assert (await si.get_content("edanmdm-nmah_1"))["record"]["id"] == "edanmdm-nmah_1"


async def test_get_content_missing(api, set_key) -> None:
    set_key("smithsonian")
    api.get(f"{BASE}/content/gone").mock(
        return_value=httpx.Response(200, json={"status": 200, "response": {}})
    )
    with pytest.raises(NotFound):
        await si.get_content("gone")


async def test_terms_and_stats(api, set_key) -> None:
    set_key("smithsonian")
    terms = api.get(f"{BASE}/terms/topic").mock(return_value=httpx.Response(200, json=load("si_terms.json")))
    api.get(f"{BASE}/stats").mock(return_value=httpx.Response(200, json={"response": {"total_objects": 5}}))
    out = await si.terms("topic", starts_with="Cav", limit=2)
    assert out["terms"] == ["Cavalry", "Cavalry horses"] and out["count"] == 3
    assert terms.calls[0].request.url.params["starts_with"] == "Cav"
    assert (await si.stats())["stats"] == {"total_objects": 5}


async def test_the_key_never_appears_in_results(api, set_key) -> None:
    key = set_key("smithsonian", "very-secret-key")
    api.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json=load("si_search.json")))
    assert key not in str(await si.search("saber"))


async def test_real_library_record_separates_authors_from_subjects(api, set_key) -> None:
    set_key("smithsonian")
    api.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json=load("si_live_search.json")))
    out = await si.search("custer")
    book = out["records"][0]
    assert book["title"].startswith("Clashes of cavalry")
    assert book["creators"] == ["Hatch, Thom 1946-"]
    assert any(s.startswith("Custer, George A.") for s in book["subjects"])
    assert book["date"] == "2001" and book["type"] == "Biography"
    assert book["holder"] == "Smithsonian Libraries" and book["landing_url"].startswith(
        "https://siris-libraries"
    )
    assert book["rights"]["reuse"] == "free" and book["rights"]["label"] == "CC0 (metadata only)"
    assert "media" not in book


async def test_real_museum_object_lists_downloadable_files_best_first(api, set_key) -> None:
    set_key("smithsonian")
    row = load("si_live_media.json")["response"]["rows"][0]
    api.get(f"{BASE}/content/{row['id']}").mock(
        return_value=httpx.Response(200, json={"status": 200, "response": row})
    )
    flag = (await si.get_content(row["id"]))["record"]
    labels = [m["label"] for m in flag["media"]]
    assert "High-resolution JPEG" in labels[0] and "High-resolution TIFF" in labels[-1]
    assert flag["media"][0]["width"] == 3000 and flag["media"][0]["height"] == 2047
    assert flag["media"][0]["url"].endswith(".jpg") and flag["media"][0]["license"] == "CC0"
    assert "Thumbnail" not in " ".join(labels)
    assert "Currently not on view" not in flag["description"]
    assert flag["description"].startswith("Wool bunting")
    assert flag["landing_url"].startswith("https://n2t.net/ark:/65665/")
    assert flag["rights"]["label"] == "CC0 (media and metadata)"


async def test_search_lists_only_the_first_two_files(api, set_key) -> None:
    set_key("smithsonian")
    api.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json=load("si_live_media.json")))
    flag = (await si.search("flag"))["records"][0]
    assert len(flag["media"]) == 2 and "High-resolution JPEG" in flag["media"][0]["label"]
    assert all("thumbnail_url" not in m for m in flag["media"])


def row(**freetext: object) -> dict:
    return {"id": "r1", "title": "T", "content": {"descriptiveNonRepeating": {}, "freetext": freetext}}


def test_names_are_split_by_their_role() -> None:
    record = si._record(
        row(
            name=[
                {"label": "Maker", "content": "Ames Manufacturing"},
                {"label": "Sitter", "content": "A General"},
                {"label": "Subject of", "content": "A Battle"},
                {"label": "Artist", "content": "Brady Studio"},
                {"label": "associated person", "content": "A Donor"},
            ]
        )
    )
    assert record is not None
    assert record.creators == ["Ames Manufacturing", "Brady Studio"]
    assert {"A General", "A Battle", "A Donor"} <= set(record.subjects)


def test_dates_prefer_a_real_year_over_a_period_label() -> None:
    labels = [{"label": "Date", "content": "Civil War era"}, {"label": "date made", "content": "ca. 1863"}]
    record = si._record(row(date=labels))
    assert record is not None and record.date == "ca. 1863"
    assert si._record(row(date=[{"label": "Date", "content": "undated"}])).date == "undated"  # type: ignore[union-attr]


def test_gallery_location_is_not_a_description() -> None:
    notes = [
        {"label": "Location", "content": "Currently not on view"},
        {"label": "Summary", "content": "A saber."},
    ]
    record = si._record(row(notes=notes))
    assert record is not None and record.description == "A saber."


def test_non_image_media_without_resources_falls_back_to_the_delivery_url() -> None:
    descriptive = {
        "online_media": {
            "media": [{"type": "Audio", "content": "https://ids.si.edu/a.mp3", "usage": {"access": "CC0"}}]
        }
    }
    media = si._media(descriptive)
    assert [(m.kind, m.url) for m in media] == [("audio", "https://ids.si.edu/a.mp3")]
