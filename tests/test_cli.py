"""`heritage-research-mcp download`: the same safe downloader, run from a shell on a host that keeps the file."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from conftest import load
from heritage_research_mcp import cli, download, server

COMMONS = "https://commons.wikimedia.org/w/api.php"
DPLA = "https://api.dp.la/v2/items"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(_host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(download, "resolve", resolve)


def test_download_writes_the_file_and_prints_the_result(api, capsys: pytest.CaptureFixture[str]) -> None:
    pages = load("commons_search.json")
    record_id = pages["query"]["pages"][0]["title"]
    api.get(COMMONS).mock(return_value=httpx.Response(200, json=pages))
    api.route(host="upload.wikimedia.org").mock(return_value=httpx.Response(200, content=b"JPEGDATA" * 50))
    assert cli.run(["commons", record_id]) == 0
    out = json.loads(capsys.readouterr().out)
    assert Path(out["path"]).read_bytes() == b"JPEGDATA" * 50
    assert out["rights"]["reuse"] == "free" and "rights_warning" not in out
    assert Path(out["path"] + ".provenance.json").exists()


def test_a_failure_prints_the_reason_and_exits_nonzero(
    api, set_key, capsys: pytest.CaptureFixture[str]
) -> None:
    set_key("dpla")
    api.get(f"{DPLA}/m").mock(
        return_value=httpx.Response(200, json={"docs": [{"id": "m", "sourceResource": {"title": "Nothing"}}]})
    )
    assert cli.run(["dpla", "m"]) == 1
    assert "no downloadable media" in json.loads(capsys.readouterr().out)["error"]


def test_the_hosted_switch_does_not_block_the_command(
    api, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HERITAGE_MCP_DISABLE_DOWNLOADS", "1")
    pages = load("commons_search.json")
    api.get(COMMONS).mock(return_value=httpx.Response(200, json=pages))
    api.route(host="upload.wikimedia.org").mock(return_value=httpx.Response(200, content=b"X" * 10))
    assert cli.run(["commons", pages["query"]["pages"][0]["title"]]) == 0
    assert "path" in json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("argv", [["google", "x"], ["commons"], ["commons", "x", "--kind", "map"]])
def test_bad_arguments_are_refused(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.run(argv)
    assert caught.value.code == 2


def test_the_server_entry_point_routes_download(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "run", lambda argv: seen.append(argv) or 0)
    monkeypatch.setattr(
        "sys.argv", ["heritage-research-mcp", "download", "commons", "File:a.jpg", "--kind", "image"]
    )
    with pytest.raises(SystemExit) as caught:
        server.main()
    assert caught.value.code == 0
    assert seen == [["commons", "File:a.jpg", "--kind", "image"]]
