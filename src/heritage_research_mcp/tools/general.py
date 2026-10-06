"""Cross-source tools: discover what is configured, search everywhere, fetch one record, cache an original."""

from __future__ import annotations

import asyncio
from typing import Any

from .. import config
from ..compat import FastMCP
from ..download import download_file
from ..errors import HeritageError
from ..sources import SOURCES, is_configured
from ..sources import commons as commons_src
from ..sources import dpla as dpla_src
from ..sources import internet_archive as ia_src
from ..sources import nara as nara_src
from ..sources import smithsonian as si_src
from ..usage import usage


async def _search_one(
    name: str, query: str, limit: int, date_from: int | None, date_to: int | None
) -> dict[str, Any]:
    after = str(date_from) if date_from is not None else None
    before = str(date_to) if date_to is not None else None
    if name == "internet_archive":
        return await ia_src.search(query, year_from=date_from, year_to=date_to, rows=limit)
    if name == "commons":
        return await commons_src.search(query, limit=limit)
    if name == "dpla":
        return await dpla_src.search(q=query, date_after=after, date_before=before, page_size=limit)
    if name == "nara":
        return await nara_src.search(q=query, start_date=after, end_date=before, limit=limit)
    if name == "smithsonian":
        return await si_src.search(query, rows=limit)
    raise HeritageError(f"unknown source {name!r}; valid sources are {sorted(SOURCES)}")


