from __future__ import annotations

import hashlib
import io
import random
import tarfile
import time
from typing import Any

import httpx
import pytest

from conftest import FIXTURES, load
from helpers import ToolFailed, call
from heritage_research_mcp.errors import Blocked, HeritageError, NotPrepared
from heritage_research_mcp.server import mcp
from heritage_research_mcp.sources import chronam_bulk as bulk
from heritage_research_mcp.sources import chronicling_america as chronam

MANIFEST = (FIXTURES / "chronam_manifest.yaml").read_text(encoding="utf-8")
TITLES = (FIXTURES / "chronam_titles.csv").read_text(encoding="utf-8")
PAGES = load("chronam_ocr_pages.json")
ABINGDON = "sn84025980"
STAUNTON = "sn84024718"
BASE = bulk.OCR_BASE


def make_archive(files: dict[str, str | bytes]) -> tuple[bytes, dict[str, Any]]:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as archive:
        for name, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    blob = buffer.getvalue()
    batch = {
        "batch": "vi_test_ver01",
        "lccns": [ABINGDON, STAUNTON],
        "size": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "pages": 0,
    }
    return blob, batch


def archive_files() -> dict[str, str | bytes]:
    files: dict[str, str | bytes] = {}
    for name, text in PAGES.items():
        files[name] = text
        files[name.replace("ocr.txt", "ocr.xml")] = "<alto>" + "x" * 5000 + "</alto>"
    files[f"{STAUNTON}/1863/05/01/ed-1/seq-1/ocr.txt"] = (
        "Staunton Spectator May 1 1863. Mosby's men were reported near Warrenton."
    )
    files[f"{STAUNTON}/1864/05/01/ed-1/seq-1/ocr.txt"] = "A page from 1864 that must not be kept."
    return files


def chunks(blob: bytes, size: int = 700):
    for start in range(0, len(blob), size):
        yield blob[start : start + size]


@pytest.fixture
def archive():
    return make_archive(archive_files())


def connect():
    return bulk._connect(write=True)


def test_manifest_and_titles_parse_the_libraries_real_files() -> None:
    manifest = bulk.parse_manifest(MANIFEST)
    assert set(manifest) == {"ak_albatross_ver01", "vi_barbaro_ver01", "vi_hansel_ver01"}
    barbaro = manifest["vi_barbaro_ver01"]
    assert barbaro["lccns"] == [ABINGDON] and barbaro["size"] == 38240760 and len(barbaro["sha256"]) == 64
    titles = bulk.parse_titles(TITLES)
    assert titles[ABINGDON]["state"] == "Virginia" and titles[ABINGDON]["first"] == "1862-10-03"
    assert titles["sn98060265"]["last"] == "1911-12-31"


def test_choose_picks_the_batches_of_papers_that_ran_in_the_range() -> None:
    manifest, titles = bulk.parse_manifest(MANIFEST), bulk.parse_titles(TITLES)
    found = bulk.choose(manifest, titles, "1863-01-01", "1863-12-31", state="Virginia")
    assert set(found["titles"]) == {ABINGDON, STAUNTON, "sn84024738"}
    assert [b["batch"] for b in found["batches"]] == ["vi_barbaro_ver01", "vi_hansel_ver01"]
    assert (
        bulk.choose(manifest, titles, "1863-01-01", "1863-12-31", lccns=[ABINGDON])["batches"][0]["batch"]
        == "vi_barbaro_ver01"
    )
    assert (
        bulk.choose(manifest, titles, "1920-01-01", "1920-12-31", state="alaska")["batches"][0]["batch"]
        == "ak_albatross_ver01"
    )
    assert bulk.choose(manifest, titles, "1700-01-01", "1700-12-31")["batches"] == []


