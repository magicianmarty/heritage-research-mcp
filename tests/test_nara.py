"""NARA against trimmed captures of the live service (tests/fixtures/nara_live_*.json, taken 2026-10-07)."""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest

from conftest import load
from heritage_research_mcp.errors import (
    HeritageError,
    NotConfigured,
    NotFound,
    QuotaExceeded,
    SourceHTTPError,
)
from heritage_research_mcp.sources import nara
from heritage_research_mcp.tools.general import download_from_record, get_record_dict

V2 = "https://catalog.archives.gov/api/v2"
SEARCH = f"{V2}/records/search"

MAP, PHOTO_CA, PHOTO_GEO, FOLD3 = "221160784", "524925", "290020335", "196288881"
POSSIBLY, UNDETERMINED, FULLY, ZIP = "41681539", "348264011", "24326142", "176210497"


@pytest.fixture
def served(api, set_key):
    set_key("nara")
    return api.get(SEARCH).mock(return_value=httpx.Response(200, json=load("nara_live_search.json")))


def only(na_id: str) -> dict[str, Any]:
    """The search response with just one record, as NARA answers a naId_is lookup."""
    data = load("nara_live_search.json")
    hits = data["body"]["hits"]["hits"]
    data["body"]["hits"]["hits"] = [h for h in hits if h["_source"]["record"]["naId"] == int(na_id)]
    return data


def without_ocr(data: dict[str, Any]) -> dict[str, Any]:
    for hit in data["body"]["hits"]["hits"]:
        for obj in hit["_source"]["record"]["digitalObjects"]:
            obj.pop("extractedText", None)
    return data


async def by_id(**kwargs: Any) -> dict[str, dict[str, Any]]:
    out = await nara.search(q="x", **kwargs)
    return {r["id"]: r for r in out["records"]}


async def test_needs_a_key(api) -> None:
    route = api.get(SEARCH)
    with pytest.raises(NotConfigured) as caught:
        await nara.search(q="herndon")
    assert "NARA_API_KEY" in str(caught.value)
    assert route.call_count == 0


async def test_key_goes_in_the_x_api_key_header_not_the_url(api, set_key) -> None:
    key = set_key("nara", "nara-secret")
    route = api.get(SEARCH).mock(return_value=httpx.Response(200, json=load("nara_live_search.json")))
    await nara.search(q="herndon")
    request = route.calls[0].request
    assert request.headers["x-api-key"] == key
    assert key not in str(request.url)


async def test_search_parameters(served) -> None:
    await nara.search(
        q="herndon station",
        start_date="1863",
        end_date="1863-03",
        available_online=True,
        type_of_materials="Textual Records",
        level="file unit",
        record_group="RG 109",
        include_extracted_text=True,
        limit=9999,
        page=3,
    )
    params = served.calls[0].request.url.params
    assert params["q"] == "herndon station"
    assert (params["startDate"], params["endDate"]) == ("1863-01-01", "1863-03-31")
    assert params["availableOnline"] == "true" and params["includeExtractedText"] == "true"
    assert params["typeOfMaterials"] == "Textual Records" and params["levelOfDescription"] == "fileUnit"
    assert params["recordGroupNumber"] == "109"
    assert (params["limit"], params["page"]) == ("100", "3")


async def test_filters_with_their_own_names(served) -> None:
    await nara.search(title="pontoon", geographic="Virginia", creators="Brady", ancestor_na_id=524418)
    params = served.calls[0].request.url.params
    assert params["title"] == "pontoon" and params["geographicReference"] == "Virginia"
    assert params["creators"] == "Brady" and params["ancestorNaId"] == "524418"


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (None, None, {}),
        ("1863", "1865", {"startDate": "1863-01-01", "endDate": "1865-12-31"}),
        ("1863-03", "1863-03-18", {"startDate": "1863-03-01", "endDate": "1863-03-18"}),
        ("1864-02", "1864-02", {"startDate": "1864-02-01", "endDate": "1864-02-29"}),
        ("1863", None, {"startDate": "1863-01-01", "endDate": "2100-12-31"}),
        (None, "1865-04-09", {"startDate": "1000-01-01", "endDate": "1865-04-09"}),
    ],
)
def test_date_bounds_always_send_both_ends_as_full_dates(start, end, expected) -> None:
    assert nara.date_bounds(start, end) == expected


