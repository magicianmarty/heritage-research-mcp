from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from heritage_research_mcp import config, download
from heritage_research_mcp.errors import DownloadTooLarge, SourceHTTPError, UnsafeURL

PUBLIC = "93.184.216.34"
URL = "https://files.example.org/a/File%20Name,1.jpg"
BODY = b"\xff\xd8 pretend this is a jpeg " * 20


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(host: str) -> list[str]:
        return ["10.0.0.5"] if host.startswith("internal") else [PUBLIC]

    monkeypatch.setattr(download, "resolve", resolve)


@pytest.mark.parametrize(
    "url",
    [
        "http://files.example.org/x.jpg",
        "ftp://files.example.org/x.jpg",
        "file:///etc/passwd",
        "https://user:pass@files.example.org/x.jpg",
        "https://files.example.org:8443/x.jpg",
        "https://127.0.0.1/x.jpg",
        "https://192.168.1.10/x.jpg",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/x.jpg",
        "https://[::ffff:127.0.0.1]/x.jpg",
        "https://internal.example.org/x.jpg",
        "https:///nohost",
    ],
)
async def test_unsafe_urls_are_refused_before_any_request(api, url: str) -> None:
    route = api.route()
    with pytest.raises(UnsafeURL):
        await download.download_file(source="commons", record_id="r", url=url)
    assert route.call_count == 0


async def test_download_writes_the_file_and_a_provenance_sidecar(api) -> None:
    api.get(URL).mock(return_value=httpx.Response(200, content=BODY, headers={"content-type": "image/jpeg"}))
    rights = {"reuse": "free", "label": "Public domain"}
    out = await download.download_file(
        source="commons",
        record_id="File:Some Photo.jpg",
        url=URL,
        title="Some Photo",
        landing_url="https://commons.example.org/wiki/File:Some_Photo.jpg",
        rights=rights,
    )
    path = Path(out["path"])
    assert path.read_bytes() == BODY and out["cached"] is False
    assert config.cache_dir() in path.parents
    sidecar = json.loads(path.with_name(path.name + ".provenance.json").read_text())
    assert sidecar["sha256"] == hashlib.sha256(BODY).hexdigest() and sidecar["bytes"] == len(BODY)
    assert sidecar["url"] == URL and sidecar["rights"] == rights and sidecar["mime"] == "image/jpeg"
    assert sidecar["title"] == "Some Photo" and sidecar["tool"].startswith("heritage-research-mcp ")
    assert sidecar["retrieved_at"].endswith("+00:00")


async def test_second_call_uses_the_cache_unless_told_to_overwrite(api) -> None:
    route = api.get(URL).mock(return_value=httpx.Response(200, content=BODY))
    await download.download_file(source="commons", record_id="r", url=URL)
    again = await download.download_file(source="commons", record_id="r", url=URL)
    assert again["cached"] is True and route.call_count == 1
    fresh = await download.download_file(source="commons", record_id="r", url=URL, overwrite=True)
    assert fresh["cached"] is False and route.call_count == 2


async def test_hostile_record_ids_cannot_escape_the_cache(api) -> None:
    api.get(URL).mock(return_value=httpx.Response(200, content=BODY))
    out = await download.download_file(source="../../etc", record_id="../../../../root/.ssh", url=URL)
    assert config.cache_dir() in Path(out["path"]).resolve().parents


async def test_redirects_to_public_hosts_are_followed(api) -> None:
    api.get("https://files.example.org/start").mock(
        return_value=httpx.Response(302, headers={"location": "https://cdn.example.org/real.jpg"})
    )
    api.get("https://cdn.example.org/real.jpg").mock(return_value=httpx.Response(200, content=BODY))
    out = await download.download_file(
        source="internet_archive", record_id="r", url="https://files.example.org/start"
    )
    assert out["final_url"] == "https://cdn.example.org/real.jpg"
    assert Path(out["path"]).name == "start"


@pytest.mark.parametrize(
    "target", ["https://internal.example.org/secret", "http://files.example.org/plain", "https://127.0.0.1/x"]
)
async def test_a_redirect_into_a_private_or_insecure_place_is_refused(api, target: str) -> None:
    api.get("https://files.example.org/start").mock(
        return_value=httpx.Response(302, headers={"location": target})
    )
    with pytest.raises(UnsafeURL):
        await download.download_file(source="commons", record_id="r", url="https://files.example.org/start")


