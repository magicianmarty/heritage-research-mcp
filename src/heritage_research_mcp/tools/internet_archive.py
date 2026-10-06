"""Internet Archive tools."""

from __future__ import annotations

from typing import Any

from ..compat import FastMCP
from ..sources import internet_archive as ia


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def ia_search(
        query: str,
        mediatype: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        collection: str | None = None,
        rows: int = 10,
        page: int = 1,
        sort: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        """Search Internet Archive item metadata (title, author, subject, description). Not the text inside books.

        Use ia_fulltext_search to find a phrase inside scanned books. The query accepts Lucene syntax, for
        example `creator:(mosby) AND subject:"Virginia"`.

        Args:
            query: The search.
            mediatype: texts, image, audio, movies, software, data, etc.
            year_from: Earliest publication year.
            year_to: Latest publication year.
            collection: Restrict to a collection identifier, e.g. americana.
            rows: Results per page (1 to 100).
            page: Page number from 1.
            sort: For example "downloads desc" or "date asc".
            kind: text, image, map, audio or video, translated into Internet Archive terms (maps are matched on
                the "maps" subject and the map collections, so results are noisy).
        """
        return await ia.search(
            query,
            mediatype=mediatype,
            year_from=year_from,
            year_to=year_to,
            collection=collection,
            rows=rows,
            page=page,
            sort=sort,
            kind=kind,
        )

    @mcp.tool()
    async def ia_fulltext_search(query: str, hits: int = 10, page: int = 1) -> dict[str, Any]:
        """Search the OCR text inside Internet Archive books and reports, with snippets and page numbers.

        This is the way to find a name, place or phrase inside a memoir, regimental history or official report.
        Matches are wrapped in ** in the snippets. It uses an experimental endpoint that may change.

        Args:
            query: Words or a "quoted phrase".
            hits: Results to return (1 to 50).
            page: Page number from 1.
        """
        return await ia.fulltext_search(query, hits=hits, page=page)

    @mcp.tool()
    async def ia_get_item(identifier: str) -> dict[str, Any]:
        """Get one item's metadata, rights and downloadable files (PDF, OCR text, page images)."""
        return await ia.get_item(identifier)

    @mcp.tool()
    async def ia_read_text(identifier: str, start: int = 0, length: int = 4000) -> dict[str, Any]:
        """Read a slice of an item's OCR text by character offset (up to 20,000 characters at a time).

        Use ia_grep_text first to find the offset you want.
        """
        return await ia.read_text(identifier, start=start, length=length)

    @mcp.tool()
    async def ia_grep_text(
        identifier: str,
        pattern: str,
        regex: bool = False,
        ignore_case: bool = True,
        context: int = 200,
        max_matches: int = 20,
    ) -> dict[str, Any]:
        """Find every occurrence of a word or phrase in an item's OCR text, with surrounding context and offsets.

        The text is fetched once and held in memory, so repeated searches of the same book are fast.

        Args:
            identifier: The Internet Archive identifier.
            pattern: Text to find (a regular expression only if `regex` is true).
            regex: Treat the pattern as a regular expression.
            ignore_case: Ignore capitalisation.
            context: Characters of context on each side (0 to 1000).
            max_matches: Most matches to return (1 to 100); the total is always reported.
        """
        return await ia.grep_text(
            identifier,
            pattern,
            regex=regex,
            ignore_case=ignore_case,
            context=context,
            max_matches=max_matches,
        )
