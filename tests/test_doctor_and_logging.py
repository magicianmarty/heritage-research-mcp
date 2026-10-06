from __future__ import annotations

import logging
import sys

import httpx
import pytest

from conftest import load
from heritage_research_mcp import __version__, doctor
from heritage_research_mcp.http import _RedactLogs
from heritage_research_mcp.server import main
from heritage_research_mcp.sources import dpla


def test_doctor_lists_every_source_and_where_to_get_missing_keys(capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor.run(live=False) == 0
    out = capsys.readouterr().out
    for name in ("internet_archive", "commons", "dpla", "nara", "smithsonian"):
        assert name in out
    assert "NO KEY: https://www.archives.gov/research/catalog/help/api" in out
    assert "no key needed" in out and "--live" in out


def test_doctor_reports_where_a_key_came_from_without_showing_it(
    capsys: pytest.CaptureFixture[str], set_key
) -> None:
    key = set_key("smithsonian", "do-not-print-me")
    doctor.run(live=False)
    out = capsys.readouterr().out
    assert "key from env" in out and key not in out


def test_doctor_live_checks_each_ready_source(api, capsys: pytest.CaptureFixture[str]) -> None:
    api.get("https://archive.org/advancedsearch.php").mock(
        return_value=httpx.Response(200, json=load("ia_advancedsearch.json"))
    )
    api.get("https://commons.wikimedia.org/w/api.php").mock(
        return_value=httpx.Response(200, json=load("commons_search.json"))
    )
    assert doctor.run(live=True) == 0
    out = capsys.readouterr().out
    assert out.count("ok ") == 2 and out.count("skipped (no key)") == 3


def test_doctor_live_exits_non_zero_when_a_source_fails(api, capsys: pytest.CaptureFixture[str]) -> None:
    api.get("https://archive.org/advancedsearch.php").mock(return_value=httpx.Response(500, text="down"))
    api.get("https://commons.wikimedia.org/w/api.php").mock(
        return_value=httpx.Response(200, json=load("commons_search.json"))
    )
    assert doctor.run(live=True) == 1
    assert "FAIL" in capsys.readouterr().out


def test_version_flag(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["heritage-research-mcp", "--version"])
    main()
    assert capsys.readouterr().out.strip() == f"heritage-research-mcp {__version__}"


def test_the_log_filter_redacts_keys_in_request_urls() -> None:
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        'HTTP Request: %s %s "%s %d %s"',
        ("GET", httpx.URL("https://api.dp.la/v2/items?api_key=SECRET123&q=x"), "HTTP/1.1", 200, "OK"),
        None,
    )
    assert _RedactLogs().filter(record) is True
    text = record.getMessage()
    assert "SECRET123" not in text and "api_key=REDACTED" in text and "q=x" in text


async def test_keys_do_not_reach_the_logs_even_at_debug_level(
    api, set_key, caplog: pytest.LogCaptureFixture
) -> None:
    key = set_key("dpla", "log-me-if-you-dare")
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    caplog.set_level(logging.DEBUG)
    try:
        api.get("https://api.dp.la/v2/items").mock(
            return_value=httpx.Response(200, json=load("dpla_items.json"))
        )
        await dpla.search(q="x")
    finally:
        logging.getLogger("httpx").setLevel(logging.WARNING)
    assert "HTTP Request" in caplog.text
    assert key not in caplog.text