def test_a_title_with_no_last_issue_ends_where_its_own_span_says() -> None:
    titles = bulk.parse_titles(TITLES)
    valdez = titles["sn98060265"]
    assert valdez["last"] == "1911-12-31"
    assert bulk._overlaps(valdez, "1910-01-01", "1910-12-31") and not bulk._overlaps(
        valdez, "1950-01-01", "1950-12-31"
    )


def test_title_spans_with_unknown_digits_are_read_generously_and_current_stays_open() -> None:
    def last(title: str) -> str | None:
        csv_text = f'state,title,lccn,first_issue,last_issue\nX,"{title}",/lccn/sn1/,1800-01-01,\n'
        return bulk.parse_titles(csv_text)["sn1"]["last"]

    assert last("Daily Richmond times. [volume]Richmond, Va., 1849-1850") == "1850-12-31"
    assert last("Lynchburg Virginian. [volume]Lynchburg [Va.], 1829-185?") == "1859-12-31"
    assert last("The Adak sun.Adak, Alaska, 1955-19??") == "1999-12-31"
    assert last("Staunton paper. [volume]Staunton, Va., 1895-????") == "9999-12-31"
    assert last("The Sun. [volume]Baltimore, Md., 1837-current") is None
    assert last("A title with no span at all") is None


def test_only_pages_dated_in_the_range_are_kept_and_the_alto_xml_is_skipped(archive) -> None:
    blob, batch = archive
    con = connect()
    kept = bulk.index_stream(con, batch, chunks(blob), "1863-01-01", "1863-12-31", None)
    assert kept == 4
    rows = con.execute("SELECT lccn, date, ed, seq FROM pages ORDER BY date, seq").fetchall()
    assert [(r[1], r[3]) for r in rows] == [
        ("1863-03-06", 1),
        ("1863-03-13", 1),
        ("1863-03-13", 2),
        ("1863-05-01", 1),
    ]
    assert con.execute("SELECT count(*) FROM done").fetchone()[0] == 1
    con.close()
    assert bulk.ready()


def test_a_lccn_filter_drops_other_papers_in_the_same_batch(archive) -> None:
    blob, batch = archive
    con = connect()
    assert bulk.index_stream(con, batch, chunks(blob), "1863-01-01", "1863-12-31", {ABINGDON}) == 3
    con.close()


def test_a_checksum_mismatch_discards_the_batch_completely(archive) -> None:
    blob, batch = archive
    con = connect()
    with pytest.raises(HeritageError, match="checksum"):
        bulk.index_stream(con, {**batch, "sha256": "0" * 64}, chunks(blob), "1863-01-01", "1863-12-31", None)
    assert con.execute("SELECT count(*) FROM pages").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM done").fetchone()[0] == 0
    con.close()


