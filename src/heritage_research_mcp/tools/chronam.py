"""Chronicling America tools (Library of Congress newspaper pages)."""

from __future__ import annotations

from typing import Any

from ..compat import FastMCP
from ..sources import chronam_bulk as bulk
from ..sources import chronicling_america as chronam


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def chronam_search(
        query: str,
        date_from: str | None = None,
        date_to: str | None = None,
        state: str | None = None,
        phrase: bool = False,
        limit: int = 10,
        page: int = 1,
    ) -> dict[str, Any]:
        """Search digitised US newspaper pages in Chronicling America (Library of Congress), 1690 to the present.

        Searches the OCR text of each page. OCR is raw and uncorrected, so names are often garbled: try spelling
        variants. Matches in `description` are in **bold**. The Library allows 20 requests a minute and can block
        a client for about an hour; this tool waits between requests and, if the Library blocks or challenges it,
        reports that and stops for 10 minutes. When that happens wait; do not keep retrying.

        Args:
            query: Words to find on a page.
            date_from: Earliest date: YYYY, YYYY-MM or YYYY-MM-DD.
            date_to: Latest date, same formats.
            state: Only newspapers published in this US state, e.g. virginia.
            phrase: Match the words as an exact phrase.
            limit: Pages to return (1 to 100).
            page: Result page number from 1.
        """
        return await chronam.search(
            query, date_from=date_from, date_to=date_to, state=state, phrase=phrase, rows=limit, page=page
        )

    @mcp.tool()
    async def chronam_get_page(page_id: str) -> dict[str, Any]:
        """Get one newspaper page's metadata, rights and links to its PDF, OCR text, ALTO XML and image.

        Args:
            page_id: The id from a search result, like sn83030214/1863-06-11/ed-1/2 (LCCN, date, edition, page).
        """
        return await chronam.get_page(page_id)

    @mcp.tool()
    async def chronam_page_text(page_id: str) -> dict[str, Any]:
        """Read the raw OCR text of one newspaper page (up to 100,000 characters).

        Args:
            page_id: The id from a search result, like sn83030214/1863-06-11/ed-1/2.
        """
        return await chronam.page_text(page_id)

    @mcp.tool()
    async def chronam_bulk_plan(
        date_from: str, date_to: str, state: str | None = None, lccn: str | None = None
    ) -> dict[str, Any]:
        """Work out what it takes to index whole years of newspapers locally, without downloading any of it.

        The Library publishes OCR as one large archive per digitisation batch; each holds many papers and years.
        This lists which batches hold papers published in the range (optionally one state or one paper) and how
        many GB they total, dropping archives that start after the range (found with small range requests).
        A month narrows what is kept, not the download: the big saving is naming the papers (lccn). Tell the user the size before starting. Virginia for 1863 is about 43 GB; every US
        paper of 1863 is about 460 GB.

        Args:
            date_from: First date: YYYY, YYYY-MM or YYYY-MM-DD.
            date_to: Last date, same formats.
            state: Only newspapers published in this state, e.g. virginia.
            lccn: Only this newspaper (an LCCN such as sn84024738).
        """
        return await bulk.plan(date_from, date_to, state=state, lccns=[lccn] if lccn else None)

    @mcp.tool()
    async def chronam_bulk_start(
        date_from: str,
        date_to: str,
        state: str | None = None,
        lccn: str | None = None,
        max_gb: float = 60.0,
        verify: bool = False,
    ) -> dict[str, Any]:
        """Start building the local index in the background; returns at once. Check progress with chronam_bulk_status.

        Streams each archive, keeps only the OCR text of pages dated in the range, checks the Library's SHA-256,
        and can be run again after an interruption (finished batches are skipped). It can take hours for large
        plans, so for those the user may prefer the terminal command `heritage-research-mcp chronam-index`.
        Refuses plans over max_gb.

        Args:
            date_from: First date: YYYY, YYYY-MM or YYYY-MM-DD.
            date_to: Last date, same formats.
            state: Only newspapers published in this state, e.g. virginia.
            lccn: Only this newspaper (an LCCN such as sn84024738).
            max_gb: Refuse to download more than this many GB.
            verify: Read every archive to the end and check its SHA-256. By default reading stops once the range
                has been passed, which downloads less but leaves that archive unchecked against the Library's hash.
        """
        return await bulk.start(
            date_from, date_to, state=state, lccns=[lccn] if lccn else None, max_gb=max_gb, verify=verify
        )

    @mcp.tool()
    async def chronam_bulk_status() -> dict[str, Any]:
        """Progress of the background indexing job, and what the local index holds (pages, dates, papers)."""
        return bulk.job_status()

    @mcp.tool()
    async def chronam_local_search(
        query: str,
        date_from: str | None = None,
        date_to: str | None = None,
        state: str | None = None,
        lccn: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search the OCR text of the newspaper pages in the local index (built with chronam_bulk_start).

        Ranked by relevance, matches in **bold**. Words are ANDed, "quoted phrases" stay together, uppercase OR
        joins spellings. OCR is raw and uncorrected: try spelling variants. Makes no network request. Read a
        whole page with chronam_page_text.

        Args:
            query: Words or phrases to find.
            date_from: Earliest date: YYYY, YYYY-MM or YYYY-MM-DD.
            date_to: Latest date, same formats.
            state: Only papers published in this state.
            lccn: Only this paper.
            limit: Results to return (1 to 50).
            offset: Skip this many results, to page.
        """
        return await bulk.search(
            query, date_from=date_from, date_to=date_to, state=state, lccn=lccn, rows=limit, offset=offset
        )