@pytest.mark.parametrize("bad", ["March 1863", "1863-13", "1863-02-30", "63"])
def test_bad_dates_are_refused_before_a_request_is_spent(bad) -> None:
    with pytest.raises(HeritageError):
        nara.date_bounds(bad, None)


async def test_the_map_keeps_a_year_only_date_and_drops_the_size_placeholder(served) -> None:
    sheet = (await by_id())[MAP]
    assert sheet["date"] == "1864"
    assert sheet["kind"] == "map" and sheet["holder"] == "National Archives at College Park - Cartographic"
    assert sheet["extra"]["record_group"] == "RG 77: Records of the Office of the Chief of Engineers"
    assert sheet["extra"]["series"] == "Civil Works Map File"
    assert "creators" not in sheet
    first = sheet["media"][0]
    assert first["kind"] == "image" and first["mime"] == "image/jpeg" and first["id"]
    assert "bytes" not in first


async def test_photographer_role_and_ca_dates_survive(served) -> None:
    photo = (await by_id())[PHOTO_CA]
    assert photo["creators"] == [
        "Brady National Photographic Art Gallery (Washington, D.C.) (1858 - ?) [Photographer]"
    ]
    assert photo["date"] == "ca. 1860 to ca. 1865"
    assert photo["subjects"] == ["American Civil War, 1861-1865"]
    assert photo["kind"] == "image"


async def test_places_are_split_from_subjects(served) -> None:
    photo = (await by_id())[PHOTO_GEO]
    assert photo["places"] == ["Kansas"] and photo["subjects"] == ["Navajo Nation"]
    assert photo["date"] == "1940-06-11"


async def test_free_records_say_so_without_the_boilerplate_note(served) -> None:
    rights = (await by_id())[MAP]["rights"]
    assert rights["reuse"] == "free" and "unrestricted" in rights["label"].lower()
    assert "note" not in rights


async def test_possibly_restricted_is_unknown_and_keeps_nara_s_own_words(served) -> None:
    rights = (await by_id())[POSSIBLY]["rights"]
    assert rights["reuse"] == "unknown" and rights["basis"] == "holder"
    assert "possibly restricted" in rights["label"].lower() and "Copyright" in rights["label"]
    assert "copyright" in rights["statement"].lower() and rights["statement"] != "Restricted - Possibly"
    assert "public domain" in rights["note"]


async def test_undetermined_is_unknown(served) -> None:
    rights = (await by_id())[UNDETERMINED]["rights"]
    assert rights["reuse"] == "unknown" and "undetermined" in rights["label"].lower()


async def test_fully_restricted_is_restricted_and_access_is_reported(served) -> None:
    record = (await by_id())[FULLY]
    assert record["rights"]["reuse"] == "restricted"
    assert "Donor Restrictions" in record["rights"]["label"]
    assert record["extra"]["access"].startswith("Restricted")


async def test_an_unknown_status_is_never_free(served, monkeypatch: pytest.MonkeyPatch) -> None:
    data = load("nara_live_search.json")
    data["body"]["hits"]["hits"][0]["_source"]["record"]["useRestriction"] = {"status": "Pending review"}
    served.mock(return_value=httpx.Response(200, json=data))
    assert (await nara.search(q="x"))["records"][0]["rights"]["reuse"] == "unknown"


async def test_file_types_come_from_the_extension_not_substrings(served) -> None:
    records = await by_id()
    assert (
        records[ZIP]["media"][0]["kind"] == "archive"
        and records[ZIP]["media"][0]["mime"] == "application/zip"
    )
    pdf = records[POSSIBLY]["media"][0]
    assert pdf["kind"] == "pdf" and pdf["bytes"] == 56823508
    assert [m["kind"] for m in records[PHOTO_GEO]["media"]] == ["image", "image"]


async def test_without_ocr_in_the_response_there_is_no_flag_and_no_excerpt(served) -> None:
    served.mock(return_value=httpx.Response(200, json=without_ocr(load("nara_live_search.json"))))
    plain = (await by_id())[FOLD3]
    assert "has_extracted_text" not in plain["extra"]
    assert "text_excerpt" not in plain["media"][0]