def test_a_truncated_download_leaves_the_index_untouched(archive) -> None:
    blob, batch = archive
    con = connect()
    with pytest.raises((HeritageError, EOFError, OSError, tarfile.TarError)):
        bulk.index_stream(con, batch, chunks(blob[: len(blob) // 2]), "1863-01-01", "1863-12-31", None)
    assert con.execute("SELECT count(*) FROM pages").fetchone()[0] == 0
    con.close()


def test_member_names_that_are_not_plain_page_paths_are_ignored() -> None:
    files = {
        "../evil/1863/03/06/ed-1/seq-1/ocr.txt": "x",
        f"/abs/{ABINGDON}/1863/03/06/ed-1/seq-1/ocr.txt": "x",
        f"{ABINGDON}/1863/03/06/ed-1/seq-1/../../ocr.txt": "x",
        f"{ABINGDON}/1863/03/06/ed-1/seq-1/ocr.txt.bak": "x",
        f"./{ABINGDON}/1863/03/06/ed-1/seq-2/ocr.txt": "A real page in a dot-prefixed archive.",
    }
    blob, batch = make_archive(files)
    con = connect()
    assert bulk.index_stream(con, batch, chunks(blob), "1863-01-01", "1863-12-31", None) == 1
    con.close()


def test_an_oversized_member_is_skipped(archive, monkeypatch: pytest.MonkeyPatch) -> None:
    blob, batch = archive
    monkeypatch.setattr(bulk, "MAX_PAGE_BYTES", 100)
    con = connect()
    assert bulk.index_stream(con, batch, chunks(blob), "1863-01-01", "1863-12-31", None) == 1
    con.close()


def test_a_finished_batch_is_not_downloaded_again_for_the_same_range(
    archive, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob, batch = archive
    calls: list[str] = []
    monkeypatch.setattr(bulk, "_download", lambda b: (calls.append(b["batch"]), chunks(blob))[1])
    chosen = {"titles": bulk.parse_titles(TITLES), "batches": [batch]}
    first = bulk.run_job(chosen, "1863-01-01", "1863-12-31", False)
    again = bulk.run_job(chosen, "1863-01-01", "1863-12-31", False)
    assert (
        first["pages_added"] == 4
        and again == {"batches_indexed": 0, "pages_added": 0}
        and calls == ["vi_test_ver01"]
    )
    narrower = bulk.run_job(chosen, "1863-03-01", "1863-03-31", False)
    assert narrower["batches_indexed"] == 0
    wider = bulk.run_job(chosen, "1863-01-01", "1864-12-31", False)
    assert wider["batches_indexed"] == 1 and len(calls) == 2


async def test_search_ranks_highlights_and_filters(archive, monkeypatch: pytest.MonkeyPatch) -> None:
    blob, batch = archive
    monkeypatch.setattr(bulk, "_download", lambda b: chunks(blob))
    bulk.run_job({"titles": bulk.parse_titles(TITLES), "batches": [batch]}, "1863-01-01", "1863-12-31", False)
    out = await bulk.search("Mosby")
    assert out["total"] == 1
    record = out["records"][0]
    assert record["id"] == f"{STAUNTON}/1863-05-01/ed-1/1" and "**Mosby**" in record["description"]
    assert record["places"] == ["Virginia"] and record["rights"]["reuse"] == "free"
    assert record["landing_url"] == f"https://www.loc.gov/resource/{STAUNTON}/1863-05-01/ed-1/?sp=1"
    assert (await bulk.search("Mosby", state="virginia"))["total"] == 1
    assert (await bulk.search("Mosby", state="ohio"))["total"] == 0
    assert (await bulk.search("Mosby", lccn=ABINGDON))["total"] == 0
    assert (await bulk.search("Mosby", date_from="1863-06"))["total"] == 0
    assert (await bulk.search("Mosby", kind="image"))["records"] == []
    assert (await bulk.search("VOLUME", lccn=ABINGDON))["total"] >= 1
    with pytest.raises(HeritageError, match="at least one word"):
        await bulk.search(" ")


async def test_a_page_in_the_index_is_read_without_any_request(
    archive, monkeypatch: pytest.MonkeyPatch, api
) -> None:
    blob, batch = archive
    monkeypatch.setattr(bulk, "_download", lambda b: chunks(blob))
    bulk.run_job({"titles": bulk.parse_titles(TITLES), "batches": [batch]}, "1863-01-01", "1863-12-31", False)
    page_id = f"{ABINGDON}/1863-03-13/ed-1/2"
    out = await chronam.page_text(page_id)
    assert out["from"].startswith("local index") and "Question" in out["text"]
    record = (await chronam.get_page(page_id))["record"]
    assert record["extra"]["text"].startswith("BYCOALE") and record["extra"]["lccn"] == ABINGDON
    assert api.calls.call_count == 0
    assert bulk.local_page("sn1/1863-03-13/ed-1/9") is None and bulk.local_page("nonsense") is None


async def test_search_without_an_index_says_how_to_build_one() -> None:
    assert not bulk.ready()
    with pytest.raises(NotPrepared):
        await bulk.search("mosby")
    with pytest.raises(ToolFailed, match="dispatch_prepare|local index"):
        await call(mcp, "chronam_local_search", {"query": "mosby"})


STARTS = {
    "vi_barbaro_ver01": (ABINGDON, "1862/10/03"),
    "vi_hansel_ver01": (STAUNTON, "1849/12/19"),
    "vi_newbatch_ver01": ("sn84024738", "1870/01/01"),
    "vi_newa_ver01": ("sn84024738", "1860/01/01"),
    "vi_newb_ver01": ("sn84024738", "1861/01/01"),
}


def start_prefix(name: str) -> bytes:
    lccn, day = STARTS[name]
    return make_archive({f"{lccn}/{day}/ed-1/seq-1/ocr.txt": "first page"})[0]


def serve_starts(api) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1].removesuffix(".tar.bz2")
        return httpx.Response(206, content=start_prefix(name)) if name in STARTS else httpx.Response(404)

    api.get(url__regex=rf"{BASE}/[a-z_0-9]+\.tar\.bz2").mock(side_effect=handler)


def routes(api) -> None:
    serve_starts(api)
    api.get(f"{BASE}/backups/").mock(
        return_value=httpx.Response(
            200,
            text='<a href="ocr_2026-04-08T17%3A30%3A45Z.yaml">a</a><a href="ocr_2026-10-05T16%3A58%3A41Z.yaml">b</a>',
        )
    )
    api.get(f"{BASE}/backups/ocr_2026-10-05T16:58:41Z.yaml").mock(
        return_value=httpx.Response(404, text="Not Found")
    )
    api.get(f"{BASE}/backups/ocr_2026-04-08T17:30:45Z.yaml").mock(
        return_value=httpx.Response(200, text=MANIFEST)
    )
    api.get(f"{BASE}/").mock(
        return_value=httpx.Response(
            200,
            text='<a href="vi_barbaro_ver01.tar.bz2">x</a><a href="vi_newbatch_ver01.tar.bz2">y</a><a href="../">up</a>',
        )
    )
    api.get(f"{BASE}/metadata/vi_newbatch_ver01.tar.bz2.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "batch": "vi_newbatch_ver01",
                "lccns": ["sn84024738"],
                "size": 1_000_000_000,
                "sha256": "ab" * 32,
                "page_count": 5000,
            },
        )
    )
    api.get(bulk.TITLES_URL).mock(return_value=httpx.Response(200, text=TITLES))


async def test_plan_reads_the_libraries_lists_and_sizes_the_job(api) -> None:
    routes(api)
    out = await bulk.plan("1863", "1863", state="Virginia")
    assert out["titles"] == 3 and out["batches"] == 2 and out["to_download"] == 2
    assert out["archives_skipped_because_they_start_after_the_range"] == 1
    assert out["download_gb"] == pytest.approx((38240760 + 283_000_000) / 1e9, abs=0.05)
    again = bulk.plan("1863", "1863", state="Virginia")
    before = api.calls.call_count
    await again
    assert api.calls.call_count == before
    with pytest.raises(HeritageError, match="date_from and date_to"):
        await bulk.plan(None, "1863")


async def test_a_plan_over_the_limit_is_refused_before_anything_downloads(api) -> None:
    routes(api)
    with pytest.raises(HeritageError, match="GB limit"):
        await bulk.start("1863", "1863", state="virginia", max_gb=0.1)
    assert bulk.job_status()["state"] == "idle"


def wait() -> dict[str, Any]:
    deadline = time.time() + 10
    while bulk._job.get("state") == "running" and time.time() < deadline:
        time.sleep(0.01)
    return bulk.job_status()


async def test_a_background_job_runs_to_the_end_and_reports(
    archive, monkeypatch: pytest.MonkeyPatch, api
) -> None:
    routes(api)
    blob, batch = archive
    api.get(f"{BASE}/metadata/vi_newbatch_ver01.tar.bz2.json").mock(return_value=httpx.Response(404))
    monkeypatch.setattr(
        bulk, "choose", lambda *a, **k: {"titles": bulk.parse_titles(TITLES), "batches": [batch]}
    )
    monkeypatch.setattr(bulk, "_download", lambda b: chunks(blob))
    started = await bulk.start("1863", "1863", state="virginia")
    assert started["state"] == "running"
    done = wait()
    assert done["state"] == "finished" and done["pages_added"] == 4 and done["index"]["pages"] == 4
    top = done["index"]["titles"][0]
    assert top["lccn"] == ABINGDON and top["title"].startswith("The Abingdon Virginian") and top["pages"] == 3


async def test_a_block_pauses_the_job_and_keeps_what_was_finished(
    archive, monkeypatch: pytest.MonkeyPatch, api
) -> None:
    routes(api)
    blob, batch = archive
    second = {**batch, "batch": "vi_second_ver01"}

    def download(b: dict[str, Any]):
        if b["batch"] == "vi_second_ver01":
            raise Blocked("chronam_bulk", 429, "the Library is limiting this client; resume later")
        return chunks(blob)

    monkeypatch.setattr(
        bulk, "choose", lambda *a, **k: {"titles": bulk.parse_titles(TITLES), "batches": [batch, second]}
    )
    monkeypatch.setattr(bulk, "_download", download)
    await bulk.start("1863", "1863", state="virginia")
    final = wait()
    assert final["state"] == "paused" and "limiting" in final["error"]
    assert final["index"]["batches_indexed"] == 1 and final["index"]["pages"] == 4


async def test_only_one_job_runs_at_a_time(monkeypatch: pytest.MonkeyPatch, api) -> None:
    routes(api)
    bulk._job.update(state="running")
    with pytest.raises(HeritageError, match="already running"):
        await bulk.start("1863", "1863", state="virginia")


def test_download_streams_the_fixed_archive_url_and_treats_429_as_a_block(api) -> None:
    batch = {"batch": "vi_test_ver01"}
    url = f"{BASE}/vi_test_ver01.tar.bz2"
    api.get(url).mock(return_value=httpx.Response(200, content=b"abc" * 100))
    assert b"".join(bulk._download(batch)) == b"abc" * 100
    api.get(url).mock(return_value=httpx.Response(429, text="slow down"))
    with pytest.raises(Blocked):
        list(bulk._download(batch))
    api.get(url).mock(return_value=httpx.Response(403, text="x", headers={"cf-mitigated": "challenge"}))
    with pytest.raises(Blocked):
        list(bulk._download(batch))
    api.get(url).mock(return_value=httpx.Response(404, text="nope"))
    with pytest.raises(HeritageError, match="HTTP 404"):
        list(bulk._download(batch))


async def test_the_index_joins_cross_source_search_once_built(
    archive, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob, batch = archive
    monkeypatch.setattr(bulk, "_download", lambda b: chunks(blob))
    sources = (await call(mcp, "list_sources"))["sources"]
    assert sources["chronam_local"]["configured"] is False and sources["chronam_local"]["needs_index"] is True
    bulk.run_job({"titles": bulk.parse_titles(TITLES), "batches": [batch]}, "1863-01-01", "1863-12-31", False)
    assert (await call(mcp, "list_sources"))["sources"]["chronam_local"]["configured"] is True
    out = await call(
        mcp, "search", {"query": "Mosby", "sources": ["chronam_local"], "date_from": 1863, "date_to": 1863}
    )
    assert out["results"]["chronam_local"]["total"] == 1 and out["errors"] == {}
    record = (
        await call(mcp, "get_record", {"source": "chronam", "record_id": f"{STAUNTON}/1863-05-01/ed-1/1"})
    )["record"]
    assert "Warrenton" in record["extra"]["text"]
    status = await call(mcp, "chronam_bulk_status")
    assert status["index"]["pages"] == 4


async def test_a_rate_limit_part_way_through_planning_keeps_what_was_learned(api) -> None:
    from heritage_research_mcp.http import http

    routes(api)
    api.get(f"{BASE}/").mock(
        return_value=httpx.Response(
            200, text='<a href="vi_newa_ver01.tar.bz2">a</a><a href="vi_newb_ver01.tar.bz2">b</a>'
        )
    )

    def meta(name: str) -> dict[str, Any]:
        return {
            "batch": name,
            "lccns": ["sn84024738"],
            "size": 5_000_000,
            "sha256": "cd" * 32,
            "page_count": 10,
        }

    api.get(f"{BASE}/metadata/vi_newa_ver01.tar.bz2.json").mock(
        return_value=httpx.Response(200, json=meta("vi_newa_ver01"))
    )
    limited = api.get(f"{BASE}/metadata/vi_newb_ver01.tar.bz2.json").mock(
        return_value=httpx.Response(429, text="slow")
    )
    first = await bulk.plan("1863", "1863", state="virginia")
    assert first["newer_batches_not_yet_checked"] == 1 and first["batches"] == 3
    assert first["paused_by_rate_limit"] is True
    assert limited.call_count > 0

    http._blocked.clear()
    limited.mock(return_value=httpx.Response(200, json=meta("vi_newb_ver01")))
    second = await bulk.plan("1863", "1863", state="virginia")
    assert second["newer_batches_not_yet_checked"] == 0 and second["batches"] == 4
    assert second["paused_by_rate_limit"] is False
    listing_calls = sum(1 for c in api.calls if str(c.request.url) == f"{BASE}/backups/")
    assert listing_calls == 1


async def test_probe_reads_an_archives_first_page_from_a_small_range_request(api) -> None:
    serve_starts(api)
    batch = {"batch": "vi_barbaro_ver01", "sha256": "aa" * 32, "lccns": [ABINGDON]}
    assert await bulk.probe_start(batch) == (ABINGDON, "1862-10-03")
    before = api.calls.call_count
    assert await bulk.probe_start(batch) == (ABINGDON, "1862-10-03")
    assert api.calls.call_count == before
    request = api.calls[0].request
    assert request.headers["range"].startswith("bytes=0-")


async def test_probe_gives_up_quietly_when_the_start_cannot_be_read(api) -> None:
    url = f"{BASE}/vi_test_ver01.tar.bz2"
    batch = {"batch": "vi_test_ver01", "sha256": "bb" * 32, "lccns": [ABINGDON]}
    api.get(url).mock(return_value=httpx.Response(200, content=b"whole file because Range was ignored"))
    assert await bulk.probe_start(batch) is None
    api.get(url).mock(return_value=httpx.Response(206, content=b"this is not bzip2 data at all"))
    assert await bulk.probe_start({**batch, "sha256": "cc" * 32}) is None
    api.get(url).mock(return_value=httpx.Response(206, content=make_archive({"README": "no dates here"})[0]))
    assert await bulk.probe_start({**batch, "sha256": "dd" * 32}) is None


async def test_a_probe_that_is_rate_limited_keeps_every_archive_in_the_plan(api) -> None:
    api.get(url__regex=rf"{BASE}/vi_[a-z]+_ver0\d\.tar\.bz2").mock(
        return_value=httpx.Response(429, text="slow")
    )
    routes(api)
    out = await bulk.plan("1863", "1863", state="virginia")
    assert out["start_dates_paused_by_rate_limit"] is True
    assert out["archives_skipped_because_they_start_after_the_range"] == 0 and out["batches"] == 3


def long_archive() -> tuple[bytes, dict[str, Any], dict[str, int]]:
    noise = random.Random(7)
    files: dict[str, str | bytes] = {}
    for month in range(1, 13):
        files[f"{ABINGDON}/1863/{month:02d}/05/ed-1/seq-1/ocr.txt"] = f"Abingdon page for month {month} mosby"
    for month in range(1, 13):
        files[f"{ABINGDON}/1864/{month:02d}/05/ed-1/seq-1/ocr.txt"] = noise.randbytes(400_000)
    blob, batch = make_archive(files)
    return blob, {**batch, "lccns": [ABINGDON]}, {}


def consume(blob: bytes, counter: dict[str, int]):
    for chunk in chunks(blob, 4096):
        counter["read"] = counter.get("read", 0) + len(chunk)
        yield chunk


def test_reading_stops_once_the_range_is_passed_and_the_archive_is_marked_unchecked() -> None:
    blob, batch, counter = long_archive()
    con = connect()
    kept = bulk.index_stream(con, batch, consume(blob, counter), "1863-03-01", "1863-04-30", {ABINGDON})
    assert kept == 2
    assert counter["read"] < len(blob) * 0.6
    assert con.execute("SELECT verified FROM done").fetchone()[0] == 0
    con.close()
    assert bulk.summary()["batches_read_early_without_checksum"] == 1


def test_verify_reads_the_whole_archive_and_checks_it() -> None:
    blob, batch, counter = long_archive()
    con = connect()
    kept = bulk.index_stream(
        con, batch, consume(blob, counter), "1863-03-01", "1863-04-30", {ABINGDON}, verify=True
    )
    assert kept == 2 and counter["read"] == len(blob)
    assert con.execute("SELECT verified FROM done").fetchone()[0] == 1
    with pytest.raises(HeritageError, match="checksum"):
        bulk.index_stream(
            con,
            {**batch, "sha256": "0" * 64},
            chunks(blob),
            "1862-01-01",
            "1862-12-31",
            {ABINGDON},
            verify=True,
        )
    con.close()


def test_a_paper_that_never_leaves_the_range_is_read_to_the_end_and_verified() -> None:
    blob, batch, counter = long_archive()
    con = connect()
    bulk.index_stream(con, batch, consume(blob, counter), "1863-03-01", "1864-12-31", {ABINGDON})
    assert counter["read"] == len(blob) and con.execute("SELECT verified FROM done").fetchone()[0] == 1
    con.close()


def test_disorder_seen_before_the_stop_point_turns_early_stop_off() -> None:
    files: dict[str, str | bytes] = {
        f"{ABINGDON}/1863/06/05/ed-1/seq-1/ocr.txt": "in range",
        f"{ABINGDON}/1863/03/05/ed-1/seq-1/ocr.txt": "earlier than the one before it",
        f"{ABINGDON}/1864/01/05/ed-1/seq-1/ocr.txt": "late page",
        f"{ABINGDON}/1863/09/05/ed-1/seq-1/ocr.txt": "in range, listed after a late page",
    }
    blob, batch = make_archive(files)
    batch = {**batch, "lccns": [ABINGDON]}
    con = connect()
    kept = bulk.index_stream(con, batch, chunks(blob), "1863-01-01", "1863-12-31", {ABINGDON})
    assert kept == 3 and con.execute("SELECT verified FROM done").fetchone()[0] == 1
    con.close()


def test_a_multi_paper_archive_waits_for_every_wanted_paper_to_pass_the_range() -> None:
    files: dict[str, str | bytes] = {
        f"{ABINGDON}/1863/05/05/ed-1/seq-1/ocr.txt": "a",
        f"{ABINGDON}/1864/05/05/ed-1/seq-1/ocr.txt": "b",
        f"{STAUNTON}/1863/05/05/ed-1/seq-1/ocr.txt": "c Mosby",
        f"{STAUNTON}/1863/09/05/ed-1/seq-1/ocr.txt": "d",
    }
    blob, batch = make_archive(files)
    con = connect()
    kept = bulk.index_stream(con, batch, chunks(blob), "1863-01-01", "1863-12-31", None)
    assert kept == 3 and con.execute("SELECT verified FROM done").fetchone()[0] == 1
    con.close()
