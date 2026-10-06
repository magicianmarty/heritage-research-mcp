"""Smithsonian Open Access tools (need a free api.data.gov key)."""

from __future__ import annotations

from typing import Any

from ..compat import FastMCP
from ..sources import smithsonian as si


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def si_search(
        q: str,
        rows: int = 10,
        start: int = 0,
        sort: str | None = None,
        type: str | None = None,
        row_group: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Search Smithsonian Open Access records. Needs SMITHSONIAN_API_KEY.

        Records whose media are all marked CC0 come back with rights.reuse "free"; otherwise "Usage conditions
        apply" is surfaced as restricted.

        Args:
            q: Search words; supports AND/OR and fielded terms such as topic:Cavalry (see si_terms).
            rows: Results to return (1 to 100).
            start: Offset of the first row.
            sort: relevancy (default), newest or updated.
            type: EDAN record type, e.g. edanmdm.
            row_group: objects or archives.
            category: Search within art_design, history_culture or science_technology instead.
        """
        return await si.search(
            q, rows=rows, start=start, sort=sort, type=type, row_group=row_group, category=category
        )

    @mcp.tool()
    async def si_get_content(content_id: str) -> dict[str, Any]:
        """Get one Smithsonian record by id (for example edanmdm-nmaahc_2012.36.4ab). Needs SMITHSONIAN_API_KEY."""
        return await si.get_content(content_id)

    @mcp.tool()
    async def si_terms(category: str, starts_with: str | None = None, limit: int = 200) -> dict[str, Any]:
        """List the vocabulary for a search field: culture, data_source, date, object_type, online_media_type,
        place, topic or unit_code. Useful for building fielded queries. Needs SMITHSONIAN_API_KEY."""
        return await si.terms(category, starts_with=starts_with, limit=limit)

    @mcp.tool()
    async def si_stats() -> dict[str, Any]:
        """Counts of CC0 objects and media in the collection. Needs SMITHSONIAN_API_KEY."""
        return await si.stats()