async def test_ocr_text_in_the_response_is_an_excerpt_only_when_asked_for(served) -> None:
    quiet = (await by_id())[FOLD3]
    assert quiet["extra"]["has_extracted_text"] is True and "text_excerpt" not in quiet["media"][0]
    asked = (await by_id(include_extracted_text=True))[FOLD3]
    assert asked["media"][0]["text_excerpt"].startswith("Va.")
    assert asked["media"][0]["label"].startswith("Names found")


async def test_first_page_carries_facets_and_later_pages_do_not(served) -> None:
    first = await nara.search(q="x")
    assert first["total"] == 16088 and "not endorsed or certified" in first["attribution"]
    assert first["facets"]["types"]["Maps and Charts"] == 48
    assert (
        first["facets"]["record_groups"][-1] == "109: War Department Collection of Confederate Records (1082)"
    )
    assert "facets" not in await nara.search(q="x", page=2)


async def test_kind_maps_to_nara_s_vocabulary(served) -> None:
    out = await nara.search(q="fairfax", kind="map")
    assert served.calls[0].request.url.params["typeOfMaterials"] == "Maps and Charts"
    assert out["kind_applied"] == "typeOfMaterials='Maps and Charts'"
    out = await nara.search(q="fairfax", kind="map", type_of_materials="Textual Records")
    assert served.calls[1].request.url.params["typeOfMaterials"] == "Textual Records"
    assert "explicit" in out["kind_applied"]


@pytest.mark.parametrize(
    ("given", "sent"),
    [
        ("maps", "Maps and Charts"),
        ("Photo", "Photographs and other Graphic Materials"),
        ("textual records", "Textual Records"),
        ("moving-images", "Moving Images"),
    ],
)
def test_material_names_are_forgiving(given, sent) -> None:
    assert nara.check_materials(given) == sent


def test_an_invalid_material_is_refused_with_the_valid_list() -> None:
    with pytest.raises(HeritageError) as caught:
        nara.check_materials("Pictures")
    assert "Maps and Charts" in str(caught.value)


async def test_input_validation(set_key) -> None:
    set_key("nara")
    with pytest.raises(HeritageError):
        await nara.search()
    with pytest.raises(HeritageError):
        await nara.search(q="x", start_date="March 1863")
    with pytest.raises(HeritageError):
        await nara.search(q="x", level="folder")


async def test_unexpected_shapes_give_empty_results_not_crashes(api, set_key) -> None:
    set_key("nara")
    api.get(SEARCH).mock(return_value=httpx.Response(200, json={"surprise": True}))
    out = await nara.search(q="x")
    assert out["records"] == [] and out["total"] is None and out["facets"] == {}


async def test_version_three_is_opt_in(api, set_key, monkeypatch: pytest.MonkeyPatch) -> None:
    set_key("nara")
    monkeypatch.setenv("NARA_API_VERSION", "v3")
    route = api.get("https://catalog.archives.gov/api/v3/records/search").mock(
        return_value=httpx.Response(200, json=load("nara_live_search.json"))
    )
    await nara.search(q="x")
    assert route.call_count == 1


def website_instead_of_data() -> httpx.Response:
    return httpx.Response(
        200, headers={"content-type": "text/html"}, text="<!doctype html><html><head></head></html>"
    )


async def test_parentheses_that_trip_the_firewall_are_named_as_the_likely_cause(api, set_key) -> None:
    set_key("nara")
    api.get(SEARCH).mock(return_value=website_instead_of_data())
    with pytest.raises(HeritageError) as caught:
        await nara.search(q="mosby AND (rangers)")
    message = str(caught.value)
    assert "website instead of data" in message and "parentheses" in message and "key is fine" in message
    assert "HTTP" not in message and "not JSON" not in message and "monthly quota" in message


async def test_without_parentheses_a_website_reply_points_at_the_key(api, set_key) -> None:
    set_key("nara")
    api.get(SEARCH).mock(return_value=website_instead_of_data())
    with pytest.raises(HeritageError) as caught:
        await nara.search(q="mosby")
    assert "NARA_API_KEY" in str(caught.value) and "parentheses" not in str(caught.value)


