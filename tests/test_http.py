from __future__ import annotations

import httpx
import pytest

from heritage_research_mcp.errors import DownloadTooLarge, QuotaExceeded, SourceHTTPError
from heritage_research_mcp.http import http, redact
from heritage_research_mcp.usage import usage

URL = "https://example.org/api"


async def test_retries_a_503_then_succeeds(api) -> None:
    route = api.get(URL).mock(side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": True})])
    assert await http.get_json("commons", URL) == {"ok": True}
    assert route.call_count == 2


async def test_honours_a_short_retry_after(api) -> None:
    route = api.get(URL).mock(
        side_effect=[httpx.Response(429, headers={"retry-after": "2"}), httpx.Response(200, json=[1])]
    )
    assert await http.get_json("commons", URL) == [1]
    assert route.call_count == 2


async def test_a_long_retry_after_is_reported_not_waited_for(api) -> None:
    route = api.get(URL).mock(return_value=httpx.Response(429, headers={"retry-after": "3600"}))
    with pytest.raises(SourceHTTPError) as caught:
        await http.get_json("smithsonian", URL)
    assert "3600" in str(caught.value)
    assert route.call_count == 1


async def test_gives_up_after_the_retries(api) -> None:
    route = api.get(URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(SourceHTTPError) as caught:
        await http.get_json("commons", URL)
    assert caught.value.status == 500
    assert route.call_count == 4


async def test_client_errors_are_not_retried(api) -> None:
    route = api.get(URL).mock(return_value=httpx.Response(404, text="nope"))
    with pytest.raises(SourceHTTPError) as caught:
        await http.get_json("commons", URL)
    assert caught.value.status == 404
    assert route.call_count == 1


async def test_secrets_are_redacted_from_error_text(api) -> None:
    api.get(URL).mock(
        return_value=httpx.Response(400, text="bad request api_key=SECRET123 and key=SECRET456")
    )
    with pytest.raises(SourceHTTPError) as caught:
        await http.get_json("dpla", URL, params={"api_key": "SECRET123"})
    text = str(caught.value)
    assert "SECRET123" not in text and "SECRET456" not in text
    assert "REDACTED" in text


async def test_transport_errors_do_not_leak_urls_with_keys(api) -> None:
    api.get(URL).mock(side_effect=httpx.ConnectError("could not reach https://x/?api_key=LEAKME"))
    with pytest.raises(SourceHTTPError) as caught:
        await http.get_json("dpla", URL)
    assert "LEAKME" not in str(caught.value)


async def test_non_json_is_a_clear_error(api) -> None:
    api.get(URL).mock(return_value=httpx.Response(200, text="<html>Just a moment...</html>"))
    with pytest.raises(SourceHTTPError) as caught:
        await http.get_json("nara", URL)
    assert "not JSON" in str(caught.value)


async def test_response_size_is_capped(api) -> None:
    api.get(URL).mock(return_value=httpx.Response(200, content=b"x" * 100))
    with pytest.raises(DownloadTooLarge):
        await http.request("commons", URL, max_bytes=10)


async def test_every_attempt_is_counted(api) -> None:
    api.get(URL).mock(side_effect=[httpx.Response(503), httpx.Response(200, json={})])
    await http.get_json("commons", URL)
    assert usage.monthly("commons") == 2


async def test_nara_stops_at_the_monthly_limit(api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARA_MONTHLY_LIMIT", "2")
    route = api.get(URL).mock(return_value=httpx.Response(200, json={}))
    await http.get_json("nara", URL)
    await http.get_json("nara", URL)
    with pytest.raises(QuotaExceeded):
        await http.get_json("nara", URL)
    assert route.call_count == 2


async def test_other_sources_ignore_the_nara_limit(api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARA_MONTHLY_LIMIT", "1")
    api.get(URL).mock(return_value=httpx.Response(200, json={}))
    for _ in range(3):
        await http.get_json("commons", URL)


async def test_provider_rate_headers_are_surfaced(api) -> None:
    api.get(URL).mock(
        return_value=httpx.Response(
            200, json={}, headers={"X-RateLimit-Limit": "1000", "X-RateLimit-Remaining": "998"}
        )
    )
    await http.get_json("smithsonian", URL)
    assert usage.snapshot()["provider_rate_limit"]["smithsonian"] == {"limit": "1000", "remaining": "998"}


async def test_user_agent_identifies_the_project(api) -> None:
    route = api.get(URL).mock(return_value=httpx.Response(200, json={}))
    await http.get_json("commons", URL)
    assert route.calls[0].request.headers["user-agent"].startswith("heritage-research-mcp/")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://x/?api_key=abc&q=1", "https://x/?api_key=REDACTED&q=1"),
        ("key=abc", "key=REDACTED"),
        ("x-api-key=abc", "x-api-key=REDACTED"),
        ("monkey=banana", "monkey=banana"),
        ("no secrets here", "no secrets here"),
    ],
)
def test_redact(raw: str, expected: str) -> None:
    assert redact(raw) == expected