async def get_record_dict(source: str, record_id: str) -> dict[str, Any]:
    if source == "internet_archive":
        return (await ia_src.get_item(record_id))["record"]
    if source == "commons":
        return (await commons_src.file_info(record_id))["record"]
    if source == "dpla":
        return (await dpla_src.get_item(record_id))["record"]
    if source == "nara":
        if not record_id.strip().isdigit():
            raise HeritageError("a NARA id is the numeric naId")
        return (await nara_src.get_record(int(record_id)))["record"]
    if source == "smithsonian":
        return (await si_src.get_content(record_id))["record"]
    raise HeritageError(f"unknown source {source!r}; valid sources are {sorted(SOURCES)}")


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def list_sources() -> dict[str, Any]:
        """List the archives this server can search, whether each is ready to use, and what it is good for.

        Sources that need a free API key show `configured: false` until a key is set; the Internet Archive and
        Wikimedia Commons need none.
        """
        out: dict[str, Any] = {}
        for name, info in SOURCES.items():
            entry: dict[str, Any] = {
                "label": info.label,
                "configured": is_configured(name),
                "good_for": info.good_for,
                "limits": info.limits,
                "terms": info.terms,
            }
            if info.keyed:
                env_var, signup = config.KEYED_SOURCES[name]
                entry.update(
                    {
                        "needs_key": True,
                        "key_from": config.key_origin(name),
                        "env_var": env_var,
                        "get_a_key": signup,
                    }
                )
            out[name] = entry
        return {"sources": out}

    @mcp.tool()
    async def usage_report() -> dict[str, Any]:
        """Requests made this month and this session per source, and the provider rate limit last seen."""
        return usage.snapshot()

    @mcp.tool()
    async def search(
        query: str,
        sources: list[str] | None = None,
        limit: int = 5,
        date_from: int | None = None,
        date_to: int | None = None,
        fulltext: bool = False,
    ) -> dict[str, Any]:
        """Search several archives at once and return normalised records grouped by source.

        Every record carries `rights.reuse` (free, attribution, share_alike, non_commercial, no_derivatives,
        restricted or unknown). This is a filtering aid, not legal advice: `unknown` means the holder stated
        nothing.

        Args:
            query: What to look for. Plain words work everywhere.
            sources: Limit to some of internet_archive, commons, dpla, nara, smithsonian. Default: every source
                that is ready to use (see list_sources).
            limit: Records per source (1 to 25).
            date_from: Earliest year. Applied by the Internet Archive, DPLA and NARA; ignored by the others.
            date_to: Latest year, applied the same way.
            fulltext: Also search the text inside Internet Archive books (an experimental endpoint). Returned
                under `fulltext`; this is the way to find a name or place inside a memoir or official report.
        """
        limit = max(1, min(25, limit))
        wanted = sources or [name for name in SOURCES if is_configured(name)]
        unknown = [name for name in wanted if name not in SOURCES]
        if unknown:
            raise HeritageError(f"unknown source(s) {unknown}; valid sources are {sorted(SOURCES)}")
        skipped = {name: "no API key; see list_sources" for name in wanted if not is_configured(name)}
        runnable = [name for name in wanted if name not in skipped]
        jobs: list[Any] = [_search_one(name, query, limit, date_from, date_to) for name in runnable]
        if fulltext:
            jobs.append(ia_src.fulltext_search(query, hits=limit))
        done = await asyncio.gather(*jobs, return_exceptions=True)
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        extra: dict[str, Any] = {}
        for name, outcome in zip(runnable, done[: len(runnable)], strict=True):
            if isinstance(outcome, BaseException):
                errors[name] = str(outcome)
                continue
            results[name] = {k: v for k, v in outcome.items() if k in {"total", "records", "attribution"}}
        if fulltext:
            outcome = done[-1]
            if isinstance(outcome, BaseException):
                errors["internet_archive_fulltext"] = str(outcome)
            else:
                extra["fulltext"] = outcome
        notes: list[str] = []
        if date_from is not None or date_to is not None:
            notes.append("Date filters apply to internet_archive, dpla and nara only.")
        return {
            "query": query,
            "results": results,
            **extra,
            "errors": errors,
            "skipped": skipped,
            "notes": notes,
        }

    @mcp.tool()
    async def get_record(source: str, record_id: str) -> dict[str, Any]:
        """Fetch one record in the normalised shape, including its media files and rights.

        Args:
            source: internet_archive, commons, dpla, nara or smithsonian.
            record_id: The id from a search result (an Internet Archive identifier, a Commons "File:" title,
                a DPLA id, a NARA naId, or a Smithsonian id).
        """
        return {"record": await get_record_dict(source, record_id)}

    @mcp.tool()
    async def download_media(
        source: str,
        record_id: str,
        media_index: int = 0,
        media_kind: str | None = None,
        overwrite: bool = False,
        max_mb: int | None = None,
    ) -> dict[str, Any]:
        """Download one original file from a record into the local cache and write a provenance sidecar.

        The sidecar (`<file>.provenance.json`) records the source, URL, retrieval time, SHA-256 and the rights
        the holder stated, so the file can be traced later. Only https URLs on public addresses are fetched.
        If `rights.reuse` is not `free` the result carries a `rights_warning`: treat the file as reference only.

        Args:
            source: internet_archive, commons, dpla, nara or smithsonian.
            record_id: The record's id, as returned by search.
            media_index: Which entry of the record's `media` list to fetch (default the first).
            media_kind: Pick the first media of this kind instead (pdf, text, image, audio, video, archive).
            overwrite: Fetch again even if the file is already cached.
            max_mb: Refuse files larger than this (default 250, or HERITAGE_MCP_MAX_DOWNLOAD_MB).
        """
        record = await get_record_dict(source, record_id)
        media = record.get("media") or []
        if not media:
            raise HeritageError("this record has no downloadable media")
        if media_kind:
            matches = [m for m in media if m.get("kind") == media_kind]
            if not matches:
                raise HeritageError(
                    f"no media of kind {media_kind!r}; kinds present: {sorted({m.get('kind') for m in media})}"
                )
            chosen = matches[0]
        else:
            if not 0 <= media_index < len(media):
                raise HeritageError(f"media_index must be between 0 and {len(media) - 1}")
            chosen = media[media_index]
        result = await download_file(
            source=source,
            record_id=str(record["id"]),
            url=chosen["url"],
            title=record.get("title"),
            landing_url=record.get("landing_url"),
            mime=chosen.get("mime"),
            rights=record.get("rights"),
            max_bytes=max_mb * 1024 * 1024 if max_mb else None,
            overwrite=overwrite,
        )
        if (record.get("rights") or {}).get("reuse") != "free":
            result["rights_warning"] = (
                "The holder did not mark this item free to reuse. Use it as reference and check the landing page "
                "before publishing or redistributing it."
            )
        return result