@pytest.mark.parametrize("size", [1234, 123456, 5242880])
async def test_placeholder_file_sizes_are_left_out(served, size) -> None:
    data = load("nara_live_search.json")
    record = data["body"]["hits"]["hits"][0]["_source"]["record"]
    record["digitalObjects"][0]["objectFileSize"] = size
    served.mock(return_value=httpx.Response(200, json=data))
    assert "bytes" not in (await nara.search(q="x"))["records"][0]["media"][0]


async def test_a_date_filter_comes_with_a_warning_that_nara_matches_loosely(served) -> None:
    assert "date_note" not in await nara.search(q="x")
    out = await nara.search(q="x", start_date="1861")
    assert "parent series" in out["date_note"] and "own `date`" in out["date_note"]


async def test_an_empty_result_names_the_filters_that_may_have_excluded_everything(api, set_key) -> None:
    set_key("nara")
    empty = {"body": {"hits": {"total": {"value": 0}, "hits": []}}}
    api.get(SEARCH).mock(return_value=httpx.Response(200, json=empty))
    out = await nara.search(q="Fairfax County", geographic="Virginia", start_date="1861")
    assert out["records"] == [] and "(geographic, dates)" in out["hint"] and "dates" in out["hint"]
    assert "q instead" in out["hint"]
    assert "hint" not in await nara.search(q="Fairfax County")


async def test_validation_errors_show_what_nara_will_accept(api, set_key) -> None:
    set_key("nara")
    api.get(SEARCH).mock(return_value=httpx.Response(422, json=load("nara_live_422.json")))
    with pytest.raises(SourceHTTPError) as caught:
        await nara.search(q="x", type_of_materials="Textual Records")
    assert "HTTP 422" in str(caught.value) and "Maps and Charts" in str(caught.value)


async def test_a_single_record_has_citation_creator_links_and_ancestry(api, set_key) -> None:
    set_key("nara")
    route = api.get(SEARCH).mock(return_value=httpx.Response(200, json=only(PHOTO_CA)))
    out = (await nara.get_record(int(PHOTO_CA)))["record"]
    assert route.calls[0].request.url.params["naId_is"] == PHOTO_CA
    assert out["id"] == PHOTO_CA
    extra = out["extra"]
    assert "RG 111" in extra["citation"] and f"NAID {PHOTO_CA}" in extra["citation"]
    assert "local ID 111-B-508" in extra["citation"] and "ca. 1860" in extra["citation"]
    assert extra["created_by"] == [
        "War Department. Office of the Chief Signal Officer. (08/01/1866 - 09/18/1947)"
    ]
    assert extra["related_links"][0]["description"] == "Teaching with Documents"
    assert [a["level"] for a in extra["ancestors"]][0] == "recordGroup"


async def test_fold3_files_name_their_microfilm_publication(api, set_key) -> None:
    set_key("nara")
    api.get(SEARCH).mock(return_value=httpx.Response(200, json=only(FOLD3)))
    out = (await nara.get_record(int(FOLD3)))["record"]
    assert out["extra"]["microfilm"] == [
        "M804 - Revolutionary War Pension and Bounty-Land Warrant Application Files"
    ]
    assert out["extra"]["related_links"][0]["description"] == "Fold3"


def many_pages(count: int) -> dict[str, Any]:
    data = load("nara_live_search.json")
    hit = next(h for h in data["body"]["hits"]["hits"] if h["_source"]["record"]["naId"] == int(FOLD3))
    record = hit["_source"]["record"]
    template = record["digitalObjects"][0]
    record["digitalObjects"] = [
        template
        | {
            "objectId": str(1000 + i),
            "objectFilename": f"page{i:03d}.jpg",
            "objectUrl": f"https://catalog.archives.gov/medialz/p{i:03d}.jpg",
        }
        for i in range(count)
    ]
    data["body"]["hits"]["hits"] = [copy.deepcopy(hit)]
    return data


async def test_long_files_are_capped_in_the_record_but_not_for_downloads(api, set_key) -> None:
    set_key("nara")
    api.get(SEARCH).mock(return_value=httpx.Response(200, json=many_pages(40)))
    shown = (await nara.get_record(int(FOLD3)))["record"]
    assert len(shown["media"]) == nara.MEDIA_LIMIT and shown["extra"]["media_total"] == 40
    assert "nara_extracted_text" in shown["extra"]["media_note"]
    whole = (await nara.get_record(int(FOLD3), media_limit=None))["record"]
    assert len(whole["media"]) == 40 and "media_total" not in whole["extra"]
    assert len((await get_record_dict("nara", FOLD3))["media"]) == nara.MEDIA_LIMIT
    assert len((await get_record_dict("nara", FOLD3, all_media=True))["media"]) == 40


