"""Richmond Daily Dispatch tools (a local index, built once with dispatch_prepare)."""

from __future__ import annotations

from typing import Any

from ..compat import FastMCP
from ..sources import dispatch


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def dispatch_prepare(refresh: bool = False) -> dict[str, Any]:
        """Build the local index the Richmond Daily Dispatch tools search. Run it once; it takes about a minute.

        Downloads the University of Richmond's TEI edition of the paper (about 92 MB, every issue from
        1860-11-01 to 1865-12-30) into the cache, with a provenance sidecar, and indexes it. Does nothing if the
        index already exists.

        Args:
            refresh: Download and index again, for example if the university has published a new edition.
        """
        return await dispatch.prepare(refresh=refresh)

    @mcp.tool()
    async def dispatch_search(
        query: str,
        date_from: str | None = None,
        date_to: str | None = None,
        section: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search the full text of the Richmond Daily Dispatch, 1860 to 1865, ranked by relevance.

        Needs the local index (dispatch_prepare). Words are ANDed, "double quotes" keep a phrase together and an
        uppercase OR joins spellings (Mosby OR Moseby). Matching ignores case and common word endings. Matches in
        `description` are in **bold**. The text is the university's keyed transcription, so names can still be
        misspelt as printed or as keyed; try variants.

        Args:
            query: Words or phrases to find.
            date_from: Earliest date: YYYY, YYYY-MM or YYYY-MM-DD.
            date_to: Latest date, same formats.
            section: Only this kind of item: article, advert, orders, death, married, died, notice, poem, letter.
            limit: Results to return (1 to 50).
            offset: Skip this many results, to page.
        """
        return await dispatch.search(
            query, date_from=date_from, date_to=date_to, section=section, rows=limit, offset=offset
        )

    @mcp.tool()
    async def dispatch_get_article(article_id: str) -> dict[str, Any]:
        """Get one Dispatch article's full text, page, rights and a link to the issue's TEI file.

        Args:
            article_id: The id from a search result, like D_021_040/2/17 (TEI file, page, article number).
        """
        return await dispatch.get_article(article_id)

    @mcp.tool()
    async def dispatch_issue(issue: str, include_adverts: bool = False) -> dict[str, Any]:
        """List the articles in one issue, with page, section and headline, to browse a day's paper.

        Args:
            issue: A date (YYYY-MM-DD) or a TEI file name such as D_021_040. A date normally matches one issue.
            include_adverts: Also list advertisements and sales notices (hundreds on some days).
        """
        return await dispatch.issue(issue, include_adverts=include_adverts)
