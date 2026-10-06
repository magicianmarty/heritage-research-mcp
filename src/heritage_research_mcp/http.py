"""One polite HTTP layer for every source: pacing, retries, size caps and secret redaction."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from . import config
from .errors import DownloadTooLarge, QuotaExceeded, SourceHTTPError
from .usage import usage

MIN_INTERVAL = {"internet_archive": 0.2, "commons": 0.2, "dpla": 0.1, "nara": 0.5, "smithsonian": 0.3}
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_BACKOFF = 20.0
MAX_JSON_BYTES = 30 * 1024 * 1024

sleep = asyncio.sleep  # tests replace this

_SECRET = re.compile(r"(?i)(?<![\w-])((?:api_key|apikey|x-api-key|key)=)[^&\s'\"]+")


def redact(text: str) -> str:
    return _SECRET.sub(r"\1REDACTED", text)


class _RedactLogs(logging.Filter):
    """httpx logs full request URLs, and two providers take the key as a query parameter."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(str(a)) if isinstance(a, str | httpx.URL) else a for a in record.args)
        return True


for _name in ("httpx", "httpcore"):
    _logger = logging.getLogger(_name)
    _logger.setLevel(logging.WARNING)
    _logger.addFilter(_RedactLogs())


@dataclass
class Resp:
    status: int
    headers: httpx.Headers
    content: bytes
    url: str

    def json(self) -> Any:
        return json.loads(self.content)

    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


_HTML_NOISE = re.compile(r"(?is)<(style|script)[^>]*>.*?</\1>|<[^>]+>")


def _snippet(content: bytes) -> str:
    text = _HTML_NOISE.sub(" ", content[:4000].decode("utf-8", errors="replace"))
    text = " ".join(text.split())
    if "request rejected" in text.lower():
        return "blocked by the provider's web firewall (this request shape is refused)"
    return redact(text[:300])


def _retry_after(headers: httpx.Headers) -> float | None:
    raw = headers.get("retry-after")
    if raw and raw.strip().isdigit():
        return float(raw.strip())
    return None


class Http:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last: dict[str, float] = {}

    def _get_client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True, max_redirects=5
            )
            self._loop = loop
            self._last = {}
        return self._client

    async def gate(self, source: str) -> None:
        interval = MIN_INTERVAL.get(source, 0.2)
        now = time.monotonic()
        slot = max(now, self._last.get(source, 0.0) + interval)
        self._last[source] = slot
        if slot > now:
            await sleep(slot - now)

    async def request(
        self,
        source: str,
        url: str,
        *,
        params: Any = None,
        headers: dict[str, str] | None = None,
        max_bytes: int = MAX_JSON_BYTES,
        retries: int = 3,
    ) -> Resp:
        client = self._get_client()
        hdrs = {"User-Agent": config.user_agent(), "Accept": "application/json", **(headers or {})}
        for attempt in range(retries + 1):
            if source == "nara":
                usage.check("nara", config.nara_monthly_limit())
            await self.gate(source)
            usage.record(source)
            try:
                async with client.stream("GET", url, params=params, headers=hdrs) as response:
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise DownloadTooLarge(
                                f"{source} response is larger than {max_bytes // 1_000_000} MB"
                            )
                        chunks.append(chunk)
                    resp = Resp(response.status_code, response.headers, b"".join(chunks), str(response.url))
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= retries:
                    raise SourceHTTPError(source, None, redact(f"{type(exc).__name__}: {exc}")) from exc
                await sleep(min(MAX_BACKOFF, 1.0 * 2**attempt))
                continue
            usage.note_rate_headers(source, resp.headers)
            if resp.status in RETRY_STATUS and attempt < retries:
                wait = _retry_after(resp.headers)
                if wait is not None and wait > MAX_BACKOFF:
                    raise SourceHTTPError(
                        source, resp.status, f"rate limited; the provider asks for {int(wait)} s"
                    )
                await sleep(wait if wait is not None else min(MAX_BACKOFF, 1.0 * 2**attempt))
                continue
            if resp.status >= 400:
                raise SourceHTTPError(source, resp.status, _snippet(resp.content))
            return resp
        raise QuotaExceeded(f"{source}: gave up after {retries + 1} attempts")  # pragma: no cover

    async def get_json(
        self, source: str, url: str, *, params: Any = None, headers: dict[str, str] | None = None
    ) -> Any:
        resp = await self.request(source, url, params=params, headers=headers)
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceHTTPError(
                source, resp.status, f"response was not JSON: {_snippet(resp.content)}"
            ) from exc

    async def get_text(
        self, source: str, url: str, *, params: Any = None, max_bytes: int = MAX_JSON_BYTES
    ) -> str:
        resp = await self.request(
            source, url, params=params, headers={"Accept": "text/plain,*/*"}, max_bytes=max_bytes
        )
        return resp.text()


http = Http()