async def test_a_download_can_reach_a_file_past_the_displayed_cap(
    api, set_key, monkeypatch: pytest.MonkeyPatch
) -> None:
    from heritage_research_mcp import download

    async def public(_host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(download, "resolve", public)
    set_key("nara")
    api.get(SEARCH).mock(return_value=httpx.Response(200, json=many_pages(40)))
    fetched = api.get("https://catalog.archives.gov/medialz/p035.jpg").mock(
        return_value=httpx.Response(200, content=b"jpegbytes", headers={"content-type": "image/jpeg"})
    )
    result = await download_from_record("nara", FOLD3, media_index=35)
    assert fetched.call_count == 1 and result["bytes"] == 9 and result["source"] == "nara"


async def test_get_record_not_found(api, set_key) -> None:
    set_key("nara")
    api.get(SEARCH).mock(return_value=httpx.Response(200, json={"body": {"hits": {"hits": []}}}))
    with pytest.raises(NotFound) as caught:
        await nara.get_record(1)
    assert "authority" in str(caught.value)


async def test_children(api, set_key) -> None:
    set_key("nara")
    route = api.get(f"{V2}/records/parentNaId/524418").mock(
        return_value=httpx.Response(200, json=load("nara_live_children.json"))
    )
    out = await nara.children(524418, limit=5)
    assert out["parent"] == 524418 and len(out["records"]) == 3 and out["total"] == 6074
    assert route.calls[0].request.url.params["limit"] == "5"
    assert all(r["date"] == "ca. 1860 to ca. 1865" and r["kind"] == "image" for r in out["records"])


async def test_machine_ocr_is_returned_per_file(api, set_key) -> None:
    set_key("nara")
    api.get(f"{V2}/extractedText/524970").mock(
        return_value=httpx.Response(200, json=load("nara_live_extracted_text.json"))
    )
    out = await nara.extracted_text(524970)
    assert out["total_objects"] == 2 and "not endorsed" in out["attribution"]
    empty, with_text = out["objects"]
    assert empty == {"object_id": "15039429"}
    assert with_text["ocr"] == "15,\nmodiul" and with_text["ocr_chars"] == 10


async def test_partner_transcriptions_are_returned_and_labelled(api, set_key) -> None:
    set_key("nara")
    route = api.get(f"{V2}/extractedText/196288881").mock(
        return_value=httpx.Response(200, json=load("nara_live_transcriptions.json"))
    )
    out = await nara.extracted_text(196288881, object_id=196288882)
    assert route.calls[0].request.url.params["objectId"] == "196288882"
    first = out["objects"][0]
    assert "ocr" not in first
    (transcription,) = first["transcriptions"]
    assert transcription["text"].startswith("Va\nMosby, Hezekiah")
    assert transcription["by"] == "FamilySearch" and transcription["machine_generated"] is True
    assert transcription["date"] == "2024-11-21"
    assert "NARA_API_KEY" not in str(out) and "userId" not in str(out)


async def test_long_text_is_shortened_to_max_chars(api, set_key) -> None:
    set_key("nara")
    long_text = ("word " * 5000).strip()
    api.get(f"{V2}/extractedText/12345").mock(
        return_value=httpx.Response(
            200,
            json={
                "naId": "12345",
                "total": 1,
                "digitalObjects": [{"objectId": "1", "extractedText": long_text}],
            },
        )
    )
    out = await nara.extracted_text(12345, max_chars=1000)
    text = out["objects"][0]["ocr"]
    assert len(text) < 1100 and "more characters" in text and out["objects"][0]["ocr_chars"] == len(long_text)


async def test_the_monthly_quota_is_enforced(api, set_key, monkeypatch: pytest.MonkeyPatch) -> None:
    set_key("nara")
    monkeypatch.setenv("NARA_MONTHLY_LIMIT", "1")
    api.get(SEARCH).mock(return_value=httpx.Response(200, json=load("nara_live_search.json")))
    await nara.search(q="x")
    with pytest.raises(QuotaExceeded):
        await nara.search(q="x")
