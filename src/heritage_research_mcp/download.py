"""Download an original into the local cache, with a provenance sidecar.

The URL always comes from a record a source returned, never from the model, but it is still checked: https only,
no credentials, standard port, and every hop of a redirect must resolve to a public address.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import glob
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import socket
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx

from . import __version__, config
from .errors import DownloadTooLarge, HeritageError, SourceHTTPError, UnsafeURL
from .http import http

MAX_REDIRECTS = 5
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_part(text: str, limit: int = 80) -> str:
    return _SAFE.sub("_", text).strip("._")[:limit] or "item"


def filename_for(url: str) -> str:
    """A readable cache name. Some archives put the real name in the query: .../download?id=ABC-001.jpg."""
    parts = urlsplit(url)
    segment = unquote(Path(unquote(parts.path)).name)
    hints = {k.lower(): v for k, v in parse_qs(parts.query).items() if v}
    hint = next((hints[k][0] for k in ("id", "file", "filename", "name") if k in hints), None)
    chosen = segment if ("." in segment or not hint) else hint
    return safe_part(chosen or "file", 120)


def _cached(folder: Path, name: str) -> Path | None:
    """The file already downloaded under this name, which may have gained an extension from its MIME type."""
    found = [folder / name, *folder.glob(glob.escape(name) + ".*")]
    for candidate in found:
        if candidate.name.endswith((".provenance.json", ".part")) or not candidate.is_file():
            continue
        if candidate.with_name(candidate.name + ".provenance.json").exists():
            return candidate
    return None


async def resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _check_ip(address: str) -> None:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not ip.is_global:
        raise UnsafeURL("refusing to fetch an address that is not on the public internet")


async def assert_public_https(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UnsafeURL("only https URLs are downloaded")
    if parts.username or parts.password:
        raise UnsafeURL("URLs with embedded credentials are refused")
    if parts.port not in (None, 443):
        raise UnsafeURL("non-standard ports are refused")
    host = parts.hostname
    if not host:
        raise UnsafeURL("the URL has no host")
    try:
        _check_ip(host)
        return
    except ValueError:
        pass
    try:
        addresses = await resolve(host)
    except OSError as exc:
        raise UnsafeURL(f"could not resolve {host}") from exc
    if not addresses:
        raise UnsafeURL(f"could not resolve {host}")
    for address in addresses:
        _check_ip(address)


async def download_file(
    *,
    source: str,
    record_id: str,
    url: str,
    title: str | None = None,
    landing_url: str | None = None,
    mime: str | None = None,
    rights: dict[str, Any] | None = None,
    max_bytes: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    limit = max_bytes or config.max_download_bytes()
    name = filename_for(url)
    folder = config.cache_dir() / safe_part(source) / safe_part(record_id, 120)
    dest = folder / name
    existing = _cached(folder, name)
    if existing is not None and not overwrite:
        sidecar = existing.with_name(existing.name + ".provenance.json")
        return {**json.loads(sidecar.read_text(encoding="utf-8")), "path": str(existing), "cached": True}

    headers = {"User-Agent": config.user_agent(), "Accept": "*/*"}
    current = url
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await assert_public_https(current)
            await http.gate(source)
            async with client.stream("GET", current, headers=headers) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise SourceHTTPError(source, response.status_code, "redirect without a location")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise SourceHTTPError(source, response.status_code, "the download was refused")
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > limit:
                    raise DownloadTooLarge(
                        f"{int(declared) // 1_000_000} MB is over the {limit // 1_000_000} MB limit"
                    )
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise HeritageError(
                        f"cannot write to the cache folder {folder}: {exc.strerror or exc}. "
                        "Set HERITAGE_MCP_CACHE_DIR to a writable folder, or HERITAGE_MCP_DISABLE_DOWNLOADS=1."
                    ) from exc
                part = dest.with_name(dest.name + ".part")
                digest = hashlib.sha256()
                total = 0
                try:
                    with part.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > limit:
                                raise DownloadTooLarge(f"download passed the {limit // 1_000_000} MB limit")
                            digest.update(chunk)
                            handle.write(chunk)
                except BaseException:
                    part.unlink(missing_ok=True)
                    raise
                os.replace(part, dest)
                served_mime = response.headers.get("content-type", "").split(";")[0].strip() or mime
                break
        else:
            raise SourceHTTPError(source, None, "too many redirects")

    final = dest
    extension = mimetypes.guess_extension(served_mime) if served_mime and not dest.suffix else None
    if extension:
        final = dest.with_name(dest.name + extension)
        os.replace(dest, final)

    info = {
        "source": source,
        "record_id": record_id,
        "title": title,
        "url": url,
        "final_url": current,
        "landing_url": landing_url,
        "retrieved_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "sha256": digest.hexdigest(),
        "bytes": total,
        "mime": served_mime,
        "rights": rights,
        "tool": f"heritage-research-mcp {__version__}",
    }
    final.with_name(final.name + ".provenance.json").write_text(
        json.dumps(info, indent=2, sort_keys=True), encoding="utf-8"
    )
    return {**info, "path": str(final), "cached": False}