async def test_redirect_loops_stop(api) -> None:
    api.get("https://files.example.org/loop").mock(
        return_value=httpx.Response(302, headers={"location": "https://files.example.org/loop"})
    )
    with pytest.raises(SourceHTTPError):
        await download.download_file(source="commons", record_id="r", url="https://files.example.org/loop")


async def test_declared_size_over_the_limit_is_refused_before_downloading(api) -> None:
    api.get(URL).mock(return_value=httpx.Response(200, content=BODY))
    with pytest.raises(DownloadTooLarge):
        await download.download_file(source="commons", record_id="r", url=URL, max_bytes=100)


async def test_undeclared_size_is_enforced_while_streaming_and_leaves_nothing_behind(api) -> None:
    async def chunks():
        yield b"x" * 60
        yield b"x" * 60

    api.get(URL).mock(return_value=httpx.Response(200, content=chunks()))
    with pytest.raises(DownloadTooLarge):
        await download.download_file(source="commons", record_id="r", url=URL, max_bytes=100)
    leftovers = [p for p in config.cache_dir().rglob("*") if p.is_file()]
    assert leftovers == []


async def test_the_limit_comes_from_the_environment(api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERITAGE_MCP_MAX_DOWNLOAD_MB", "1")
    api.get(URL).mock(return_value=httpx.Response(200, content=b"x" * (2 * 1024 * 1024)))
    with pytest.raises(DownloadTooLarge):
        await download.download_file(source="commons", record_id="r", url=URL)


async def test_http_errors_are_reported(api) -> None:
    api.get(URL).mock(return_value=httpx.Response(404))
    with pytest.raises(SourceHTTPError):
        await download.download_file(source="commons", record_id="r", url=URL)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://ids.si.edu/ids/download?id=NMAH-AHB2010q05514-000001.jpg", "NMAH-AHB2010q05514-000001.jpg"),
        ("https://ids.si.edu/ids/download?id=NMAH-1_screen", "NMAH-1_screen"),
        ("https://files.example.org/a/File%20Name,1.jpg", "File_Name_1.jpg"),
        ("https://files.example.org/a/page.jpg?id=ignored.png", "page.jpg"),
        ("https://files.example.org/a/b", "b"),
        ("https://files.example.org/", "file"),
        ("https://files.example.org/x?name=report.pdf", "report.pdf"),
    ],
)
def test_filenames_come_from_the_path_or_a_query_hint(url: str, expected: str) -> None:
    assert download.filename_for(url) == expected


def test_a_long_name_is_shortened_without_losing_its_extension() -> None:
    title = "A map of Fairfax County, and parts of Loudoun and Prince William Counties, Va., and the District of Columbia"
    name = download.filename_for(f"https://upload.example.org/x/{title.replace(' ', '_')}_LOC_2002627423.jpg")
    assert len(name) <= 120 and name.endswith(".jpg") and name.startswith("A_map_of_Fairfax_County")
    assert download.filename_for("https://files.example.org/" + "a" * 200) == "a" * 120
    assert (
        download.filename_for("https://files.example.org/" + "a" * 200 + ".verylongsuffix")
        == (("a" * 200 + ".verylongsuffix")[:120])
    )


async def test_an_extension_is_added_from_the_mime_type_and_the_cache_still_finds_it(api) -> None:
    url = "https://files.example.org/ids/download?id=NMAH-1_screen"
    route = api.get(url).mock(
        return_value=httpx.Response(200, content=BODY, headers={"content-type": "image/jpeg"})
    )
    first = await download.download_file(source="smithsonian", record_id="r", url=url)
    assert Path(first["path"]).name in {"NMAH-1_screen.jpg", "NMAH-1_screen.jpeg"}
    assert Path(first["path"]).read_bytes() == BODY
    assert Path(first["path"] + ".provenance.json").exists()
    again = await download.download_file(source="smithsonian", record_id="r", url=url)
    assert again["cached"] is True and again["path"] == first["path"] and route.call_count == 1
