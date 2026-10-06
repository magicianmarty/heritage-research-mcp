"""DPLA tools (need a free API key)."""

from __future__ import annotations

from typing import Any

from ..compat import FastMCP
from ..sources import dpla


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def dpla_search(
        q: str | None = None,
        title: str | None = None,
        creator: str | None = None,
        subject: str | None = None,
        place_state: str | None = None,
        date_after: str | None = None,
        date_before: str | None = None,
        type: str | None = None,
        provider: str | None = None,
        data_provider: str | None = None,
        page: int = 1,
        page_size: int = 10,
        sort_by: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        """Search the Digital Public Library of America for items held by US libraries, archives and museums.

        Needs DPLA_API_KEY. Give a search term, any filter, or both. DPLA indexes descriptions, not the item
        itself: follow `landing_url` to the holding institution, and read `rights` before reusing anything.

        Args:
            q: Free-text search across the record.
            title: Words in the title.
            creator: Creator or author.
            subject: Subject heading.
            place_state: US state name, e.g. Virginia.
            date_after: Earliest date (YYYY or YYYY-MM-DD).
            date_before: Latest date.
            type: image, text, sound, moving image, physical object, etc.
            provider: DPLA hub name.
            data_provider: Contributing institution name.
            page: Page number from 1.
            page_size: Results per page (1 to 100).
            sort_by: A DPLA field such as sourceResource.date.begin.
            kind: text, image, map, audio or video. Maps are images with the subject "Maps".
        """
        return await dpla.search(
            q=q, title=title, creator=creator, subject=subject, place_state=place_state, date_after=date_after,
            date_before=date_before, type=type, provider=provider, data_provider=data_provider, page=page,
            page_size=page_size, sort_by=sort_by, kind=kind,
        )  # fmt: skip

    @mcp.tool()
    async def dpla_get_item(item_id: str) -> dict[str, Any]:
        """Get one DPLA item by its id. Needs DPLA_API_KEY."""
        return await dpla.get_item(item_id)
